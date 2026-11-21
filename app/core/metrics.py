# app/core/metrics.py
"""极简进程内指标（Prometheus 文本格式），无第三方依赖。

设计取舍
--------
- 只做 counter / gauge / summary 三种，标签用 dict，序列化时排序 → 输出稳定；
- 线程安全（RLock）；单实例部署下内存指标足够，多实例时再换 prometheus_client；
- `render()` 接受额外行，供运维端点补 DB 派生指标（任务总数、token 累计）。

命名规范：`agent_` 前缀。
"""
from __future__ import annotations

import threading
from typing import Dict, Iterable, List, Optional, Tuple

_LOCK = threading.RLock()

# (name, labels_key) -> value
_counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
_gauges: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
_summaries: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], List[float]] = {}

_counter_names = set()
_gauge_names = set()
_summary_names = set()


def _key(labels: Optional[dict]) -> Tuple[Tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc(name: str, labels: Optional[dict] = None, value: float = 1.0) -> None:
    with _LOCK:
        _counter_names.add(name)
        k = (name, _key(labels))
        _counters[k] = _counters.get(k, 0.0) + float(value)


def set_gauge(name: str, value: float, labels: Optional[dict] = None) -> None:
    with _LOCK:
        _gauge_names.add(name)
        _gauges[(name, _key(labels))] = float(value)


def add_gauge(name: str, delta: float, labels: Optional[dict] = None) -> None:
    with _LOCK:
        _gauge_names.add(name)
        k = (name, _key(labels))
        _gauges[k] = _gauges.get(k, 0.0) + float(delta)


def observe(name: str, value: float, labels: Optional[dict] = None) -> None:
    """summary：记录 sum / count（不做分位数，够用且便宜）。"""
    with _LOCK:
        _summary_names.add(name)
        _summaries.setdefault((name, _key(labels)), []).append(float(value))


def _fmt_labels(labels: Tuple[Tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + body + "}"


def _num(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:.6f}".rstrip("0").rstrip(".")


def render(extra_lines: Optional[Iterable[str]] = None) -> str:
    """输出 Prometheus 文本格式。"""
    out: List[str] = []
    with _LOCK:
        for name in sorted(_counter_names):
            out.append(f"# TYPE {name} counter")
            for (n, labels), v in sorted(_counters.items()):
                if n == name:
                    out.append(f"{name}{_fmt_labels(labels)} {_num(v)}")
            out.append("")

        for name in sorted(_gauge_names):
            out.append(f"# TYPE {name} gauge")
            for (n, labels), v in sorted(_gauges.items()):
                if n == name:
                    out.append(f"{name}{_fmt_labels(labels)} {_num(v)}")
            out.append("")

        for name in sorted(_summary_names):
            out.append(f"# TYPE {name} summary")
            for (n, labels), vals in sorted(_summaries.items()):
                if n != name:
                    continue
                # 只输出 _sum / _count（不分位数），标签与原序列一致
                out.append(f"{name}_sum{_fmt_labels(labels)} {_num(sum(vals))}")
                out.append(f"{name}_count{_fmt_labels(labels)} {_num(len(vals))}")
            out.append("")

    if extra_lines:
        out.extend(extra_lines)
        out.append("")
    return "\n".join(out)


def reset() -> None:
    """测试用：清空所有指标。"""
    with _LOCK:
        _counters.clear()
        _gauges.clear()
        _summaries.clear()
        _counter_names.clear()
        _gauge_names.clear()
        _summary_names.clear()