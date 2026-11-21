# app/models/attachment.py
"""统一的素材抽象：file_id 模式和 base64 模式共用一个 Attachment

【本轮修复 · 低危】get_path() 原来用**请求可控**的 name 直接拼临时文件 suffix
（下划线 + 原始 name）。name 里带 / 、反斜杠、.. 或换行时，NamedTemporaryFile 的行为
不确定（可能写到意外目录、也可能抛错被静默吞掉），属"文件名注入"类问题。
现在：os.path.basename + 字符白名单 + 长度截断 + 只保留合法扩展名；
拿不到安全名字就不带 suffix（功能不受影响，临时文件由后续解析器按内容判定类型）。
同时给临时文件加 0600 权限——素材可能含用户隐私内容。
"""
from __future__ import annotations

import base64
import os
import re
import tempfile
from dataclasses import dataclass
from typing import List

from app.core.logger import logger
from app.utils.signed_url import thumb_url as signed_thumb_url

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-]+")
_SAFE_MAX_LEN = 80
_ALLOWED_EXT = {
    "jpg", "jpeg", "png", "webp", "bmp", "gif",
    "mp4", "mov", "avi", "mkv", "webm",
    "mp3", "wav", "m4a", "aac", "ogg", "flac",
    "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "md", "csv", "json",
}


def safe_tmp_suffix(raw_name: str) -> str:
    """把请求可控的文件名收敛成可安全用作 tempfile suffix 的字符串。"""
    name = os.path.basename(str(raw_name or "").strip())
    if not name:
        return ""
    name = _SAFE_NAME_RE.sub("_", name)[:_SAFE_MAX_LEN].lstrip("._-")
    if not name:
        return ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in _ALLOWED_EXT:
        return "_." + ext          # 只保留扩展名，主体交给 tempfile 随机串
    stem = name.split(".")[0][:40].strip("._-")
    return ("_" + stem) if stem else ""


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
                tmp = tempfile.NamedTemporaryFile(
                    suffix=safe_tmp_suffix(self.name), delete=False
                )
                try:
                    tmp.write(raw)
                finally:
                    tmp.close()
                try:
                    os.chmod(tmp.name, 0o600)
                except Exception:
                    pass
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
                        signed_thumb_url(fid) if meta.get("thumb_path") else ""
                    ),
                    size=meta.get("size", 0),
                    duration=meta.get("duration") or 0.0,
                ))
                continue

            # 分支 2：base64 模式
            if b64:
                name = os.path.basename(str(r.get("name", "") or ""))
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
