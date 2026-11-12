# app/middleware/rate_limit.py
"""限流中间件"""
import time
from collections import defaultdict, deque
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from app.config.loader import config


class RateLimitMiddleware(BaseHTTPMiddleware):

    def __init__(self, app):
        super().__init__(app)
        self.enabled = config.get("rate_limit.enabled", True)
        self.default_qps = config.get("rate_limit.default_qps", 10)
        self._windows = defaultdict(deque)

    def _get_limit(self, path: str) -> int:
        if path.startswith("/agent/v1/chat"):
            return config.get("rate_limit.chat_qps", 5)
        if "upload" in path or "process-file" in path:
            return config.get("rate_limit.upload_qps", 2)
        return self.default_qps

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        key = f"{ip}:{request.url.path}"
        now = time.time()
        limit = self._get_limit(request.url.path)

        window = self._windows[key]
        while window and now - window[0] > 1:
            window.popleft()

        if len(window) >= limit:
            return JSONResponse(
                status_code=429,
                content={"code": 429, "message": "请求过于频繁，请稍后重试"},
            )

        window.append(now)
        return await call_next(request)