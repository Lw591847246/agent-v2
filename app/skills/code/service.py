# app/skills/code/service.py
"""代码生成业务：同步 + 流式。"""
from __future__ import annotations

from typing import Iterator

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.code.prompts import SYSTEM_PROMPT


class CodeService:

    def generate(self, requirement: str, language: str = "") -> str:
        messages = self._build_messages(requirement, language)
        try:
            return ollama_client.chat(
                service="chat", model_key="code", messages=messages,
            )
        except Exception as e:
            logger.error(f"[CodeService] generate 失败: {e}")
            return f"代码生成失败：{e}"

    def generate_stream(self, requirement: str, language: str = "") -> Iterator[str]:
        messages = self._build_messages(requirement, language)
        try:
            yield from ollama_client.stream(
                service="chat", model_key="code", messages=messages,
            )
        except Exception as e:
            logger.error(f"[CodeService] stream 失败: {e}")
            yield f"代码生成失败：{e}"

    @staticmethod
    def _build_messages(requirement: str, language: str = "") -> list:
        if language:
            user = f"请用 {language} 实现：\n{requirement}"
        else:
            user = requirement
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]


code_service = CodeService()