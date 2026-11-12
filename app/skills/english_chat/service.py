# app/skills/english_chat/service.py
"""English chat 业务：过滤中文历史 + 强制英文。"""
from __future__ import annotations

from typing import Dict, Iterator, List

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.english_chat.prompts import REMINDER, SYSTEM_PROMPT


class EnglishChatService:

    def chat_stream(self, text: str, history: List[Dict] = None) -> Iterator[str]:
        messages = self._build_messages(text, history)
        try:
            yield from ollama_client.stream(
                service="chat", model_key="chat", messages=messages,
            )
        except Exception as e:
            logger.error(f"[EnglishChat] stream 失败: {e}")
            yield f"Sorry, something went wrong: {e}"

    def chat(self, text: str, history: List[Dict] = None) -> str:
        messages = self._build_messages(text, history)
        try:
            return ollama_client.chat(
                service="chat", model_key="chat", messages=messages,
            )
        except Exception as e:
            logger.error(f"[EnglishChat] chat 失败: {e}")
            return f"Sorry, something went wrong: {e}"

    @staticmethod
    def _build_messages(text: str, history: List[Dict] = None) -> list:
        filtered = []
        for h in (history or []):
            if h.get("role") != "user":
                continue
            content = (h.get("content") or "").strip()
            if content and EnglishChatService._looks_english(content):
                filtered.append({"role": "user", "content": content})
        filtered = filtered[-4:]

        return (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + filtered
            + [{"role": "user", "content": text}]
            + [{"role": "system", "content": REMINDER}]
        )

    @staticmethod
    def _looks_english(text: str) -> bool:
        if not text:
            return False
        letters = sum(1 for c in text if c.isascii() and c.isalpha())
        total = len(text.strip())
        return total > 0 and letters / total > 0.6


english_chat_service = EnglishChatService()