"""网页正文抓取。

本版加固（架构复核 P0-2）
------------------------
原来只做 `urlparse` + 域名黑名单，**不判断目标 IP**：一条搜索结果就能让 agent
去访问 `http://erp-internal:8080`、`http://192.168.1.10/api`，把内网服务探一遍。
现在：

1. 每个候选 URL 先过 `utils/net_guard.check_url()`（主机名 + DNS 解析结果都检查）；
2. **禁用 httpx 自动重定向**，改为手动跟随（最多 `max_redirects` 跳），
   每一跳都重新校验 —— 否则 302 到内网就能绕过；
3. 可用 `services.search.fetcher.block_private_ips=false` 关闭（不建议）。

保留原有能力：并发抓取、trafilatura 正文提取、bs4 兜底、LRU 缓存、熔断、黑名单。
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False

from app.config.loader import config
from app.core.logger import logger
from app.utils.net_guard import check_url


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
_BLOCK_PRIVATE_IPS = bool(_cfg("block_private_ips", True))
_MAX_REDIRECTS = int(_cfg("max_redirects", 3))


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


def _url_allowed(url: str) -> tuple:
    """域名黑名单 + 私网/SSRF 校验。返回 (ok, reason)。"""
    if not url or not url.startswith("http"):
        return False, "scheme"
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "bad_url"

    domain = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()

    if any(d in domain for d in _BLACKLIST_DOMAINS):
        return False, "blacklist"
    if path.endswith(_SKIP_EXTENSIONS):
        return False, "ext"

    if _BLOCK_PRIVATE_IPS:
        ok, reason = check_url(url)
        if not ok:
            return False, reason
    return True, ""


def _should_fetch(url: str) -> bool:
    """判断 URL 是否值得抓取"""
    return _url_allowed(url)[0]


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
            include_tables=True,
            include_images=False,
            favor_recall=True,
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
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
            tag.decompose()

        for selector in ("article", "main", "[role=main]", ".article-content", ".post-content", ".content", "#content"):
            node = soup.select_one(selector)
            if node:
                text = node.get_text(separator="\n", strip=True)
                if len(text) > 100:
                    return text

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
    lines = [line for line in lines if line and len(line) > 2]

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
def _get_following_redirects(client: httpx.Client, url: str) -> httpx.Response:
    """手动跟随重定向，每一跳都重新做 SSRF 校验。"""
    current = url
    for hop in range(_MAX_REDIRECTS + 1):
        ok, reason = _url_allowed(current)
        if not ok:
            raise httpx.ConnectError(f"blocked_url:{reason}")
        r = client.get(current)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            nxt = urljoin(current, r.headers["location"])
            logger.debug(f"[Fetcher] 重定向 {current} -> {nxt}")
            current = nxt
            continue
        return r
    raise httpx.TooManyRedirects(f">{_MAX_REDIRECTS} redirects")


def fetch_one(url: str, max_chars: int = _MAX_CHARS_PER_URL) -> Dict:
    """抓取单个 URL，返回 {url, title, content, success, reason}"""
    ok, reason = _url_allowed(url)
    if not ok:
        logger.info(f"[Fetcher] 拒绝抓取 {url} ({reason})")
        return {"url": url, "title": "", "content": "", "success": False, "reason": f"skip:{reason}"}

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
            follow_redirects=False,          # ⭐ 手动跟随，保证每跳都校验
        ) as c:
            r = _get_following_redirects(c, url)

        if r.status_code != 200:
            return {"url": url, "title": "", "content": "", "success": False, "reason": f"http_{r.status_code}"}

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
    except httpx.TooManyRedirects:
        return {"url": url, "title": "", "content": "", "success": False, "reason": "too_many_redirects"}
    except httpx.ConnectError as e:
        # blocked_url 也走这里（内部抛出 ConnectError 包装）
        return {"url": url, "title": "", "content": "", "success": False, "reason": str(e)[:100]}
    except Exception as e:
        return {"url": url, "title": "", "content": "", "success": False, "reason": str(e)[:100]}


# ============================================================
# 批量抓取
# ============================================================
def fetch_batch(urls: List[str], max_urls: int = None) -> List[Dict]:
    """并发抓取多个 URL 的正文。返回成功抓到的结果列表。"""
    if not urls:
        return []

    if max_urls is None:
        max_urls = _MAX_FETCH_URLS

    if not _circuit_allow():
        logger.info("[Fetcher] 熔断中，跳过抓取")
        return []

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