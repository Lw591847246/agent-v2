# app/middleware/access_log.py
"""访问日志中间件（纯 ASGI 实现）。

【本轮修复 · 中危 6】原实现是 BaseHTTPMiddleware：
  1) 会把 StreamingResponse（chat 主链路的 SSE）经内存流转发，破坏真流式；
  2) 每个请求额外占用一个线程；
  3) 只能在响应完整返回后拿 status_code，流式响应的"首包耗时"完全看不到。

改为纯 ASGI：只在 http.response.start 上取 status_code 并计时，零拷贝、零额外线程，
SSE 的 cost 反映的是**整条流的结束时间**，另记 first_byte_ms 便于排查首包慢。
"""
from __future__ import annotations

import time

from app.core.context import RequestContext
from app.core.logger import logger


class AccessLogMiddleware:

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.time()
        state = {"status": 0, "first_byte": 0.0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message.get("status", 0)
                state["first_byte"] = (time.time() - start) * 1000
            await send(message)

        method = scope.get("method", "-")
        path = scope.get("path", "-")
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            cost = (time.time() - start) * 1000
            logger.error(
                f"[{RequestContext.get_trace_id()}] {method} {path} "
                f"status=500(unhandled) cost={cost:.1f}ms"
            )
            raise

        cost = (time.time() - start) * 1000
        logger.info(
            f"[{RequestContext.get_trace_id()}] {method} {path} "
            f"status={state['status'] or 500} cost={cost:.1f}ms "
            f"first_byte={state['first_byte']:.1f}ms"
        )
