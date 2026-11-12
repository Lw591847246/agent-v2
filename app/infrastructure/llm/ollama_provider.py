from __future__ import annotations

from typing import Any, Optional

from app.core.logger import logger
from app.infrastructure.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    """Wrap existing ollama_client."""

    def __init__(self, service: str = "chat") -> None:
        self.service = service

    def _client(self):
        from app.infrastructure.clients.ollama_client import ollama_client
        return ollama_client

    def chat(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        client = self._client()
        model_key = model or "intent"
        try:
            return client.chat(
                service=self.service,
                model_key=model_key,
                messages=messages,
                temperature=temperature,
            )
        except Exception as e:
            logger.error(f"[OllamaProvider] chat failed: {e}")
            raise

    def is_available(self) -> bool:
        try:
            self._client()
            return True
        except Exception:
            return False
