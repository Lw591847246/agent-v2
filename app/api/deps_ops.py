# app/api/deps_ops.py
"""运维端点（/metrics 等）的访问守卫 —— **依赖式**实现。

为什么不是中间件
----------------
本项目的鉴权不是 Starlette 中间件，而是依赖注入： /
 挂在路由上。/metrics 挂在 router.py 的 public 组
（**无任何依赖**），所以在中间件里做拦截根本不会触发。守卫必须做成依赖，
显式挂到运维端点上。

策略（与 app/middleware/auth.py 的 _ops_allowed 共用同一实现）
    1) 配了 auth.ops_token      -> 必须带 X-Ops-Token 或 Bearer <ops_token>（常量时间比对）
    2) 配了 auth.ops_allow_ips  -> 来源 IP 命中白名单
    3) 两者都没配               -> 404（隐藏端点存在，fail-closed）
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config.settings import auth_settings
from app.core.logger import logger


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def ops_source_ip(request: Request) -> str:
    """来源 IP：默认只信 socket 对端；仅 ops_trust_forwarded_for=true 才看转发头。"""
    peer = request.client.host if request.client else ""
    if not bool(getattr(auth_settings, "ops_trust_forwarded_for", False)):
        return peer
    import ipaddress
    for h in ("x-real-ip", "x-forwarded-for"):
        v = (request.headers.get(h) or "").strip()
        if not v:
            continue
        cand = v.split(",")[0].strip()
        try:
            ipaddress.ip_address(cand)
        except ValueError:
            continue
        return cand
    return peer


def ip_in_allowlist(ip: str, allow) -> bool:
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for item in allow:
        try:
            if "/" in item:
                if addr in ipaddress.ip_network(item, strict=False):
                    return True
            elif addr == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


def check_ops_access(request: Request, path: Optional[str] = None) -> None:
    """通过则返回 None；不通过抛 404（不暴露端点是否存在）。"""
    import secrets

    allow_paths = set(_as_list(getattr(auth_settings, "ops_paths", [])))
    if path is not None and allow_paths and path not in allow_paths:
        return  # 不在受保护清单里：交给调用方自己的策略

    token = (request.headers.get("X-Ops-Token") or "").strip()
    if not token:
        authz = (request.headers.get("Authorization") or "").strip()
        if authz.lower().startswith("bearer "):
            token = authz.split(" ", 1)[1].strip()

    expected = str(getattr(auth_settings, "ops_token", "") or "").strip()
    if expected:
        ok = bool(token) and secrets.compare_digest(token, expected)
    else:
        allow = _as_list(getattr(auth_settings, "ops_allow_ips", []))
        if allow:
            ok = ip_in_allowlist(ops_source_ip(request), allow)
        else:
            ok = False

    if not ok:
        logger.warning(
            f"[OpsGuard] 拒绝访问 path={path or request.url.path} "
            f"ip={ops_source_ip(request)} has_token_header={bool(token)}"
        )
        raise StarletteHTTPException(status_code=404, detail="not found")


async def ops_guard(request: Request) -> dict:
    """FastAPI 依赖：。"""
    check_ops_access(request, request.url.path)
    return {"userId": 0, "username": "ops", "ops": True}