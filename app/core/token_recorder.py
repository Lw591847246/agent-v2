# app/core/token_recorder.py
from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict
from typing import Dict, List

from app.core.logger import logger
from app.infrastructure.repositories.token_repo import token_repo


# 价格配置（每 1000 token 单价，单位：元）
# 本地 Ollama 记为 0；将来接云端 API 时在这里加
_PRICING = {
    # "deepseek-chat": {"input": 0.001, "output": 0.002},
    # "gpt-4o-mini":   {"input": 0.001, "output": 0.004},
}
_DEFAULT_PRICE = {"input": 0.0, "output": 0.0}


def _calc_cost(model_key: str, input_tokens: int, output_tokens: int) -> float:
    p = _PRICING.get(model_key, _DEFAULT_PRICE)
    return round(input_tokens / 1000 * p["input"] + output_tokens / 1000 * p["output"], 6)


class TokenRecorder:
    """异步写入 agent_llm_call_log，不阻塞主流程。
    同时维护 trace 级汇总，供 finalize 时回填 agent_message 聚合字段。
    """

    def __init__(self, queue_size: int = 10000, batch_size: int = 100):
        self._q: "queue.Queue[Dict]" = queue.Queue(maxsize=queue_size)
        self._batch_size = batch_size
        self._stats: Dict[str, Dict] = defaultdict(
            lambda: {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0}
        )
        self._lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True, name="token-writer").start()

    def record(self, ctx: Dict) -> None:
        """llm_tracker 结束时调用；异步入队，同时累加 trace 级统计。"""
        if not ctx:
            return
        model_key = ctx.get("model_key", "")
        in_t = int(ctx.get("input_tokens") or 0)
        out_t = int(ctx.get("output_tokens") or 0)
        cost = _calc_cost(model_key, in_t, out_t)
        ctx["cost"] = cost

        trace_id = ctx.get("trace_id") or ""
        if trace_id:
            with self._lock:
                s = self._stats[trace_id]
                s["input_tokens"] += in_t
                s["output_tokens"] += out_t
                s["cost"] += cost
                s["calls"] += 1

        try:
            self._q.put_nowait(ctx)
        except queue.Full:
            logger.warning(f"[TokenRecorder] 队列满，丢弃一条 service={ctx.get('service')}")

    def pop_trace_stats(self, trace_id: str) -> Dict:
        """finalize 时调用，取出并清空 trace 级汇总。"""
        if not trace_id:
            return {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0}
        with self._lock:
            return self._stats.pop(
                trace_id,
                {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0},
            )

    def _worker(self):
        while True:
            batch: List[Dict] = []
            first = self._q.get()
            if first is None:
                continue
            batch.append(first)
            deadline = time.time() + 1.0
            while len(batch) < self._batch_size and time.time() < deadline:
                try:
                    batch.append(self._q.get(timeout=0.05))
                except queue.Empty:
                    break
            try:
                token_repo.batch_insert(batch)
            except Exception as e:
                logger.warning(f"[TokenRecorder] 批量写入失败: {e}")


token_recorder = TokenRecorder()