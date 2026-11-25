from __future__ import annotations

import time
from typing import Dict, List, Optional

from app.core.exceptions import BadRequestException
from app.core.logger import logger
from app.skills.code.workspace.config import code_cfg
from app.skills.code.workspace.contracts import CodeMap, FileMeta
from app.skills.code.workspace.redis_store import code_redis_store
from app.skills.code.workspace.store import workspace_store
from app.skills.code.workspace.validation import validate_upload
from app.utils.masking import is_text_file, mask_text


class CodeWorkspaceService:
    def upload_batch(
        self,
        uid: int,
        sid: str,
        batch_id: str,
        files: List[tuple],  # [(relpath, bytes)]
        scope: str = "all",
        module_id: str = "",
    ) -> Dict:
        if len(files) > code_cfg.upload_batch_files:
            raise BadRequestException(f"单批最多 {code_cfg.upload_batch_files} 个文件")

        meta = code_redis_store.get_meta(uid, sid)
        if not meta:
            meta = {"created_at": int(time.time()), "total_bytes": 0, "file_count": 0}
        if int(meta.get("file_count", 0)) + len(files) > code_cfg.max_files:
            raise BadRequestException(f"文件总数超过 {code_cfg.max_files}")

        saved: List[FileMeta] = []
        masked_files: List[str] = []
        skipped: List[dict] = []

        for relpath, content in files:
            # 1) 单文件校验:命中敏感名/非法路径/超限 -> 跳过该文件,绝不终止整批
            try:
                rel, _ = validate_upload(relpath, content, code_cfg)
            except BadRequestException as e:
                skipped.append({"file": relpath, "reason": str(e)})
                continue

            # 2) 入库前兜底脱敏:mask_text 内部已吞异常,这里再加一层 decode 兜底
            content, did_mask = self._mask_content(rel, content)
            if did_mask:
                masked_files.append(rel)

            # 3) 总大小用脱敏后的 content 长度判断(脱敏只会变短,不会撑爆上限)
            if meta.get("total_bytes", 0) + len(content) > code_cfg.max_total_mb * 1024 * 1024:
                skipped.append({"file": rel, "reason": f"工作区总大小超过 {code_cfg.max_total_mb}MB"})
                continue

            fm = workspace_store.save_file(uid, sid, rel, content, scope=scope, module_id=module_id)
            saved.append(fm)
            meta["total_bytes"] = int(meta.get("total_bytes", 0)) + len(content)
            meta["file_count"] = int(meta.get("file_count", 0)) + 1

        meta["updated_at"] = int(time.time())
        meta["last_batch_id"] = batch_id
        code_redis_store.set_meta(uid, sid, meta)
        logger.info(
            f"[CodeWorkspace] uid={uid} sid={sid} 保存 {len(saved)} 个文件 "
            f"(脱敏 {len(masked_files)} 个, 跳过 {len(skipped)} 个)"
        )
        return {
            "saved": len(saved),
            "saved_files": [fm.relpath for fm in saved] if saved and hasattr(saved[0], "relpath") else [str(f) for f in saved],
            "masked": masked_files,
            "skipped": skipped,
            "total_bytes": meta["total_bytes"],
            "file_count": meta["file_count"],
        }

    @staticmethod
    def _mask_content(relpath: str, raw: bytes) -> tuple[bytes, bool]:
        """文本文件入库前脱敏。非文本/解码失败 -> 原样返回,绝不抛。
        Returns: (脱敏后bytes, 是否命中脱敏)
        """
        try:
            if not is_text_file(relpath):
                return raw, False
            text = raw.decode("utf-8")
            masked_text, changed = mask_text(text)
            if not changed:
                return raw, False
            return masked_text.encode("utf-8"), True
        except Exception:
            # 脱敏本身任何异常都吞掉,原样入库,不中断上传
            return raw, False

    def status(self, uid: int, sid: str) -> Dict:
        meta = code_redis_store.get_meta(uid, sid)
        latest = code_redis_store.get_latest_job_id(uid, sid)
        progress = code_redis_store.get_progress(uid, sid, latest) if latest else None
        return {
            "meta": meta,
            "latest_job_id": latest,
            "progress": progress.model_dump() if progress else None,
            "has_map": code_redis_store.get_map(uid, sid) is not None,
        }

    def get_map(self, uid: int, sid: str, scope: str = "all", module_id: str = "") -> Optional[CodeMap]:
        return code_redis_store.get_map(uid, sid, scope=scope, module_id=module_id)

    def clear(self, uid: int, sid: str) -> Dict:
        n = code_redis_store.clear(uid, sid)
        workspace_store.clear(uid, sid)
        logger.info(f"[CodeWorkspace] 清理 uid={uid} sid={sid} redis_keys={n}")
        return {"redis_keys_deleted": n}


code_workspace_service = CodeWorkspaceService()