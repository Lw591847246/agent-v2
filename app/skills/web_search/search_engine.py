# app/application/skills/search_skill/service.py
"""
网络搜索服务：
  - Bing（cn.bing.com，国内主路径）
  - DuckDuckGo HTML（备选）
  - 搜狗（国内兜底）
  - SearXNG（自托管，可选）
  - 进程内 LRU + TTL 缓存
  - 熔断：连续失败 3 次 → 暂停 5 分钟
"""
import hashlib
import re
import threading
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config.loader import config
from app.skills.web_search.realtime_rules import load_realtime_rules
from app.core.logger import logger


# ============================================================
# 缓存（LRU + TTL，线程安全）
# ============================================================
_cache: Dict[str, Tuple[List[dict], float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600
_CACHE_MAX = 128


def _cache_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode("utf-8")).hexdigest()


def _cache_get(query: str) -> Optional[List[dict]]:
    key = _cache_key(query)
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        value, ts = item
        if time.time() - ts > _CACHE_TTL:
            _cache.pop(key, None)
            return None
        return list(value)


def _cache_set(query: str, value: List[dict]) -> None:
    key = _cache_key(query)
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            try:
                oldest = min(_cache.items(), key=lambda kv: kv[1][1])[0]
                _cache.pop(oldest, None)
            except Exception:
                _cache.clear()
        _cache[key] = (list(value), time.time())


# ============================================================
# 熔断器：连续失败 N 次 → 暂停 T 秒
# ============================================================
_circuit: Dict[str, dict] = {}
_circuit_lock = threading.Lock()
_FAIL_THRESHOLD = 3
_COOLDOWN_SEC = 300


def _circuit_allow(engine: str) -> bool:
    with _circuit_lock:
        state = _circuit.get(engine)
        if not state:
            return True
        if state["fail_count"] < _FAIL_THRESHOLD:
            return True
        if time.time() - state["last_fail_at"] > _COOLDOWN_SEC:
            # 冷却结束，重置
            _circuit[engine] = {"fail_count": 0, "last_fail_at": 0}
            return True
        return False


def _circuit_record(engine: str, success: bool) -> None:
    with _circuit_lock:
        state = _circuit.setdefault(engine, {"fail_count": 0, "last_fail_at": 0})
        if success:
            state["fail_count"] = 0
            state["last_fail_at"] = 0
        else:
            state["fail_count"] += 1
            state["last_fail_at"] = time.time()
            if state["fail_count"] >= _FAIL_THRESHOLD:
                logger.warning(
                    f"[Search] {engine} 熔断，{_COOLDOWN_SEC}s 内不再尝试"
                )


# ============================================================
# HTTP 配置
# ============================================================
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 6   # 从 15 秒降到 6 秒，Bing 通常 1-2 秒返回


# ============================================================
# Bing 搜索（主路径，国内稳定）
# ============================================================
def _search_bing(query: str, max_results: int = 8) -> List[dict]:
    """爬 cn.bing.com（国内版），比 www.bing.com 稳定。"""
    if not _circuit_allow("bing"):
        return []

    url = f"https://cn.bing.com/search?q={quote(query)}&ensearch=0"
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            follow_redirects=True,
        ) as c:
            r = c.get(url)

        if r.status_code != 200:
            logger.warning(f"[Search] Bing 返回 HTTP {r.status_code}")
            _circuit_record("bing", False)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        results: List[dict] = []

        for item in soup.select("li.b_algo"):
            if len(results) >= max_results:
                break

            a_tag = item.select_one("h2 a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "").strip()

            # 摘要：优先 .b_caption > p，兜底整段
            snippet_tag = item.select_one(".b_caption p") or item.select_one(".b_lineclamp")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            if not snippet:
                caption = item.select_one(".b_caption")
                snippet = caption.get_text(strip=True) if caption else ""

            if title and href and href.startswith("http"):
                results.append({
                    "title": title,
                    "url": href,
                    "snippet": snippet,
                    "source": "bing",
                })

        logger.info(f"[Search] Bing 返回 {len(results)} 条")
        _circuit_record("bing", True)
        return results

    except httpx.TimeoutException:
        logger.warning("[Search] Bing 超时")
        _circuit_record("bing", False)
        return []
    except Exception as e:
        logger.warning(f"[Search] Bing 失败: {e}")
        _circuit_record("bing", False)
        return []


# ============================================================
# DuckDuckGo（备选，海外环境可用）
# ============================================================
def _extract_ddg_url(href: str) -> str:
    if not href:
        return ""
    try:
        if href.startswith("//"):
            full = "https:" + href
        elif href.startswith("/"):
            full = "https://duckduckgo.com" + href
        else:
            return href
        parsed = urlparse(full)
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(uddg) if uddg else href
    except Exception:
        return href


def _search_duckduckgo(query: str, max_results: int = 8) -> List[dict]:
    if not _circuit_allow("ddg"):
        return []

    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT, headers={"User-Agent": _USER_AGENT},
        ) as c:
            r = c.get(url, follow_redirects=True)

        if r.status_code != 200:
            _circuit_record("ddg", False)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        results: List[dict] = []
        for item in soup.select(".result"):
            if len(results) >= max_results:
                break
            a_tag = item.select_one(".result__a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = _extract_ddg_url(a_tag.get("href", ""))
            snippet_tag = item.select_one(".result__snippet")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            if title and href:
                results.append({"title": title, "url": href, "snippet": snippet, "source": "duckduckgo"})

        logger.info(f"[Search] DuckDuckGo 返回 {len(results)} 条")
        _circuit_record("ddg", True)
        return results

    except httpx.TimeoutException:
        logger.warning("[Search] DuckDuckGo 超时")
        _circuit_record("ddg", False)
        return []
    except Exception as e:
        logger.warning(f"[Search] DuckDuckGo 失败: {e}")
        _circuit_record("ddg", False)
        return []


# ============================================================
# 搜狗（国内兜底）
# ============================================================
def _search_sogou(query: str, max_results: int = 8) -> List[dict]:
    if not _circuit_allow("sogou"):
        return []

    url = f"https://www.sogou.com/web?query={quote(query)}"
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            follow_redirects=True,
        ) as c:
            r = c.get(url)

        if r.status_code != 200:
            _circuit_record("sogou", False)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        results: List[dict] = []
        # 搜狗结果块：div.vrwrap 里 h3 a
        for item in soup.select("div.vrwrap"):
            if len(results) >= max_results:
                break
            a_tag = item.select_one("h3 a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            # 搜狗链接是跳转链接 /link?url=xxx，需从跳转拿真实 URL
            if href.startswith("/link"):
                href = "https://www.sogou.com" + href
            snippet_tag = item.select_one(".text-layout") or item.select_one(".str-text-info")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            if title and href:
                results.append({"title": title, "url": href, "snippet": snippet, "source": "sogou"})

        logger.info(f"[Search] 搜狗返回 {len(results)} 条")
        _circuit_record("sogou", True)
        return results

    except httpx.TimeoutException:
        logger.warning("[Search] 搜狗超时")
        _circuit_record("sogou", False)
        return []
    except Exception as e:
        logger.warning(f"[Search] 搜狗失败: {e}")
        _circuit_record("sogou", False)
        return []


# ============================================================
# SearXNG（自托管，可选）
# ============================================================
def _search_searxng(query: str, max_results: int = 8) -> List[dict]:
    base = (config.get("services.searxng.base_url") or "").rstrip("/")
    if not base or not _circuit_allow("searxng"):
        return []

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as c:
            r = c.get(
                f"{base}/search",
                params={"q": query, "format": "json", "categories": "general"},
            )
        if r.status_code != 200:
            _circuit_record("searxng", False)
            return []

        data = r.json()
        results: List[dict] = []
        for item in (data.get("results") or [])[:max_results]:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            content = (item.get("content") or "").strip()
            if title and url:
                results.append({"title": title, "url": url, "snippet": content, "source": "searxng"})
        logger.info(f"[Search] SearXNG 返回 {len(results)} 条")
        _circuit_record("searxng", True)
        return results

    except Exception as e:
        logger.warning(f"[Search] SearXNG 失败: {e}")
        _circuit_record("searxng", False)
        return []


# ============================================================
# 主入口：按优先级依次尝试
# ============================================================
_ENGINE_ORDER = ("bing", "ddg", "sogou", "searxng")
_ENGINE_FUNCS = {
    "bing": _search_bing,
    "ddg": _search_duckduckgo,
    "sogou": _search_sogou,
    "searxng": _search_searxng,
}


def web_search(query: str, max_results: int = 8) -> List[dict]:
    """
    分层搜索：缓存 → Bing → DuckDuckGo → 搜狗 → SearXNG。
    每个引擎有熔断保护，避免反复超时拖累响应。
    """
    query = (query or "").strip()
    if not query:
        return []

    cached = _cache_get(query)
    if cached is not None:
        logger.info(f"[Search] 缓存命中: {query[:40]}")
        return cached

    for engine in _ENGINE_ORDER:
        if not _circuit_allow(engine):
            logger.info(f"[Search] {engine} 熔断中，跳过")
            continue
        func = _ENGINE_FUNCS[engine]
        results = func(query, max_results)
        if results:
            _cache_set(query, results)
            return results

    logger.warning(f"[Search] 全部引擎失败: {query[:40]}")
    return []


def format_for_llm(results: List[dict], max_chars: int = 3000) -> str:
    """
    把搜索结果格式化成 LLM 可读的上下文。
    （保持向后兼容，WebSearchSkill 仍用这个做降级）
    """
    if not results:
        return ""
    lines: List[str] = []
    total = 0
    for i, r in enumerate(results, 1):
        block = (
            f"[{i}] {r.get('title', '')}\n"
            f"来源：{r.get('url', '')}\n"
            f"摘要：{r.get('snippet', '')}\n"
        )
        if total + len(block) > max_chars:
            break
        lines.append(block)
        total += len(block)
    return "\n".join(lines)


# ============================================================
# 实时性判断
# ============================================================
_RT_KEYWORDS, _RT_PATTERNS = load_realtime_rules()
_RT_COMPILED: List[re.Pattern] = []
for _p in _RT_PATTERNS:
    try:
        _RT_COMPILED.append(re.compile(_p))
    except re.error as e:
        logger.warning(f"[Search] 非法正则 {_p!r}: {e}")


def _llm_judge_needs_search(text: str) -> bool:
    try:
        from app.infrastructure.clients.model_manager import model_manager
        prompt = (
            "判断用户问题是否需要联网获取实时信息。只回答 yes 或 no，不要解释。\n\n"
            "需要联网的典型场景：新闻、股价、汇率、天气、比赛比分、"
            "最新发布、近期事件、某公司/某人的最新动态。\n"
            "不需要联网的典型场景：闲聊、常识问答、写故事、写代码、翻译、"
            "纯数学/逻辑推理、技术问答。\n\n"
            f"用户问题：{text}\n"
            "是否需要联网（yes/no）："
        )
        raw = model_manager.chat(
            task_type="intent",
            messages=[{"role": "user", "content": prompt}],
        )
        ans = (raw or "").strip().lower()
        return ans.startswith("yes") or "是" in ans[:5]
    except Exception as e:
        logger.warning(f"[Search] LLM 判断失败: {e}")
        return False