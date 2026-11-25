# app/skills/code/workspace/context/store.py
"""会话级上下文存储抽象。

当前 Redis 实现；未来换向量库只改实现，不动 builder/skill。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from app.skills.code.workspace.redis_store import code_redis_store


class ContextStore(ABC):
    @abstractmethod
    def append_turn(self, uid: int, sid: str, turn: Dict[str, Any]) -> None: ...

    @abstractmethod
    def get_turns(self, uid: int, sid: str, limit: int) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_summary(self, uid: int, sid: str) -> str: ...

    @abstractmethod
    def set_summary(self, uid: int, sid: str, text: str) -> None: ...

    @abstractmethod
    def add_pin(self, uid: int, sid: str, text: str) -> None: ...

    @abstractmethod
    def get_pins(self, uid: int, sid: str) -> List[str]: ...


class RedisContextStore(ContextStore):
    def append_turn(self, uid, sid, turn):
        code_redis_store.append_turn(uid, sid, turn)

    def get_turns(self, uid, sid, limit):
        return code_redis_store.get_turns(uid, sid, limit)

    def get_summary(self, uid, sid):
        return code_redis_store.get_summary(uid, sid)

    def set_summary(self, uid, sid, text):
        code_redis_store.set_summary_text(uid, sid, text)

    def add_pin(self, uid, sid, text):
        code_redis_store.add_pin(uid, sid, text)

    def get_pins(self, uid, sid):
        return code_redis_store.get_pins(uid, sid)


context_store: ContextStore = RedisContextStore()