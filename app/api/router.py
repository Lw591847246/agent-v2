"""API 路由注册。"""
from fastapi import APIRouter

from app.api.v1 import (
    action_controller,
    chat_controller,
    conversation_controller,
    custom_avatar_controller,
    file_controller,
    image_controller,
    job_controller,
    knowledge_controller,
    liveportrait_controller,
    ppt_controller,
    skill_download_controller,
    storyboard_controller,
    tts_controller,
    upload_controller,
    word_controller,
)

api_router = APIRouter()
api_router.include_router(chat_controller.router)
api_router.include_router(action_controller.router)
api_router.include_router(conversation_controller.router)
api_router.include_router(job_controller.router)
api_router.include_router(image_controller.router)
api_router.include_router(file_controller.router)
api_router.include_router(upload_controller.router)
api_router.include_router(ppt_controller.router)
api_router.include_router(word_controller.router)
api_router.include_router(storyboard_controller.router)
api_router.include_router(custom_avatar_controller.router)
api_router.include_router(liveportrait_controller.router)
api_router.include_router(tts_controller.router)
api_router.include_router(knowledge_controller.router)
api_router.include_router(skill_download_controller.router)