# app/api/router.py
"""API 路由注册。

分三组，避免"默认鉴权"误伤签名下载：
  protected      : 需登录（router 级依赖，新增接口默认受保护）
  public         : 无鉴权的公开资源（HMAC 签名 / 不可猜 file_id / 运维端点）
  download_router: 下载类，授权在端点内由 authorize_download 完成
                   （签名有效 或 登录态+归属）
"""
from fastapi import APIRouter, Depends

from app.api.deps import current_user
from app.api.v1 import (
    action_controller,
    chat_controller,
    conversation_controller,
    custom_avatar_controller,
    file_controller,
    health_controller,
    image_controller,
    job_controller,
    knowledge_controller,
    liveportrait_controller,
    ppt_controller,
    sign_controller,
    skill_download_controller,
    storyboard_controller,
    tts_controller,
    upload_controller,
    word_controller,
)

# ---------- 1. 需登录 ----------
protected = APIRouter(dependencies=[Depends(current_user)])
for _mod in (
    chat_controller,
    action_controller,
    conversation_controller,
    job_controller,
    file_controller,
    upload_controller,
    ppt_controller,
    word_controller,
    storyboard_controller,
    custom_avatar_controller,
    liveportrait_controller,
    tts_controller,
    knowledge_controller,
    skill_download_controller,
    sign_controller,
):
    protected.include_router(_mod.router)

# ---------- 2. 公开（自带签名 / 不可猜 ID / 运维） ----------
public = APIRouter()
public.include_router(image_controller.router)         # HMAC 签名（按 message_id）
public.include_router(upload_controller.thumb_router)  # 签名 或 登录态+归属
public.include_router(health_controller.router)        # /healthz /readyz /metrics

# ---------- 3. 下载（端点内授权：签名 或 登录态+归属） ----------
for _mod in (
    job_controller,
    ppt_controller,
    word_controller,
    storyboard_controller,
    custom_avatar_controller,
    liveportrait_controller,
    skill_download_controller,
    tts_controller,
):
    public.include_router(_mod.download_router)

api_router = APIRouter()
api_router.include_router(protected)
api_router.include_router(public)