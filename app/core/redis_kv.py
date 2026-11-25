# app/core/redis_kv.py
# ============================================================
# 通用工具层（Generic Utility Layer / 无状态）
# 把 JSON 编解码、Hash / List / Set / ZSET、SCAN 删除、分布式锁、
# 限流、发布订阅等 Redis 命令语法糖收拢成通用函数。
#
# 设计纪律：
# 1. key / field 完全由调用方决定，本层不含任何业务语义（不出现 session/workspace 等词）。
# 2. 每个函数都接受可选 client=None，默认复用全局单例；需要多实例时直接传连接。
#    —— 这就是“其他业务无需改本文件即可复用”的关键：import 即用，传 client 即可换实例。
# 3. 本层未覆盖的原生命令，逃生口：
#        from app.core.redis_client import redis_client
#        redis_client.client.xxx(...)        # 或下方 command()
#    永远不需要回头改本文件。
# ============================================================
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import redis as _redis

from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)


def _client(client: Optional["_redis.Redis"] = None) -> "_redis.Redis":
    """统一取连接：传入即用，否则走全局单例的公开 client。"""
    return client if client is not None else redis_client.client


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw, default=None):
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


# ---------- 逃生口：调用任意原生命令，未来新命令无需改本文件 ----------
def command(name: str, *args, client=None, **kwargs):
    return getattr(_client(client), name)(*args, **kwargs)


# ---------------------- String / JSON ----------------------
def get_json(key: str, default=None, client=None):
    return _loads(_client(client).get(key), default)


def set_json(key: str, value: Any, ttl: int = 0, client=None) -> bool:
    c = _client(client)
    payload = _dumps(value)
    if ttl and ttl > 0:
        return bool(c.setex(key, ttl, payload))
    return bool(c.set(key, payload))


def delete(key: str, client=None) -> int:
    return _client(client).delete(key)


def delete_many(keys: Iterable[str], client=None) -> int:
    keys = list(keys)
    return _client(client).delete(*keys) if keys else 0


def exists(key: str, client=None) -> bool:
    return bool(_client(client).exists(key))


def expire(key: str, ttl: int, client=None) -> bool:
    return bool(_client(client).expire(key, ttl))


def ttl(key: str, client=None) -> int:
    return _client(client).ttl(key)


def incr(key: str, ttl: int = 0, amount: int = 1, client=None) -> int:
    """自增计数器；仅首次创建时设置窗口 TTL。"""
    c = _client(client)
    n = c.incrby(key, amount)
    if ttl and ttl > 0 and n == amount:
        c.expire(key, ttl)
    return n


# ---------------------- Hash ----------------------
def hset_json(key: str, field: str, value: Any, ttl: int = 0, client=None) -> int:
    c = _client(client)
    n = c.hset(key, field, _dumps(value))
    if ttl and ttl > 0:
        c.expire(key, ttl)
    return n


def hset_many_json(key: str, mapping: Dict[str, Any], ttl: int = 0, client=None) -> int:
    c = _client(client)
    payload = {k: _dumps(v) for k, v in mapping.items()}
    n = c.hset(key, mapping=payload) if payload else 0
    if ttl and ttl > 0:
        c.expire(key, ttl)
    return n


def hget_json(key: str, field: str, default=None, client=None):
    return _loads(_client(client).hget(key, field), default)


def hgetall_json(key: str, default=None, client=None) -> Dict[str, Any]:
    raw = _client(client).hgetall(key)
    return {k: _loads(v) for k, v in raw.items()} if raw else (default or {})


def hdel(key: str, *fields: str, client=None) -> int:
    if not fields:
        return 0
    return _client(client).hdel(key, *fields)


def hincr(key: str, field: str, amount: int = 1, ttl: int = 0, client=None) -> int:
    c = _client(client)
    n = c.hincrby(key, field, amount)
    if ttl and ttl > 0 and n == amount:
        c.expire(key, ttl)
    return n


# ---------------------- List（队列 / 有界列表）----------------------
def lpush_trim(key: str, value: Any, maxlen: int = 1000, ttl: int = 0, client=None) -> int:
    c = _client(client)
    c.lpush(key, _dumps(value))
    c.ltrim(key, 0, maxlen - 1)
    if ttl and ttl > 0:
        c.expire(key, ttl)
    return c.llen(key)


def rpush_json(key: str, value: Any, ttl: int = 0, client=None) -> int:
    c = _client(client)
    n = c.rpush(key, _dumps(value))
    if ttl and ttl > 0:
        c.expire(key, ttl)
    return n


def lrange_json(key: str, start: int = 0, end: int = -1, default=None, client=None) -> List[Any]:
    raw = _client(client).lrange(key, start, end)
    return [_loads(x) for x in raw] if raw else (default or [])


def rpop_json(key: str, default=None, client=None):
    return _loads(_client(client).rpop(key), default)


def llen(key: str, client=None) -> int:
    return _client(client).llen(key)


# ---------------------- Set ----------------------
def sadd(key: str, *members: Any, ttl: int = 0, client=None) -> int:
    c = _client(client)
    vals = [_dumps(m) if not isinstance(m, str) else m for m in members]
    n = c.sadd(key, *vals) if vals else 0
    if ttl and ttl > 0:
        c.expire(key, ttl)
    return n


def srem(key: str, *members: Any, client=None) -> int:
    vals = [_dumps(m) if not isinstance(m, str) else m for m in members]
    return _client(client).srem(key, *vals) if vals else 0


def smembers(key: str, client=None) -> List[Any]:
    raw = _client(client).smembers(key)
    return [_loads(x) if isinstance(x, str) and x[:1] in "{[" else x for x in raw]


def sismember(key: str, member: Any, client=None) -> bool:
    val = member if isinstance(member, str) else _dumps(member)
    return bool(_client(client).sismember(key, val))


# ---------------------- ZSET（排行榜 / 延迟队列 / 滑动窗口）----------------------
def zadd(key: str, mapping: Dict[Any, float], ttl: int = 0, client=None) -> int:
    c = _client(client)
    payload = {(_dumps(k) if not isinstance(k, str) else k): float(s) for k, s in mapping.items()}
    n = c.zadd(key, payload) if payload else 0
    if ttl and ttl > 0:
        c.expire(key, ttl)
    return n


def zincr(key: str, member: Any, amount: float = 1, ttl: int = 0, client=None) -> float:
    c = _client(client)
    val = member if isinstance(member, str) else _dumps(member)
    score = c.zincrby(key, amount, val)
    if ttl and ttl > 0:
        c.expire(key, ttl)
    return score


def zrange_json(key: str, start: int = 0, end: int = -1, with_scores: bool = False, client=None):
    raw = _client(client).zrange(key, start, end, withscores=with_scores)
    if with_scores:
        return [((_loads(m) if isinstance(m, str) and m[:1] in "{[" else m), s) for m, s in raw]
    return [_loads(m) if isinstance(m, str) and m[:1] in "{[" else m for m in raw]


def zpop_by_score(key: str, max_score: float, client=None) -> List[Any]:
    """取出并删除 score <= max_score 的元素（轻量延迟队列）。"""
    c = _client(client)
    members = c.zrangebyscore(key, "-inf", max_score)
    if not members:
        return []
    c.zremrangebyscore(key, "-inf", max_score)
    return [_loads(m) if isinstance(m, str) and m[:1] in "{[" else m for m in members]


# ---------------------- 批量删除（SCAN，禁用 KEYS 阻塞）----------------------
def delete_by_pattern(pattern: str, batch: int = 500, client=None) -> int:
    c = _client(client)
    n = 0
    for k in c.scan_iter(match=pattern, count=batch):
        c.delete(k)
        n += 1
    return n


def delete_by_prefix(prefix: str, batch: int = 500, client=None) -> int:
    return delete_by_pattern(prefix + "*", batch=batch, client=client)


# ---------------------- 分布式锁（token + CAS 释放）----------------------
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def acquire_lock(name: str, ttl: int = 10, retry: float = 0,
                 retry_delay: float = 0.1, client=None) -> Optional[str]:
    """返回持有令牌（成功）或 None（超时未抢到）。"""
    token = uuid.uuid4().hex
    c = _client(client)
    key = f"lock:{name}"
    deadline = time.monotonic() + max(retry, 0)
    while True:
        if c.set(key, token, nx=True, ex=ttl):
            return token
        if time.monotonic() >= deadline:
            return None
        time.sleep(retry_delay)


def release_lock(name: str, token: Optional[str], client=None) -> bool:
    if not token:
        return False
    return bool(_client(client).eval(_RELEASE_LUA, 1, f"lock:{name}", token))


# ---------------------- 固定窗口限流 ----------------------
def rate_limit(key: str, limit: int, window: int, client=None) -> Tuple[bool, int]:
    """返回 (是否放行, 当前窗口已用次数)。窗口内超过 limit 则放行 False。"""
    used = incr(key, ttl=window, client=client)
    return used <= limit, used


# ---------------------- 发布订阅 ----------------------
def publish(channel: str, message: Any, client=None) -> int:
    return _client(client).publish(channel, _dumps(message))


def subscribe(channel: str, handler: Callable[[Any], None],
              client=None, stop_flag: Optional[Callable[[], bool]] = None) -> None:
    """阻塞式订阅：每条消息回调 handler(已反序列化 payload)。
    需要自定义循环 / 多频道时，直接用逃生口 redis_client.client.pubsub()。"""
    ps = _client(client).pubsub()
    ps.subscribe(channel)
    try:
        for msg in ps.listen():
            if stop_flag and stop_flag():
                break
            if msg and msg.get("type") == "message":
                handler(_loads(msg.get("data")))
    finally:
        ps.unsubscribe(channel)
        ps.close()
