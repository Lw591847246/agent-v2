
"""统一的素材抽象：file_id 模式和 base64 模式共用一个 Attachment"""
from __future__ import annotations

import base64
import os
import tempfile
from dataclasses import dataclass
from typing import List

from app.core.logger import logger


@dataclass
class Attachment:
    """一个用户上传的素材"""

    file_id: str = ""
    type: str = ""              # image | video | doc | audio
    name: str = ""
    path: str = ""              # 本地绝对路径
    base64: str = ""            # 内存内容（懒加载后可填）
    thumb_url: str = ""
    size: int = 0
    duration: float = 0.0       # 视频才有

    # ---------- Handler 只用这三个访问接口 ----------

    def get_base64(self) -> str:
        if self.base64:
            return self.base64
        if self.path and os.path.isfile(self.path):
            try:
                with open(self.path, "rb") as f:
                    self.base64 = base64.b64encode(f.read()).decode("utf-8")
                return self.base64
            except Exception as e:
                logger.warning(f"[Attachment] read path failed: {e}")
        return ""

    def get_bytes(self) -> bytes:
        if self.base64:
            try:
                return base64.b64decode(self.base64)
            except Exception:
                return b""
        if self.path and os.path.isfile(self.path):
            try:
                with open(self.path, "rb") as f:
                    return f.read()
            except Exception:
                return b""
        return b""

    def get_path(self) -> str:
        if self.path and os.path.isfile(self.path):
            return self.path
        if self.base64:
            try:
                raw = base64.b64decode(self.base64)
                suffix = f"_{self.name}" if self.name else ""
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.write(raw)
                tmp.close()
                self.path = tmp.name
                return self.path
            except Exception as e:
                logger.warning(f"[Attachment] write tmp failed: {e}")
        return ""

    # ---------- 类型判断 ----------

    def is_image(self) -> bool:
        return self.type == "image"

    def is_video(self) -> bool:
        return self.type == "video"

    def is_doc(self) -> bool:
        return self.type == "doc"

    def is_audio(self) -> bool:
        return self.type == "audio"

    # ---------- 卡片用 ----------

    def to_card_item(self) -> dict:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "duration": self.duration,
            "thumb_url": self.thumb_url,
            "removable": True,
        }


class AttachmentResolver:
    """统一解析：file_id 模式 和 base64 模式"""

    def resolve_many(self, refs: List[dict]) -> List[Attachment]:
        """
        refs 每条支持两种形态：
          1) {"file_id": "xxx", "type": "image"}
          2) {"base64": "xxx", "name": "a.png", "type": "image"}
        """
        from app.utils.file_registry import file_registry

        result: List[Attachment] = []
        for r in refs or []:
            r = r or {}
            fid = r.get("file_id", "")
            b64 = r.get("base64", "")

            # 分支 1：file_id 模式
            if fid:
                meta = file_registry.get(fid)
                if not meta:
                    logger.warning(f"[AttachmentResolver] file_id not found: {fid}")
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

            # 分支 2：base64 模式
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