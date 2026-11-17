# app/core/llm_tracker.py
from __future__ import annotations

import time
from contextlib import contextmanager

from app.core.trace_context import get_trace_id, get_session_id, get_user_id
from app.core.token_recorder import token_recorder


class LlmCallContext:
    def __init__(self, service: str, model_key: str):
        self.service = service
        self.model_key = model_key
        self.trace_id = get_trace_id()
        self.session_id = get_session_id()
        self.user_id = get_user_id()
        self.input_tokens = 0
        self.output_tokens = 0
        self.status = "ok"
        self.error_msg = None
        self._t0 = time.time()

    def from_ollama_response(self, resp: dict) -> None:
        """非流式：直接读 prompt_eval_count / eval_count"""
        if not isinstance(resp, dict):
            return
        self.input_tokens = int(resp.get("prompt_eval_count") or 0)
        self.output_tokens = int(resp.get("eval_count") or 0)

    def from_ollama_chunk(self, chunk: dict) -> None:
        """流式：每个 chunk 都调一次，done=true 时记录"""
        if not isinstance(chunk, dict):
            return
        if chunk.get("done"):
            self.input_tokens = int(chunk.get("prompt_eval_count") or 0)
            self.output_tokens = int(chunk.get("eval_count") or 0)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "service": self.service,
            "model_key": self.model_key,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration_ms": int((time.time() - self._t0) * 1000),
            "status": self.status,
            "error_msg": self.error_msg,
        }


@contextmanager
def track_llm(service: str, model_key: str):
    """
    用法：
        with track_llm("intent", "qwen2.5:7b") as t:
            resp = ollama_client.chat(...)
            t.from_ollama_response(resp)
    流式：
        with track_llm("chat", "qwen2.5:7b") as t:
            for chunk in stream:
                t.from_ollama_chunk(chunk)
                yield chunk
    """
    ctx = LlmCallContext(service=service, model_key=model_key)
    try:
        yield ctx
    except Exception as e:
        ctx.status = "error"
        ctx.error_msg = str(e)[:255]
        raise
    finally:
        token_recorder.record(ctx.to_dict())