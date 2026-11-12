# app/bootstrap.py
"""启动装配 + Skill 自检。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import app_settings
from app.core.logger import logger
from app.middleware.access_log import AccessLogMiddleware
from app.middleware.exception import register_exception_handlers
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.infrastructure.repositories.mysql_pool import mysql_pool


def create_app() -> FastAPI:
    app = FastAPI(title=app_settings.name, version=app_settings.version)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)
    mysql_pool.init()

    # 1. 发现并注册所有 Skill（必须在 include_router 前）
    from app.registry import auto_discover_skills
    skill_count = auto_discover_skills()
    logger.info(f"[Bootstrap] 共注册 {skill_count} 个 Skill")

    # 2. 自检：Intent 是否全覆盖
    _self_check_intents()

    # 3. 挂载路由
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