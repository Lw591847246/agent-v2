# app/utils/safe_path.py
"""路径安全工具：所有"用户可控字符串 -> 文件路径"都必须经过这里。

设计要点
1. 段级白名单：session_id / task_id 只允许 [A-Za-z0-9_-]（uuid hex、雪花 id 都满足）。
2. 文件名：禁止 / \\ .. NUL 与首字符点，限长。
3. safe_join / ensure_under 均先 realpath 归一化再断言仍在根目录内，
   同时覆盖 Windows 反斜杠、符号链接与 .. 的情况。

约定：根目录用 os.path.abspath("data/xxx")，与各 service 内部的
      "./data/xxx" 保持一致（app/main.py 启动时 chdir 到项目根）。
"""
from __future__ import annotations

import os
import re

from fastapi import HTTPException

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _invalid(label: str) -> HTTPException:
    return HTTPException(status_code=400, detail=f"invalid {label}")


def safe_segment(value: str, label: str = "segment", default: str = "") -> str:
    """校验单个路径段（目录名）。"""
    v = (value or "").strip()
    if not v:
        if default:
            return default
        raise _invalid(label)
    if not _SEGMENT_RE.match(v):
        raise _invalid(label)
    return v


def safe_filename(value: str, label: str = "filename", max_len: int = 128) -> str:
    """校验文件名（不含任何分隔符）。"""
    v = (value or "").strip()
    if not v or len(v) > max_len:
        raise _invalid(label)
    if "/" in v or "\\" in v or ".." in v or "\x00" in v or v.startswith("."):
        raise _invalid(label)
    return v


def safe_join(root: str, *parts: str) -> str:
    """逐段校验后拼接到 root 下；最后一段按文件名规则校验。"""
    root_abs = os.path.realpath(os.path.abspath(root))
    cleaned = []
    last = len(parts) - 1
    for i, p in enumerate(parts):
        if i == last:
            cleaned.append(safe_filename(p, "filename"))
        else:
            cleaned.append(safe_segment(p, "path segment"))
    target = os.path.realpath(os.path.join(root_abs, *cleaned))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise HTTPException(status_code=403, detail="path not allowed")
    return target


def ensure_under(root: str, path: str) -> str:
    """路径已在别处拼好时，用这个做最终断言。"""
    root_abs = os.path.realpath(os.path.abspath(root))
    target = os.path.realpath(os.path.abspath(path))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise HTTPException(status_code=403, detail="path not allowed")
    return target


def data_root(*parts: str) -> str:
    """data 下的绝对根，与 service 内部 "./data" 指向同一位置。"""
    return os.path.abspath(os.path.join("data", *parts))


def rel_posix(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/")