"""统一链路追踪核心（OpenTelemetry）。"""
from __future__ import annotations

import functools
import json
import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, Optional

# ---------- 可选依赖探测 ----------
try:
    from opentelemetry import context as _otel_ctx
    from opentelemetry import trace as _otel_trace
    _OTEL_AVAILABLE = True
except Exception:
    _otel_ctx = None
    _otel_trace = None
    _OTEL_AVAILABLE = False

_initialized = False
_last_error: Optional[str] = None
_provider_ref = None  # 保存 init 时创建的 SDK provider，供 shutdown 用


class _NoopSpan:
    is_recording = False
    def set_attribute(self, *a, **k): return self
    def set_attributes(self, *a, **k): return self
    def add_event(self, *a, **k): return self
    def record_exception(self, *a, **k): return self
    def end(self, *a, **k): return self
    def set_status(self, *a, **k): return self
    def get_span_context(self): return None


class _NoopTracer:
    def start_span(self, *a, **k): return _NoopSpan()
    def start_as_current_span(self, *a, **k):
        @contextmanager
        def _cm():
            yield _NoopSpan()
        return _cm()


_NOOP_TRACER = _NoopTracer()


class JSONLinesSpanExporter:
    """把 span 按 JSON Lines 落盘（SDK 官方没有 FileSpanExporter，这里自实现）。"""

    def __init__(self, path: str):
        self._path = os.path.abspath(path)
        self._lock = threading.Lock()
        dir_ = os.path.dirname(self._path)
        if dir_:
            os.makedirs(dir_, exist_ok=True)

    def _to_dict(self, s) -> dict:
        ctx = s.get_span_context()
        parent = getattr(s, "parent", None)
        return {
            "name": s.name,
            "trace_id": format(ctx.trace_id, "032x") if ctx else "",
            "span_id": format(ctx.span_id, "016x") if ctx else "",
            "parent_span_id": format(parent.span_id, "016x") if parent else "",
            "start_time": getattr(s, "start_time", 0),
            "end_time": getattr(s, "end_time", 0),
            "attributes": dict(s.attributes or {}),
            "status": str(getattr(getattr(s, "status", None), "status", "")),
        }

    def export(self, spans):
        try:
            from opentelemetry.sdk.trace.export import SpanExportResult
        except Exception:
            return None
        try:
            with self._lock, open(self._path, "a", encoding="utf-8") as f:
                for s in spans:
                    f.write(json.dumps(self._to_dict(s), ensure_ascii=False) + "\n")
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _build_exporter(cfg):
    """按配置构造 span exporter；返回 None 表示不初始化 provider。"""
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor,
    )

    otlp_endpoint = (cfg.get("otlp_endpoint") or "").strip()
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            return BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=otlp_endpoint,
                    timeout=int(cfg.get("export_timeout", 10)),
                )
            )
        except Exception as e:
            print(f"[Tracing] OTLP exporter 不可用，退回 console: {e}")
            return BatchSpanProcessor(ConsoleSpanExporter())

    if cfg.get("console", False):
        # 本地调试：SimpleSpanProcessor 让 span end 立即打印，不等 batch
        return SimpleSpanProcessor(ConsoleSpanExporter())

    file_path = (cfg.get("file") or "").strip()
    if file_path:
        return SimpleSpanProcessor(JSONLinesSpanExporter(file_path))

    return None


def _instrument_all(app):
    """在 provider 就绪后统一安装 auto-instrument。"""
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app)
        except Exception as e:
            print(f"[Tracing] FastAPI instrument 失败: {e}")
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except Exception as e:
        print(f"[Tracing] Requests instrument 失败: {e}")


def init_tracing(service_name: str = "agent-v2", cfg: Optional[dict] = None, app=None) -> bool:
    """进程启动时调用一次。返回是否真正启用了 OTel。幂等。

    app 传入时，会在 provider 就绪后自动安装 FastAPI / Requests instrument，
    避免"instrument 先于 provider set 导致 no-op"的时序问题。
    """
    global _initialized, _last_error, _provider_ref
    if _initialized:
        return _OTEL_AVAILABLE and _last_error is None

    if not _OTEL_AVAILABLE:
        _last_error = "opentelemetry 未安装，tracing 降级为 no-op"
        _initialized = True
        print(f"[Tracing] {_last_error}")
        return False

    if cfg is None:
        try:
            from app.config.loader import config
            cfg = config.get("tracing", {}) or {}
        except Exception:
            cfg = {}

    processor = _build_exporter(cfg)
    if processor is None:
        _last_error = "未配置 tracing 导出端点，tracing 保持 no-op"
        _initialized = True
        print(f"[Tracing] {_last_error}")
        return False

    try:
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

        resource = Resource.create(
            {SERVICE_NAME: service_name, "service.version": cfg.get("version", "dev")}
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(processor)
        _otel_trace.set_tracer_provider(provider)
        _provider_ref = provider

        try:
            from opentelemetry import propagate
            propagate.set_global_textmap(TraceContextTextMapPropagator())
        except Exception:
            pass

        _initialized = True
        _last_error = None
        print(f"[Tracing] OpenTelemetry 已启用 service={service_name}")

        # 关键顺序：provider set 之后再 instrument
        _instrument_all(app)
        return True
    except Exception as e:
        _last_error = f"init_tracing 失败: {e}"
        _initialized = True
        print(f"[Tracing] {_last_error}")
        return False


def shutdown_tracing():
    """关闭 tracer provider（只对 SDK provider 生效；no-op 时安全返回）。"""
    global _provider_ref
    if not _OTEL_AVAILABLE or not _initialized or _provider_ref is None:
        return
    try:
        _provider_ref.shutdown()
    except Exception:
        pass
    _provider_ref = None


def get_tracer(name: str = "agent"):
    if not _OTEL_AVAILABLE:
        return _NOOP_TRACER
    try:
        return _otel_trace.get_tracer(name)
    except Exception:
        return _NOOP_TRACER


@contextmanager
def span(name: str, tracer_name: str = "agent", attributes: Optional[dict] = None):
    tracer = get_tracer(tracer_name)
    cm = tracer.start_as_current_span(name)
    sp = cm.__enter__()
    try:
        if attributes:
            try:
                sp.set_attributes(attributes)
            except Exception:
                pass
        yield sp
    except Exception as e:
        try:
            sp.record_exception(e)
            try:
                from opentelemetry.trace import Status, StatusCode
                sp.set_status(Status(StatusCode.ERROR, str(e)[:500]))
            except Exception:
                sp.set_attribute("error", True)
                sp.set_attribute("error.message", str(e)[:500])
        except Exception:
            pass
        raise
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass


def traced(name: Optional[str] = None, tracer_name: str = "agent"):
    def deco(fn: Callable):
        span_name = name or fn.__name__
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with span(span_name, tracer_name):
                return fn(*args, **kwargs)
        return wrapper
    if callable(name):
        f = name
        name = None
        return deco(f)
    return deco


def capture_context():
    if not _OTEL_AVAILABLE:
        return None
    try:
        return _otel_ctx.get_current()
    except Exception:
        return None


def attach_context(ctx):
    if not _OTEL_AVAILABLE or ctx is None:
        return None
    try:
        return _otel_ctx.attach(ctx)
    except Exception:
        return None


def detach_context(token):
    if not _OTEL_AVAILABLE or token is None:
        return
    try:
        _otel_ctx.detach(token)
    except Exception:
        pass


def run_in_context(ctx, fn: Callable, *args, **kwargs):
    token = attach_context(ctx)
    try:
        return fn(*args, **kwargs)
    finally:
        detach_context(token)


def current_trace_ids() -> tuple:
    if not _OTEL_AVAILABLE:
        return "", ""
    try:
        sc = _otel_trace.get_current_span().get_span_context()
        if not sc or not getattr(sc, "is_valid", False):
            return "", ""
        return format(sc.trace_id, "032x"), format(sc.span_id, "016x")
    except Exception:
        return "", ""


def is_enabled() -> bool:
    return _OTEL_AVAILABLE and _initialized and _last_error is None