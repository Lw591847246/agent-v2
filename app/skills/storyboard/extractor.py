# app/skills/storyboard/extractor.py
"""故事内容提取器。"""
from __future__ import annotations

import re
from typing import List, Optional

from app.core.logger import logger


_INSTRUCTION_PREFIX_RE = re.compile(
    r'^(?:'
    r'把(?:下面|以下|这|那|我)?(?:的)?(?:这|那|一段|一篇|个)?(?:故事)'
    r'(?:做成|转换成|改写成|变成|制作成)(?:一个|一段)?'
    r'(?:漫剧|动漫|视频|短剧|有声绘本|广播剧|作品)?[，,。：:]\s*|'
    r'(?:做成|转换成|改写成|变成|制作成)(?:一个|一段)?'
    r'(?:漫剧|动漫|视频|短剧|有声绘本|广播剧|作品)[，,。：:]\s*|'
    r'帮我|请|麻烦|能不能|可以|'
    r'把(?:下面|以下)(?:的|这段|这个)?(?:内容|故事|文字)[，,]?\s*|'
    r'把(?:这段|这个|这)(?:内容|故事|文字)[，,]?\s*|'
    r'(?:下面|以下)(?:是|的)(?:一个|一段|这个)?(?:故事)?[，,]?\s*|'
    r'(?:这|那)(?:是|就是)(?:一个|一段)?故事[，,]?\s*'
    r')',
    re.IGNORECASE,
)

_ACTION_VERBS = ("做成", "转成", "生成", "转换为", "变成", "改造成", "改写成", "加工成", "转为")
_INSTRUCTION_PREFIX_WORDS = ("帮我", "请", "麻烦", "能不能", "可以")

_INSTRUCTION_SUFFIX_PATTERNS = [
    re.compile(
        r'[。！？!?]\s*'
        r'(?:' + '|'.join(_INSTRUCTION_PREFIX_WORDS) + r')\s*'
        r'把.{0,50}?'
        r'(?:' + '|'.join(_ACTION_VERBS) + r')'
        r'.*$',
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r'[。！？!?]\s*'
        r'把.{0,50}?'
        r'(?:' + '|'.join(_ACTION_VERBS) + r')'
        r'.{0,30}?'
        r'(?:吗|啊|吧|呢|$)',
        re.IGNORECASE | re.MULTILINE,
    ),
]


def _strip_instruction_suffix(text: str) -> str:
    if not text or len(text) < 20:
        return text
    head_len = max(0, len(text) - 150)
    head, tail = text[:head_len], text[head_len:]
    for pat in _INSTRUCTION_SUFFIX_PATTERNS:
        match = pat.search(tail)
        if match:
            stripped = tail[:match.start()].rstrip()
            return (head + stripped).rstrip()
    return text


def _strip_instruction_prefix(text: str, max_rounds: int = 8) -> str:
    for _ in range(max_rounds):
        new_text = _INSTRUCTION_PREFIX_RE.sub('', text, count=1).strip()
        if new_text == text:
            break
        text = new_text
    return text


_POLITE_OPENING_PATTERNS = [
    re.compile(r'^(?:希望|但愿)(?:这个|这段)?(?:故事|内容)?(?:你|您)?(?:会)?(?:喜欢|满意)[。！!]?\s*$', re.MULTILINE),
    re.compile(r'^(?:以下|下面)(?:是|我|你)?(?:创作|写的|分享的)?(?:一个|一段)?故事[，,]?\s*$', re.MULTILINE),
    re.compile(r'^(?:好的|OK|okay)[，,。]?\s*$', re.MULTILINE),
]

_POLITE_ENDING_PATTERNS = [
    re.compile(r'^(?:希望|但愿)(?:这个|这段)?(?:故事|内容)?(?:你|您)?(?:会)?(?:喜欢|满意)[。！!]?\s*$', re.MULTILINE),
    re.compile(r'^(?:故事)?(?:就)?(?:到|说|讲|写|念)(?:这里|此|这|结束)?(?:了|啦|吧)?[。！!]?\s*$', re.MULTILINE),
    re.compile(r'^(?:以上|全文)(?:就是|是)?(?:故事)?(?:的)?(?:全部|内容)?[。！!]?\s*$', re.MULTILINE),
    re.compile(r'^(?:完|end|the\s*end)[。.]?\s*$', re.IGNORECASE | re.MULTILINE),
]

_MD_CODE_BLOCK_RE = re.compile(r'^```(?:markdown|text|md)?\s*\n(.*?)\n```\s*$', re.DOTALL | re.MULTILINE)
_SEPARATOR_RE = re.compile(r'^\s*(?:[-*_]{3,})\s*$', re.MULTILINE)


def _strip_code_block(text: str) -> str:
    m = _MD_CODE_BLOCK_RE.search(text)
    return m.group(1).strip() if m else text


def _strip_polite_wraps(text: str) -> str:
    paragraphs = text.split("\n")
    head_cut = 0
    for i, para in enumerate(paragraphs[:3]):
        stripped = para.strip()
        if not stripped:
            head_cut = i + 1
            continue
        if any(p.search(stripped) for p in _POLITE_OPENING_PATTERNS):
            head_cut = i + 1
            continue
        break

    tail_cut = len(paragraphs)
    for i in range(len(paragraphs) - 1, max(len(paragraphs) - 4, head_cut - 1), -1):
        stripped = paragraphs[i].strip()
        if not stripped:
            tail_cut = i
            continue
        if any(p.search(stripped) for p in _POLITE_ENDING_PATTERNS):
            tail_cut = i
            continue
        break
    return "\n".join(paragraphs[head_cut:tail_cut]).strip()


def _strip_separators(text: str) -> str:
    return _SEPARATOR_RE.sub('', text).strip()


def extract_pure_story(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = raw_text.strip()
    text = _strip_code_block(text)
    text = _strip_instruction_prefix(text)
    text = _strip_instruction_suffix(text)
    text = _strip_polite_wraps(text)
    text = _strip_separators(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_story_from_history(history: List[dict]) -> Optional[str]:
    if not history:
        return None
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if content.startswith('{') or content.startswith('['):
            continue
        if len(content) < 200:
            continue
        return content
    return None


def extract_story(user_text: str, history: List[dict]) -> tuple:
    raw = user_text.strip()
    pure = extract_pure_story(raw)
    logger.info(f"[Extractor] 本轮提取 {len(pure)} 字，首部={pure[:40]!r}")

    if len(pure) >= 200:
        return pure, "inline"

    referent_keywords = (
        "上一轮", "上一个", "刚才", "刚刚", "之前", "前面", "上面的", "上述",
        "重新生成", "重做", "再来一次", "再来一个", "再生成", "重新做",
    )
    is_referent = any(k in raw for k in referent_keywords)

    if is_referent or len(pure) < 200:
        hist_story = extract_story_from_history(history)
        if hist_story:
            cleaned = extract_pure_story(hist_story)
            logger.info(f"[Extractor] ✅ 从历史提取故事，长度: {len(cleaned)}")
            return cleaned, "history"

    return "", "empty"


_STYLE_KEYWORDS = {
    "anime_drama": re.compile(r'动漫|anime|二次元|卡通|漫画|日式|动画', re.IGNORECASE),
    "ink_wash": re.compile(r'水墨|国画|古典|中国风|山水画', re.IGNORECASE),
}


def detect_style(user_text: str) -> str:
    for style, pattern in _STYLE_KEYWORDS.items():
        if pattern.search(user_text):
            logger.info(f"[Style] ✅ 检测到风格关键词: {style}")
            return style
    logger.info(f"[Style] 未检测到风格关键词，使用默认: realistic_cinema")
    return "realistic_cinema"