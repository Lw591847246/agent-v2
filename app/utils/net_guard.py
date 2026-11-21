# app/utils/net_guard.py
"""出站 URL 安全校验（防 SSRF）。

背景
----
`skills/web_search/fetcher.py` 会去抓搜索结果里的任意链接。原实现只做
`urlparse` + 域名黑名单，**不判断目标 IP**。在制造业内网里，一条搜索结果
就能让 agent 去访问 `http://erp-internal:8080`、`http://192.168.1.10/api`，
把内网服务探一遍（甚至读到接口数据）。

策略
----
1. 只允许 http/https；
2. 主机名解析出的**所有** A/AAAA 必须都是公网地址；
   （`is_private / is_loopback / is_link_local / is_reserved / is_multicast /
     is_unspecified` 任一命中即拒，另加显式的内网网段兜底）
3. 主机名是 localhost / *.local / *.internal / 无点单标签名 → 直接拒；
4. 已知 TOCTOU：校验与请求之间 DNS 可能被重绑定。所以 fetcher 侧
   **禁用自动重定向**，每一跳都重新校验，把窗口压到最小。

纯函数 + 标准库，可单测（`check_url("http://127.0.0.1/")` 必须 False）。
"""
from __future__ import annotations

import ipaddress
import socket
from typing import List, Tuple
from urllib.parse import urlparse

# 显式兜底网段（is_private 已覆盖大部分，这里补齐保留段与云元数据地址）
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",       # CGNAT
        "127.0.0.0/8",
        "169.254.0.0/16",      # link-local / 云元数据 169.254.169.254
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)

_BLOCKED_HOST_SUFFIX = (".local", ".internal", ".intranet", ".lan", ".home", ".corp")
_BLOCKED_HOST_EXACT = {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _host_is_blocked_name(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return True
    if h in _BLOCKED_HOST_EXACT:
        return True
    if any(h.endswith(suf) for suf in _BLOCKED_HOST_SUFFIX):
        return True
    # 无点单标签名（erp、mes、pdm 这类内网短名）
    if "." not in h and ":" not in h:
        return True
    return False


def resolve_host(host: str, port: int = 80) -> List[str]:
    """解析出所有地址（IPv4 + IPv6）。失败返回空列表。"""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        return []
    out = []
    for info in infos:
        addr = info[4][0]
        if addr not in out:
            out.append(addr)
    return out


def check_url(url: str, resolve: bool = True) -> Tuple[bool, str]:
    """返回 (是否允许, 原因)。原因用于日志，不返回给用户。"""
    if not url or not isinstance(url, str):
        return False, "empty"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "bad_url"

    if parsed.scheme not in ("http", "https"):
        return False, f"scheme:{parsed.scheme or 'none'}"
    host = parsed.hostname or ""
    if not host:
        return False, "no_host"
    if _host_is_blocked_name(host):
        return False, f"host_name:{host}"

    # 直接是 IP 字面量
    try:
        ip = ipaddress.ip_address(host)
        return (False, f"ip:{host}") if _ip_is_blocked(ip) else (True, "")
    except ValueError:
        pass

    if not resolve:
        return True, ""

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addrs = resolve_host(host, port)
    if not addrs:
        return False, f"dns_fail:{host}"
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"bad_addr:{addr}"
        if _ip_is_blocked(ip):
            return False, f"resolved_private:{host}->{addr}"
    return True, ""