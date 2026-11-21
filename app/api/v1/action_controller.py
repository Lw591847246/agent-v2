# app/api/v1/action_controller.py
"""卡片点击 action 协议：SSE。

修复（P0-1）：入口增加会话归属校验。
action 一定发生在既有会话上，所以用 assert_session_owned（不做"不存在就创建"）：
会话不存在或不属于本人 -> 404，避免用他人 session_id 触发 skill。
"""
import json
import time

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import CurrentUser, assert_session_owned, uid_of
from app.api.v1._sse import SSE_HEADERS, sse, sse_done
from app.application.action_service import action_service
from app.application.conversation_service import conversation_service
from app.contracts.action import ActionRequest
from app.contracts.result import Result
from app.utils.url_signer import sign_deep

router = APIRouter(tags=["Action"])


class ChatActionRequest(BaseModel):
    session_id: str
    card_id: str
    card_type: str = ""
    option_key: str = ""
    text: str = ""
    client_id: str = ""
    file_id: str = ""


@router.post("/agent/v1/chat/action")
def action(req: ChatActionRequest, user=CurrentUser):
    start_ms = int(time.time() * 1000)
    user_id = uid_of(user)

    session_id = assert_session_owned(req.session_id, user_id)

    action_req = ActionRequest(
        session_id=session_id, card_id=req.card_id,
        card_type=req.card_type, option_key=req.option_key,
        text=req.text, file_id=req.file_id,
        client_id=req.client_id, user_id=user_id,
    )
    result = action_service.handle(action_req)
    duration_ms = int(time.time() * 1000) - start_ms

    msg_id = 0
    try:
        if isinstance(result, Result) and result.parts:
            types = {p.get("type") for p in result.parts}
            message_type = "card" if "card" in types else ("video" if "video" in types else "text")
            msg_id = conversation_service.save_assistant_message(
                session_id=session_id,
                content=json.dumps(result.parts, ensure_ascii=False),
                message_type=message_type,
                outputs=result.outputs or [],
                duration_ms=duration_ms,
            )
    except Exception:
        pass

    def gen():
        if isinstance(result, Result):
            for part in result.parts:
                yield sse({"type": "chunk", "part": sign_deep(part)})
        yield sse({"type": "done", "message_id": msg_id, "duration_ms": duration_ms})
        yield sse_done()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)