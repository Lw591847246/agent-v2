"""文件类型校验：扩展名白名单 + 文件头（魔数）校验。

修复说明（本次根因）
--------------------
原 check_magic 对纯文本类（txt/md/csv/json）的判定是：
    前 4096 字节若含 0x00(NUL) 即直接判 False；否则尝试 utf-8/gb18030 解码。

由于 gb18030 几乎能解码任意字节序列、极少失败，纯文本被判 False 的
真正触发条件只有一个：文件前若干字节里存在 0x00 字节。
最常见诱因是 txt 实际以 UTF-16(LE/BE) 编码写出（Windows 脚本 / 部分 dump
工具默认 UTF-16LE），ASCII 字符间夹满 00，于是被误判为非文本，导致这类
纯文本永远上传失败（HTTP 400，detail 检测为 unknown）。

新策略：纯文本没有统一魔数，凡 sniff_magic 嗅探不出任何已知二进制签名
（返回 None）的内容一律视为合法文本放行；只有嗅出了具体二进制签名
（exe/elf/zip/ole/图片/office 等）才判定为"伪装成 .txt 的二进制"并拒绝。
既不再误杀 UTF-8 / UTF-16 / GBK 等任意编码的文本，又保留对常见伪装文件的拦截。
"""
from __future__ import annotations

from typing import Optional

_PNG = b'\x89PNG\r\n\x1a\n'
_JPG = b'\xff\xd8\xff'
_GIF = b'GIF8'
_BMP = b'BM'
_RIFF = b'RIFF'
_WEBP = b'WEBP'
_AVI = b'AVI '
_WAVE = b'WAVE'
_PDF = b'%PDF'
_ZIP = b'PK\x03\x04'
_OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_EBML = b'\x1aE\xdf\xa3'
_FTYP = b'ftyp'
_FLAC = b'fLaC'
_OGGS = b'OggS'
_ID3 = b'ID3'
_MP3_FRAME = (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2', b'\xff\xfa')
_EXE = b'MZ'
_ELF = b'\x7fELF'
_TEXT_EXTS = {'txt', 'csv', 'md', 'json'}


def _startswith(content: bytes, sig: bytes, offset: int = 0) -> bool:
    return content[offset:offset + len(sig)] == sig


def sniff_magic(content: bytes) -> Optional[str]:
    # 反推真实类型（用于日志/告警 + 文本类扩展名的伪装判定）。
    if not content:
        return None
    if _startswith(content, _PNG):
        return 'png'
    if _startswith(content, _JPG):
        return 'jpg'
    if _startswith(content, _GIF):
        return 'gif'
    if _startswith(content, _BMP):
        return 'bmp'
    if _startswith(content, _PDF):
        return 'pdf'
    if _startswith(content, _ZIP):
        return 'zip'
    if _startswith(content, _OLE):
        return 'ole'
    if _startswith(content, _EBML):
        return 'mkv/webm'
    if _startswith(content, _FTYP, 4):
        return 'mp4/mov'
    if _startswith(content, _RIFF) and _startswith(content, _WEBP, 8):
        return 'webp'
    if _startswith(content, _RIFF) and _startswith(content, _WAVE, 8):
        return 'wav'
    if _startswith(content, _FLAC):
        return 'flac'
    if _startswith(content, _OGGS):
        return 'ogg'
    if _startswith(content, _ID3):
        return 'mp3'
    if _startswith(content, _EXE):
        return 'exe'
    if _startswith(content, _ELF):
        return 'elf'
    return None


def _looks_like_text(content: bytes) -> bool:
    # 纯文本无统一魔数：嗅不出任何已知二进制签名就当合法文本放行；
    # 能嗅出具体二进制签名（exe/zip/图片/office 等）才是伪装，拒绝。
    return sniff_magic(content) is None


def check_magic(content: bytes, ext: str) -> bool:
    # 按扩展名校验文件头。未登记的扩展名一律放行（由白名单负责拦）。
    if not content:
        return False
    ext = (ext or '').lower().lstrip('.')

    if ext in _TEXT_EXTS:
        return _looks_like_text(content)

    if ext == 'png':
        return _startswith(content, _PNG)
    if ext in ('jpg', 'jpeg'):
        return _startswith(content, _JPG)
    if ext == 'gif':
        return _startswith(content, _GIF)
    if ext == 'bmp':
        return _startswith(content, _BMP)
    if ext == 'webp':
        return _startswith(content, _RIFF) and _startswith(content, _WEBP, 8)
    if ext == 'pdf':
        return _startswith(content, _PDF)

    if ext in ('docx', 'xlsx', 'pptx'):
        return _startswith(content, _ZIP)
    if ext in ('doc', 'xls', 'ppt'):
        return _startswith(content, _OLE)
    if ext == 'zip':
        return _startswith(content, _ZIP)

    if ext in ('mp4', 'mov', 'm4v'):
        return _startswith(content, _FTYP, 4)
    if ext in ('mkv', 'webm'):
        return _startswith(content, _EBML)
    if ext == 'avi':
        return _startswith(content, _RIFF) and _startswith(content, _AVI, 8)

    if ext == 'mp3':
        return _startswith(content, _ID3) or any(
            _startswith(content, s) for s in _MP3_FRAME
        )
    if ext == 'wav':
        return _startswith(content, _RIFF) and _startswith(content, _WAVE, 8)
    if ext == 'flac':
        return _startswith(content, _FLAC)
    if ext == 'ogg':
        return _startswith(content, _OGGS)
    if ext == 'm4a':
        return _startswith(content, _FTYP, 4)

    return True
