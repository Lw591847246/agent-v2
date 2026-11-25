# app/skills/code/workspace/context/builder.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.skills.code.workspace.config import code_cfg
from app.skills.code.workspace.contracts import ContextPack
from app.skills.code.workspace.context.store import context_store
from app.skills.code.workspace.redis_store import code_redis_store
from app.skills.code.workspace.validation import estimate_tokens


def _pick_relevant_chunks(uid: int, sid: str, keyword: str, limit: int = 8) -> List[Dict[str, Any]]:
    chunks = code_redis_store.get_chunks(uid, sid)
    if not keyword:
        return []
    kw = keyword.lower()
    scored = []
    for c in chunks:
        score = 0
        if kw in c.relpath.lower():
            score += 5
        if kw in c.content.lower():
            score += 1
        if score:
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"chunk_id": c.chunk_id, "file": c.relpath, "lines": f"{c.start_line}-{c.end_line}",
         "content": c.content[:2000]}
        for _, c in scored[:limit]
    ]


def build_code_context(
    uid: int,
    sid: str,
    user_text: str = "",
    scope: str = "all",
    module_id: str = "",
    max_tokens: Optional[int] = None,
) -> ContextPack:
    budget = int(max_tokens or code_cfg.context_max_tokens)
    recent = context_store.get_turns(uid, sid, code_cfg.context_recent_turns)
    pins = context_store.get_pins(uid, sid)

    cmap = code_redis_store.get_map(uid, sid, scope="all")
    mmap = code_redis_store.get_map(uid, sid, scope="module", module_id=module_id) if module_id else None
    chunks = _pick_relevant_chunks(uid, sid, user_text)

    pack = ContextPack(
        session_id=sid,
        code_map=cmap.overview if cmap else "",
        module_map=mmap.overview if mmap else "",
        relevant_chunks=chunks,
        recent_turns=recent,
        summary=context_store.get_summary(uid, sid),
        pins=pins,
    )
    # 简单预算裁剪：超预算先裁相关切片，再裁最近轮次
    while estimate_tokens(pack.model_dump_json(), 3) > budget:
        if pack.relevant_chunks:
            pack.relevant_chunks.pop()
        elif pack.recent_turns:
            pack.recent_turns.pop(0)
        else:
            break
    pack.token_est = estimate_tokens(pack.model_dump_json(), 3)
    return pack


def render_context_for_prompt(pack: ContextPack) -> str:
    """把上下文包渲染成给 code skill 的纯文本。"""
    parts = []
    if pack.code_map:
        parts.append("【项目地图】\n" + pack.code_map)
    if pack.module_map:
        parts.append("【模块地图】\n" + pack.module_map)
    if pack.pins:
        parts.append("【固定约束】\n" + "\n".join(f"- {p}" for p in pack.pins))
    if pack.summary:
        parts.append("【历史摘要】\n" + pack.summary)
    if pack.recent_turns:
        lines = []
        for t in pack.recent_turns[-8:]:
            lines.append(f"{t.get('role','')}: {str(t.get('content',''))[:500]}")
        parts.append("【最近对话】\n" + "\n".join(lines))
    if pack.relevant_chunks:
        lines = []
        for c in pack.relevant_chunks:
            lines.append(f"--- {c['file']} ({c['lines']}) ---\n{c['content']}")
        parts.append("【相关代码切片】\n" + "\n".join(lines))
    return "\n\n".join(parts)