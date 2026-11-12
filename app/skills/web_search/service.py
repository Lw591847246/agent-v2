"""WebSearch 业务：query rewrite → 搜索 → 抓正文 → 相关性预筛 → LLM 整合。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, Iterator, List, Optional

from app.config.loader import config
from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.web_search.fetcher import fetch_batch, format_fetched
from app.skills.web_search.prompts import SYSTEM_PROMPT
from app.skills.web_search.query_rewriter import rewrite
from app.skills.web_search.search_engine import format_for_llm, web_search


# ============================================================
# 核心词抽取（用于相关性预筛）
# ============================================================
_TIME_STRIP_PATTERNS = [
    r"大后天", r"后天", r"明天", r"明日", r"今天", r"今日", r"昨天", r"昨日",
    r"下周[一二三四五六日天]?", r"这周", r"本周",
    r"下个?月", r"这个?月", r"本月",
    r"明年", r"今年", r"去年",
    r"(?:最近|近|过去)\s*[半一二三四五六七八九十]+\s*[年月周天日]",
    r"最近|近期|现在|当前|最新|实时",
]

_FILLER_STRIP = (
    "有没有", "是否有", "怎么样", "如何", "什么", "多少", "请问", "帮我",
    "查一下", "查查", "关于", "相关的", "的新闻", "吧", "吗", "呢",
    "是", "在", "有", "和", "与", "我", "人", "的", "得", "地",
)

_STOPWORDS = set(_TIME_STRIP_PATTERNS) | set(_FILLER_STRIP) | {
    "新闻", "事件", "消息", "情况", "信息",
}


def _extract_core_terms(text: str) -> List[str]:
    if not text:
        return []
    q = text
    for pat in _TIME_STRIP_PATTERNS:
        q = re.sub(pat, " ", q)
    for filler in _FILLER_STRIP:
        q = q.replace(filler, " ")
    q = re.sub(r"[，。！!？?；;：:\d]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if not q:
        return []

    raw_segments = [s.strip() for s in q.split(" ") if s.strip()]
    cores: List[str] = []
    for seg in raw_segments:
        if seg in _STOPWORDS:
            continue
        if 2 <= len(seg) <= 4:
            if seg not in cores:
                cores.append(seg)
            if len(seg) == 4:
                for sub in (seg[:2], seg[2:]):
                    if sub not in _STOPWORDS and sub not in cores:
                        cores.append(sub)
        elif len(seg) > 4:
            for i in range(len(seg) - 1):
                sub = seg[i:i + 2]
                if sub not in _STOPWORDS and sub not in cores:
                    cores.append(sub)
    return cores


def _is_content_relevant(content: str, core_terms: List[str]) -> bool:
    if not core_terms:
        return True
    head = content[:500].lower()
    return any(term.lower() in head for term in core_terms)


def _build_current_time_str() -> str:
    now = datetime.now()
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    tomorrow = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)).strftime("%Y年%m月%d日")
    return (
        f"今天：{now.strftime('%Y年%m月%d日')} 星期{weekdays[now.weekday()]} "
        f"{now.strftime('%H:%M')}；明天：{tomorrow}"
    )


class WebSearchService:

    def search_and_answer(
        self, text: str, history: List[Dict] = None,
    ) -> Dict:
        """
        执行完整流程，返回 {messages, inject_mode}。
        调用方拿到 messages 后用 chat 或 stream 调 LLM。
        """
        if not text or not text.strip():
            return {"messages": [], "inject_mode": "empty", "error": "请输入要搜索的内容"}

        # 1. query rewrite
        search_query = rewrite(text)
        logger.info(f"【WebSearch】原始={text!r} 查询词={search_query!r}")

        # 2. 搜索
        max_results = int(config.get("services.search.max_results", 5))
        results = web_search(search_query, max_results=max_results)
        logger.info(f"【WebSearch】搜索返回 {len(results)} 条")

        # 3. 抓正文
        fetched = []
        core_terms = _extract_core_terms(text)
        logger.info(f"【WebSearch】核心词={core_terms}")

        if results:
            urls = [r.get("url", "") for r in results if r.get("url")]
            fetched = fetch_batch(urls)
            logger.info(f"【WebSearch】抓到 {len(fetched)} 个网页正文")

            enable_prefilter = config.get("services.search.fetcher.relevance_prefilter", True)
            if enable_prefilter and core_terms and fetched:
                before = len(fetched)
                filtered = [
                    f for f in fetched
                    if _is_content_relevant(f.get("content", ""), core_terms)
                ]
                dropped = before - len(filtered)
                if dropped > 0:
                    logger.info(f"【WebSearch】相关性预筛丢弃 {dropped} 个无关正文")
                if not filtered:
                    logger.warning(f"【WebSearch】预筛丢弃全部正文，回退到全部 {before} 个")
                    filtered = fetched
                fetched = filtered

        # 4. 构造 messages
        messages = self._build_messages(text, history or [])
        current_time_str = _build_current_time_str()

        if fetched:
            context = format_fetched(fetched, max_total_chars=5000)
            user_content = (
                f"【当前时间】{current_time_str}\n\n"
                f"【网页正文（实时抓取）】\n{context}\n\n"
                f"【用户问题】\n{text}\n\n"
                "请基于上述网页正文回答：\n"
                "1. 从正文中提取具体数据/事实\n"
                "2. ⚠️ 注意：正文里的日期可能是旧的（如'11月5日'），"
                "请以【当前时间】为准推算真实日期\n"
                "3. 如果正文与问题不相关，请直接说'搜索结果不相关'\n"
                "4. 末尾注明信息来源"
            )
            inject_mode = "正文"
        elif results:
            context = format_for_llm(results, max_chars=1500)
            user_content = (
                f"【当前时间】{current_time_str}\n\n"
                f"【搜索结果摘要】\n{context}\n\n"
                f"【用户问题】\n{text}\n\n"
                "请基于上述搜索结果回答：\n"
                "1. 只使用本次搜索结果中的信息\n"
                "2. ⚠️ 摘要里的日期可能过期，请以【当前时间】为准\n"
                "3. 如果搜索结果与问题不相关，请直接说'搜索结果不相关'\n"
                "4. 摘要信息有限时，说明数据可能不完整"
            )
            inject_mode = "摘要"
        else:
            user_content = (
                f"【当前时间】{current_time_str}\n\n"
                f"【实时搜索无结果】\n"
                f"【用户问题】\n{text}\n\n"
                "请基于你已有的知识回答。\n"
                "如果问的是具体实时数据（天气、股价、新闻），"
                "请说明你无法获取实时数据，建议用户通过对应渠道查询。"
            )
            inject_mode = "降级"

        # 替换最后一条 user
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                messages[i] = {"role": "user", "content": user_content}
                break
        else:
            messages.append({"role": "user", "content": user_content})

        logger.info(f"【WebSearch】注入模式：{inject_mode}")
        return {"messages": messages, "inject_mode": inject_mode}

    def chat(self, text: str, history: List[Dict] = None) -> str:
        info = self.search_and_answer(text, history)
        if info.get("error"):
            return info["error"]
        try:
            return ollama_client.chat(
                service="chat", model_key="chat",
                messages=info["messages"],
            )
        except Exception as e:
            logger.error(f"【WebSearch】chat 失败: {e}")
            return f"搜索失败：{e}"

    def chat_stream(self, text: str, history: List[Dict] = None) -> Iterator[str]:
        info = self.search_and_answer(text, history)
        if info.get("error"):
            yield info["error"]
            return
        try:
            yield from ollama_client.stream(
                service="chat", model_key="chat",
                messages=info["messages"],
            )
        except Exception as e:
            logger.error(f"【WebSearch】stream 失败: {e}")
            yield f"搜索失败：{e}"

    @staticmethod
    def _build_messages(text: str, history: List[Dict]) -> List[Dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for h in (history or [])[-5:]:
            role = h.get("role")
            content = (h.get("content") or "").strip()
            if not content or role not in ("user", "assistant"):
                continue
            if len(content) > 300:
                content = content[:300] + "...(截断)"
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": text})
        return messages


web_search_service = WebSearchService()