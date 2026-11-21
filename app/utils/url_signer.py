# app/utils/url_signer.py
"""站内下载/媒体 URL 的签名器（单一实现）。

为什么需要它：
`<a href>` / `<img src>` / `<video src>` 是浏览器直连，带不了 Authorization 头。
与其让前端逐个改造，不如**在后端出口统一把 URL 换成带签名的版本**：
    - 消息接口 messages：历史消息
    - job 状态接口：轮询得到的 parts/outputs
    - SSE 推流：即时卡片与产物
这样前端一行都不用改。

签名种类必须与各下载端点里的 verify 调用严格一致：
    image / thumb       -> ?sig=<hmac(message_id)>          （沿用旧签名格式）
    upload_thumb        -> exp/sig（kind=upload_thumb, ident=file_id）
    job_video           -> ident=task_id
    ppt / word          -> ident=f"{session_id}/{filename}"
    storyboard          -> ident=task_id
    skill / custom_avatar / liveportrait -> ident=原始 path 查询参数值
    tts                 -> ident=filename
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Optional, Tuple

from app.utils.signed_url import sign, sign_url

_KINDS_BY_PREFIX = ("ppt", "word")


def parse_kind_ident(path: str, query: dict) -> Optional[Tuple[str, str]]:
    """从站内路径 + 查询参数解析出 (kind, ident)；不认识的返回 None。"""
    if not path or not path.startswith("/agent/v1/"):
        return None

    if path.startswith("/agent/v1/job/") and path.endswith("/video"):
        task_id = path[len("/agent/v1/job/"):-len("/video")].strip("/")
        return ("job_video", task_id) if task_id else None

    for name in _KINDS_BY_PREFIX:
        prefix = f"/agent/v1/{name}/download/"
        if path.startswith(prefix):
            rest = path[len(prefix):]
            if "/" not in rest:
                return None
            session_id, filename = rest.split("/", 1)
            return (name, f"{session_id}/{filename}")

    if path == "/agent/v1/storyboard/download":
        task_id = (query or {}).get("task_id", "")
        return ("storyboard", task_id) if task_id else None

    if path in ("/agent/v1/skill/download", "/agent/v1/custom_avatar/download"):
        p = (query or {}).get("path", "")
        if not p:
            return None
        kind = "skill" if path.endswith("skill/download") else "custom_avatar"
        return (kind, p)

    if path == "/agent/v1/liveportrait/download":
        q = query or {}
        if q.get("path"):
            return ("liveportrait", q["path"])
        if q.get("job_id"):
            return ("liveportrait_job", q["job_id"])
        return None

    if path.startswith("/agent/v1/tts/"):
        filename = path[len("/agent/v1/tts/"):]
        return ("tts", filename) if filename and "/" not in filename else None

    if path.startswith("/agent/v1/upload/thumb/"):
        file_id = path[len("/agent/v1/upload/thumb/"):]
        return ("upload_thumb", file_id) if file_id and "/" not in file_id else None

    return None


# 【本轮修复 · 高危 2】ttl 默认值从 0（等价于"永久有效"）改为 None（用默认时效 7 天）。
# 所有自动签名出口（消息 / job / SSE）从此不再产生永久能力型 URL。
def sign_internal_url(url: str, ttl: Optional[int] = None) -> str:
    """把一条站内 URL 换成带签名的版本；已签名或不认识的 URL 原样返回。"""
    if not url or not isinstance(url, str):
        return url
    if not url.startswith("/agent/v1/"):
        return url
    if "sig=" in url:
        return url
    # 模板串（如 /agent/v1/job/{task_id}/video）不要签：前端替换 {task_id} 之后
    # 签名会对不上，反而把可用的 URL 变成 403/404。这类卡片应改为逐任务签名。
    if "{" in url or "}" in url:
        return url

    path, _, raw_query = url.partition("?")

    # 图片/缩略图沿用旧格式：?sig=<hmac(message_id)>
    if path.startswith("/agent/v1/image/") or path.startswith("/agent/v1/thumb/"):
        rid = path.rsplit("/", 1)[-1]
        if rid.isdigit():
            return f"{path}?sig={sign(int(rid))}"
        return url

    pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True)
    query = dict(pairs)
    parsed = parse_kind_ident(path, query)
    if not parsed:
        return url

    kind, ident = parsed
    sig_qs = sign_url(kind, ident, ttl)
    clean = urllib.parse.urlencode([(k, v) for k, v in pairs if k not in ("exp", "sig")])
    return f"{path}?{clean}&{sig_qs}" if clean else f"{path}?{sig_qs}"


def sign_deep(obj, ttl: Optional[int] = None):
    """递归给任意结构（dict / list / str）里的站内 URL 加签名。

    额外处理"JSON 字符串"：部分 skill 把卡片/产物整体 json.dumps 进 message.content
    （如 face_swap 的 video 消息），这里解析后递归签名再序列化回去，
    保证前端从 content 里读 video_url 时也是签名版。
    """
    if isinstance(obj, str):
        return _sign_string(obj, ttl)
    if isinstance(obj, dict):
        return {k: sign_deep(v, ttl) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sign_deep(v, ttl) for v in obj]
    return obj


def _sign_string(s: str, ttl: Optional[int]) -> str:
    if not s or "/agent/v1/" not in s:
        return s
    head = s.lstrip()[:1]
    if head in ("{", "["):
        try:
            parsed = json.loads(s)
        except Exception:
            return sign_internal_url(s, ttl)
        if isinstance(parsed, (dict, list)):
            return json.dumps(sign_deep(parsed, ttl), ensure_ascii=False)
        return s
    return sign_internal_url(s, ttl)