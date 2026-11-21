# app/utils/file_magic.py
"""文件类型校验：扩展名白名单 + 文件头（魔数）校验。

为什么需要
----------
上传原来只看扩展名（`filename.rsplit(".",1)[-1]`），一个 `.png` 里装可执行内容
照样能落盘；也没有大小上限，2GB 的文件会把进程内存/磁盘打爆。

本模块只做两件事，纯函数、无外部依赖、可单测：
1. `check_magic(content, ext)`：按扩展名核对文件头；
2. `sniff_magic(content)`：反推真实类型（用于日志/告警，不强制拦截）。

约定：Office 新格式（docx/xlsx/pptx）本质是 zip（PK\\x03\\x04），
      老格式（doc/xls/ppt）是 OLE 复合文档（D0 CF 11 E0 A1 B1 1A E1）。
"""
from __future__ import annotations

from typing import Optional

_PNG = b"\x89PNG\r\n\x1a\n"
_JPG = b"\xff\xd8\xff"
_GIF = b"GIF8"
_BMP = b"BM"
_RIFF = b"RIFF"
_WEBP = b"WEBP"
_AVI = b"AVI "
_WAVE = b"WAVE"
_PDF = b"%PDF"
_ZIP = b"PK\x03\x04"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_EBML = b"\x1a\x45\xdf\xa3"
_FTYP = b"ftyp"
_FLAC = b"fLaC"
_OGGS = b"OggS"
_ID3 = b"ID3"
_MP3_FRAME = (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa")

# 纯文本类：没有统一魔数，用"无 NUL + 可按 utf-8/gb18030 解码"判定
_TEXT_EXTS = {"txt", "md", "csv", "json"}


def _startswith(content: bytes, sig: bytes, offset: int = 0) -> bool:
    return content[offset:offset + len(sig)] == sig


def check_magic(content: bytes, ext: str) -> bool:
    """按扩展名校验文件头。未登记的扩展名一律放行（由白名单负责拦）。"""
    if not content:
        return False
    ext = (ext or "").lower().lstrip(".")

    if ext in _TEXT_EXTS:
        head = content[:4096]
        if b"\x00" in head:
            return False
        for enc in ("utf-8", "gb18030"):
            try:
                head.decode(enc)
                return True
            except UnicodeDecodeError:
                continue
        return False

    if ext == "png":
        return _startswith(content, _PNG)
    if ext in ("jpg", "jpeg"):
        return _startswith(content, _JPG)
    if ext == "gif":
        return _startswith(content, _GIF)
    if ext == "bmp":
        return _startswith(content, _BMP)
    if ext == "webp":
        return _startswith(content, _RIFF) and _startswith(content, _WEBP, 8)
    if ext == "pdf":
        return _startswith(content, _PDF)

    # Office
    if ext in ("docx", "xlsx", "pptx"):
        return _startswith(content, _ZIP)
    if ext in ("doc", "xls", "ppt"):
        return _startswith(content, _OLE)
    if ext == "zip":
        return _startswith(content, _ZIP)

    # 视频
    if ext in ("mp4", "mov", "m4v"):
        return _startswith(content, _FTYP, 4)
    if ext in ("mkv", "webm"):
        return _startswith(content, _EBML)
    if ext == "avi":
        return _startswith(content, _RIFF) and _startswith(content, _AVI, 8)

    # 音频
    if ext == "mp3":
        return _startswith(content, _ID3) or any(
            _startswith(content, s) for s in _MP3_FRAME
        )
    if ext == "wav":
        return _startswith(content, _RIFF) and _startswith(content, _WAVE, 8)
    if ext == "flac":
        return _startswith(content, _FLAC)
    if ext == "ogg":
        return _startswith(content, _OGGS)
    if ext == "m4a":
        return _startswith(content, _FTYP, 4)

    # 未登记：放行（白名单外的类型进不到这里）
    return True


def sniff_magic(content: bytes) -> Optional[str]:
    """反推真实类型（仅用于日志/告警，不做拦截）。"""
    if not content:
        return None
    if _startswith(content, _PNG):
        return "png"
    if _startswith(content, _JPG):
        return "jpg"
    if _startswith(content, _GIF):
        return "gif"
    if _startswith(content, _BMP):
        return "bmp"
    if _startswith(content, _PDF):
        return "pdf"
    if _startswith(content, _ZIP):
        return "zip"
    if _startswith(content, _OLE):
        return "ole"
    if _startswith(content, _EBML):
        return "mkv/webm"
    if _startswith(content, _FTYP, 4):
        return "mp4/mov"
    if _startswith(content, _RIFF) and _startswith(content, _WEBP, 8):
        return "webp"
    if _startswith(content, _RIFF) and _startswith(content, _WAVE, 8):
        return "wav"
    if _startswith(content, _FLAC):
        return "flac"
    if _startswith(content, _OGGS):
        return "ogg"
    if _startswith(content, _ID3):
        return "mp3"
    if _startswith(content, b"MZ"):
        return "exe"
    if _startswith(content, b"\x7fELF"):
        return "elf"
    return None