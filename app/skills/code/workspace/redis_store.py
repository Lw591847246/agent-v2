# app/skills/code/workspace/redis_store.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core import redis_kv
from app.skills.code.workspace.config import code_cfg
from app.skills.code.workspace.contracts import (
    ChunkMeta, CodeMap, FileMeta, MapChunkSummary, ScanProgress,
)


def _k(uid: int, sid: str, suffix: str) -> str:
    return f"ws:{uid}:{sid}:{suffix}"


class CodeRedisStore:
    """code skill 私有的 Redis 元数据/摘要/进度/上下文存储。"""

    def __init__(self):
        self.ttl = code_cfg.ttl_seconds

    # ---------- 工作区元数据 ----------
    def set_meta(self, uid: int, sid: str, meta: Dict[str, Any]) -> None:
        redis_kv.set_json(_k(uid, sid, "meta"), meta, ttl=self.ttl)

    def get_meta(self, uid: int, sid: str) -> Dict[str, Any]:
        return redis_kv.get_json(_k(uid, sid, "meta"), default={}) or {}

    def set_manifest(self, uid: int, sid: str, files: List[FileMeta]) -> None:
        redis_kv.set_json(
            _k(uid, sid, "manifest"),
            [f.model_dump() for f in files],
            ttl=self.ttl,
        )

    def get_manifest(self, uid: int, sid: str, scope: str = "all", module_id: str = "") -> List[FileMeta]:
        rows = redis_kv.get_json(_k(uid, sid, "manifest"), default=[]) or []
        out = []
        for r in rows:
            try:
                f = FileMeta(**r)
            except Exception:
                continue
            if scope == "module" and module_id:
                if f.scope != "module" or f.module_id != module_id:
                    continue
            elif f.scope == "module":
                # all 视图默认包含 module 文件，便于上下文合并
                pass
            out.append(f)
        return out

    # ---------- 切片与摘要 ----------
    def set_chunks(self, uid: int, sid: str, chunks: List[ChunkMeta]) -> None:
        key = _k(uid, sid, "chunks")
        redis_kv.delete(key)
        for c in chunks:
            redis_kv.hset_json(key, c.chunk_id, c.model_dump(), ttl=self.ttl)

    def get_chunks(self, uid: int, sid: str) -> List[ChunkMeta]:
        rows = redis_kv.hgetall_json(_k(uid, sid, "chunks"))
        out = []
        for r in rows.values():
            try:
                out.append(ChunkMeta(**r))
            except Exception:
                continue
        return out

    def set_summary(self, uid: int, sid: str, s: MapChunkSummary) -> None:
        redis_kv.hset_json(_k(uid, sid, "summaries"), s.chunk_id, s.model_dump(), ttl=self.ttl)

    def get_summaries(self, uid: int, sid: str) -> List[MapChunkSummary]:
        rows = redis_kv.hgetall_json(_k(uid, sid, "summaries"))
        out = []
        for r in rows.values():
            try:
                out.append(MapChunkSummary(**r))
            except Exception:
                continue
        return out

    # ---------- 地图 ----------
    def set_map(self, uid: int, sid: str, cmap: CodeMap, scope: str = "all", module_id: str = "") -> None:
        suffix = f"map:{scope}:{module_id}" if scope == "module" else "map:all"
        redis_kv.set_json(_k(uid, sid, suffix), cmap.model_dump(), ttl=self.ttl)

    def get_map(self, uid: int, sid: str, scope: str = "all", module_id: str = "") -> Optional[CodeMap]:
        suffix = f"map:{scope}:{module_id}" if scope == "module" else "map:all"
        data = redis_kv.get_json(_k(uid, sid, suffix), default=None)
        if not data:
            return None
        try:
            return CodeMap(**data)
        except Exception:
            return None

    # ---------- 进度 ----------
    def set_progress(self, uid: int, sid: str, p: ScanProgress) -> None:
        redis_kv.set_json(_k(uid, sid, f"scan:{p.job_id}:progress"), p.model_dump(), ttl=self.ttl)
        redis_kv.set_json(_k(uid, sid, "scan:latest"), {"job_id": p.job_id}, ttl=self.ttl)

    def get_progress(self, uid: int, sid: str, job_id: str) -> Optional[ScanProgress]:
        data = redis_kv.get_json(_k(uid, sid, f"scan:{job_id}:progress"), default=None)
        if not data:
            return None
        try:
            return ScanProgress(**data)
        except Exception:
            return None

    def get_latest_job_id(self, uid: int, sid: str) -> str:
        data = redis_kv.get_json(_k(uid, sid, "scan:latest"), default={}) or {}
        return str(data.get("job_id") or "")

    # ---------- 清理 ----------
    def clear(self, uid: int, sid: str) -> int:
        return redis_kv.delete_by_prefix(f"ws:{uid}:{sid}:")

    # ---------- 上下文（code 专用，未来可上移通用 ContextStore） ----------
    def append_turn(self, uid: int, sid: str, turn: Dict[str, Any], max_len: int = 50) -> None:
        redis_kv.lpush_trim(f"ctx:{uid}:{sid}:turns", turn, max_len=max_len, ttl=self.ttl)

    def get_turns(self, uid: int, sid: str, limit: int = 12) -> List[Dict[str, Any]]:
        return redis_kv.lrange_json(f"ctx:{uid}:{sid}:turns", 0, limit - 1)

    def get_summary(self, uid: int, sid: str) -> str:
        return str(redis_kv.get_json(f"ctx:{uid}:{sid}:summary", default="") or "")

    def set_summary_text(self, uid: int, sid: str, text: str) -> None:
        redis_kv.set_json(f"ctx:{uid}:{sid}:summary", text, ttl=self.ttl)

    def add_pin(self, uid: int, sid: str, text: str) -> None:
        redis_kv.sadd(f"ctx:{uid}:{sid}:pins", text)
        redis_kv._client().expire(f"ctx:{uid}:{sid}:pins", self.ttl)

    def get_pins(self, uid: int, sid: str) -> List[str]:
        return sorted(redis_kv.smembers(f"ctx:{uid}:{sid}:pins"))


code_redis_store = CodeRedisStore()