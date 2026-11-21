# app/api/v1/custom_avatar_controller.py
"""自定义头像视频下载（路径白名单 + 授权 + 任务归属）。

原实现只把路径限制在 ./data/custom_avatar/ 下，完全没有鉴权 —— 任何人知道路径
即可下载。现在：签名有效 或 登录态 + 该视频对应的 task_id 属于当前用户。
"""
import os
import urllib.parse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import OptionalUser, authorize_download
from app.api.v1._ownership import assert_file_owned
from app.core.logger import logger
from app.utils.signed_url import verify_url

router = APIRouter(tags=["CustomAvatar"])
download_router = APIRouter(tags=["CustomAvatar"])

# 唯一允许下载的根目录（启动时归一化，后续比较使用绝对路径）
_CUSTOM_AVATAR_ROOT = os.path.abspath(os.path.join("data", "custom_avatar"))


def _safe_resolve(raw_path: str) -> str:
    """解码 → 归一化 → 白名单校验；返回可安全使用的绝对路径。"""
    decoded = urllib.parse.unquote(raw_path or "").strip()
    if not decoded:
        raise HTTPException(status_code=400, detail="empty path")
    if "\x00" in decoded:
        raise HTTPException(status_code=400, detail="invalid path")

    abs_path = os.path.abspath(decoded)  # abspath 内含 normpath，能消掉 ..
    if abs_path != _CUSTOM_AVATAR_ROOT and not abs_path.startswith(
        _CUSTOM_AVATAR_ROOT + os.sep
    ):
        logger.warning(
            f"[CustomAvatar-Download] 越权路径拒绝: {raw_path!r} -> {abs_path}"
        )
        raise HTTPException(status_code=403, detail="path not allowed")

    return abs_path


@download_router.get("/agent/v1/custom_avatar/download")
async def download_custom_avatar(
    path: str = Query(...),
    exp: str = "",
    sig: str = "",
    user=OptionalUser,
):
    abs_path = _safe_resolve(path)

    authorize_download(
        sig_ok=verify_url("custom_avatar", path, exp, sig),
        user=user,
        check_owner=lambda uid: assert_file_owned(abs_path, uid),
    )

    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="video not found")
    return FileResponse(abs_path, media_type="video/mp4")