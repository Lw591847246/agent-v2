"""代码生成业务：同步 + 流式，支持快速模式（code_fast）和思考模式（code_think）。"""
from __future__ import annotations

from typing import Iterator, Optional

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.code.prompts import (
    SYSTEM_PROMPT_FAST, SYSTEM_PROMPT_THINK,
)


class CodeService:

    def generate(
        self,
        requirement: str,
        language: str = "",
        current_code: str = "",
        use_think: bool = False,
    ) -> str:
        model_key = "code_think" if use_think else "code_fast"
        messages = self._build_messages(
            requirement, language, current_code, use_think,
        )
        try:
            return ollama_client.chat(
                service="chat", model_key=model_key, messages=messages,
            )
        except Exception as e:
            logger.error(f"[CodeService] generate 失败 model={model_key}: {e}")
            return f"代码生成失败：{e}"

    def generate_stream(
        self,
        requirement: str,
        language: str = "",
        current_code: str = "",
        use_think: bool = False,
    ) -> Iterator[str]:
        model_key = "code_think" if use_think else "code_fast"
        messages = self._build_messages(
            requirement, language, current_code, use_think,
        )
        try:
            yield from ollama_client.stream(
                service="chat", model_key=model_key, messages=messages,
            )
        except Exception as e:
            logger.error(f"[CodeService] stream 失败 model={model_key}: {e}")
            yield f"代码生成失败：{e}"

    @staticmethod
    def _build_messages(
        requirement: str,
        language: str = "",
        current_code: str = "",
        use_think: bool = False,
    ) -> list:
        system_prompt = SYSTEM_PROMPT_THINK if use_think else SYSTEM_PROMPT_FAST

        parts = []
        if language:
            parts.append(f"请用 {language} 实现：")
        parts.append(requirement)
        if current_code:
            parts.append(
                f"\n\n【当前工作区的代码版本如下，请在此基础上修改】：\n"
                f"```\n{current_code}\n```"
            )

        user_content = "\n".join(parts)
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]


code_service = CodeService()