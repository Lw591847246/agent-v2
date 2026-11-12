"""file_id ↔ 本地文件 注册表（Redis）"""
from __future__ import annotations

import json
import time
from typing import Optional

from app.core.logger import logger
from app.core.redis_client import redis_client          # ⭐ 模块级导入


PREFIX = "upload:"
DEFAULT_TTL = 60 * 60 * 24          # 24h


class FileRegistry:

    def _redis(self):
        return redis_client                              # ⭐ 直接用

    def _key(self, file_id: str) -> str:
        return f"{PREFIX}{file_id}"

    def save(self, file_id: str, meta: dict, ttl: int = DEFAULT_TTL) -> None:
        r = self._redis()
        if not r:
            return
        try:
            r.setex(self._key(file_id), ttl, json.dumps(meta, ensure_ascii=False))
        except Exception as e:
            logger.error(f"[FileRegistry] save failed: {e}")
            raise  # ⭐ 写失败就抛

    def get(self, file_id: str) -> Optional[dict]:
        r = self._redis()
        if not r:
            return None
        try:
            raw = r.get(self._key(file_id))
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except Exception as e:
            logger.error(f"[FileRegistry] get failed: {e}")
            return None

    def delete(self, file_id: str) -> None:
        r = self._redis()
        if not r:
            return
        try:
            r.delete(self._key(file_id))
        except Exception:
            pass

    def get_many(self, file_ids: list[str]) -> list[dict]:
        out = []
        for fid in file_ids or []:
            meta = self.get(fid)
            if meta:
                out.append(meta)
        return out


file_registry = FileRegistry()