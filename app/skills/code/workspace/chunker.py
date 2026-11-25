# app/skills/code/workspace/chunker.py
from __future__ import annotations

from typing import List

from app.skills.code.workspace.config import code_cfg
from app.skills.code.workspace.contracts import ChunkMeta, FileMeta
from app.skills.code.workspace.validation import estimate_tokens, redact_secrets


def chunk_file(uid: int, sid: str, f: FileMeta, content: bytes) -> List[ChunkMeta]:
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:
        return []
    text = redact_secrets(text)
    lines = text.splitlines()
    if not lines:
        return []

    max_tokens = code_cfg.chunk_tokens
    overlap = code_cfg.chunk_overlap_tokens
    ratio = code_cfg.token_est_ratio

    chunks: List[ChunkMeta] = []
    start = 0
    idx = 0
    while start < len(lines):
        end = start
        buf = []
        while end < len(lines):
            buf.append(lines[end])
            if estimate_tokens("\n".join(buf), ratio) >= max_tokens:
                break
            end += 1
        end = min(end, len(lines) - 1)
        body = "\n".join(lines[start:end + 1])
        if body.strip():
            chunks.append(ChunkMeta(
                chunk_id=f"{uid}:{sid}:{f.relpath}:{idx}",
                relpath=f.relpath, scope=f.scope, module_id=f.module_id,
                language=f.language, start_line=start + 1, end_line=end + 1,
                content=body, token_est=estimate_tokens(body, ratio),
            ))
            idx += 1
        if end >= len(lines) - 1:
            break
        # 回退 overlap 行，按 token 估算近似
        back_lines = max(1, overlap // max(1, ratio * 20))
        start = max(start + 1, end - back_lines + 1)
    return chunks