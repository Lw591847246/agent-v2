# app/executor/stream_bridge.py
"""执行结果 → SSE 桥接。"""
from __future__ import annotations

import json
from typing import Iterator, Optional

from app.contracts.result import Result, StreamResult


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class StreamBridge:

    def to_sse(
        self,
        result,
        message_id: int = 0,
        duration_ms: int = 0,
    ) -> Iterator[str]:
        """把 Result 或 StreamResult 转成 SSE 帧序列。"""
        if isinstance(result, StreamResult):
            yield from self._stream_result(result.iterator, message_id, duration_ms)
            return

        if isinstance(result, Result):
            yield from self._final_result(result, message_id, duration_ms)
            return

        # 兜底
        yield _sse({"type": "chunk", "part": {"type": "text", "content": str(result)}})
        yield _sse({"type": "done", "message_id": message_id, "duration_ms": duration_ms})
        yield "data: [DONE]\n\n"

    def _stream_result(self, iterator: Iterator[str], message_id: int, duration_ms: int) -> Iterator[str]:
        try:
            for chunk in iterator:
                if chunk:
                    yield _sse({"type": "chunk", "part": {"type": "text", "content": chunk}})
        except Exception as e:
            yield _sse({"type": "error", "error": str(e)})
        yield _sse({"type": "done", "message_id": message_id, "duration_ms": duration_ms})
        yield "data: [DONE]\n\n"

    def _final_result(self, result: Result, message_id: int, duration_ms: int) -> Iterator[str]:
        for part in result.parts:
            yield _sse({"type": "chunk", "part": part})
        yield _sse({"type": "done", "message_id": message_id, "duration_ms": duration_ms})
        yield "data: [DONE]\n\n"


stream_bridge = StreamBridge()