# app/middleware/auth.py
"""鉴权拦截器（基于 Java SSO 写入 Redis 的 Token）"""
import json
from fastapi import Request
from app.config.settings import auth_settings
from app.core.exceptions import AuthException
from app.core.redis_client import redis_client
from app.core.context import RequestContext
from app.core.logger import logger


class AuthInterceptor:

    @staticmethod
    def _is_white_list(path: str) -> bool:
        return any(path.startswith(p) for p in auth_settings.white_list)

    async def __call__(self, request: Request) -> dict:
        path = request.url.path

        # 白名单直接放行
        if self._is_white_list(path):
            return {"userId": "anonymous", "username": "anonymous"}

        # 从请求头提取 token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthException("缺少访问令牌")

        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise AuthException("访问令牌为空")

        # 去 Redis 查询（Java 存的是纯 token 作为 key）
        if not redis_client.exists(token):
            raise AuthException("访问令牌无效或已过期")

        user_json = redis_client.get(token)
        if not user_json:
            raise AuthException("用户信息不存在")

        try:
            user_info = json.loads(user_json)
        except json.JSONDecodeError:
            raise AuthException("用户信息格式错误")

        # 写入请求上下文
        user_id = str(user_info.get("userId", ""))
        RequestContext.set_user_id(user_id)

        logger.info(
            f"[{RequestContext.get_trace_id()}] 用户认证成功 "
            f"userId={user_id} username={user_info.get('username')}"
        )

        return user_info


auth_interceptor = AuthInterceptor()