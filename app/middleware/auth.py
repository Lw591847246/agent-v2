# app/middleware/auth.py
"""鉴权拦截器（基于 Java SSO 写入 Redis 的 Token）

【本轮修复】
1. 低危（白名单前缀匹配过宽）：原先的裸 startswith 会连带放行 /auth/loginXX、
   /docsXXX 这类"白名单前缀 + 额外段"的路径。改为**精确匹配 或 前缀 + "/" 边界**
   （配置里以 * 结尾的条目仍表示前缀匹配，需显式声明）。
2. 低危（TOCTOU）：原来 exists 之后再 get，两次往返之间
   token 可能已过期/被登出，存在极小竞态窗口（且多一次 RTT）。改为**单次 get**。
3. 中危 6（运维端点匿名可访问）：/metrics 会把任务状态分布、全站累计 input/output
   token 等业务量指标原样吐给未认证访问者。white_list 里已移除 /metrics、/docs、
   /openapi.json。注意本项目的鉴权是**依赖式**（Depends 挂在路由上），/metrics 挂在
   router.py 的 public 组、无依赖，因此真正的守卫做成依赖挂在端点上
   （见 app/api/deps_ops.py 的 ops_guard + health_controller）；本文件里的
   OpsNotFoundException 分支只是**纵深防御**，防止有人把运维路径塞进 white_list。
   策略（两处共用）：ops_token 常量时间比对 -> 或 ops_allow_ips -> 都没有则 404。
4. 加固：userId 必须为正整数（原逻辑 int() 失败才报错，负数/0 会静默通过）；
   认证失败日志不打印 token 内容。

新增配置（都有安全默认值）：
    auth:
      white_list: [ /auth/login, /auth/register, /healthz, /readyz, /health ]
      ops_paths: [ /metrics, /docs, /docs/oauth2-redirect, /redoc, /openapi.json ]
      ops_token: ""
      ops_allow_ips: []
      ops_trust_forwarded_for: false
"""
import json

from fastapi import Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config.settings import auth_settings
from app.core.context import RequestContext
from app.core.exceptions import AuthException
from app.core.logger import logger
from app.core.redis_client import redis_client


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


class AuthInterceptor:

    # ---------------- 白名单 ----------------

    def _is_white_list(self, path: str) -> bool:
        """精确匹配，或以 '/' 为边界的前缀匹配；不再用裸 startswith。"""
        for rule in auth_settings.white_list or []:
            rule = (rule or "").strip()
            if not rule:
                continue
            if rule.endswith("*"):
                base = rule.rstrip("*").rstrip("/")
                if path == base or path.startswith(base + "/"):
                    return True
            elif path == rule:
                return True
        return False

    # ---------------- 运维端点守卫（与 app/api/deps_ops.py 共用实现） ----------------
    # 说明：/metrics 这类端点挂在 router.py 的 public 组、没有任何路由依赖，
    # 真正的拦截由 health_controller 上挂的 Depends(ops_guard) 完成；
    # 这里保留一道**纵深防御**：万一有人把运维路径塞进 white_list 或加了走
    # auth_interceptor 的重复路由，这里同样按同一套策略拒绝。

    def _is_ops_path(self, path: str) -> bool:
        return path in set(_as_list(getattr(auth_settings, "ops_paths", None)))

    # ---------------- 主流程 ----------------

    async def __call__(self, request: Request) -> dict:
        path = request.url.path

        if self._is_white_list(path):
            return {"userId": 0, "username": "anonymous", "anonymous": True}

        if self._is_ops_path(path):
            try:
                from app.api.deps_ops import check_ops_access
                check_ops_access(request, path)
            except StarletteHTTPException:
                logger.warning(
                    f"[{RequestContext.get_trace_id()}] 运维端点拒绝访问 path={path}"
                )
                # 对外表现为 404（不暴露端点存在）；由 exception handler 统一出参
                raise OpsNotFoundException()
            return {"userId": 0, "username": "ops", "anonymous": True}

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthException("缺少访问令牌")

        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise AuthException("访问令牌为空")

        # 单次 get：消除 exists+get 的 TOCTOU 窗口，并省一次 RTT
        user_json = redis_client.get(token)
        if not user_json:
            raise AuthException("访问令牌无效或已过期")

        try:
            user_info = json.loads(user_json)
        except (json.JSONDecodeError, TypeError):
            raise AuthException("用户信息格式错误")

        if not isinstance(user_info, dict):
            raise AuthException("用户信息格式错误")

        raw_uid = user_info.get("userId")
        try:
            user_id = int(raw_uid)
        except (TypeError, ValueError):
            raise AuthException("用户信息缺少合法 userId")
        if user_id <= 0:
            raise AuthException("用户信息缺少合法 userId")

        RequestContext.set_user_id(str(user_id))
        user_info["userId"] = user_id

        logger.info(
            f"[{RequestContext.get_trace_id()}] 用户认证成功 "
            f"userId={user_id} username={user_info.get('username')}"
        )
        return user_info


class OpsNotFoundException(StarletteHTTPException):
    """运维端点未授权：对外表现为 404（不暴露端点是否存在）。"""

    def __init__(self):
        super().__init__(status_code=404, detail="not found")


auth_interceptor = AuthInterceptor()
