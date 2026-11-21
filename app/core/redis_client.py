# app/core/redis_client.py
"""Redis 客户端（Token 存储 / 流程状态 / 文件注册表）

本版改动：
1. `password` 为空串时按 None 处理 —— 这样 yml 里可以写 `password: "${REDIS_PASSWORD:-}"`，
   没配密码时不会因为 `AUTH ""` 报错；
2. 加建连超时（3s），避免 Redis 挂掉时 heredoc 式的长阻塞；
3. 增加 `ping()` / `healthy()` 供 /healthz 使用（不再让运维端点自己摸私有属性）。
"""
from __future__ import annotations

import redis

from app.config.loader import config
from app.core.logger import logger


class RedisClient:

    def __init__(self):
        password = config.get("redis.password") or None
        if isinstance(password, str) and not password.strip():
            password = None

        self._pool = redis.ConnectionPool(
            host=config.get("redis.host"),
            port=config.get("redis.port"),
            db=config.get("redis.db"),
            password=password,
            max_connections=config.get("redis.pool_size", 10),
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=5,
            health_check_interval=30,
        )
        self._client = redis.Redis(connection_pool=self._pool)

    def exists(self, key: str) -> bool:
        return self._client.exists(key) > 0

    def get(self, key: str) -> str | None:
        return self._client.get(key)

    def setex(self, key: str, seconds: int, value: str):
        self._client.setex(key, seconds, value)

    def delete(self, key: str):
        self._client.delete(key)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception as e:
            logger.warning(f"[Redis] ping 失败: {e}")
            return False

    def healthy(self) -> tuple[bool, str]:
        """供 /healthz 使用：返回 (是否健康, 错误摘要)。"""
        try:
            self._client.ping()
            return True, ""
        except Exception as e:
            return False, str(e)[:200]


redis_client = RedisClient()