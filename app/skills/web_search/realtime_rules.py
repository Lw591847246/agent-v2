# app/application/skills/search_skill/realtime_rules.py
"""
实时性判断规则（兼容占位）。

注意：现在 web_search 走独立 intent，由 L2 LLM 判断"是否需要搜索"，
不再依赖关键词规则。此文件仅为兼容 service.py 的 import 保留。
"""
from typing import List, Tuple


DEFAULT_REALTIME_KEYWORDS: List[str] = []
DEFAULT_REALTIME_PATTERNS: List[str] = []


def load_realtime_rules() -> Tuple[List[str], List[str]]:
    """返回空列表（不再用关键词判断）"""
    return [], []