# app/api/v1/file_controller.py
"""文件处理兼容接口（V2）：上传文件 + 提问，内部走 upload + chat 链路。

⚠️ Deprecated：
  推荐前端迁移到：
    1. POST /agent/v1/upload/asset  → 拿 file_id
    2. POST /agent/v1/chat          → attachments 里带 file_id

本轮修复：
- 会话准入改用 resolve_session（旧 ensure_conversation 在他人 session_id 上
  会走裸 INSERT，表现为 500 或双主，反而更危险）；
- 流式收尾改用 sse_finalize_stream，done/[DONE] 不再在 finally 里 yield；
- **上传分段读取 + 单文件/合计上限**（原来 `f.file.read()` 无上限）。
"""
import time
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, resolve_session, uid_of
from app.api.v1._sse import (
    SSE_HEADERS, sse, sse_done, sse_finalize_stream,
)
from app.application.chat_service import chat_service
from app.application.file_upload_service import (
    MAX_FILE_BYTES, MAX_SIZE_MB, MAX_TOTAL_BYTES, MAX_TOTAL_MB, file_upload_service,
)
from app.contracts.result import Result
from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.logger import logger
from app.core.trace_context import set_trace_context, get_trace_id
from app.core.tracing import capture_context, attach_context, detach_context
from app.utils.url_signer import sign_deep

router = APIRouter(tags=["文件"])

_CHUNK = 1024 * 1024


def _read_fileobj_capped(fp, limit: int, name: str) -> bytes:
    """同步分段读取（SpooledTemporaryFile），超过 limit 抛 413。"""
    buf = bytearray()
    while True:
        chunk = fp.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"{name} 超过 {MAX_SIZE_MB}MB 上限",
            )
    return bytes(buf)


@router.post("/agent/v1/process-file")
def process_file_with_prompt(
    text: str = Form(""),
    session_id: str = Form(...),
    client_id: str = Form(None),
    files: List[UploadFile] = File(...),
    user=CurrentUser,
):
    logger.info(f"[ProcessFile] V2 兼容调用 text={text!r} files={len(files)}")
    start_ms = int(time.time() * 1000)
    user_id = uid_of(user)

    session_id = resolve_session(session_id, user_id)

    set_trace_context(session_id=session_id, user_id=user_id)
    trace_id = get_trace_id()
    otel_ctx = capture_context()

    # 保存文件（sync def 已在线程池，直接同步读 SpooledTemporaryFile）
    attachments = []
    file_names = []
    total_bytes = 0
    for f in files:
        try:
            content = _read_fileobj_capped(f.file, MAX_FILE_BYTES, f.filename or "file")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[ProcessFile] 读取 {f.filename} 失败: {e}")
            continue
        if not content:
            continue

        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"单次上传合计超过 {MAX_TOTAL_MB}MB 上限",
            )

        try:
            meta = file_upload_service.save_asset(
                content=content,
                filename=f.filename or "unnamed",
                user_id=user_id,
                session_id=session_id,
            )
            attachments.append({"file_id": meta["file_id"], "type": meta["type"]})
            file_names.append(meta["name"])
        except Exception as e:
            logger.warning(f"[ProcessFile] 保存 {f.filename} 失败: {e}")

    user_text = text or ""
    if file_names:
        line = "文件：" + "、".join(file_names)
        user_text = (user_text + "\n" + line) if user_text else line

    # 与 chat_controller 对齐：流式阶段也要持锁，bind() 跨线程复用
    ticket = gpu_lock.acquire(
        GPUResource.OLLAMA_CHAT, task="process-file", timeout=600,
    )
    try:
        hr = chat_service.handle(
            text=user_text,
            history=[],
            user_id=user_id,
            session_id=session_id,
            attachments=attachments,
            want_stream=True,
            client_id=client_id or "",
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
                with gpu_lock.bind(ticket):
                    yield from sse_finalize_stream(
                        iterator=hr.result.iterator,
                        finalize=lambda parts, full_text, _ms: chat_service.finalize(
                            hr, parts=parts, full_text=full_text,
                        ),
                        release=_release,
                        detach=lambda: detach_context(otel_token),
                        start_ms=start_ms,
                        error_tag="ProcessFile",
                    )
            finally:
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