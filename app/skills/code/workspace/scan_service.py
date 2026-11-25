# app/skills/code/workspace/scan_service.py
from __future__ import annotations

import time
import uuid
from typing import Optional

from app.core.logger import logger
from app.skills.code.workspace.chunker import chunk_file
from app.skills.code.workspace.contracts import ScanProgress
from app.skills.code.workspace.manifest import build_manifest
from app.skills.code.workspace.mapreduce.map_runner import run_map
from app.skills.code.workspace.mapreduce.reduce_runner import run_reduce
from app.skills.code.workspace.queue import job_queue
from app.skills.code.workspace.redis_store import code_redis_store
from app.skills.code.workspace.store import workspace_store


class CodeScanService:
    def start_scan(self, uid: int, sid: str, scope: str = "all", module_id: str = "") -> str:
        latest = code_redis_store.get_latest_job_id(uid, sid)
        if latest:
            p = code_redis_store.get_progress(uid, sid, latest)
            if p and p.status in ("queued", "scanning", "mapping", "reducing"):
                logger.info(f"[CodeScan] 复用进行中的 job={latest}")
                return latest

        job_id = uuid.uuid4().hex
        progress = ScanProgress(
            job_id=job_id, session_id=sid, scope=scope, module_id=module_id,
            status="queued", phase="queued", started_at=int(time.time()),
            updated_at=int(time.time()),
        )
        code_redis_store.set_progress(uid, sid, progress)
        job_queue.submit(self._run, uid, sid, scope, module_id, job_id)
        return job_id

    def _update(self, uid: int, sid: str, p: ScanProgress) -> None:
        p.updated_at = int(time.time())
        code_redis_store.set_progress(uid, sid, p)

    def _run(self, job_id: str, uid: int, sid: str, scope: str, module_id: str) -> None:
        p = code_redis_store.get_progress(uid, sid, job_id)
        if not p:
            return
        try:
            p.status = "scanning"
            p.phase = "manifest"
            self._update(uid, sid, p)

            files = build_manifest(uid, sid, scope=scope, module_id=module_id)
            chunks = []
            for f in files:
                try:
                    content = workspace_store.read_file(uid, sid, f.storage_key)
                except Exception as e:
                    logger.warning(f"[CodeScan] 读取失败 {f.relpath}: {e}")
                    continue
                chunks.extend(chunk_file(uid, sid, f, content))

            code_redis_store.set_chunks(uid, sid, chunks)
            p.total = len(chunks)
            p.processed = 0
            p.status = "mapping"
            self._update(uid, sid, p)

            summaries = run_map(uid, sid, chunks, p, lambda x: self._update(uid, sid, x))
            p.status = "reducing"
            self._update(uid, sid, p)

            cmap = run_reduce(uid, sid, summaries, p, scope=scope, module_id=module_id)
            code_redis_store.set_map(uid, sid, cmap, scope=scope, module_id=module_id)

            p.status = "done"
            p.phase = "done"
            p.current = ""
            p.finished_at = int(time.time())
            self._update(uid, sid, p)
            logger.info(f"[CodeScan] 完成 job={job_id} files={len(files)} chunks={len(chunks)}")
        except Exception as e:
            logger.error(f"[CodeScan] job={job_id} 失败: {e}", exc_info=True)
            p.status = "failed"
            p.error = str(e)
            p.finished_at = int(time.time())
            self._update(uid, sid, p)

    def get_progress(self, uid: int, sid: str, job_id: Optional[str] = None) -> Optional[ScanProgress]:
        jid = job_id or code_redis_store.get_latest_job_id(uid, sid)
        if not jid:
            return None
        return code_redis_store.get_progress(uid, sid, jid)

    def cancel(self, uid: int, sid: str, job_id: str) -> None:
        p = code_redis_store.get_progress(uid, sid, job_id)
        if p and p.status not in ("done", "failed", "cancelled"):
            p.status = "cancelled"
            p.phase = "cancelled"
            p.finished_at = int(time.time())
            self._update(uid, sid, p)


code_scan_service = CodeScanService()