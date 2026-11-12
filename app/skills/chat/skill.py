# app/skills/chat/skill.py
"""Chat Skill：纯文本对话，兜底能力。"""
from __future__ import annotations

from typing import Dict, Iterator, List, Optional

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.chat.service import chat_service


class ChatSkill(BaseSkill):
    name = "chat"
    description = (
        "通用中文对话。适用于：闲聊、通用问答、写故事/文章/诗歌、解释概念、"
        "开放式讨论。不适用于：需要实时信息（天气/新闻/股价）、需要生成文件/图片/视频、"
        "需要翻译（用 translate_text）、需要写代码（用 code_generate）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "用户输入"},
            "history": {
                "type": "array",
                "description": "历史对话（role/content 列表），可选",
            },
        },
        "required": ["text"],
    }
    intent = "chat"
    category = "general"
    sync = True
    returns = ["text"]
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, text: str, history: Optional[List[Dict]] = None, **kwargs,
    ) -> Result:
        answer = chat_service.chat(text, history)
        return Result(parts=[text_part(answer)])

    def stream(
        self, text: str, history: Optional[List[Dict]] = None, **kwargs,
    ) -> Iterator[str]:
        yield from chat_service.chat_stream(text, history)


skill = ChatSkill()