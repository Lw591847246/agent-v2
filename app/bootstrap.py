# app/bootstrap.py
"""启动装配 + Skill 自检 + 安全基线自检。

【本轮修复】
1. 启动期强制校验签名密钥（assert_secure_config 现在在所有环境都会因"无密钥"而 fail-fast，
   不再依赖 APP_ENV 是否设对）；同时新增 _self_check_security()：
      - auth.require_download_auth=false 时打**显著** WARNING（这是上一版的头号裸奔开关）；
      - 白名单里出现业务前缀时告警；
      - CORS 通配 + allow_credentials 组合时自动降级为不携带凭据并告警。
2. CORS 从写死的 ["*"] 改为配置驱动（cors.allow_origins / methods / headers /
   allow_credentials）。默认仍是 * 以兼容现有前端，但生产必须收敛：
      cors:
        allow_origins: ["https://your-frontend.example.com"]
        allow_credentials: false
"""
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.loader import config
from app.config.settings import app_settings, auth_settings, cors_settings
from app.core.logger import logger
from app.infrastructure.repositories.mysql_pool import mysql_pool
from app.middleware.access_log import AccessLogMiddleware
from app.middleware.exception import register_exception_handlers
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.utils.signed_url import assert_secure_config


def create_app(lifespan=None) -> FastAPI:
    # 所有环境都必须有可用签名密钥，否则任何人都能伪造图片/缩略图/下载链接签名
    assert_secure_config()

    # 交互式文档 / OpenAPI 规范会枚举整套接口面，给攻击者当"地图"。
    # 默认：非生产开放、生产关闭（app.docs_enabled 可显式覆盖）。
    docs_enabled = config.get("app.docs_enabled", None)
    if docs_enabled is None:
        docs_enabled = str(app_settings.env or "").strip().lower() not in {
            "prod", "production", "prd"
        }
    docs_enabled = bool(docs_enabled)
    # B3 defense in depth: warn when APP_ENV and yml app.env disagree. Env decision
    # (docs / signing secret) is unified on APP_ENV (app_settings.env now prefers it);
    # here we only surface the mismatch to the log so ops cannot be misled.
    _yml_env = str(config.get("app.env") or "").strip().lower()
    _deploy_env = (os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    if _deploy_env and _yml_env and _deploy_env != _yml_env:
        logger.warning(
            f"[Security] APP_ENV={_deploy_env} disagrees with application.yml app.env={_yml_env}: "
            f"env decision uses APP_ENV (docs / signing secret). Unify both configs to avoid "
            f"silent \"thought docs were off but they were on\" mistakes."
        )
    if not docs_enabled:
        logger.info("[Security] /docs、/redoc、/openapi.json 已关闭（app.docs_enabled=false）")

    app = FastAPI(
        title=app_settings.name,
        version=app_settings.version,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    origins = list(cors_settings.allow_origins or [])
    allow_credentials = bool(cors_settings.allow_credentials)
    if allow_credentials and ("*" in origins or not origins):
        logger.warning(
            "[Security] CORS：allow_credentials=true 与 allow_origins=* 不能并存，"
            "已强制 allow_credentials=false；请显式配置 cors.allow_origins 白名单"
        )
        allow_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_methods=list(cors_settings.allow_methods or ["*"]),
        allow_headers=list(cors_settings.allow_headers or ["*"]),
        allow_credentials=allow_credentials,
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AccessLogMiddleware)
    # RequestIdMiddleware 保持在**最后 add**（即中间件栈最外层）：
    # 纯 ASGI 实现在调用下游前 set contextvar，内层 BaseHTTPMiddleware 派生的
    # task 才能继承；顺序变了会导致内层日志取不到 trace_id。
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)
    mysql_pool.init()

    # 1. 发现并注册所有 Skill（必须在 include_router 前）
    from app.registry import auto_discover_skills
    skill_count = auto_discover_skills()
    logger.info(f"[Bootstrap] 共注册 {skill_count} 个 Skill")

    # 2. 自检：Intent 是否全覆盖
    _self_check_intents()

    # 3. 自检：安全基线
    _self_check_security()

    # 4. 挂载路由
    from app.api.router import api_router
    app.include_router(api_router)

    logger.info(f"应用启动 env={app_settings.env}")
    return app


def _self_check_intents() -> None:
    """启动自检：Intent 是否都有对应 Skill。"""
    from app.registry.skill_registry import registry
    from app.scheduler.intents import Intent

    covered = set(registry.intents())
    missing = [i.value for i in Intent if i.value not in covered]

    if missing:
        logger.warning(f"[Bootstrap] 未注册的 Intent（将降级到 chat）：{missing}")
    else:
        logger.info(f"[Bootstrap] ✅ 所有 {len(list(Intent))} 个 Intent 已注册")


def _self_check_security() -> None:
    """启动自检：把"配置层能一把裸奔全站"的开关显式打出来。"""
    bad = []

    try:
        require_dl = bool(config.get("auth.require_download_auth", True))
    except Exception:
        require_dl = True
    if not require_dl:
        bad.append(
            "auth.require_download_auth=false —— 未签名且未登录的下载会被放行"
            "（只该在灰度期短暂使用，请立刻置 true）"
        )

    # 白名单里不该出现业务前缀
    _BIZ_HINTS = ("/agent", "/tts", "/upload", "/download", "/sign", "/job")
    for rule in (auth_settings.white_list or []):
        r = (rule or "").strip()
        if r and any(h in r for h in _BIZ_HINTS):
            bad.append(f"auth.white_list 含业务路径：{r}（会让业务接口匿名可访问）")

    # 运维端点既无 token 也无 IP 白名单 -> 会被 fail-closed 成 404，提示运维
    if not auth_settings.ops_token and not auth_settings.ops_allow_ips:
        logger.info(
            "[Security] 未配置 auth.ops_token / auth.ops_allow_ips："
            f"运维端点 {auth_settings.ops_paths} 将统一返回 404（预期行为）"
        )

    if bad:
        for b in bad:
            logger.error(f"[Security][BASELINE] {b}")


