# app/context/reference_resolver.py
"""多轮引用解析：从 history 的 outputs 里挑出"上一轮/第N张/上上轮"的产出。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.logger import logger
from app.models.attachment import Attachment


# ============ 数据结构 ============

@dataclass
class Referent:
    kind: str
    turn_offset: int = -1
    index: Optional[int] = None
    pick_all: bool = False
    raw_text: str = ""
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "turn_offset": self.turn_offset,
            "index": self.index,
            "pick_all": self.pick_all,
        }


# ============ 正则 ============

_TURN_PATTERNS = [
    (re.compile(r"上上一?轮|上上轮|倒数第二(轮|次|张|个)"), -2),
    (re.compile(r"上上上一?轮|倒数第三(轮|次|张|个)"), -3),
    (re.compile(r"上一?轮|上一?次|上一?条|刚才|刚刚|前面|上面|之前"), -1),
]

_INDEX_PATTERN = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*(张|个|条|份)")

# ⭐ 修复：text 关键词补上 故事/小说/正文/段落/作文，
#    否则"把上一轮的故事做成..."会因 image/video/text 都不命中而落到默认 kind=image，
#    导致需要文本引用的场景被错判成找图片。
_KIND_PATTERNS = {
    "image": re.compile(r"图|图片|照片|画面|image|picture|photo"),
    "video": re.compile(r"视频|短片|video|clip"),
    "text": re.compile(r"文案|文字|内容|文本|text|故事|小说|正文|段落|作文|这篇|这段|那段"),
}

_FIRST_PATTERN = re.compile(r"最早|最开?始|第一次|首张|第1")
_ALL_PATTERN = re.compile(r"所有|全部|每一张|每张")

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_cn_number(s: str) -> Optional[int]:
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def resolve_referent(text: str, default_kind: str = "image") -> Optional[Referent]:
    if not text:
        return None

    kind = None
    for k, pat in _KIND_PATTERNS.items():
        if pat.search(text):
            kind = k
            break
    if kind is None:
        kind = default_kind

    turn_offset = None
    for pat, off in _TURN_PATTERNS:
        if pat.search(text):
            turn_offset = off
            break

    index = None
    m_idx = _INDEX_PATTERN.search(text)
    if m_idx:
        index = _parse_cn_number(m_idx.group(1))
        if turn_offset is None:
            turn_offset = -1

    pick_all = bool(_ALL_PATTERN.search(text))

    if _FIRST_PATTERN.search(text):
        return Referent(kind=kind, turn_offset=-9999, index=None, pick_all=pick_all, raw_text=text, confidence=0.9)

    if turn_offset is None and not pick_all:
        return None

    conf = 0.9 if turn_offset is not None else 0.6
    if index is not None:
        conf = min(conf + 0.05, 1.0)

    return Referent(
        kind=kind,
        turn_offset=turn_offset if turn_offset is not None else -1,
        index=index,
        pick_all=pick_all,
        raw_text=text,
        confidence=conf,
    )


# ============ 从 outputs 挑 ============

def pick_outputs_strict(history: List[Dict[str, Any]], referent: Referent) -> Dict[str, Any]:
    """按 user 轮次分组，返回 {"outputs": [...], "reason": "ok"|"empty"|"out_of_range"}"""
    turns: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for msg in history:
        role = msg.get("role")
        mid = msg.get("id")
        if role == "user":
            if current is not None:
                turns.append(current)
            current = {"user_id": mid, "outputs": []}
        elif role == "assistant":
            if current is None:
                current = {"user_id": None, "outputs": []}
            for o in (msg.get("outputs") or []):
                if isinstance(o, dict) and o.get("kind") == referent.kind:
                    current["outputs"].append(o)
    if current is not None:
        turns.append(current)

    if not turns:
        return {"outputs": [], "reason": "empty"}

    if referent.turn_offset == -9999:
        for t in turns:
            if t["outputs"]:
                return {"outputs": [t["outputs"][0]], "reason": "ok"}
        return {"outputs": [], "reason": "empty"}

    idx = len(turns) + referent.turn_offset
    if idx < 0:
        return {"outputs": [], "reason": "out_of_range"}
    idx = min(idx, len(turns) - 1)
    turn = turns[idx]

    if not turn["outputs"]:
        return {"outputs": [], "reason": "empty"}

    if referent.index is not None:
        i = referent.index - 1
        if 0 <= i < len(turn["outputs"]):
            return {"outputs": [turn["outputs"][i]], "reason": "ok"}
        return {"outputs": [], "reason": "empty"}

    return {"outputs": [turn["outputs"][-1]], "reason": "ok"}


# ============ 对外入口 ============

class ReferenceResolver:
    def resolve(
        self, text: str, history: List[Dict[str, Any]], default_kind: str = "image",
    ) -> List[Attachment]:
        referent = resolve_referent(text, default_kind=default_kind)
        if not referent:
            return []

        logger.info(f"[RefResolver] 解析={referent.to_dict()}")
        result = pick_outputs_strict(history, referent)
        if result["reason"] != "ok":
            logger.info(f"[RefResolver] 未命中 reason={result['reason']}")
            return []

        attachments: List[Attachment] = []
        for out in result["outputs"]:
            a = self._output_to_attachment(out)
            if a:
                attachments.append(a)
        return attachments

    @staticmethod
    def _output_to_attachment(output: Dict) -> Optional[Attachment]:
        kind = output.get("kind") or "image"
        filename = output.get("filename") or ""
        path = output.get("path") or ""
        if not filename and not path:
            return None

        if not path and filename:
            return Attachment(
                file_id=f"ref_{output.get('task_id') or filename}",
                type=kind,
                name=filename,
                path="",
                thumb_url=output.get("url", ""),
            )

        return Attachment(
            file_id=f"ref_{output.get('task_id') or filename}",
            type=kind,
            name=filename,
            path=path,
            thumb_url=output.get("thumb") or "",
        )


reference_resolver = ReferenceResolver()
