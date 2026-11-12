"""文件上传服务：通用类型（image / video / doc / audio）"""
from __future__ import annotations

import os
import uuid
import time
from typing import Optional

from app.core.logger import logger
from app.utils.file_registry import file_registry


UPLOAD_ROOT = "./data/uploads"
THUMB_ROOT = "./data/uploads_thumbs"

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}
VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm"}
AUDIO_EXTS = {"mp3", "wav", "m4a", "aac", "ogg", "flac"}
DOC_EXTS = {
    "pdf", "doc", "docx", "xls", "xlsx",
    "ppt", "pptx", "txt", "md", "csv", "json",
}


def _detect_kind(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in DOC_EXTS:
        return "doc"
    return ""


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
                   user_id: int, session_id: str) -> dict:
        kind = _detect_kind(filename)
        if not kind:
            raise ValueError(f"不支持的文件类型: {filename}")

        prefix = {"image": "img_", "video": "vid_",
                  "audio": "aud_", "doc": "doc_"}[kind]
        file_id = prefix + uuid.uuid4().hex[:12]

        user_dir = os.path.join(UPLOAD_ROOT, str(user_id), session_id or "default")
        os.makedirs(user_dir, exist_ok=True)

        ext = filename.rsplit(".", 1)[-1].lower()
        save_path = os.path.join(user_dir, f"{file_id}.{ext}")
        with open(save_path, "wb") as f:
            f.write(content)

        thumb_dir = os.path.join(THUMB_ROOT, str(user_id), session_id or "default")
        thumb_path = os.path.join(thumb_dir, f"{file_id}.jpg")

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
            "duration": duration, "user_id": user_id,
            "session_id": session_id, "created_at": time.time(),
        }
        file_registry.save(file_id, meta)
        logger.info(f"[FileUpload] saved {file_id} kind={kind} size={len(content)}")
        return meta

    def get_thumb_bytes(self, file_id: str) -> Optional[bytes]:
        meta = file_registry.get(file_id)
        if not meta:
            return None
        thumb = meta.get("thumb_path")
        if not thumb or not os.path.isfile(thumb):
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