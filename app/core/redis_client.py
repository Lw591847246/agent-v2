# app/core/redis_client.py
# ============================================================
# 连接层（Connection Layer）
# 唯一职责：连接池、单例、健康检查。
# 不实现任何业务命令；需要原始命令时通过 .client 逃生口直接使用。
# 业务永远不要 import 本类去拼 Redis 命令，命令统一走 redis_kv。
# ============================================================
from __future__ import annotations

import redis
from redis.exceptions import RedisError
from app.config.loader import config




#    def __init__(self):
#         password = config.get("redis.password") or None
#         if isinstance(password, str) and not password.strip():
#             password = None
#
#         self._pool = redis.ConnectionPool(
#             host=config.get("redis.host"),
#             port=config.get("redis.port"),
#             db=config.get("redis.db"),
#             password=password,
#             max_connections=config.get("redis.pool_size", 10),
#             decode_responses=True,
#             socket_connect_timeout=3,
#             socket_timeout=5,
#             health_check_interval=30,
#         )
#         self._client = redis.Redis(connection_pool=self._pool)

class RedisClient:
    def __init__(self):
        self._pool = None
        self._client = None
        self._build()

    def _build(self):

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
        # # 空串密码归一为 None，避免 redis-py 误发 AUTH
        # password = config.get("redis.password") or None
        # self._pool = redis.ConnectionPool.from_url(
        #     settings.REDIS_URL,
        #     max_connections=getattr(settings, "REDIS_MAX_CONN", 50),
        #     decode_responses=True,
        #     socket_timeout=getattr(settings, "REDIS_SOCKET_TIMEOUT", 5),
        #     socket_connect_timeout=getattr(settings, "REDIS_CONNECT_TIMEOUT", 3),
        #     health_check_interval=30,
        #     password=pwd,
        # )
        # self._client = redis.Redis(connection_pool=self._pool)

    # ---- 扩展逃生口：把底层连接堂堂正正交出去，杜绝“猜私有成员” ----
    @property
    def client(self):
        return self._client

    @property
    def pool(self):
        return self._pool

    def pipeline(self, transaction: bool = True):
        return self._client.pipeline(transaction=transaction)

    def reconfigure(self):
        """断线重连 / 配置热更时重建连接池。"""
        if self._pool is not None:
            try:
                self._pool.disconnect(inuse_connections=True)
            except Exception:
                pass
        self._build()

    def healthy(self) -> bool:
        try:
            return bool(self._client.ping())
        except RedisError:
            return False

    # ---- 极少量直连命令，仅为兼容旧调用方（鉴权/健康检查），非通用入口 ----
    def get(self, key):
        return self._client.get(key)

    def setex(self, key, ttl, value):
        return self._client.setex(key, ttl, value)

    def delete(self, *keys):
        return self._client.delete(*keys)

    def exists(self, *keys):
        return self._client.exists(*keys)


# 模块级单例：进程内只建一条连接池
redis_client = RedisClient()
