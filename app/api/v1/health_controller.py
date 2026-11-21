# app/api/v1/health_controller.py
"""运维端点：/healthz、/readyz、/metrics。

【本轮修复 · 中危 6】
/metrics 原来挂在 router.py 的 public 组（无任何依赖），会把任务状态分布、
全站累计 input/output token 等业务量指标原样吐给未认证访问者。
本项目的鉴权是**依赖式**而非中间件式（Depends(current_user) 挂在路由上），
所以在中间件里拦 public 路由根本不会触发 —— 守卫必须做成依赖挂到端点上。
现在 /metrics 必须通过 app/api/deps_ops.ops_guard（token 或 IP 白名单，
两者都没配则 404 fail-closed）；/healthz、/readyz 仍匿名开放
（只含状态与延迟，不含业务量）。


为什么补
--------
原来只有"启动时探活"（`core/healthcheck.py`），运行期没有任何健康/度量端点，
`auth.white_list` 里写的 `/health` 也没有对应路由（死配置）。
结果就是"页面能打开"成了唯一的质量信号。

约定
----
- 无需鉴权（在 `router.py` 的 public 组），但**只暴露状态与计数，不暴露配置/密钥**；
- `/healthz`  ：MySQL + Redis 必须通（否则 503）；Ollama 挂只降级（200 + degraded）；
- `/readyz`   ：在 healthz 基础上把 Ollama 也算必需（适合反代/监控做 readiness）；
- `/metrics`  ：Prometheus 文本格式，进程内指标 + DB 派生指标。
"""
from __future__ import annotations

import time

import requests
from fastapi import APIRouter, Depends, Response

from app.api.deps_ops import ops_guard
from app.config.loader import config
from app.context.task_runner import task_runner
from app.core import metrics
from app.core.logger import logger
from app.core.redis_client import redis_client
from app.infrastructure.repositories.mysql_pool import mysql_pool

router = APIRouter(tags=["运维"])

_PROBE_TIMEOUT = float(config.get("health.probe_timeout", 3) or 3)


def _probe_mysql() -> tuple[bool, float, str]:
    t0 = time.time()
    conn = None
    try:
        conn = mysql_pool.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        return True, round((time.time() - t0) * 1000, 1), ""
    except Exception as e:
        return False, round((time.time() - t0) * 1000, 1), str(e)[:200]
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _probe_redis() -> tuple[bool, float, str]:
    t0 = time.time()
    ok, err = redis_client.healthy()
    return ok, round((time.time() - t0) * 1000, 1), err


def _probe_ollama() -> tuple[bool, float, str]:
    t0 = time.time()
    base = str(config.get("ollama.base_url") or "").rstrip("/")
    for suffix in ("/api/chat", "/api/generate", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    if not base:
        return False, 0.0, "ollama.base_url 未配置"
    try:
        r = requests.get(f"{base}/api/tags", timeout=_PROBE_TIMEOUT)
        ok = r.status_code == 200
        return ok, round((time.time() - t0) * 1000, 1), "" if ok else f"http_{r.status_code}"
    except Exception as e:
        return False, round((time.time() - t0) * 1000, 1), str(e)[:200]


def _snapshot(require_ollama: bool) -> tuple[dict, int]:
    my_ok, my_ms, my_err = _probe_mysql()
    rd_ok, rd_ms, rd_err = _probe_redis()
    ol_ok, ol_ms, ol_err = _probe_ollama()

    core_ok = my_ok and rd_ok and (ol_ok if require_ollama else True)
    status = "ok" if core_ok else "degraded"
    if require_ollama and not ol_ok:
        status = "unavailable"
    elif not (my_ok and rd_ok):
        status = "unavailable"

    body = {
        "status": status,
        "ts": int(time.time()),
        "deps": {
            "mysql": {"ok": my_ok, "latency_ms": my_ms, **({"error": my_err} if my_err else {})},
            "redis": {"ok": rd_ok, "latency_ms": rd_ms, **({"error": rd_err} if rd_err else {})},
            "ollama": {"ok": ol_ok, "latency_ms": ol_ms, **({"error": ol_err} if ol_err else {})},
        },
        "task": task_runner.stats(),
    }
    return body, (200 if status != "unavailable" else 503)


@router.get("/healthz")
def healthz():
    body, code = _snapshot(require_ollama=False)
    return Response(
        content=_json(body),
        status_code=code,
        media_type="application/json",
    )


@router.get("/readyz")
def readyz():
    body, code = _snapshot(require_ollama=True)
    return Response(
        content=_json(body),
        status_code=code,
        media_type="application/json",
    )


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


# ============================================================
# /metrics
# ============================================================
_TASK_STATUS_SQL = "SELECT status, COUNT(*) AS c FROM agent_video_task GROUP BY status"
_TOKEN_SQL = (
    "SELECT COALESCE(SUM(input_tokens),0) AS inp, "
    "COALESCE(SUM(output_tokens),0) AS outp FROM agent_message"
)


def _db_metric_lines() -> list:
    """DB 派生指标：任务状态分布、累计 token。失败不影响进程内指标输出。"""
    lines: list = []
    conn = None
    try:
        conn = mysql_pool.get_connection()
        cur = conn.cursor()

        try:
            cur.execute(_TASK_STATUS_SQL)
            rows = cur.fetchall() or []
            lines.append("# TYPE agent_task_total gauge")
            total = 0
            for row in rows:
                status = row.get("status") if isinstance(row, dict) else row[0]
                cnt = row.get("c") if isinstance(row, dict) else row[1]
                total += int(cnt or 0)
                lines.append(f'agent_task_total{{status="{status}"}} {int(cnt or 0)}')
            lines.append(f"agent_task_total_all {total}")
        except Exception as e:
            logger.warning(f"[Metrics] 任务统计失败: {e}")
            lines.append("agent_metrics_db_up 0")
            return lines

        try:
            cur.execute(_TOKEN_SQL)
            row = cur.fetchone() or {}
            inp = int((row.get("inp") if isinstance(row, dict) else row[0]) or 0)
            outp = int((row.get("outp") if isinstance(row, dict) else row[1]) or 0)
            lines.append("# TYPE agent_llm_tokens_total gauge")
            lines.append(f'agent_llm_tokens_total{{direction="input"}} {inp}')
            lines.append(f'agent_llm_tokens_total{{direction="output"}} {outp}')
        except Exception as e:
            logger.warning(f"[Metrics] token 统计失败: {e}")

        cur.close()
        lines.append("agent_metrics_db_up 1")
    except Exception as e:
        logger.warning(f"[Metrics] DB 不可用: {e}")
        lines.append("agent_metrics_db_up 0")
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return lines


@router.get("/metrics", dependencies=[Depends(ops_guard)])
def prometheus_metrics():
    st = task_runner.stats()
    metrics.set_gauge("agent_task_users_running", len(st.get("users") or {}))
    metrics.set_gauge("agent_task_max_concurrent", st.get("max_concurrent", 0))
    metrics.set_gauge("agent_task_max_per_user", st.get("max_per_user", 0))

    text = metrics.render(extra_lines=_db_metric_lines())
    return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")
