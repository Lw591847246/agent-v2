# app/api/v1/chat_controller.py
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, resolve_session, uid_of
from app.api.v1._sse import (
    SSE_HEADERS, sse, sse_done, sse_finalize_stream,
)
from app.application.chat_service import chat_service
from app.contracts.result import Result
from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.trace_context import set_trace_context, get_trace_id
from app.core.tracing import capture_context, attach_context, detach_context
from app.models.schemas import ChatRequest
from app.utils.url_signer import sign_deep

router = APIRouter(tags=["聊天"])


@router.post("/agent/v1/chat")
def chat(payload: ChatRequest, user=CurrentUser):
    start_ms = int(time.time() * 1000)
    user_id = uid_of(user)

    # 会话准入：不存在则创建，存在但非本人 -> 404（修复越权写入）
    session_id = resolve_session(payload.session_id, user_id)
    client_id = payload.client_id or ""

    set_trace_context(session_id=session_id, user_id=user_id)
    trace_id = get_trace_id()
    # 请求线程捕获 OTel context（gen_stream 在另一线程消费时恢复）
    otel_ctx = capture_context()

    # 请求级租约：整个 chat 生命周期持锁；流式阶段用 bind() 跨线程复用
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
            otel_token = attach_context(otel_ctx)

            released = {"done": False}

            def _release():
                if not released["done"]:
                    released["done"] = True
                    try:
                        ticket.release()
                    except Exception:
                        pass

            try:
                # 关键：把 root ticket 绑到消费线程的 context，
                # 使消费阶段内部的 nested lease 复用外层租约而非重新抢锁。
                with gpu_lock.bind(ticket):
                    yield from sse_finalize_stream(
                        iterator=hr.result.iterator,
                        finalize=lambda parts, full_text, _ms: chat_service.finalize(
                            hr, parts=parts, full_text=full_text,
                        ),
                        release=_release,
                        detach=lambda: detach_context(otel_token),
                        start_ms=start_ms,
                        error_tag="ChatController",
                    )
            finally:
                # 兜底（幂等）：bind()/attach 阶段抛异常时也不会漏锁
                _release()

        return StreamingResponse(gen_stream(), media_type="text/event-stream",
                                 headers=SSE_HEADERS)

    # 非流式
    duration_ms = int(time.time() * 1000) - start_ms
    parts = hr.result.parts if isinstance(hr.result, Result) else []
    outputs = hr.result.outputs if isinstance(hr.result, Result) else []
    msg_id = 0
    try:
        msg_id = chat_service.finalize(hr, parts=parts, outputs=outputs)
    finally:
        ticket.release()

    def gen_final():
        set_trace_context(trace_id=trace_id, session_id=session_id, user_id=user_id)
        otel_token = attach_context(otel_ctx)
        try:
            for part in parts:
                yield sse({"type": "chunk", "part": sign_deep(part)})
            yield sse({"type": "done", "message_id": msg_id, "duration_ms": duration_ms})
            yield sse_done()
        finally:
            detach_context(otel_token)

    return StreamingResponse(gen_final(), media_type="text/event-stream",
                             headers=SSE_HEADERS)