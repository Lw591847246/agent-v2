# app/contracts/result.py
"""统一返回契约：Result + Part 构造器 + build_output。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, List, Optional


# ============================================================
# Part 构造器
# ============================================================

def text_part(content: str) -> dict:
    return {"type": "text", "content": content}


def image_part(url: str, thumb_url: Optional[str] = None) -> dict:
    p = {"type": "image", "url": url}
    if thumb_url:
        p["thumb_url"] = thumb_url
    return p


def video_part(url: str, task_id: str, cover_url: Optional[str] = None) -> dict:
    p = {"type": "video", "url": url, "task_id": task_id}
    if cover_url:
        p["cover_url"] = cover_url
    return p


def audio_part(url: str) -> dict:
    return {"type": "audio", "url": url}


def card_part(card_type: str, data: dict, status: str = "done") -> dict:
    return {
        "type": "card",
        "card_type": card_type,
        "status": status,
        "data": data,
    }


def file_part(card_type: str, data: dict) -> dict:
    return {
        "type": "card",
        "card_type": card_type,
        "status": "done",
        "data": data,
    }


def error_part(message: str) -> dict:
    return {"type": "text", "content": f"❌ {message}"}


def build_output(
    kind: str,
    filename: str,
    path: str,
    url: str,
    thumb: Optional[str] = None,
    task_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """构造可引用产出。kind: image/video/audio/file"""
    out = {
        "kind": kind,
        "filename": filename,
        "path": path,
        "url": url,
        "thumb": thumb or "",
        "task_id": task_id or "",
    }
    if extra:
        out.update(extra)
    return out


# ============================================================
# Result
# ============================================================

@dataclass
class Result:
    """Skill 执行结果。"""
    parts: List[dict] = field(default_factory=list)
    outputs: List[dict] = field(default_factory=list)
    task_id: Optional[str] = None
    raw: Optional[dict] = None

    def as_text(self) -> str:
        for p in self.parts:
            if p.get("type") == "text":
                return p.get("content", "")
        return ""

    def is_empty(self) -> bool:
        return not self.parts

    @classmethod
    def text(cls, content: str) -> "Result":
        return cls(parts=[text_part(content)])

    @classmethod
    def error(cls, message: str) -> "Result":
        return cls(parts=[error_part(message)])


@dataclass
class StreamResult:
    """流式结果：Executor 检测到此类时走 SSE。"""
    iterator: Iterator[str]

    def __iter__(self):
        return self.iterator