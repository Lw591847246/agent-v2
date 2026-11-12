# app/skills/chat/service.py
"""Chat 业务：纯文本对话，流式/非流式两种。"""
from __future__ import annotations

from typing import Dict, Iterator, List, Optional

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.chat.prompts import SYSTEM_PROMPT


class ChatService:

    def chat(
        self, text: str, history: Optional[List[Dict]] = None,
    ) -> str:
        messages = self._build_messages(text, history)
        try:
            return ollama_client.chat(
                service="chat", model_key="chat", messages=messages,
            )
        except Exception as e:
            logger.error(f"[ChatService] chat 失败: {e}")
            return f"抱歉，回答失败：{e}"

    def chat_stream(
        self, text: str, history: Optional[List[Dict]] = None,
    ) -> Iterator[str]:
        messages = self._build_messages(text, history)
        try:
            yield from ollama_client.stream(
                service="chat", model_key="chat", messages=messages,
            )
        except Exception as e:
            logger.error(f"[ChatService] stream 失败: {e}")
            yield f"抱歉，回答失败：{e}"

    @staticmethod
    def _build_messages(text: str, history: Optional[List[Dict]]) -> List[Dict]:
        messages: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for h in (history or []):
            role = h.get("role", "user")
            content = (h.get("content") or "").strip()
            if not content:
                continue
            if role not in ("user", "assistant"):
                continue
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": text})
        return messages


chat_service = ChatService()