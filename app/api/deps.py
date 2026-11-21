# app/api/deps.py
"""请求级依赖：当前用户、会话归属、任务归属、下载链接授权。

约定
- 需要登录的 HTTP 入口一律用 `CurrentUser`；
- 允许"签名 URL 访问"的下载入口用 `OptionalUser`（浏览器 <a href> 带不了请求头）；
- 所有"客户端传进来的 session_id"都必须先过 `resolve_session`；
- 所有"客户端传进来的 task_id"都必须先过 `get_owned_task`。
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, Request

from app.application.conversation_service import conversation_service
from app.core.exceptions import AuthException, NotFoundException
from app.core.logger import logger
from app.infrastructure.repositories.task_repo import task_repo
from app.middleware.auth import auth_interceptor


# ============================================================
# 用户
# ============================================================

def current_user(user: dict = Depends(auth_interceptor)) -> dict:
    """强制登录：白名单出来的 anonymous 在这里被拒绝。"""
    raw = user.get("userId")
    if raw in (None, "", "anonymous"):
        raise AuthException("未登录")
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        raise AuthException("非法用户标识")
    if uid <= 0:
        raise AuthException("未登录")
    return {"userId": uid, "username": user.get("username") or ""}


CurrentUser = Depends(current_user)


async def optional_user(request: Request) -> Optional[dict]:
    """有 Bearer 头就解析用户，没有就返回 None（供签名 URL 场景使用）。"""
    auth_header = (request.headers.get("Authorization") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        return None
    try:
        raw = await auth_interceptor(request)
        uid = int(raw.get("userId"))
    except Exception:
        # 下载接口里 token 失效不应直接 401，交给签名分支决定
        return None
    if uid <= 0:
        return None
    return {"userId": uid, "username": raw.get("username") or ""}


OptionalUser = Depends(optional_user)


def uid_of(user: dict) -> int:
    return int(user["userId"])


# ============================================================
# 会话 / 任务归属
# ============================================================

def resolve_session(session_id: Optional[str], user_id: int) -> str:
    """会话准入：不存在则创建；存在但非本人 -> NotFound。"""
    return conversation_service.resolve_session(session_id, user_id)


def assert_session_owned(session_id: str, user_id: int) -> str:
    return conversation_service.assert_owned(session_id, user_id)


def get_owned_task(task_id: str, user_id: int):
    """任务归属校验，返回 TaskInfo；不符一律 404（不暴露资源是否存在）。

    兼容历史数据：storyboard 的「重新生成」链路历史上未透传 user_id，
    新任务行 user_id 可能为 0；此时回落到"任务所属会话的归属"，仍然 fail-closed。
    """
    info = task_repo.get(task_id)
    if not info:
        raise NotFoundException("任务不存在")

    uid = int(user_id)
    owner = int(info.user_id or 0)
    if uid > 0 and owner == uid:
        return info
    if owner == 0 and info.session_id and conversation_service.is_owned(info.session_id, uid):
        return info

    logger.warning(f"[Authz] 任务归属拒绝 task={task_id} owner={owner} req={uid}")
    raise NotFoundException("任务不存在")


# ============================================================
# 下载链接授权（签名 URL 优先，其次登录态 + 归属）
# ============================================================

def _legacy_download_allowed() -> bool:
    """过渡开关：auth.require_download_auth=false 时允许未签名下载（仅灰色期使用）。"""
    try:
        from app.config.loader import config
        return not bool(config.get("auth.require_download_auth", True))
    except Exception:
        return False


def authorize_download(
    sig_ok: bool,
    user: Optional[dict],
    check_owner: Optional[Callable[[int], None]] = None,
) -> None:
    """统一下载授权：

    1) 签名有效 -> 放行（浏览器 <a href> / <video src> 走这条）；
    2) 有登录态 -> 校验归属后放行（前端 fetch / axios 带 Authorization 走这条）；
    3) auth.require_download_auth=false 时放行并告警（上线过渡期专用）。
    """
    if sig_ok:
        return
    if user:
        uid = uid_of(user)
        if check_owner is not None:
            check_owner(uid)
        return
    if _legacy_download_allowed():
        logger.warning(
            "[Authz] 未签名且未登录的下载被放行"
            "（auth.require_download_auth=false，仅过渡期可用）"
        )
        return
    raise NotFoundException("文件不存在")