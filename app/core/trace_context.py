# app/core/trace_context.py
from __future__ import annotations

import uuid
import contextvars

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="")
_user_id: contextvars.ContextVar[int] = contextvars.ContextVar("user_id", default=0)


def set_trace_context(trace_id: str = "", session_id: str = "", user_id: int = 0) -> str:
    tid = trace_id or uuid.uuid4().hex
    _trace_id.set(tid)
    if session_id:
        _session_id.set(session_id)
    if user_id:
        _user_id.set(user_id)
    return tid


def ensure_trace_id() -> str:
    tid = _trace_id.get()
    if not tid:
        tid = set_trace_context()
    return tid


def get_trace_id() -> str:
    return _trace_id.get() or ensure_trace_id()


def get_session_id() -> str:
    return _session_id.get()


def get_user_id() -> int:
    return _user_id.get()