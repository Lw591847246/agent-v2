"""请求上下文，对标 ThreadLocal"""
import contextvars
from typing import Optional

_trace_id = contextvars.ContextVar("trace_id", default=None)
_user_id = contextvars.ContextVar("user_id", default=None)
_session_id = contextvars.ContextVar("session_id", default=None)


class RequestContext:

    @staticmethod
    def set_trace_id(v: str): _trace_id.set(v)

    @staticmethod
    def get_trace_id() -> str: return _trace_id.get() or "-"

    @staticmethod
    def set_user_id(v: str): _user_id.set(v)

    @staticmethod
    def get_user_id() -> str: return _user_id.get() or ""

    @staticmethod
    def set_session_id(v: str): _session_id.set(v)

    @staticmethod
    def get_session_id() -> str: return _session_id.get() or ""