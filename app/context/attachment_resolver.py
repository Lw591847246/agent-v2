# app/context/attachment_resolver.py
"""附件解析：file_id / base64 两种来源 → Attachment 列表。"""
from __future__ import annotations

from typing import List

from app.core.logger import logger
from app.models.attachment import Attachment


class AttachmentResolver:
    def resolve_many(self, refs: List[dict]) -> List[Attachment]:
        """
        refs 每条支持：
          1) {"file_id": "xxx", "type": "image"}
          2) {"base64": "xxx", "name": "a.png", "type": "image"}
        """
        from app.utils.file_registry import file_registry

        result: List[Attachment] = []
        for r in refs or []:
            r = r or {}
            fid = r.get("file_id", "")
            b64 = r.get("base64", "")

            if fid:
                meta = file_registry.get(fid)
                if not meta:
                    logger.warning(f"[AttachResolver] file_id not found: {fid}")
                    continue
                result.append(Attachment(
                    file_id=fid,
                    type=meta.get("type", ""),
                    name=meta.get("name", ""),
                    path=meta.get("path", ""),
                    base64="",
                    thumb_url=(
                        f"/agent/v1/upload/thumb/{fid}"
                        if meta.get("thumb_path") else ""
                    ),
                    size=meta.get("size", 0),
                    duration=meta.get("duration") or 0.0,
                ))
                continue

            if b64:
                name = r.get("name", "")
                result.append(Attachment(
                    file_id="",
                    type=r.get("type") or self._guess_type(name),
                    name=name,
                    path="",
                    base64=b64,
                ))
                continue

        return result

    @staticmethod
    def _guess_type(name: str) -> str:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in {"jpg", "jpeg", "png", "webp", "bmp", "gif"}:
            return "image"
        if ext in {"mp4", "mov", "avi", "mkv", "webm"}:
            return "video"
        if ext in {"mp3", "wav", "m4a", "aac", "ogg", "flac"}:
            return "audio"
        return "doc"


attachment_resolver = AttachmentResolver()