# app/core/trace_context.py
"""业务侧链路上下文（LLM 调用记录 / token 统计 / tracing span 共用）。

【本轮修复 · 高危 1】与 RequestContext（中间件写入的请求级 trace_id）打通。

原实现
------
    def set_trace_context(trace_id="", ...):
        tid = trace_id or uuid.uuid4().hex      # ← 各自现生成新 uuid

controller 里调的都是 set_trace_context(session_id=..., user_id=...)（不传 trace_id），
于是本模块的 trace_id 永远是**新 uuid**；而 logger / access_log / 异常响应体 /
X-Trace-Id 响应头走的是 app.core.context.RequestContext 那套（来自 X-Trace-Id 头
或 16 位 uuid）。两套 ContextVar 同名但对象不同、无任何交叉赋值 ——
**按响应头给的 trace_id 去 grep，永远捞不到对应的 LLM 调用记录 / token / span**，
链路看着通、实际是断的。

修复
----
优先级：**显式入参 > RequestContext（中间件）> 本模块已有值 > 新生成**。
零侵入：所有 controller 一行都不用改，从源头把两套 id 统一。
兜底路径（RequestContext 不可用 / 无请求上下文，如后台线程、脚本）保持原行为。
"""
from __future__ import annotations

import uuid
import contextvars

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="")
_user_id: contextvars.ContextVar[int] = contextvars.ContextVar("user_id", default=0)


def _request_trace_id() -> str:
    """取中间件（app.core.context.RequestContext）写入的请求级 trace_id。

    取不到（循环导入 / 无请求上下文 / 中间件未跑到）一律返回 ""，由调用方兜底。
    """
    try:
        from app.core.context import RequestContext
    except Exception:
        return ""
    try:
        tid = RequestContext.get_trace_id() or ""
    except Exception:
        return ""
    # RequestContext.get_trace_id() 空值时返回占位符 "-"，不是真实 id
    if not tid or tid == "-":
        return ""
    return tid


def set_trace_context(trace_id: str = "", session_id: str = "", user_id: int = 0) -> str:
    """写入链路上下文；trace_id 缺省时继承中间件的请求级 id，不再各自 uuid4。"""
    tid = (trace_id or "").strip() or _request_trace_id() or _trace_id.get() or uuid.uuid4().hex
    _trace_id.set(tid)
    if session_id:
        _session_id.set(session_id)
    if user_id:
        _user_id.set(user_id)
    return tid


def ensure_trace_id() -> str:
    tid = _trace_id.get() or _request_trace_id()
    if not tid:
        tid = set_trace_context()
    else:
        _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get() or ensure_trace_id()


def get_session_id() -> str:
    return _session_id.get()


def get_user_id() -> int:
    return _user_id.get()


def clear_trace_context() -> None:
    """清理当前上下文的三个值（后台线程 / 定时任务复用线程时必须调用，防串号）。"""
    _trace_id.set("")
    _session_id.set("")
    _user_id.set(0)
