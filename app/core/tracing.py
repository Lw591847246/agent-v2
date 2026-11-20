"""统一链路追踪核心（OpenTelemetry）。

设计目标
--------
1. 零硬依赖：未安装 opentelemetry 时整体降级为 no-op，业务代码照常运行，
   不会因为 import 失败而拖垮启动（单人调试环境可先集成代码，后装依赖）。
2. 显式跨线程传播：threading.Thread 不继承 OTel 的 contextvar，异步后台任务
   （TaskRunner）必须显式 capture/attach 父上下文，子任务的 span 才能挂在
   发起它的 HTTP 请求 trace 下——这正是"长任务也能串成一条链"的关键。
3. 与现有 RequestContext(trace_id/session_id/user_id) 并存：trace_id 继续作为
   日志关联主键，OTel 的 trace_id/span_id 负责 span 父子关系与耗时统计，
   两者通过 span 属性互相印证，互不替换。

导出端点（按优先级，在 application.yml 的 tracing 段配置）
--------------------------------------------------------
- tracing.otlp_endpoint 配了 -> OTLP/HTTP 导出（接 Jaeger / Tempo / OTel Collector）
- 否则 tracing.console=true -> ConsoleSpanExporter（stdout，本地肉眼看）
- 否则 tracing.file 路径 -> 落 JSON Lines 文件（本地留档，可后续导入 Jaeger）
- 都没配 -> 不初始化 provider，tracing 完全 no-op

公开 API
--------
init_tracing(service_name)        进程启动时调用一次，幂等
get_tracer(name)                 取 tracer（no-op 时返回安全替身）
start_span / span                起 span（上下文管理器 + 装饰器两种形态）
capture_context / attach_context / detach_context   跨线程上下文桥（给 TaskRunner 用）
run_in_context(ctx, fn, *a, **k)  在指定上下文里执行函数并自动 detach
current_trace_ids()              返回 (otel_trace_id, otel_span_id) 供日志注入
"""
from __future__ import annotations

import functools
import os
from contextlib import contextmanager
from typing import Any, Callable, Optional

# ---------- 可选依赖探测（缺失即全链路 no-op，不抛） ----------
try:
    from opentelemetry import context as _otel_ctx
    from opentelemetry import trace as _otel_trace
    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - 环境未装 otel
    _otel_ctx = None
    _otel_trace = None
    _OTEL_AVAILABLE = False

# 全局是否已完成 provider 初始化
_initialized = False
_last_error: Optional[str] = None

_NOOP = None  # no-op span 单例（占位对象，所有方法空转）


class _NoopSpan:
    """otel 不可用时返回的替身 span：所有方法空转、不抛。"""
    is_recording = False

    def set_attribute(self, *a, **k):  # noqa: D401
        return self

    def set_attributes(self, *a, **k):
        return self

    def add_event(self, *a, **k):
        return self

    def record_exception(self, *a, **k):
        return self

    def end(self, *a, **k):
        return self

    def get_span_context(self):
        return None


class _NoopTracer:
    def start_span(self, *a, **k):
        return _NoopSpan()

    def start_as_current_span(self, *a, **k):
        @contextmanager
        def _cm():
            yield _NoopSpan()
        return _cm()


_NOOP_SPAN = _NoopSpan()
_NOOP_TRACER = _NoopTracer()


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
            # OTLP 包没装或不可达 -> 退到 console，至少不静默丢失
            print(f"[Tracing] OTLP exporter 不可用，退回 console: {e}")
            return BatchSpanProcessor(ConsoleSpanExporter())

    if cfg.get("console", False):
        return BatchSpanProcessor(ConsoleSpanExporter())

    file_path = (cfg.get("file") or "").strip()
    if file_path:
        # 落 JSON Lines：每条 span 一行，便于离线导入 Jaeger / 用 jq 分析
        from opentelemetry.sdk.trace.export.file_span_exporter import (
            FileSpanExporter,
        )
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        return SimpleSpanProcessor(
            FileSpanExporter(
                out_dir=os.path.dirname(os.path.abspath(file_path)),
                result_callback=None,
            )
        )

    return None


def init_tracing(service_name: str = "agent-v2", cfg: Optional[dict] = None) -> bool:
    """进程启动时调用一次。返回是否真正启用了 OTel。幂等。"""
    global _initialized, _last_error
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
        # W3C TraceContext：跨进程/线程标准传播器
        try:
            from opentelemetry import propagate
            propagate.set_global_textmap(TraceContextTextMapPropagator())
        except Exception:
            pass

        _initialized = True
        _last_error = None
        print(f"[Tracing] OpenTelemetry 已启用 service={service_name}")
        return True
    except Exception as e:
        _last_error = f"init_tracing 失败: {e}"
        _initialized = True
        print(f"[Tracing] {_last_error}")
        return False


def _provider():
    if not _OTEL_AVAILABLE:
        return None
    try:
        return _otel_trace.get_tracer_provider()
    except Exception:
        return None


def get_tracer(name: str = "agent"):
    """取 tracer；OTel 不可用时返回 no-op 替身。"""
    if not _OTEL_AVAILABLE:
        return _NOOP_TRACER
    try:
        return _otel_trace.get_tracer(name)
    except Exception:
        return _NOOP_TRACER


# ---------- span：上下文管理器 + 装饰器 ----------

@contextmanager
def span(name: str, tracer_name: str = "agent", attributes: Optional[dict] = None):
    """with span('xxx') as sp: ...  —— OTel 不可用时安全空转。"""
    tracer = get_tracer(tracer_name)
    cm = tracer.start_as_current_span(name)
    try:
        sp = cm.__enter__()
        if attributes:
            try:
                sp.set_attributes(attributes)
            except Exception:
                pass
        try:
            yield sp
        except Exception as e:
            try:
                sp.record_exception(e)
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
    """装饰器：with traced()(func) 或 @traced('name') 包裹函数为 span。"""
    def deco(fn: Callable):
        span_name = name or fn.__name__
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with span(span_name, tracer_name):
                return fn(*args, **kwargs)
        return wrapper
    # 兼容 @traced 不带括号
    if callable(name):
        f = name
        name = None
        return deco(f)
    return deco


# ---------- 跨线程上下文桥（给 TaskRunner / 任何子线程用） ----------

def capture_context():
    """在当前线程捕获 OTel 上下文快照，可带到子线程 attach。
    OTel 不可用时返回 None（桥接自动 no-op）。"""
    if not _OTEL_AVAILABLE:
        return None
    try:
        return _otel_ctx.get_current()
    except Exception:
        return None


def attach_context(ctx):
    """在目标线程把上下文快照设为当前，返回 detach token。"""
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
    """在指定上下文快照内执行 fn，自动 attach/detach；用于子线程入口。"""
    token = attach_context(ctx)
    try:
        return fn(*args, **kwargs)
    finally:
        detach_context(token)


def current_trace_ids() -> tuple:
    """返回当前 (otel_trace_id, otel_span_id) 的十六进制串；no-op 时 ('','')."""
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
