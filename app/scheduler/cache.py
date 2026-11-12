# app/scheduler/cache.py
"""意图缓存：LRU + TTL + 两级版本号（L1 领域 / L2 每个领域独立）。"""
import hashlib
import time
from collections import OrderedDict
from typing import Tuple


class IntentCache:

    def __init__(self, max_size: int = 512, ttl: int = 3600):
        self._max_size = max_size
        self._ttl = ttl
        self._store: "OrderedDict[str, Tuple[object, float]]" = OrderedDict()
        self._version_l1 = self._compute_version_l1()
        self._version_l2 = self._compute_version_l2()

    @staticmethod
    def _compute_version_l1() -> str:
        try:
            from app.scheduler.prompts import domain_prompt
            content = (
                domain_prompt.DOMAIN_SYSTEM_PROMPT
                + domain_prompt.DOMAIN_USER_TEMPLATE
            )
            return hashlib.md5(content.encode("utf-8")).hexdigest()[:8]
        except Exception:
            return "v0"

    @staticmethod
    def _compute_version_l2() -> dict:
        try:
            from app.scheduler.prompts import intent_prompts
            result = {}
            for domain, prompt in intent_prompts.L2_PROMPTS.items():
                result[domain] = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8]
            return result
        except Exception:
            return {"general": "v0", "content": "v0", "image": "v0", "video": "v0"}

    def key_l1(self, text: str, has_att: bool, att_types: list, model: str = "") -> str:
        raw = (
            f"{self._version_l1}|{model}|{text}|{has_att}|"
            f"{','.join(sorted(att_types or []))}"
        )
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def key_l2(self, domain: str, text: str, has_att: bool, att_types: list, model: str = "") -> str:
        v = self._version_l2.get(domain, "v0")
        raw = (
            f"{v}|{model}|{domain}|{text}|{has_att}|"
            f"{','.join(sorted(att_types or []))}"
        )
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def get(self, key: str):
        item = self._store.get(key)
        if not item:
            return None
        value, ts = item
        if time.time() - ts > self._ttl:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value):
        self._store[key] = (value, time.time())
        self._store.move_to_end(key)
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def stats(self) -> dict:
        return {
            "version_l1": self._version_l1,
            "version_l2": self._version_l2,
            "size": len(self._store),
            "max_size": self._max_size,
            "ttl": self._ttl,
        }


intent_cache = IntentCache()