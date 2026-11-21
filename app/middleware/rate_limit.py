# app/middleware/rate_limit.py
"""限流中间件（纯 ASGI + 可信代理真实客户端 IP + 路径判定精确化）。

【本轮修复】
1. 中危 4：限流 key 原来取 request.client.host。nginx / 网关之后该值恒为
   127.0.0.1，全站共享同一个 chat 计数器（默认 5 QPS），并发一上来整站 429。
   现在按 **可信代理白名单** 决定是否信任 X-Forwarded-For / X-Real-IP：
     - 直连对端在 auth.trusted_proxies 内 -> 取 XFF 最左（或 X-Real-IP，按配置）；
     - 不在可信代理内 -> 一律用 socket 对端地址（**不信任任何伪造头**）。
2. 中危 6：改纯 ASGI，不再把 SSE 经内存流转发。
3. 沿用上一版对 _get_limit 的修复：只按**精确路由**判定上传写接口，
   不用子串匹配，避免误伤 /agent/v1/upload/thumb 这类纯读接口。

新增配置（application.yml，都有安全默认值，不配也能跑）：
    rate_limit:
      trust_forwarded_for: remote_only   # remote_only | always | never
      trusted_proxies: ["127.0.0.1", "::1", "10.0.0.0/8"]
"""
from __future__ import annotations

import ipaddress
import time
from collections import deque

from starlette.responses import JSONResponse

from app.config.loader import config

_HDR_XFF = b"x-forwarded-for"
_HDR_REAL_IP = b"x-real-ip"


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


class RateLimitMiddleware:

    # 只限"写盘/重活"这两个端点；不要用子串匹配，否则会误伤 upload/thumb 等读接口
    _UPLOAD_WRITE_PATHS = frozenset({
        "/agent/v1/upload/asset",
        "/agent/v1/process-file",
    })

    def __init__(self, app):
        self.app = app
        self.enabled = config.get("rate_limit.enabled", True)
        self.default_qps = int(config.get("rate_limit.default_qps", 10) or 10)
        self.chat_qps = int(config.get("rate_limit.chat_qps", 5) or 5)
        self.upload_qps = int(config.get("rate_limit.upload_qps", 2) or 2)

        self._windows = {}  # key -> deque[float]
        self._retention = float(config.get("rate_limit.retention_seconds", 300) or 300)
        self._sweep_interval = float(config.get("rate_limit.sweep_interval", 60) or 60)
        self._max_keys = int(config.get("rate_limit.max_keys", 50000) or 50000)
        self._last_sweep = time.time()

        # ---- 真实客户端 IP ----
        self._xff_mode = str(config.get("rate_limit.trust_forwarded_for", "remote_only") or "remote_only").strip().lower()
        self._prefer_real_ip = bool(config.get("rate_limit.prefer_real_ip", False))
        self._trusted_nets = []
        self._trusted_ips = set()
        for item in _as_list(config.get("rate_limit.trusted_proxies", [])):
            try:
                if "/" in item:
                    self._trusted_nets.append(ipaddress.ip_network(item, strict=False))
                else:
                    self._trusted_ips.add(ipaddress.ip_address(item))
            except ValueError:
                continue

    # ---------------- IP 解析 ----------------

    def _peer_ip(self, scope) -> str:
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _in_trusted(self, ip_str: str):
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if ip in self._trusted_ips:
            return True
        return any(ip in net for net in self._trusted_nets)

    def _header(self, scope, name: bytes) -> str:
        for k, v in scope.get("headers") or []:
            if k == name:
                try:
                    return v.decode("latin-1").strip()
                except Exception:
                    return ""
        return ""

    def _client_ip(self, scope) -> str:
        peer = self._peer_ip(scope)

        if self._xff_mode == "never":
            return peer

        # 只有直连对端确属可信代理链时才信任转发头；always 仅供"应用一定在反代后面"的部署
        trust = self._in_trusted(peer) if self._xff_mode != "always" else True
        if not trust:
            return peer

        candidates = []
        if self._prefer_real_ip:
            candidates.append(self._header(scope, _HDR_REAL_IP))
        xff = self._header(scope, _HDR_XFF)
        if xff:
            # B1 fix: XFF take the left-most (origin) entry as the client candidate.
            # MUST append the whole string. Old code used extend() on a *string*, which
            # iterates it char-by-char ("1.2.3.4" -> [1,.,2,...]); none of those chars is a
            # valid IP, so every candidate is rejected by ip_address and we fall back to the
            # socket peer. Behind a gateway that only sends X-Forwarded-For (no X-Real-IP)
            # the real client IP was NEVER obtained -> rate limit collapsed to one shared
            # bucket site-wide (exactly what the medium-risk #4 fix was supposed to solve).
            candidates.append(xff.split(",")[0].strip())
        if not self._prefer_real_ip:
            candidates.append(self._header(scope, _HDR_REAL_IP))

        for c in candidates:
            c = (c or "").strip()
            if not c:
                continue
            try:
                ipaddress.ip_address(c)
            except ValueError:
                continue  # 伪造/畸形值直接忽略，回落 socket 对端
            return c
        return peer

    # ---------------- 额度 ----------------

    def _get_limit(self, path: str) -> int:
        p = (path or "/").rstrip("/") or "/"
        if p.startswith("/agent/v1/chat"):
            return self.chat_qps
        if p in self._UPLOAD_WRITE_PATHS:
            return self.upload_qps
        return self.default_qps

    def _maybe_sweep(self, now: float):
        """按需回收闲置 key + 总量上限保护。同步执行、不跨 await，无竞态。"""
        if now - self._last_sweep < self._sweep_interval and len(self._windows) < self._max_keys:
            return
        self._last_sweep = now
        cutoff = now - self._retention
        dead = [k for k, w in self._windows.items() if (not w) or w[-1] < cutoff]
        for k in dead:
            del self._windows[k]
        if len(self._windows) >= self._max_keys:
            self._windows.clear()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        key = f"{self._client_ip(scope)}:{path}"
        now = time.time()
        limit = self._get_limit(path)

        window = self._windows.get(key)
        if window is None:
            self._maybe_sweep(now)
            window = deque()
            self._windows[key] = window
        else:
            while window and now - window[0] > 1:
                window.popleft()

        if len(window) >= limit:
            await JSONResponse(
                status_code=429,
                content={"code": 429, "message": "请求过于频繁，请稍后重试"},
            )(scope, receive, send)
            return

        window.append(now)
        await self.app(scope, receive, send)


