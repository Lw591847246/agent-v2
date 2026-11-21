# app/api/v1/upload_controller.py
"""通用文件上传接口。

鉴权分工
--------
- `router`        : /asset  需登录（router.py 的 protected 组）
- `thumb_router`  : /thumb  公开无状态路由，端点内强校验：
      1) 带 exp/sig 且签名有效 -> 放行（浏览器 <img src> 走这条）
      2) 带 Authorization     -> 校验 file_registry 里的 user_id 与登录用户一致
      3) 都没有               -> 404
  （file_id 本身也已提升到 128 bit 随机）

本版加固
--------
- **分段读取 + 上限**：不再 `await file.read()` 一口气读进内存，
  改为 1MB 分块累积，超过 `upload.max_size_mb` 立即 413 —— 防内存打爆；
- 真正的类型/魔数校验在 `file_upload_service.validate_upload()`，这里只做入口闸。
"""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.api.deps import (
    CurrentUser, OptionalUser, assert_session_owned, authorize_download, uid_of,
)
from app.application.file_upload_service import (
    MAX_FILE_BYTES, MAX_SIZE_MB, file_upload_service,
)
from app.core.logger import logger
from app.utils.signed_url import thumb_url, verify_url

router = APIRouter(prefix="/agent/v1/upload", tags=["上传"])
thumb_router = APIRouter(prefix="/agent/v1/upload", tags=["上传"])

_CHUNK = 1024 * 1024


async def read_upload_capped(upload: UploadFile) -> bytes:
    """分块读取并封顶，避免大文件把内存打爆。"""
    buf = bytearray()
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大：超过 {MAX_SIZE_MB}MB 上限",
            )
    return bytes(buf)


@router.post("/asset")
async def upload_asset(
    file: UploadFile = File(...),
    session_id: str = Form(""),
    user=CurrentUser,
):
    user_id = uid_of(user)

    content = await read_upload_capped(file)
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    # 会话归属：非空 session_id 必须存在且属于本人，否则拒绝
    session_id = (session_id or "").strip()
    if session_id:
        session_id = assert_session_owned(session_id, user_id)

    try:
        meta = file_upload_service.save_asset(
            content=content,
            filename=file.filename or "unnamed",
            user_id=user_id,
            session_id=session_id,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[Upload] save failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="upload failed")

    return {
        "file_id": meta["file_id"],
        "type": meta["type"],
        "name": meta["name"],
        "size": meta["size"],
        "duration": meta.get("duration"),
        "thumb_url": thumb_url(meta["file_id"]) if meta.get("thumb_path") else None,
    }


def _check_thumb_owner(file_id: str, user_id: int) -> None:
    meta = file_upload_service.get_meta(file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="thumb not found")
    if int(meta.get("user_id") or 0) != int(user_id):
        logger.warning(
            f"[Upload] 缩略图归属拒绝 file={file_id} "
            f"owner={meta.get('user_id')} req={user_id}"
        )
        raise HTTPException(status_code=404, detail="thumb not found")


@thumb_router.get("/thumb/{file_id}")
def get_thumb(file_id: str, exp: str = "", sig: str = "", user=OptionalUser):
    authorize_download(
        sig_ok=verify_url("upload_thumb", file_id, exp, sig),
        user=user,
        check_owner=lambda uid: _check_thumb_owner(file_id, uid),
    )

    data = file_upload_service.get_thumb_bytes(file_id)
    if not data:
        raise HTTPException(status_code=404, detail="thumb not found")

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )