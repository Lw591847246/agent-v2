# app/skills/english_chat/skill.py
"""EnglishChat Skill：英文口语练习。"""
from __future__ import annotations

from typing import Dict, Iterator, List

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.english_chat.service import english_chat_service


class EnglishChatSkill(BaseSkill):
    name = "english_chat"
    description = (
        "用英文聊天、练习英语口语。适用于：'练英语/用英文聊/practice english'、"
        "用户用英文发消息。不适用于：翻译（用 translate_text）、通用中文问答（用 chat）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "用户输入"},
            "history": {"type": "array", "description": "历史对话，可选"},
        },
        "required": ["text"],
    }
    intent = "english_chat"
    category = "general"
    sync = True
    returns = ["text"]
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(self, text: str, history: List[Dict] = None, **kwargs) -> Result:
        answer = english_chat_service.chat(text, history)
        return Result(parts=[text_part(answer)])

    def stream(self, text: str, history: List[Dict] = None, **kwargs) -> Iterator[str]:
        yield from english_chat_service.chat_stream(text, history)


skill = EnglishChatSkill()