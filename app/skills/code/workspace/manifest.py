# app/skills/code/workspace/manifest.py
from __future__ import annotations

from typing import List

from app.skills.code.workspace.contracts import FileMeta
from app.skills.code.workspace.redis_store import code_redis_store
from app.skills.code.workspace.store import workspace_store
from app.skills.code.workspace.validation import detect_language, is_ignored


def build_manifest(uid: int, sid: str, scope: str = "all", module_id: str = "") -> List[FileMeta]:
    files = workspace_store.list_files(uid, sid, scope=scope, module_id=module_id)
    out: List[FileMeta] = []
    for f in files:
        ignored, reason = is_ignored(f.relpath)
        f.ignored = ignored
        f.reason = reason
        f.language = detect_language(f.relpath)
        if not ignored:
            out.append(f)
    code_redis_store.set_manifest(uid, sid, out)
    return out