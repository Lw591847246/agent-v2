"""统一日志：结构化 key=value 格式。

固定字段：time / level / logger / trace_id / session_id / user_id / thread，
外加业务自定义字段（通过 extra={"extra_fields": {...}} 注入，如 task_type、duration_ms）。
不依赖任何日志服务，grep / awk 可直接按 key 过滤：
    grep "trace_id=abc123" logs/app.log
    grep "duration_ms=9" logs/app.log
"""
import json
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from contextvars import ContextVar
from typing import Optional

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 结构化附加字段（task_type / duration_ms 等），随线程/协程作用域传播
_extra_fields: ContextVar[dict] = ContextVar("log_extra_fields", default=None)


def set_extra_fields(**kwargs) -> None:
    cur = _extra_fields.get() or {}
    cur = dict(cur)
    cur.update({k: v for k, v in kwargs.items() if v is not None})
    _extra_fields.set(cur)


def clear_extra_fields() -> None:
    _extra_fields.set(None)


def get_extra_fields() -> dict:
    return _extra_fields.get() or {}


def _trace_id() -> str:
    try:
        from app.core.context import RequestContext
        return RequestContext.get_trace_id()
    except Exception:
        return "-"


def _session_id() -> str:
    try:
        from app.core.context import RequestContext
        return RequestContext.get_session_id()
    except Exception:
        return ""


def _user_id() -> str:
    try:
        from app.core.context import RequestContext
        return RequestContext.get_user_id()
    except Exception:
        return ""


class StructuredFormatter(logging.Formatter):
    """key=value 一行一记录；自定义字段通过 record.extra_fields 或 contextvar 注入。"""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"time={self.formatTime(record, datefmt=DATE_FORMAT)} "
            f"level={record.levelname} logger={record.name} "
            f"trace_id={_trace_id()} session_id={_session_id()} "
            f"user_id={_user_id()} thread={record.threadName} "
            f"msg={record.getMessage()!r}"
        )
        fields = dict(get_extra_fields())
        rec_extra = getattr(record, "extra_fields", None)
        if isinstance(rec_extra, dict):
            fields.update(rec_extra)
        if fields:
            kv = " ".join(f"{k}={v!r}" for k, v in fields.items())
            base = f"{base} {kv}"
        if record.exc_info and record.exc_info[0]:
            base = f"{base} exc={self.formatException(record.exc_info)!r}"
        return base


class JsonFormatter(logging.Formatter):
    """可选：JSON 一行一记录，由环境变量 AGENT_LOG_FORMAT=json 启用。"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, datefmt=DATE_FORMAT),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": _trace_id(),
            "session_id": _session_id(),
            "user_id": _user_id(),
            "thread": record.threadName,
            "msg": record.getMessage(),
        }
        fields = dict(get_extra_fields())
        rec_extra = getattr(record, "extra_fields", None)
        if isinstance(rec_extra, dict):
            fields.update(rec_extra)
        if fields:
            entry["fields"] = fields
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _pick_formatter() -> logging.Formatter:
    import os
    if os.getenv("AGENT_LOG_FORMAT", "").lower() == "json":
        return JsonFormatter(datefmt=DATE_FORMAT)
    return StructuredFormatter(datefmt=DATE_FORMAT)


def _build_logger(name: str = "agent") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = _pick_formatter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=10 * 1024 * 1024,
        backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    error_handler = RotatingFileHandler(
        LOG_DIR / "error.log", maxBytes=10 * 1024 * 1024,
        backupCount=5, encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    return logger


logger = _build_logger()
