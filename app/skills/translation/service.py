# app/skills/translation/service.py
"""翻译业务：同步 + 流式。"""
from __future__ import annotations

from typing import Iterator, Optional

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.translation.prompts import SYSTEM_PROMPT


class TranslationService:

    def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str = "",
        glossary: str = "",
    ) -> str:
        messages = self._build_messages(text, target_lang, source_lang, glossary)
        try:
            return ollama_client.chat(
                service="chat", model_key="chat", messages=messages,
            )
        except Exception as e:
            logger.error(f"[TranslationService] 失败: {e}")
            return f"翻译失败：{e}"

    def translate_stream(
        self,
        text: str,
        target_lang: str,
        source_lang: str = "",
        glossary: str = "",
    ) -> Iterator[str]:
        messages = self._build_messages(text, target_lang, source_lang, glossary)
        try:
            yield from ollama_client.stream(
                service="chat", model_key="chat", messages=messages,
            )
        except Exception as e:
            logger.error(f"[TranslationService] 流式失败: {e}")
            yield f"翻译失败：{e}"

    @staticmethod
    def _build_messages(
        text: str, target_lang: str,
        source_lang: str = "", glossary: str = "",
    ) -> list:
        system = SYSTEM_PROMPT
        if glossary:
            system += f"\n\n术语表（严格遵守）：\n{glossary}"

        src_hint = f"源语言：{source_lang}\n" if source_lang else ""
        user = f"{src_hint}请将以下文本翻译成{target_lang}：\n\n{text}"

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]


translation_service = TranslationService()