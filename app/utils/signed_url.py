# app/utils/signed_url.py
"""签名 URL 工具（强制密钥 + 默认时效 + 可吊销）。

三种能力：
1. sign / verify             —— 对整数 id 签名（图片 / 缩略图按 message_id 访问的旧形态）；
2. sign_path / verify_path   —— 对 (kind, ident) 签名；
3. sign_url / verify_url     —— 带过期时间的资源签名，用于 <a href> / <video src>
                                这类"浏览器直连、带不上 Authorization 头"的下载地址。

【本轮修复 · 高危 2】
(a) 默认密钥
    原实现密钥缺省回落到硬编码在源码里的 _DEFAULT_SECRET，而 assert_secure_config
    只在 APP_ENV in {prod,production,prd} 时才拦 —— configloader 里 APP_ENV 缺省是
    dev，于是**任何忘记显式设 APP_ENV 的部署会静默使用源码里这个公开默认密钥**，
    任何人可伪造 image / thumb / ppt / word / job / tts 等全部签名。
    现在：**默认无密钥**。未配置 SIGNED_URL_SECRET 时：
      - 默认直接 RuntimeError（所有环境一致，不再依赖 APP_ENV 是否设对）；
      - 仅当显式设 SIGNED_URL_ALLOW_INSECURE_DEV=1（或本地 dev 环境开关）才允许
        临时用开发密钥，且每次签发都打 WARNING，密钥值本身不再出现在源码里。

(b) 永不过期
    原 sign_url(ttl=0) 默认"永久有效"，能力型 URL 一旦外泄无法吊销。
    现在默认 TTL = SIGNED_URL_DEFAULT_TTL（缺省 7 天）；verify 强制校验 exp；
    轮换 SIGNED_URL_SECRET 即可一次性吊销全部已签发 URL。
    历史永久链接（exp=0）由 SIGNED_URL_ALLOW_LEGACY_PERMANENT 控制：
      true （默认，灰度期兼容老链接，前端刷新消息即可自动换新链接）
      false（收口后：exp=0 一律拒绝）

(c) 签名覆盖范围
    sign / sign_path 旧格式仍只签 id，容易被"改路径不换签"利用；
    新增 sign_url 一律签 (kind, ident, exp)，kind 参与 HMAC，跨资源类型不能复用签名。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

# 开发环境显式开关：仅用于本地/CI，必须自行设置环境变量，代码里不再内置任何密钥字面量
_INSECURE_FLAGS = {"1", "true", "yes", "on"}
_DEV_ENVS = {"dev", "development", "local", "test", ""}

DEFAULT_TTL_SECONDS = int(os.getenv("SIGNED_URL_DEFAULT_TTL", "604800") or 604800)  # fallback const (7d)


def _default_ttl_seconds() -> int:
    """B2 fix: single, live source for the default URL TTL.
    Priority: env SIGNED_URL_DEFAULT_TTL (ops hot-override) > yml
    security.default_url_ttl_seconds > 7 days. Old code read the env var once at
    import and NEVER consumed config/settings.py SecuritySettings.default_url_ttl_seconds
    (a dead config): ops changed the yml TTL to 3600 thinking it took effect, while the
    signer still issued 7-day URLs. This wires the config item for real. config is imported
    lazily so merely importing this util stays side-effect free and cycle-free.
    """
    env_v = (os.getenv("SIGNED_URL_DEFAULT_TTL") or "").strip()
    if env_v.isdigit() and int(env_v) > 0:
        return int(env_v)
    try:
        from app.config.loader import config
        cfg_v = config.get("security.default_url_ttl_seconds", None)
        if cfg_v is not None and int(cfg_v) > 0:
            return int(cfg_v)
    except Exception:
        pass
    return DEFAULT_TTL_SECONDS

_warned = False
_runtime_dev_secret: Optional[bytes] = None


def _env_name() -> str:
    return (os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()


def _allow_insecure_dev() -> bool:
    return (os.getenv("SIGNED_URL_ALLOW_INSECURE_DEV") or "").strip().lower() in _INSECURE_FLAGS


def _secret() -> bytes:
    """取签名密钥。未配置即拒绝，不再静默使用源码里的默认值。"""
    global _runtime_dev_secret
    s = os.getenv("SIGNED_URL_SECRET")
    if s:
        if len(s.encode("utf-8")) < 32:
            raise RuntimeError(
                "SIGNED_URL_SECRET 过短：至少 32 字节（建议 openssl rand -hex 32）"
            )
        return s.encode("utf-8")

    if _allow_insecure_dev() and _env_name() in _DEV_ENVS:
        from app.core.logger import logger
        global _warned
        if not _warned:
            logger.warning(
                "[Security] SIGNED_URL_SECRET 未配置，正在使用本次进程随机生成的开发密钥；"
                "重启即失效、多实例之间签名互不相通。生产必须显式配置固定密钥。"
            )
            _warned = True
        if _runtime_dev_secret is None:
            import secrets as _secrets
            _runtime_dev_secret = _secrets.token_bytes(32)
        return _runtime_dev_secret

    raise RuntimeError(
        "SIGNED_URL_SECRET 未设置：拒绝签发/校验任何签名 URL"
        "（否则任何人可伪造图片/缩略图/下载链接）。"
        "生成方式：openssl rand -hex 32；"
        "本地开发如需临时放行，设 SIGNED_URL_ALLOW_INSECURE_DEV=1"
    )


def assert_secure_config() -> None:
    """启动期调用（bootstrap.create_app 已接）：密钥不可用则 fail-fast。"""
    _secret()


def _allow_legacy_permanent() -> bool:
    return (os.getenv("SIGNED_URL_ALLOW_LEGACY_PERMANENT", "true") or "").strip().lower() in _INSECURE_FLAGS


def _digest(*parts: str) -> str:
    raw = ":".join(parts).encode("utf-8")
    return hmac.new(_secret(), raw, hashlib.sha256).hexdigest()


def _mac(*parts: str) -> str:
    """新格式签名：32 位 hex（原为 16 位，提升碰撞/暴力成本）。

    兼容说明：历史签发的是 16 位 hex。为避免灰度期"库里/前端缓存的旧链接集体 403"，
    verify 系列函数会额外比对一次 16 位旧值（见 _match）。前端重新拉一次列表即会拿到
    新格式链接；确认线上无旧链接残留后，把 SIGNED_URL_LEGACY_SIG_WIDTH 置空即可关闭兼容。
    """
    return _digest(*parts)[:32]


def _legacy_mac(*parts: str) -> str:
    width = (os.getenv("SIGNED_URL_LEGACY_SIG_WIDTH") or "16").strip()
    if not width.isdigit() or int(width) <= 0:
        return ""
    return _digest(*parts)[: int(width)]


def _match(expected: str, old_expected: str, sig: str) -> bool:
    if hmac.compare_digest(expected, sig):
        return True
    if old_expected and hmac.compare_digest(old_expected, sig):
        return True
    return False


# ---------------- 整数 id（图片 / 缩略图旧形态） ----------------

def sign(rid: int) -> str:
    """对整数 id 生成签名。注意：不含时效，仅用于同一进程生命周期内的短链拼装。"""
    return _mac(str(int(rid)))


def verify(rid: int, sig: Optional[str]) -> bool:
    if not sig:
        return False
    try:
        return _match(sign(rid), _legacy_mac(str(int(rid))), sig)
    except RuntimeError:
        return False
    except Exception:
        return False


# ---------------- (kind, ident) ----------------

def sign_path(kind: str, ident: str) -> str:
    return _mac(str(kind), str(ident))


def verify_path(kind: str, ident: str, sig: Optional[str]) -> bool:
    if not sig:
        return False
    try:
        return _match(sign_path(kind, ident), _legacy_mac(str(kind), str(ident)), sig)
    except RuntimeError:
        return False
    except Exception:
        return False


# ---------------- 带过期的资源签名（推荐用法） ----------------

def _resolve_ttl(ttl: Optional[int]) -> int:
    """ttl 语义（本轮收紧）：
        None  -> 用默认时效（SIGNED_URL_DEFAULT_TTL / security 配置，缺省 7 天）
        > 0   -> 按传入秒数
        <= 0  -> **不再表示永久**，等同默认时效。历史调用方普遍传 ttl=0 表达"永久"，
                 这正是"能力型 URL 一旦外泄无法吊销"的根因；这里统一回落到默认时效。
                 历史已签发的 exp=0 老链接由 verify_url 的灰度开关继续放行。
    """
    if ttl is None:
        return _default_ttl_seconds()
    ttl = int(ttl)
    if ttl > 0:
        return ttl
    return _default_ttl_seconds()


def sign_url(kind: str, ident: str, ttl: Optional[int] = None) -> str:
    """返回可直接拼到 URL 后的查询串："exp=<ts>&sig=<hex>"。永不过期的 URL 不再产生。"""
    exp = int(time.time()) + _resolve_ttl(ttl)
    return f"exp={exp}&sig={_mac(str(kind), str(ident), str(exp))}"


def verify_url(kind: str, ident: str, exp: Optional[str], sig: Optional[str]) -> bool:
    if not sig:
        return False
    try:
        exp_i = int(exp or "0")
    except (TypeError, ValueError):
        return False

    now = int(time.time())
    if exp_i <= 0:
        # 无 exp / exp=0：只有"灰度期兼容历史永久链接"打开时才继续校验签名本身
        if not _allow_legacy_permanent():
            return False
    elif exp_i < now:
        return False

    try:
        return _match(
            _mac(str(kind), str(ident), str(exp_i)),
            _legacy_mac(str(kind), str(ident), str(exp_i)),
            sig,
        )
    except RuntimeError:
        return False
    except Exception:
        return False


# ---------------- URL 构造（唯一出口，避免各处手拼） ----------------

def thumb_url(file_id: str, ttl: Optional[int] = None) -> str:
    """上传文件缩略图地址（公开接口，靠签名 + 128bit file_id 保护）。"""
    return f"/agent/v1/upload/thumb/{file_id}?{sign_url('upload_thumb', file_id, ttl)}"


def file_url(kind: str, base: str, ident: str, ttl: Optional[int] = None) -> str:
    """给某个下载地址追加签名，
    如 file_url("ppt", "/agent/v1/ppt/download/{sid}/{fn}", "{sid}/{fn}")。"""
    return f"{base}?{sign_url(kind, ident, ttl)}"


