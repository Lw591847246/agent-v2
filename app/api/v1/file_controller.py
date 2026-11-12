"""文件处理兼容接口（V2）：上传文件 + 提问，内部走 upload + chat 链路。

⚠️ Deprecated：
  推荐前端迁移到：
    1. POST /agent/v1/upload/asset  → 拿 file_id
    2. POST /agent/v1/chat          → attachments 里带 file_id
"""
import json
import time
from typing import List

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.application.chat_service import chat_service
from app.application.conversation_service import conversation_service
from app.application.file_upload_service import file_upload_service
from app.contracts.result import Result
from app.core.logger import logger
from app.middleware.auth import auth_interceptor

router = APIRouter(tags=["文件"])
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/agent/v1/process-file")
async def process_file_with_prompt(
    text: str = Form(""),
    session_id: str = Form(...),
    client_id: str = Form(None),
    files: List[UploadFile] = File(...),
    user: dict = Depends(auth_interceptor),
):
    logger.info(f"[ProcessFile] V2 兼容调用 text={text!r} files={len(files)}")
    start_ms = int(time.time() * 1000)
    user_id = _uid(user)

    if not session_id or not session_id.strip():
        session_id = conversation_service.new_conversation(user_id)
    conversation_service.ensure_conversation(session_id, user_id)

    # 保存文件
    attachments = []
    file_names = []
    for f in files:
        content = await f.read()
        if not content:
            continue
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

    # 走 ChatService（内部：ContextProvider → RouterScheduler → Executor → Skill）
    hr = chat_service.handle(
        text=user_text,
        history=[],
        user_id=user_id,
        session_id=session_id,
        attachments=attachments,
        want_stream=True,
        client_id=client_id or "",
    )

    # 流式
    if hr.is_stream:
        def gen_stream():
            full_text = ""
            try:
                for chunk in hr.result.iterator:
                    if chunk:
                        full_text += chunk
                        yield _sse({"type": "chunk", "part": {"type": "text", "content": chunk}})
            except Exception as e:
                logger.error(f"[ProcessFile] stream 异常: {e}")
                yield _sse({"type": "error", "error": str(e)})
            finally:
                duration_ms = int(time.time() * 1000) - start_ms
                parts = [{"type": "text", "content": full_text}] if full_text else []
                msg_id = chat_service.finalize(hr, parts=parts, full_text=full_text)
                yield _sse({"type": "done", "message_id": msg_id, "duration_ms": duration_ms})
                yield "data: [DONE]\n\n"
        return StreamingResponse(gen_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)

    # 非流式
    duration_ms = int(time.time() * 1000) - start_ms
    parts = hr.result.parts if isinstance(hr.result, Result) else []
    outputs = hr.result.outputs if isinstance(hr.result, Result) else []
    msg_id = chat_service.finalize(hr, parts=parts, outputs=outputs)

    def gen_final():
        for part in parts:
            yield _sse({"type": "chunk", "part": part})
        yield _sse({"type": "done", "message_id": msg_id, "duration_ms": duration_ms})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen_final(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _uid(user: dict) -> int:
    v = user.get("userId", 0)
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0