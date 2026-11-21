# app/middleware/request_id.py
"""请求级 trace_id 初始化中间件（纯 ASGI 实现）。

【本轮修复】
1. 高危 1（链路割裂）：在请求入口**同时**初始化两套 ContextVar
   （app.core.context.RequestContext 与 app.core.trace_context），
   并把中间件解析出的 trace_id 显式写进 trace_context —— 日志 / 响应体 / 响应头 /
   LLM 调用记录 / token / tracing span 从此共用同一个 trace_id。
2. 中危 6（BaseHTTPMiddleware 包 SSE）：改为纯 ASGI 中间件。
   BaseHTTPMiddleware 会把响应体经内存流转发，破坏 StreamingResponse 的真流式
   （首包缓冲、背压异常），并且额外占用线程；同时它 dispatch 里 set 的 contextvar
   对 call_next 派生的子任务不保证可见。纯 ASGI 中间件在调用下游**之前** set，
   下游 task 创建时会继承该上下文，流式 generator 也能读到。
3. 低危（日志注入）：入站 X-Trace-Id 做字符白名单 + 长度截断，
   防止外部传入换行/控制字符污染 key=value 结构化日志或撑爆日志行。
4. 低危（上下文泄漏）：finally 里 reset ContextVar，避免线程/任务复用串号。

注册方式不变：app.add_middleware(RequestIdMiddleware)
（保持它是**最后 add** 的那个，即在中间件栈最外层，内层 BaseHTTPMiddleware
  派生的 task 才能继承这里 set 的 contextvar。）
"""
from __future__ import annotations

import re
import uuid

from app.core.context import RequestContext
from app.core.trace_context import set_trace_context, clear_trace_context

# 只允许字母数字、下划线、连字符、点；其余（含换行、空格、控制字符）一律丢弃
_TRACE_ID_ALLOWED = re.compile(r"[^A-Za-z0-9_.\-]")
_TRACE_ID_MAX_LEN = 64
_TRACE_ID_HEADER = b"x-trace-id"


def sanitize_trace_id(raw: str | None) -> str:
    """净化外部传入的 trace_id：白名单字符 + 截断；非法/为空则新生成。"""
    if not raw:
        return ""
    cleaned = _TRACE_ID_ALLOWED.sub("", raw.strip())[:_TRACE_ID_MAX_LEN]
    return cleaned


def new_trace_id() -> str:
    return uuid.uuid4().hex


class RequestIdMiddleware:

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw = None
        for name, value in scope.get("headers") or []:
            if name == _TRACE_ID_HEADER:
                try:
                    raw = value.decode("latin-1")
                except Exception:
                    raw = None
                break

        trace_id = sanitize_trace_id(raw) or new_trace_id()

        # —— 两套上下文统一初始化（高危 1 的根治点）——
        RequestContext.set_trace_id(trace_id)
        set_trace_context(trace_id=trace_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers[:] = [
                    h for h in headers
                    if (h[0].lower() if isinstance(h[0], bytes) else str(h[0]).lower().encode()) != _TRACE_ID_HEADER
                ]
                headers.append((_TRACE_ID_HEADER, trace_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # 防线程/任务复用导致的 trace_id 串号
            RequestContext.set_trace_id("")
            clear_trace_context()
