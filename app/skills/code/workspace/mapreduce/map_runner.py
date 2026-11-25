# app/skills/code/workspace/mapreduce/map_runner.py
from __future__ import annotations

import time
from typing import Callable, List

from app.core.logger import logger
from app.skills.code.workspace.config import code_cfg
from app.skills.code.workspace.contracts import ChunkMeta, MapChunkSummary, ScanProgress
from app.skills.code.workspace.executor import model_executor
from app.skills.code.workspace.mapreduce.prompts import MAP_SYSTEM
from app.skills.code.workspace.mapreduce.schemas import parse_model
from app.skills.code.workspace.redis_store import code_redis_store


def run_map(
    uid: int,
    sid: str,
    chunks: List[ChunkMeta],
    progress: ScanProgress,
    on_progress: Callable[[ScanProgress], None],
) -> List[MapChunkSummary]:
    out: List[MapChunkSummary] = []
    total = len(chunks)
    for i, c in enumerate(chunks, 1):
        if progress.status == "cancelled":
            break
        user = (
            f"chunk_id={c.chunk_id}\nfile={c.relpath}\n"
            f"language={c.language}\nlines={c.start_line}-{c.end_line}\n"
            f"```{c.language}\n{c.content}\n```"
        )
        last_err = ""
        for attempt in range(code_cfg.retry + 1):
            try:
                text = model_executor.complete(
                    code_cfg.map_model,
                    [{"role": "system", "content": MAP_SYSTEM},
                     {"role": "user", "content": user}],
                    max_tokens=code_cfg.map_max_tokens,
                )
                s = parse_model(MapChunkSummary, text)
                s.chunk_id = c.chunk_id
                s.file = c.relpath
                code_redis_store.set_summary(uid, sid, s)
                out.append(s)
                break
            except Exception as e:
                last_err = str(e)
                logger.warning(f"[CodeMap] chunk={c.chunk_id} 第 {attempt+1} 次失败: {e}")
        else:
            logger.error(f"[CodeMap] chunk={c.chunk_id} 放弃: {last_err}")

        progress.phase = "mapping"
        progress.processed = i
        progress.total = total
        progress.current = c.relpath
        progress.updated_at = int(time.time())
        on_progress(progress)
        # 分片间主动让锁：模型调用已释放，这里再给前台 chat 一点插队窗口
        time.sleep(code_cfg.yield_sleep_ms / 1000.0)
    return out