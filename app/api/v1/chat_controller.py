# app/api/v1/chat_controller.py
import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.application.chat_service import chat_service
from app.application.conversation_service import conversation_service
from app.contracts.result import Result
from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.logger import logger
from app.core.trace_context import set_trace_context, get_trace_id
from app.middleware.auth import auth_interceptor
from app.models.schemas import ChatRequest

router = APIRouter(tags=["聊天"])
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/agent/v1/chat")
def chat(payload: ChatRequest, request: Request, user=Depends(auth_interceptor)):
    start_ms = int(time.time() * 1000)
    user_id = _uid(user)
    session_id = (payload.session_id or "").strip()
    client_id = payload.client_id or ""

    if not session_id:
        session_id = conversation_service.new_conversation(user_id)

    set_trace_context(session_id=session_id, user_id=user_id)
    trace_id = get_trace_id()

    # ⭐ 请求级租约：整个 chat 生命周期持锁
    #    handle 内部嵌套的 intent 调用会自动复用，不再抢锁
    ticket = gpu_lock.acquire(
        GPUResource.OLLAMA_CHAT, task="chat", timeout=600,
    )
    try:
        hr = chat_service.handle(
            text=payload.text,
            history=[],
            user_id=user_id,
            session_id=session_id,
            attachments=[
                {"file_id": a.file_id, "type": a.type}
                for a in (payload.attachments or [])
            ],
            flow_context=payload.flow_context,
            want_stream=True,
            client_id=client_id,
        )
    except Exception:
        ticket.release()
        raise

    if hr.is_stream:
        def gen_stream():
            set_trace_context(trace_id=trace_id, session_id=session_id, user_id=user_id)
            full_text = ""
            try:
                for chunk in hr.result.iterator:
                    if chunk:
                        full_text += chunk
                        yield _sse({"type": "chunk",
                                    "part": {"type": "text", "content": chunk}})
            except Exception as e:
                logger.error(f"[ChatController] stream 失败: {e}")
                yield _sse({"type": "error", "error": str(e)})
            finally:
                duration_ms = int(time.time() * 1000) - start_ms
                parts = [{"type": "text", "content": full_text}] if full_text else []
                try:
                    msg_id = chat_service.finalize(hr, parts=parts, full_text=full_text)
                finally:
                    # ⭐ 在 generator 线程释放，锁生命周期与流一致
                    ticket.release()
                yield _sse({"type": "done", "message_id": msg_id,
                            "duration_ms": duration_ms})
                yield "data: [DONE]\n\n"

        return StreamingResponse(gen_stream(), media_type="text/event-stream",
                                 headers=_SSE_HEADERS)

    # 非流式
    duration_ms = int(time.time() * 1000) - start_ms
    parts = hr.result.parts if isinstance(hr.result, Result) else []
    outputs = hr.result.outputs if isinstance(hr.result, Result) else []
    try:
        msg_id = chat_service.finalize(hr, parts=parts, outputs=outputs)
    finally:
        ticket.release()

    def gen_final():
        set_trace_context(trace_id=trace_id, session_id=session_id, user_id=user_id)
        for part in parts:
            yield _sse({"type": "chunk", "part": part})
        yield _sse({"type": "done", "message_id": msg_id, "duration_ms": duration_ms})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen_final(), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


def _uid(user: dict) -> int:
    v = user.get("userId", 0)
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0