# app/config/settings.py
"""强类型配置。

【本轮新增的安全相关配置项】（全部有安全默认值，不配也能起服务）
  auth.white_list            —— 只放"没有用户态"的路径；支持尾部 * 表示前缀匹配
  auth.ops_paths             —— 运维端点（默认 /metrics /docs /openapi.json /redoc）
  auth.ops_token             —— 运维令牌；配了就必须带 X-Ops-Token / Bearer 才能访问
  auth.ops_allow_ips         —— 运维端点 IP 白名单（未配 ops_token 时的替代方案）
  auth.ops_trust_forwarded_for —— 运维端点是否信任转发头取来源 IP（默认否）
  auth.require_download_auth —— 下载授权收口开关，**代码默认 true**（fail-closed）；
                                 yml 里的显式 false 仍会生效，务必确认线上是 true
  cors.allow_origins         —— 逗号分隔或列表；留空 = 不放任何跨域（默认 * 兼容现状）
  cors.allow_credentials     —— 默认 False；True 时禁止 allow_origins 为 *
  rate_limit.*               —— 见 app/middleware/rate_limit.py
  security.default_url_ttl_seconds —— 签名下载 URL 默认时效（默认 7 天）
"""
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

import os

from dotenv import load_dotenv

# 项目根目录（settings.py 往上 3 层）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

from app.config.loader import config  # noqa: E402


def _as_list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


@dataclass(frozen=True)
class AppSettings:
    name: str = config.get("app.name")
    version: str = config.get("app.version")
    # B3 fix: single source of truth for env = injected APP_ENV (then ENV); the yml
    # value app.env is only a fallback. Old code read ONLY config.get("app.env"),
    # while loader picks application-{APP_ENV}.yml and signed_url._env_name reads APP_ENV.
    # Once these diverge you get "APP_ENV=prod but yml still app.env: dev" -> production
    # Swagger/OpenAPI left wide open (bootstrap decides docs_url from app_settings.env).
    env: str = field(default_factory=lambda: (
        os.getenv("APP_ENV") or os.getenv("ENV")
        or str(config.get("app.env") or "dev")
    ).strip().lower())


@dataclass(frozen=True)
class ServerSettings:
    host: str = config.get("server.host")
    port: int = config.get("server.port")
    workers: int = config.get("server.workers")


@dataclass(frozen=True)
class AuthSettings:
    white_list: List[str] = field(
        default_factory=lambda: config.get("auth.white_list", [])
    )
    # ---- 本轮新增 ----
    ops_paths: List[str] = field(
        default_factory=lambda: _as_list(
            config.get(
                "auth.ops_paths",
                ["/metrics", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"],
            )
        )
    )
    ops_token: str = field(
        default_factory=lambda: str(config.get("auth.ops_token", "") or "").strip()
    )
    ops_allow_ips: List[str] = field(
        default_factory=lambda: _as_list(config.get("auth.ops_allow_ips", []))
    )
    ops_trust_forwarded_for: bool = field(
        default_factory=lambda: bool(config.get("auth.ops_trust_forwarded_for", False))
    )
    require_download_auth: bool = field(
        default_factory=lambda: bool(config.get("auth.require_download_auth", True))
    )


@dataclass(frozen=True)
class CorsSettings:
    allow_origins: List[str] = field(
        default_factory=lambda: _as_list(config.get("cors.allow_origins", "*")) or ["*"]
    )
    allow_methods: List[str] = field(
        default_factory=lambda: _as_list(config.get("cors.allow_methods", "*")) or ["*"]
    )
    allow_headers: List[str] = field(
        default_factory=lambda: _as_list(config.get("cors.allow_headers", "*")) or ["*"]
    )
    allow_credentials: bool = field(
        default_factory=lambda: bool(config.get("cors.allow_credentials", False))
    )


@dataclass(frozen=True)
class SecuritySettings:
    default_url_ttl_seconds: int = field(
        default_factory=lambda: int(config.get("security.default_url_ttl_seconds", 604800) or 604800)
    )


@dataclass(frozen=True)
class FileSettings:
    max_file_tokens: int = config.get("file.max_file_tokens")
    max_file_size_mb: int = config.get("file.max_file_size_mb")
    allowed_extensions: List[str] = field(
        default_factory=lambda: config.get("file.allowed_extensions", [])
    )


@dataclass(frozen=True)
class VectorStoreSettings:
    path: str = config.get("vector_store.path")
    collection: str = config.get("vector_store.collection")
    chunk_size: int = config.get("vector_store.chunk_size")
    chunk_overlap: int = config.get("vector_store.chunk_overlap")
    top_k: int = config.get("vector_store.top_k")


app_settings = AppSettings()
server_settings = ServerSettings()
auth_settings = AuthSettings()
cors_settings = CorsSettings()
security_settings = SecuritySettings()
file_settings = FileSettings()
vector_store_settings = VectorStoreSettings()


