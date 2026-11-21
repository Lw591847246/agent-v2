# app/api/v1/sign_controller.py
"""下载 URL 签名器（给前端 <a href> / <video src> 显式换取签名地址用）。

⚠️ 本接口是"可选增强"：后端的消息/任务/SSE 出口已经自动签名
（见 app/utils/url_signer.sign_deep），前端不改也能用。
只有在需要手工拼地址、或想把签名时机前移时才调用本接口。

它与下载端点共用同一套归属校验：签不出来的资源直接 404。

【本轮修复】
1. 中危 5：/agent/v1/sign 对 tts / liveportrait_job 原来 **直接 return 放行**，
   任意登录用户可给别人的 tts filename / LP job_id 签发合法签名；配合此前
   "签名永久有效"，等于越权读取他人音频/视频。
   现在改为 **fail-closed**：这两类资源当前没有可靠的归属元数据（音频文件名扁平、
   不入 file_registry / task 表），因此不再接受显式签发，直接 404。
   前端需要播放时：用带 Authorization 的 fetch/blob（下载端点本身支持登录态 + 归属），
   或让后端在产出该资源的出口自动签名（已有 url_signer.sign_deep 链路）。
   如确实需要放开，显式配 security.allow_sign_ownerless=true（默认 false）。
2. 签发一律带 TTL（sign_url 的 ttl 传 None -> 用 SIGNED_URL_DEFAULT_TTL / 配置默认值），
   不再产生永久有效链接。
3. url 校验收紧：必须解析出以 /agent/v1/ 开头的**站内相对路径**；
   拒绝含协议头的绝对 URL、含片段/双斜杠协议相对写法，防"给任意外站 URL 签名"。
"""
import os
import urllib.parse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import (
    CurrentUser, assert_session_owned, get_owned_task, uid_of,
)
from app.api.v1._ownership import assert_file_owned, assert_storyboard_task
from app.application.file_upload_service import file_upload_service
from app.config.loader import config
from app.core.logger import logger
from app.utils.signed_url import sign_url
from app.utils.url_signer import parse_kind_ident

router = APIRouter(tags=["URL签名"])

_ALLOWED_PREFIX = "/agent/v1/"
# 明确"无归属元数据"的类型：默认拒绝签发（见模块 docstring 第 1 条）
_OWNERLESS_KINDS = ("tts", "liveportrait_job")


class SignRequest(BaseModel):
    url: str


def _allow_ownerless() -> bool:
    return bool(config.get("security.allow_sign_ownerless", False))


def _check_owner(kind: str, ident: str, query: dict, uid: int) -> None:
    """按资源类型做归属校验；无权/无法判定的一律 404（不暴露存在性）。"""
    if kind == "job_video":
        get_owned_task(ident, uid)
    elif kind in ("ppt", "word"):
        if "/" not in ident:
            raise HTTPException(status_code=400, detail="invalid url")
        session_id, _ = ident.split("/", 1)
        assert_session_owned(session_id, uid)
    elif kind == "storyboard":
        assert_storyboard_task(ident, uid)
    elif kind in ("skill", "custom_avatar", "liveportrait"):
        abs_path = os.path.realpath(os.path.abspath(ident))
        assert_file_owned(abs_path, uid)
    elif kind == "upload_thumb":
        meta = file_upload_service.get_meta(ident)
        if not meta or int(meta.get("user_id") or 0) != int(uid):
            raise HTTPException(status_code=404, detail="not found")
    elif kind in _OWNERLESS_KINDS:
        # 无归属元数据：默认 fail-closed，不接受显式签发
        if not _allow_ownerless():
            logger.warning(
                f"[Sign] 拒绝为无归属元数据类型签发 kind={kind} uid={uid}"
            )
            raise HTTPException(status_code=404, detail="not found")
        logger.warning(
            "[Sign] security.allow_sign_ownerless=true，已为 tts/LP-job 签发签名，"
            "这类资源仅靠 128bit 不可猜标识保护，请尽快补归属元数据"
        )
    else:
        raise HTTPException(status_code=400, detail="unsupported url")


def _normalize_insite_url(url: str) -> str:
    """只接受站内相对路径；剥掉代理前缀（如 /agent-api）与协议头。"""
    if not url or url.startswith("//"):
        raise HTTPException(status_code=400, detail="invalid url")

    idx = url.find(_ALLOWED_PREFIX)
    if idx < 0:
        raise HTTPException(status_code=400, detail="invalid url")
    # 协议前缀（http://host/agent/v1/...）只在 idx 之前出现时才允许剥离
    head = url[:idx]
    if head and not (head.startswith("/") or head.startswith("http://") or head.startswith("https://")):
        raise HTTPException(status_code=400, detail="invalid url")
    if "://" in url[idx:]:
        raise HTTPException(status_code=400, detail="invalid url")
    return url[idx:]


@router.post("/agent/v1/sign")
def sign_download_url(payload: SignRequest, user=CurrentUser):
    url = _normalize_insite_url((payload.url or "").strip())

    path, _, raw_query = url.partition("?")
    pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True)
    # 已带签名参数的一律剥离后重签，避免"旧 exp 直接沿用"
    query = {k: v for k, v in pairs if k not in ("exp", "sig")}

    parsed = parse_kind_ident(path, query)
    if not parsed:
        raise HTTPException(status_code=400, detail="unsupported url")
    kind, ident = parsed

    uid = uid_of(user)
    _check_owner(kind, ident, query, uid)

    clean = urllib.parse.urlencode(list(query.items()))
    sig_qs = sign_url(kind, ident)  # ttl=None -> 统一用默认时效
    signed = f"{path}?{clean}&{sig_qs}" if clean else f"{path}?{sig_qs}"
    logger.info(f"[Sign] user={uid} kind={kind} {path} -> signed")
    return {"url": signed}
