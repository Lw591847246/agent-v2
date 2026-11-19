# app/middleware/rate_limit.py
"""限流中间件 -- 修复计数器内存泄漏版

原实现 self._windows = defaultdict(deque) 存在两个泄漏点：
  1) key=ip:path 只增不删：deque 内过期时间戳被 popleft 清空后，key 仍永久
     留在字典里，"再也不来的 IP/路径"组合永久占位；
  2) 无总量上限：海量随机 IP(或被刷)可把字典无限撑大。
本版保留原有"滑动窗口 + 按路径分级限流"语义，新增：
  - 闲置 key 回收：定期扫描，删除窗口空 / 最近请求早于 retention 的 key；
  - 总量硬上限 max_keys：逼近上限时强制清扫，极端情况整体清空兜底；
  - 改用普通 dict + get 判定，避免 defaultdict 的"访问即建条目"。
所有治理参数走 yml(rate_limit.*)，缺省回落合理默认，不改主流程。

并发安全：BaseHTTPMiddleware 在 workers=1 的 asyncio 单事件循环内，dispatch
的同步段(含 _maybe_sweep 遍历删除)不会跨 await 切换，无竞争。
"""
import time
from collections import deque
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from app.config.loader import config


class RateLimitMiddleware(BaseHTTPMiddleware):

    def __init__(self, app):
        super().__init__(app)
        self.enabled = config.get("rate_limit.enabled", True)
        self.default_qps = config.get("rate_limit.default_qps", 10)
        self._windows = {}  # key -> deque[float](普通 dict，不用 defaultdict)

        # ---- 内存治理参数(全部可配置)----
        # 闲置回收阈值：key 最近一次请求早于 now-retention 才可删(须 >> 1s 窗口，避免误删活跃 key)
        self._retention = config.get("rate_limit.retention_seconds", 300)
        # 两次主动清扫的最小间隔(秒)，限制扫描频率
        self._sweep_interval = config.get("rate_limit.sweep_interval", 60)
        # key 总量硬上限，逼近即强制清扫；极端活跃 IP 攻击下整体清空兜底
        self._max_keys = config.get("rate_limit.max_keys", 50000)
        self._last_sweep = time.time()

    def _get_limit(self, path: str) -> int:
        if path.startswith("/agent/v1/chat"):
            return config.get("rate_limit.chat_qps", 5)
        if "upload" in path or "process-file" in path:
            return config.get("rate_limit.upload_qps", 2)
        return self.default_qps

    def _maybe_sweep(self, now: float):
        """按需回收闲置 key + 总量上限保护。同步执行，不跨 await，安全。"""
        # 未到清扫周期且 key 数未逼近上限：跳过，保持热路径开销近零
        if now - self._last_sweep < self._sweep_interval and len(self._windows) < self._max_keys:
            return
        self._last_sweep = now
        cutoff = now - self._retention
        dead = [k for k, w in self._windows.items() if (not w) or w[-1] < cutoff]
        for k in dead:
            del self._windows[k]
        # 清理后仍逼近上限(海量活跃 IP)：整体清空兜底，宁可短暂放松限流也不让内存打爆
        if len(self._windows) >= self._max_keys:
            self._windows.clear()

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        key = f"{ip}:{request.url.path}"
        now = time.time()
        limit = self._get_limit(request.url.path)

        window = self._windows.get(key)
        if window is None:
            # 新 key：先做一次回收/上限保护，避免字典无界增长
            self._maybe_sweep(now)
            window = deque()
            self._windows[key] = window
        else:
            # 活跃 key：滑窗丢弃过期时间戳
            while window and now - window[0] > 1:
                window.popleft()

        if len(window) >= limit:
            return JSONResponse(
                status_code=429,
                content={"code": 429, "message": "请求过于频繁，请稍后重试"},
            )

        window.append(now)
        return await call_next(request)
