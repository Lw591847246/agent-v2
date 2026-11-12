# app/api/v1/conversation_controller.py
"""会话管理。"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.application.conversation_service import conversation_service
from app.core.logger import logger
from app.middleware.auth import auth_interceptor

router = APIRouter(tags=["会话"])


@router.get("/agent/v1/conversations")
def list_conversations(user=Depends(auth_interceptor)):
    return JSONResponse(conversation_service.list(user["userId"]))


@router.post("/agent/v1/conversation/new")
def new_conversation(user=Depends(auth_interceptor)):
    session_id = conversation_service.new_conversation(user["userId"])
    return JSONResponse({"session_id": session_id})


@router.get("/agent/v1/conversation/{session_id}/messages")
def get_messages(session_id: str, user=Depends(auth_interceptor)):
    data = conversation_service.messages(session_id, user["userId"])
    messages = data.get("messages") or []
    logger.info(f"【会话】session={session_id}, 返回 {len(messages)} 条消息")
    return JSONResponse(data)