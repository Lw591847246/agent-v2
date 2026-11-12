# app/skills/translation/skill.py
"""Translation Skill：文本翻译。"""
from __future__ import annotations

from typing import Iterator

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.translation.service import translation_service


class TranslationSkill(BaseSkill):
    name = "translate_text"
    description = (
        "将文本翻译成目标语言，只输出译文。适用于：用户明确说'翻译成英文/日文/...'、"
        "'translate to ...'。不适用于：解释外文含义（用 chat）、翻译整份文档（用 document_qa 或后续 document_translate）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "待翻译文本"},
            "target_lang": {"type": "string", "description": "目标语言，如 英文、日文、法文"},
            "source_lang": {"type": "string", "description": "源语言，可空，留空自动检测"},
            "glossary": {"type": "string", "description": "术语表，可空"},
        },
        "required": ["text", "target_lang"],
    }
    intent = "translation"
    category = "general"
    sync = True
    returns = ["text"]
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, text: str, target_lang: str,
        source_lang: str = "", glossary: str = "", **kwargs,
    ) -> Result:
        answer = translation_service.translate(text, target_lang, source_lang, glossary)
        return Result(parts=[text_part(answer)])

    def stream(
        self, text: str, target_lang: str,
        source_lang: str = "", glossary: str = "", **kwargs,
    ) -> Iterator[str]:
        yield from translation_service.translate_stream(
            text, target_lang, source_lang, glossary,
        )


skill = TranslationSkill()