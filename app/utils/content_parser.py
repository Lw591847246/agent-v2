"""把 assistant 消息的 JSON parts 解析成纯文本，供 LLM 消费。"""
import json
from typing import Any, List


def parse_content_for_llm(content: Any) -> str:
    """
    content 可能是：
      1. 纯文本字符串 → 原样返回
      2. JSON Part 数组字符串：'[{"type":"text","content":"..."}]' → 解析为纯文本
      3. 其他格式 → str
    """
    if content is None:
        return ""
    if isinstance(content, str):
        stripped = content.strip()
        if not stripped:
            return ""
        if not (stripped.startswith("[") and stripped.endswith("]")):
            return content
        try:
            parts = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return content
        if not isinstance(parts, list):
            return content
        texts: List[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "text":
                continue
            t = part.get("content", "")
            if t:
                texts.append(t)
        if texts:
            return "\n\n".join(texts)
        return content
    return str(content)


# 兼容旧名
_parse_content_for_llm = parse_content_for_llm