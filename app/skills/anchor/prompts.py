# app/skills/anchor/prompts.py
"""Anchor（孔子授课）常量。"""

DEFAULT_TEMPLATE_ID = "kongzi_scholar"
LP_BASE_URL = "http://127.0.0.1:8001"
SKILL_NAME = "anchor"
POLL_INTERVAL = 3
POLL_MAX_SECONDS = 1800

# 提取引号内台词的正则
import re

_QUOTE_RE = re.compile(r'[“”"\'‘’](.*?)[“”"\'‘’]')

_PREFIX_CLEAN_RE = re.compile(
    r'^(用|让|请)?\s*(.+?)\s*(画像)?\s*(给我|帮我)?\s*'
    r'(讲一段|讲个|说一段|说个|播报|念一段|读一段|读一个|讲一个|说一个)\s*',
    re.IGNORECASE,
)
_JUNK_PREFIX_RE = re.compile(r'^(里的|中的|的)\s*')