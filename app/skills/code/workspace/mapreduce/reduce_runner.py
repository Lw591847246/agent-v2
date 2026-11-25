# app/skills/code/workspace/mapreduce/reduce_runner.py
from __future__ import annotations

import json
import time
from typing import Dict, List

from app.core.logger import logger
from app.skills.code.workspace.config import code_cfg
from app.skills.code.workspace.contracts import CodeMap, MapChunkSummary, ScanProgress
from app.skills.code.workspace.executor import model_executor
from app.skills.code.workspace.mapreduce.prompts import REDUCE_SYSTEM
from app.skills.code.workspace.mapreduce.schemas import extract_json


def _group_by_file(summaries: List[MapChunkSummary]) -> Dict[str, List[MapChunkSummary]]:
    out: Dict[str, List[MapChunkSummary]] = {}
    for s in summaries:
        out.setdefault(s.file, []).append(s)
    return out


def _reduce_once(payload: str, max_tokens: int) -> dict:
    text = model_executor.complete(
        code_cfg.reduce_model,
        [{"role": "system", "content": REDUCE_SYSTEM},
         {"role": "user", "content": payload}],
        max_tokens=max_tokens,
    )
    return extract_json(text)


def run_reduce(
    uid: int,
    sid: str,
    summaries: List[MapChunkSummary],
    progress: ScanProgress,
    scope: str = "all",
    module_id: str = "",
) -> CodeMap:
    by_file = _group_by_file(summaries)
    # 第一层：按文件归并
    file_maps = []
    for file, items in by_file.items():
        payload = json.dumps([i.model_dump() for i in items], ensure_ascii=False)
        try:
            fm = _reduce_once(payload, code_cfg.reduce_max_tokens)
            fm["file"] = file
            file_maps.append(fm)
        except Exception as e:
            logger.warning(f"[CodeMap] 文件归并失败 {file}: {e}")
            file_maps.append({"file": file, "overview": "UNKNOWN", "risks": [], "citations": [file]})

    progress.phase = "reducing"
    progress.updated_at = int(time.time())
    progress.current = "file-level reduce"

    # 第二层：全局归并；输入过大时按目录分批
    final_payload = json.dumps(file_maps, ensure_ascii=False)
    if len(final_payload) > code_cfg.reduce_max_tokens * code_cfg.token_est_ratio * 2:
        # 简化的分批归并：按文件路径前缀分组
        groups: Dict[str, list] = {}
        for fm in file_maps:
            key = fm.get("file", "").split("/")[0]
            groups.setdefault(key, []).append(fm)
        partials = []
        for key, items in groups.items():
            try:
                partials.append(_reduce_once(
                    json.dumps(items, ensure_ascii=False), code_cfg.reduce_max_tokens,
                ))
            except Exception as e:
                logger.warning(f"[CodeMap] 分组归并失败 {key}: {e}")
        final_payload = json.dumps(partials, ensure_ascii=False)

    try:
        result = _reduce_once(final_payload, code_cfg.reduce_max_tokens)
    except Exception as e:
        logger.error(f"[CodeMap] 最终归并失败: {e}")
        result = {"overview": "UNKNOWN", "modules": [], "key_files": [], "dependencies": [],
                  "risks": [str(e)], "unknowns": ["reduce_failed"], "citations": []}

    cmap = CodeMap(
        session_id=sid,
        scope=scope,
        module_id=module_id,
        generated_at=int(time.time()),
        file_count=len(by_file),
        chunk_count=len(summaries),
        overview=str(result.get("overview") or ""),
        modules=result.get("modules") or [],
        key_files=result.get("key_files") or [],
        dependencies=result.get("dependencies") or [],
        risks=result.get("risks") or [],
        unknowns=result.get("unknowns") or [],
        citations=result.get("citations") or [],
    )
    return cmap