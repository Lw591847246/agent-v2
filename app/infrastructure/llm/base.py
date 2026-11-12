from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class LLMProvider(ABC):
    """Abstract LLM provider.

    Concrete implementations: OllamaProvider, OpenAIProvider, VLLMProvider ...
    """

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.1,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> str:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...
