# app/utils/context_budget.py
"""上下文预算装配（不依赖向量库的长记忆基石）。

设计目标
--------
Ollama 本地模型 num_ctx 有限（8GB 显存下通常 16k~32k token），不能把"50 轮原始对话 +
整库代码"无脑塞进上下文。本模块提供**按 token 预算裁剪**的纯函数：调用方按优先级把
各上下文块交给它，它在预算内尽量保留高价值内容（系统指令/项目地图/滚动摘要/近窗对话/
本轮需求），超预算时按优先级从低到高丢弃。

字符→token 估算：中文≈1.5 字符/token、代码英文≈3~4 字符/token，混合取一个保守的
chars_per_token（默认 1.7）即可；宁大勿小。所有函数为纯函数、无外部依赖、可单测。
"""
from __future__ import annotations

import math
from typing import List, Tuple, Iterable


def estimate_tokens(text: str, chars_per_token: float = 1.7) -> int:
    """保守估算 token 数。空串返回 0。"""
    if not text:
        return 0
    if chars_per_token <= 0:
        chars_per_token = 1.7
    return int(math.ceil(len(text) / chars_per_token)) + 1


def fit(text: str, max_tokens: int, chars_per_token: float = 1.7,
        keep: str = "head_tail",
        marker: str = "\n...[已按预算压缩 {omitted} 字]...\n") -> str:
    """把 text 裁到 max_tokens 以内。keep: head / tail / head_tail（默认）。"""
    if not text:
        return ""
    if max_tokens <= 0:
        return ""
    budget_chars = max(0, int(max_tokens * chars_per_token))
    if len(text) <= budget_chars:
        return text

    if keep == "head":
        return text[:budget_chars]
    if keep == "tail":
        return text[-budget_chars:]

    omitted = len(text) - budget_chars
    label = marker.format(omitted=omitted) if marker else "\n...\n"
    usable = max(0, budget_chars - len(label))
    head = int(usable * (1 / 3))
    tail = usable - head
    head_part = text[:head]
    tail_part = text[-tail:] if tail > 0 else ""
    return head_part + label + tail_part


def fit_messages(messages: List[dict], max_tokens: int,
                 chars_per_token: float = 1.7,
                 protect_roles: Tuple[str, ...] = ("system",)) -> List[dict]:
    """把一组 messages 裁到 max_tokens 以内：受保护 role（默认 system）永不裁剪；
    其余按"从最旧开始丢弃"的顺序回收预算（优先保住最近的对话）。"""
    if not messages:
        return []
    total = sum(estimate_tokens(m.get("content", ""), chars_per_token) for m in messages)
    if total <= max_tokens:
        return messages

    protected_tokens = sum(
        estimate_tokens(m.get("content", ""), chars_per_token)
        for m in messages if m.get("role") in protect_roles
    )
    droppable = [m for m in messages if m.get("role") not in protect_roles]
    keepable = [m for m in messages if m.get("role") in protect_roles]

    remain = max(0, max_tokens - protected_tokens)
    kept_droppable: List[dict] = []
    for m in reversed(droppable):
        t = estimate_tokens(m.get("content", ""), chars_per_token)
        if t <= remain:
            kept_droppable.insert(0, m)
            remain -= t
        else:
            if remain > 20:
                shrunken = dict(m)
                shrunken["content"] = fit(m.get("content", ""), remain,
                                         chars_per_token, keep="head")
                kept_droppable.insert(0, shrunken)
            break

    return keepable + kept_droppable


def assemble_sections(sections: Iterable[Tuple[str, int]], total_budget_tokens: int,
                      chars_per_token: float = 1.7,
                      min_tokens_per_section: int = 60) -> str:
    """按"优先级从高到低"装配多个文本块，总量不超过 total_budget_tokens。

    sections: [(text, priority), ...]，priority 越大越优先（越先得到预算）。
    """
    items = [(t, p) for t, p in sections if t and t.strip()]
    items.sort(key=lambda x: x[1], reverse=True)

    out: List[str] = []
    used = 0
    for text, _prio in items:
        t = estimate_tokens(text, chars_per_token)
        if used + t <= total_budget_tokens:
            out.append(text)
            used += t
            continue
        left = total_budget_tokens - used
        if left >= max(min_tokens_per_section, 1):
            out.append(fit(text, left, chars_per_token, keep="head_tail"))
            used = total_budget_tokens
        if used >= total_budget_tokens:
            break
    return "\n\n".join(out)
