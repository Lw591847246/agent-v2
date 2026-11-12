# app/application/skills/search_skill/fetcher.py
"""
网页正文抓取：
  - 并发抓取（默认 3 个 URL）
  - 正文提取（trafilatura 优先，BeautifulSoup 兜底）
  - 进程内 LRU 缓存（1 小时）
  - 熔断保护（连续失败 5 次暂停 5 分钟）
  - 黑名单域名（视频/PDF/百科，避免垃圾内容）
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False

from app.config.loader import config
from app.core.logger import logger


# ============================================================
# 配置（可从 application.yml 覆盖）
# ============================================================
def _cfg(key: str, default):
    return config.get(f"services.search.fetcher.{key}", default)


_MAX_FETCH_URLS = int(_cfg("max_urls", 3))
_MAX_CHARS_PER_URL = int(_cfg("max_chars_per_url", 2000))
_FETCH_TIMEOUT = int(_cfg("timeout", 8))
_MAX_CONCURRENT = int(_cfg("max_concurrent", 3))
_CACHE_TTL = int(_cfg("cache_ttl", 3600))
_CIRCUIT_THRESHOLD = int(_cfg("circuit_threshold", 5))
_CIRCUIT_COOLDOWN = int(_cfg("circuit_cooldown", 300))


# ============================================================
# 缓存
# ============================================================
_fetch_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


# ============================================================
# 熔断
# ============================================================
_circuit_fail = 0
_circuit_last_fail = 0.0
_circuit_lock = threading.Lock()


def _circuit_allow() -> bool:
    with _circuit_lock:
        if _circuit_fail < _CIRCUIT_THRESHOLD:
            return True
        if time.time() - _circuit_last_fail > _CIRCUIT_COOLDOWN:
            return True
        return False


def _circuit_record(success: bool) -> None:
    global _circuit_fail, _circuit_last_fail
    with _circuit_lock:
        if success:
            _circuit_fail = 0
        else:
            _circuit_fail += 1
            _circuit_last_fail = time.time()


# ============================================================
# HTTP 配置
# ============================================================
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 黑名单：视频/社交/百科（这些页面没正文价值，或需要 JS 渲染）
_BLACKLIST_DOMAINS = (
    "youtube.com", "bilibili.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "tiktok.com", "douyin.com",
    "baike.baidu.com",     # 百科太泛，容易污染
    "zhihu.com/question",  # 知乎问题页需要登录
)

# 跳过的文件后缀
_SKIP_EXTENSIONS = (".pdf", ".zip", ".rar", ".mp4", ".mp3", ".jpg", ".png", ".gif", ".webp", ".exe")


def _should_fetch(url: str) -> bool:
    """判断 URL 是否值得抓取"""
    if not url or not url.startswith("http"):
        return False
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()

        if any(d in domain for d in _BLACKLIST_DOMAINS):
            return False
        if path.endswith(_SKIP_EXTENSIONS):
            return False
        return True
    except Exception:
        return False


# ============================================================
# 正文提取
# ============================================================
def _extract_with_trafilatura(html: str, url: str) -> str:
    """trafilatura 提取正文（准确率最高）"""
    if not _HAS_TRAFILATURA:
        return ""
    try:
        content = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,       # 保留表格（天气页可能有表格）
            include_images=False,
            favor_recall=True,         # 优先召回（宁可多不可少）
            url=url,
        )
        return (content or "").strip()
    except Exception as e:
        logger.debug(f"[Fetcher] trafilatura 失败 {url}: {e}")
        return ""


def _extract_with_bs4(html: str) -> str:
    """BeautifulSoup 启发式兜底"""
    try:
        soup = BeautifulSoup(html, "html.parser")
        # 移除噪音标签
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
            tag.decompose()

        # 优先找语义化容器
        for selector in ("article", "main", "[role=main]", ".article-content", ".post-content", ".content", "#content"):
            node = soup.select_one(selector)
            if node:
                text = node.get_text(separator="\n", strip=True)
                if len(text) > 100:
                    return text

        # 兜底：body 全文
        body = soup.find("body")
        if body:
            return body.get_text(separator="\n", strip=True)
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        logger.debug(f"[Fetcher] bs4 提取失败: {e}")
        return ""


def _extract_content(html: str, url: str) -> str:
    """正文提取：trafilatura 优先，bs4 兜底"""
    content = _extract_with_trafilatura(html, url)
    if content and len(content) > 100:
        return content
    return _extract_with_bs4(html)


def _extract_title(html: str) -> str:
    """提取页面标题"""
    try:
        soup = BeautifulSoup(html[:8000], "html.parser")
        t = soup.find("title")
        if t:
            return t.get_text(strip=True)[:200]
        # 兜底：og:title
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"][:200]
    except Exception:
        pass
    return ""


def _clean_text(text: str, max_chars: int) -> str:
    """清理正文：去空行、去短行（导航残留）、去重复行、截断"""
    if not text:
        return ""

    lines = [line.strip() for line in text.split("\n")]
    # 去空行和极短行
    lines = [line for line in lines if line and len(line) > 2]

    # 相邻行去重
    deduped = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)

    text = "\n".join(deduped)

    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text


# ============================================================
# 单 URL 抓取
# ============================================================
def fetch_one(url: str, max_chars: int = _MAX_CHARS_PER_URL) -> Dict:
    """抓取单个 URL，返回 {url, title, content, success, reason}"""
    if not _should_fetch(url):
        return {"url": url, "title": "", "content": "", "success": False, "reason": "skip"}

    # 查缓存
    with _cache_lock:
        cached = _fetch_cache.get(url)
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

    try:
        with httpx.Client(
            timeout=_FETCH_TIMEOUT,
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
        ) as c:
            r = c.get(url)

        if r.status_code != 200:
            return {"url": url, "title": "", "content": "", "success": False, "reason": f"http_{r.status_code}"}

        # 编码处理（httpx 已自动处理，但某些站点会乱码，保险起见）
        html = r.text
        if not html or len(html) < 200:
            return {"url": url, "title": "", "content": "", "success": False, "reason": "empty_html"}

        title = _extract_title(html)
        content = _extract_content(html, url)
        content = _clean_text(content, max_chars)

        if len(content) < 100:
            return {"url": url, "title": title, "content": content, "success": False, "reason": "too_short"}

        result = {"url": url, "title": title, "content": content, "success": True, "reason": ""}
        with _cache_lock:
            _fetch_cache[url] = (result, time.time())
        return result

    except httpx.TimeoutException:
        return {"url": url, "title": "", "content": "", "success": False, "reason": "timeout"}
    except httpx.ConnectError:
        return {"url": url, "title": "", "content": "", "success": False, "reason": "connect_error"}
    except Exception as e:
        return {"url": url, "title": "", "content": "", "success": False, "reason": str(e)[:100]}


# ============================================================
# 批量抓取
# ============================================================
def fetch_batch(urls: List[str], max_urls: int = None) -> List[Dict]:
    """
    并发抓取多个 URL 的正文。
    返回成功抓到的结果列表（可能少于 max_urls）。
    """
    if not urls:
        return []

    if max_urls is None:
        max_urls = _MAX_FETCH_URLS

    if not _circuit_allow():
        logger.info("[Fetcher] 熔断中，跳过抓取")
        return []

    # 过滤 + 截断
    valid_urls = []
    for u in urls:
        if _should_fetch(u) and u not in valid_urls:
            valid_urls.append(u)
        if len(valid_urls) >= max_urls:
            break

    if not valid_urls:
        return []

    results = []
    fail_count = 0

    try:
        with ThreadPoolExecutor(max_workers=min(_MAX_CONCURRENT, len(valid_urls))) as executor:
            future_to_url = {executor.submit(fetch_one, u): u for u in valid_urls}
            for future in as_completed(future_to_url, timeout=_FETCH_TIMEOUT + 3):
                try:
                    r = future.result()
                    if r["success"]:
                        results.append(r)
                    else:
                        fail_count += 1
                        logger.debug(f"[Fetcher] {r['url']} 失败: {r['reason']}")
                except Exception as e:
                    fail_count += 1
                    logger.debug(f"[Fetcher] 任务异常: {e}")
    except Exception as e:
        logger.warning(f"[Fetcher] 并发抓取异常: {e}")

    # 全部失败 → 记一次熔断
    if results:
        _circuit_record(True)
    else:
        _circuit_record(False)

    logger.info(f"[Fetcher] 抓取完成: {len(results)}/{len(valid_urls)} 成功")
    return results


def format_fetched(fetched: List[Dict], max_total_chars: int = 5000) -> str:
    """把抓到的正文格式化成 LLM 可读的文本"""
    if not fetched:
        return ""
    lines = []
    total = 0
    for i, item in enumerate(fetched, 1):
        block = (
            f"[{i}] {item.get('title', '')}\n"
            f"来源：{item.get('url', '')}\n"
            f"正文：\n{item.get('content', '')}\n\n"
        )
        if total + len(block) > max_total_chars:
            break
        lines.append(block)
        total += len(block)
    return "\n".join(lines)