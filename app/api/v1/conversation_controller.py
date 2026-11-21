# app/api/v1/conversation_controller.py
"""会话管理。

本轮改动：
1. 统一用 deps.current_user（userId 已是 int，且强制登录）；
2. 返回历史消息前统一给产物 URL 加签名 —— 历史卡片里的
   `<a href>` / `<video src>` / 缩略图因此不需要前端做任何改造。
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, uid_of
from app.application.conversation_service import conversation_service
from app.core.logger import logger
from app.utils.url_signer import sign_deep

router = APIRouter(tags=["会话"])


@router.get("/agent/v1/conversations")
def list_conversations(user=CurrentUser):
    return JSONResponse(conversation_service.list(uid_of(user)))


@router.post("/agent/v1/conversation/new")
def new_conversation(user=CurrentUser):
    session_id = conversation_service.new_conversation(uid_of(user))
    return JSONResponse({"session_id": session_id})


@router.get("/agent/v1/conversation/{session_id}/messages")
def get_messages(session_id: str, user=CurrentUser):
    data = conversation_service.messages(session_id, uid_of(user))
    messages = data.get("messages") or []
    logger.info(f"【会话】session={session_id}, 返回 {len(messages)} 条消息")

    # 产物 URL 统一签名（已签名的会被跳过）
    return JSONResponse(sign_deep(data))