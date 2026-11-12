# app/context/flow_state_store.py
"""Flow 多轮状态存储（Redis）"""
from __future__ import annotations

import time
from typing import Optional

from app.core.logger import logger
from app.domain.models.flow_state import FlowState
from app.core.redis_client import redis_client


class FlowStateStore:
    PREFIX = "flow:"
    ACTIVE_PREFIX = "active_flow:"
    DEFAULT_TTL = 60 * 60

    def _key(self, session_id: str, card_id: str) -> str:
        return f"{self.PREFIX}{session_id}:{card_id}"

    def _active_key(self, intent: str, session_id: str) -> str:
        return f"{self.ACTIVE_PREFIX}{intent}:{session_id}"

    def _redis(self):
        return redis_client

    def save(self, state: FlowState, ttl: int = DEFAULT_TTL) -> None:
        state.updated_at = time.time()
        r = self._redis()
        if not r:
            return
        try:
            r.setex(self._key(state.session_id, state.message_id), ttl, state.model_dump_json())
        except Exception as e:
            logger.error(f"[FlowStateStore] save failed: {e}")

    def load(self, session_id: str, card_id: str) -> Optional[FlowState]:
        r = self._redis()
        if not r:
            return None
        try:
            raw = r.get(self._key(session_id, card_id))
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return FlowState.model_validate_json(raw)
        except Exception as e:
            logger.error(f"[FlowStateStore] load failed: {e}")
            return None

    def load_by_card(self, session_id: str, card_id: str) -> Optional[FlowState]:
        return self.load(session_id, card_id)

    def delete(self, session_id: str, card_id: str) -> None:
        r = self._redis()
        if not r:
            return
        try:
            r.delete(self._key(session_id, card_id))
        except Exception:
            pass

    def set_active(self, intent: str, session_id: str, card_id: str, ttl: int = DEFAULT_TTL) -> None:
        r = self._redis()
        if not r:
            return
        try:
            r.setex(self._active_key(intent, session_id), ttl, card_id)
        except Exception as e:
            logger.error(f"[FlowStateStore] set_active failed: {e}")

    def get_active_card_id(self, intent: str, session_id: str) -> Optional[str]:
        r = self._redis()
        if not r:
            return None
        try:
            raw = r.get(self._active_key(intent, session_id))
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return raw
        except Exception:
            return None

    def get_active(self, intent: str, session_id: str) -> Optional[FlowState]:
        card_id = self.get_active_card_id(intent, session_id)
        if not card_id:
            return None
        return self.load(session_id, card_id)

    def clear_active(self, intent: str, session_id: str) -> None:
        r = self._redis()
        if not r:
            return
        try:
            r.delete(self._active_key(intent, session_id))
        except Exception:
            pass


flow_state_store = FlowStateStore()