# app/api/v1/action_controller.py
"""卡片点击 action 协议：SSE。"""
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.application.action_service import action_service
from app.application.conversation_service import conversation_service
from app.contracts.action import ActionRequest
from app.contracts.result import Result
from app.middleware.auth import auth_interceptor

router = APIRouter(tags=["Action"])
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ChatActionRequest(BaseModel):
    session_id: str
    card_id: str
    card_type: str = ""
    option_key: str = ""
    text: str = ""
    client_id: str = ""
    file_id: str = ""


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/agent/v1/chat/action")
def action(req: ChatActionRequest, user=Depends(auth_interceptor)):
    start_ms = int(time.time() * 1000)
    user_id = _uid(user)

    action_req = ActionRequest(
        session_id=req.session_id, card_id=req.card_id,
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
                session_id=req.session_id,
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
                yield _sse({"type": "chunk", "part": part})
        yield _sse({"type": "done", "message_id": msg_id, "duration_ms": duration_ms})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _uid(user: dict) -> int:
    v = user.get("userId", 0)
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0