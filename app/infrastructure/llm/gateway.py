from __future__ import annotations

from typing import Any, Optional

from app.core.logger import logger
from app.infrastructure.llm.base import LLMProvider
from app.infrastructure.llm.ollama_provider import OllamaProvider


class LLMGateway:
    """Route calls to registered providers.

    Usage:
        gateway.register("default", OllamaProvider())
        gateway.chat(messages=[...], provider="default")
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._default: Optional[str] = None

    def register(self, name: str, provider: LLMProvider, default: bool = False) -> None:
        self._providers[name] = provider
        if default or self._default is None:
            self._default = name
        logger.info(f"[LLMGateway] registered provider '{name}' (default={self._default == name})")

    def get(self, name: Optional[str] = None) -> LLMProvider:
        key = name or self._default
        if not key or key not in self._providers:
            raise RuntimeError(f"no LLM provider registered for '{key}'")
        return self._providers[key]

    def chat(
        self,
        messages: list[dict[str, str]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.1,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        p = self.get(provider)
        return p.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            json_mode=json_mode,
            **kwargs,
        )


llm_gateway = LLMGateway()
llm_gateway.register("default", OllamaProvider(service="chat"), default=True)
