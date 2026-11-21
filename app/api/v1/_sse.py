# app/api/v1/_sse.py
"""SSE 流式工具。

核心约束（本轮修复的 P0-5）：
    finalize 必须在 finally 里执行（客户端断连也要落库），
    但 `yield` 绝对不能出现在 finally 里——客户端断连时 Starlette 会对生成器
    throw(GeneratorExit)，在 finally 里再 yield 会抛
    RuntimeError: generator ignored GeneratorExit。

因此把结构固化成：
    try: ... 逐块 yield chunk ...
    except Exception: yield error
    finally: finalize() + release()      # 不含任何 yield
    # 只有正常结束 / 非 GeneratorExit 异常才会走到下面两行
    yield done
    yield [DONE]
"""
from __future__ import annotations

import json
import time
from typing import Callable, Iterator

from app.core.logger import logger

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"


def sse_finalize_stream(
    *,
    iterator,
    finalize: Callable[[list, str, int], int],
    release: Callable[[], None],
    detach: Callable[[], None],
    start_ms: int,
    error_tag: str,
) -> Iterator[str]:
    """prefix/正文流式输出 + finally 落库 + 正常收尾事件。

    finalize(parts, full_text, duration_ms) -> message_id
    release()：释放 GPU 租约（只会被调用一次；异常不影响收尾事件）
    detach()：解绑 OTel context
    """
    full_text = ""
    msg_id = 0
    duration_ms = 0

    try:
        for chunk in iterator:
            if not chunk:
                continue
            full_text += chunk
            yield sse({"type": "chunk", "part": {"type": "text", "content": chunk}})
    except Exception as e:  # GeneratorExit / CancelledError 不继承 Exception，不会进这里
        logger.error(f"[{error_tag}] stream 失败: {e}", exc_info=True)
        yield sse({"type": "error", "error": str(e)})
    finally:
        try:
            detach()
        except Exception as e:
            logger.warning(f"[{error_tag}] detach context 失败: {e}")
        duration_ms = int(time.time() * 1000) - start_ms
        parts = [{"type": "text", "content": full_text}] if full_text else []
        try:
            msg_id = finalize(parts, full_text, duration_ms)
        except Exception as e:
            logger.error(f"[{error_tag}] finalize 失败: {e}", exc_info=True)
        finally:
            try:
                release()
            except Exception as e:
                logger.warning(f"[{error_tag}] 释放 GPU 租约失败: {e}")

    # 仅正常结束 / 业务异常后到达（GeneratorExit 时函数已在此前退出）
    yield sse({"type": "done", "message_id": msg_id, "duration_ms": duration_ms})
    yield sse_done()