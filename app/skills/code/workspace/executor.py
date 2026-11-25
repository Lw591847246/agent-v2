# app/skills/code/workspace/executor.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Dict

from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.logger import logger


class ModelExecutor(ABC):
    @abstractmethod
    def complete(self, model: str, messages: List[Dict[str, str]], max_tokens: int = 800) -> str:
        ...


class LocalGpuModelExecutor(ModelExecutor):
    """本地实现：每次模型调用短租约 GPU 锁，避免扫描长时间占锁。

    适配点：ollama_client.chat 的签名/返回结构按你项目实际调整；
    这里兼容 message.content / response 两种常见返回。
    """

    def complete(self, model, messages, max_tokens=800):
        from app.infrastructure.clients.ollama_client import ollama_client

        ticket = gpu_lock.acquire(
            GPUResource.OLLAMA_CHAT, task="code-scan", timeout=600,
        )
        try:
            resp = ollama_client.chat(
                model=model,
                messages=messages,
                options={"num_predict": max_tokens, "temperature": 0.1},
            )
            if isinstance(resp, dict):
                msg = resp.get("message") or {}
                return str(msg.get("content") or resp.get("response") or "")
            return str(resp or "")
        finally:
            try:
                ticket.release()
            except Exception as e:
                logger.warning(f"[CodeExecutor] 释放 GPU 租约失败: {e}")


model_executor: ModelExecutor = LocalGpuModelExecutor()