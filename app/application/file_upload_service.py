"""文件上传服务：通用类型（image / video / doc / audio）

本版加固（架构复核 P0-1）
------------------------
1. **大小上限**：`upload.max_size_mb`（兜底 `file.max_file_size_mb`，再兜底 20MB），
   在 `save_asset` 入口强制校验 —— 之前的实现把配置读进来了却从没用过；
2. **扩展名白名单**：`upload.allowed_extensions`（缺省用服务自己支持的集合）；
   与 `file.allowed_extensions`（文档解析用）解耦，避免误伤视频/音频上传；
3. **文件头（魔数）校验**：`upload.magic_check` 为真时调 `utils/file_magic.check_magic`，
   拦掉"`.png` 里装可执行内容"这类伪装；
4. `session_id` / `user_id` 进路径前白名单校验 + realpath 断言（上一版已加）；
5. `file_id` 熵 128 bit（上一版已加）。
"""
from __future__ import annotations

import os
import re
import time
import uuid
from typing import Optional

from fastapi import HTTPException

from app.config.loader import config
from app.core.logger import logger
from app.utils.file_magic import check_magic, sniff_magic
from app.utils.file_registry import file_registry
from app.utils.safe_path import data_root, ensure_under

UPLOAD_ROOT = data_root("uploads")
THUMB_ROOT = data_root("uploads_thumbs")

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}
VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm"}
AUDIO_EXTS = {"mp3", "wav", "m4a", "aac", "ogg", "flac"}
DOC_EXTS = {
    "pdf", "doc", "docx", "xls", "xlsx",
    "ppt", "pptx", "txt", "md", "csv", "json",
}

_SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOC_EXTS

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ============================================================
# 配置（每次启动读一次）
# ============================================================
def _cfg_int(key: str, default: int) -> int:
    try:
        v = config.get(key, default)
        return int(v) if v not in (None, "") else default
    except Exception:
        return default


MAX_SIZE_MB = _cfg_int("upload.max_size_mb", _cfg_int("file.max_file_size_mb", 20))
MAX_TOTAL_MB = _cfg_int("upload.max_total_mb", 100)
MAX_FILE_BYTES = MAX_SIZE_MB * 1024 * 1024
MAX_TOTAL_BYTES = MAX_TOTAL_MB * 1024 * 1024

_raw_exts = config.get("upload.allowed_extensions") or []
ALLOWED_EXTS = {
    str(x).lower().lstrip(".")
    for x in _raw_exts
    if str(x).strip()
} or set(_SUPPORTED_EXTS)

MAGIC_CHECK = bool(config.get("upload.magic_check", True))


def _ext_of(filename: str) -> str:
    name = (filename or "").strip()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _detect_kind(filename: str) -> str:
    ext = _ext_of(filename)
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in DOC_EXTS:
        return "doc"
    return ""


def _safe_session_dir(session_id: str) -> str:
    """session_id 只允许 [A-Za-z0-9_-]{1,64}；空值归到 default。"""
    v = (session_id or "").strip()
    if not v:
        return "default"
    if not _SESSION_RE.match(v):
        logger.warning(f"[FileUpload] 非法 session_id 被拒绝: {session_id!r}")
        raise HTTPException(status_code=400, detail="invalid session_id")
    return v


def _safe_user_dir(user_id) -> str:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid user_id")
    if uid <= 0:
        raise HTTPException(status_code=400, detail="invalid user_id")
    return str(uid)


def validate_upload(content: bytes, filename: str) -> str:
    """上传三道闸：大小 → 扩展名 → 魔数。返回规范化扩展名。

    失败抛 HTTPException（400/413），由全局异常处理器转成统一响应体。
    """
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    # 1) 大小
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大：{len(content) / 1024 / 1024:.1f}MB > {MAX_SIZE_MB}MB",
        )

    # 2) 扩展名
    ext = _ext_of(filename)
    if not ext:
        raise HTTPException(status_code=400, detail="文件名缺少扩展名")
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    # 3) 魔数
    if MAGIC_CHECK and not check_magic(content, ext):
        sniffed = sniff_magic(content) or "unknown"
        logger.warning(
            f"[FileUpload] 魔数校验失败 name={filename!r} ext={ext} "
            f"sniffed={sniffed} size={len(content)}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"文件内容与扩展名不匹配（检测为 {sniffed}）",
        )

    return ext


def _video_duration(path: str) -> Optional[float]:
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        if fps > 0 and frames > 0:
            return round(frames / fps, 2)
    except Exception as e:
        logger.warning(f"[FileUpload] duration failed: {e}")
    return None


def _video_thumb(path: str, thumb_path: str) -> bool:
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            cap.release()
            return False
        cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return False
        h, w = frame.shape[:2]
        if max(h, w) > 400:
            scale = 400 / max(h, w)
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        cv2.imwrite(thumb_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return True
    except Exception as e:
        logger.warning(f"[FileUpload] video thumb failed: {e}")
        return False


def _image_thumb(path: str, thumb_path: str) -> bool:
    try:
        from PIL import Image
        img = Image.open(path)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.thumbnail((300, 300))
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        img.save(thumb_path, format="JPEG", quality=70, optimize=True)
        return True
    except Exception as e:
        logger.warning(f"[FileUpload] image thumb failed: {e}")
        return False


class FileUploadService:

    def save_asset(self, content: bytes, filename: str,
                   user_id, session_id: str) -> dict:
        # 三道闸（大小/扩展名/魔数）—— 所有入口（upload/asset、process-file）都经过这里
        ext = validate_upload(content, filename)

        kind = _detect_kind(filename)
        if not kind:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {filename}")

        prefix = {"image": "img_", "video": "vid_",
                  "audio": "aud_", "doc": "doc_"}[kind]
        file_id = prefix + uuid.uuid4().hex          # 128 bit 熵

        user_dir_name = _safe_user_dir(user_id)
        session_dir_name = _safe_session_dir(session_id)

        user_dir = ensure_under(
            UPLOAD_ROOT,
            os.path.join(UPLOAD_ROOT, user_dir_name, session_dir_name),
        )
        os.makedirs(user_dir, exist_ok=True)

        save_path = ensure_under(UPLOAD_ROOT, os.path.join(user_dir, f"{file_id}.{ext}"))
        with open(save_path, "wb") as f:
            f.write(content)

        thumb_dir = ensure_under(
            THUMB_ROOT,
            os.path.join(THUMB_ROOT, user_dir_name, session_dir_name),
        )
        thumb_path = ensure_under(THUMB_ROOT, os.path.join(thumb_dir, f"{file_id}.jpg"))

        duration = None
        thumb_ok = False
        if kind == "video":
            duration = _video_duration(save_path)
            thumb_ok = _video_thumb(save_path, thumb_path)
        elif kind == "image":
            thumb_ok = _image_thumb(save_path, thumb_path)

        meta = {
            "file_id": file_id, "type": kind, "name": filename,
            "size": len(content), "path": save_path,
            "thumb_path": thumb_path if thumb_ok else None,
            "duration": duration, "user_id": int(user_dir_name),
            "session_id": session_id if session_dir_name != "default" else "",
            "created_at": time.time(),
        }
        file_registry.save(file_id, meta)
        logger.info(
            f"[FileUpload] saved {file_id} kind={kind} ext={ext} size={len(content)}"
        )
        return meta

    def get_meta(self, file_id: str) -> Optional[dict]:
        return file_registry.get(file_id)

    def get_thumb_bytes(self, file_id: str) -> Optional[bytes]:
        meta = file_registry.get(file_id)
        if not meta:
            return None
        thumb = meta.get("thumb_path")
        if not thumb or not os.path.isfile(thumb):
            return None
        try:
            ensure_under(THUMB_ROOT, thumb)
        except Exception:
            logger.warning(f"[FileUpload] 缩略图路径越界，拒绝读取: {thumb}")
            return None
        with open(thumb, "rb") as f:
            return f.read()

    def delete_asset(self, file_id: str) -> None:
        meta = file_registry.get(file_id)
        if meta:
            for p in (meta.get("path"), meta.get("thumb_path")):
                try:
                    if p and os.path.isfile(p):
                        os.remove(p)
                except Exception as e:
                    logger.warning(f"[FileUpload] delete failed {p}: {e}")
        file_registry.delete(file_id)


file_upload_service = FileUploadService()