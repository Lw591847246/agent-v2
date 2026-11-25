# app/skills/code/workspace/validation.py
from __future__ import annotations

import os
import re
from pathlib import PurePosixPath
from typing import Optional

from app.core.exceptions import BadRequestException

IGNORED_DIRS = {
    ".git", "__pycache__", "venv", ".venv", "node_modules", ".idea", ".vscode",
    "dist", "build", ".eggs", ".tox", ".mypy_cache", ".pytest_cache", "htmlcov",
    "logs", "data",
}
IGNORED_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".zip", ".tar",
    ".gz", ".7z", ".rar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".mp4", ".mp3", ".wav", ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls",
    ".xlsx", ".sqlite", ".db", ".lock",
}
SECRET_NAME_RE = re.compile(
    r"""
    (^\.env(\..+)?)          # .env / .env.local

    | (^secrets?\.(ya?ml|json|ini|env|properties)$)
    | (^credentials?\.(ya?ml|json|ini|properties)$)
    | (^id_rsa(_[a-z0-9]+)?$)
    | (\.(pem|p12|pfx|key)$)
    """,
    re.I | re.VERBOSE,
)
SECRET_CONTENT_RE = re.compile(
    r"(API[_-]?KEY|SECRET[_-]?KEY|ACCESS[_-]?TOKEN|PRIVATE[_-]?KEY)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}",
    re.I,
)


def normalize_relpath(raw: str) -> str:
    s = (raw or "").replace("\\", "/").strip().lstrip("/")
    if not s or "\x00" in s:
        raise BadRequestException("非法文件路径")
    p = PurePosixPath(s)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts):
        raise BadRequestException("非法文件路径")
    return str(p)


def is_ignored(relpath: str) -> tuple[bool, str]:
    p = PurePosixPath(relpath)
    if any(part in IGNORED_DIRS for part in p.parts[:-1]):
        return True, "ignored_dir"
    if p.suffix.lower() in IGNORED_SUFFIXES:
        return True, "ignored_suffix"
    if SECRET_NAME_RE.search(p.name):
        return True, "secret_name"
    return False, ""


def redact_secrets(text: str) -> str:
    return SECRET_CONTENT_RE.sub(lambda m: m.group(0).split("=")[0] + "=***", text)


def detect_language(relpath: str) -> str:
    ext = PurePosixPath(relpath).suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "tsx", ".jsx": "jsx", ".vue": "vue", ".java": "java",
        ".go": "go", ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp",
        ".hpp": "cpp", ".cs": "csharp", ".php": "php", ".rb": "ruby",
        ".sh": "shell", ".sql": "sql", ".yml": "yaml", ".yaml": "yaml",
        ".json": "json", ".md": "markdown", ".html": "html", ".css": "css",
    }.get(ext, "text")


def estimate_tokens(text: str, ratio: int = 3) -> int:
    ratio = max(1, int(ratio or 3))
    return max(1, len(text or "") // ratio)


def validate_upload(relpath: str, content: bytes, cfg) -> tuple[str, str]:
    """返回 (规范化路径, 语言)；不通过抛 BadRequestException。"""
    rel = normalize_relpath(relpath)
    ignored, reason = is_ignored(rel)
    if ignored:
        raise BadRequestException(f"文件被忽略：{rel} ({reason})")
    if len(content) > cfg.max_file_mb * 1024 * 1024:
        raise BadRequestException(f"单文件超过 {cfg.max_file_mb}MB：{rel}")
    return rel, detect_language(rel)


def safe_join(root: str, *parts: str) -> str:
    root_abs = os.path.realpath(os.path.abspath(root))
    target = os.path.realpath(os.path.abspath(os.path.join(root_abs, *parts)))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise BadRequestException("路径越界")
    return target