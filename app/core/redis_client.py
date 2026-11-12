"""Redis 客户端（Token 存储）"""
import redis
from app.config.loader import config
from app.core.logger import logger


class RedisClient:

    def __init__(self):
        self._pool = redis.ConnectionPool(
            host=config.get("redis.host"),
            port=config.get("redis.port"),
            db=config.get("redis.db"),
            password=config.get("redis.password"),
            max_connections=config.get("redis.pool_size", 10),
            decode_responses=True,
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


redis_client = RedisClient()