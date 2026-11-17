# app/infrastructure/clients/model_manager.py
import requests
from app.config.loader import config
from app.core.gpu_lock import gpu_lock
from app.core.logger import logger
from typing import List, Dict


class ModelManager:
    def chat(self, task_type: str, messages: List[Dict], format: str = ""):
        # 直接取 task_type 对应的模型别名
        keep_alive = config.get(f"ollama.keep_alive.{task_type}", "-1")

        # ⭐ 核心修复：使用全局单例 gpu_lock 的 acquire 方法，而不是实例化 GlobalGPULock
        with gpu_lock.acquire(task_name=f"LLM-{task_type}", timeout=300):
            logger.info(f"【ModelManager】调用模型: {task_type}, keep_alive={keep_alive}")

            # 动态引入，避免循环依赖
            from app.infrastructure.clients.ollama_client import ollama_client

            return ollama_client.chat(
                service="chat",
                model_key=task_type,
                messages=messages,
                keep_alive=keep_alive,
                format=format
            )

    def unload_all_models(self):
        """在调用 ComfyUI 等其它重负载前，清理 Ollama 显存"""
        models = config.get("ollama.models", {})
        for name in set(models.values()):
            try:
                url = "http://localhost:11434/api/generate"
                requests.post(url, json={"model": name, "keep_alive": 0}, timeout=2)
                logger.info(f"【显存清理】已卸载模型: {name}")
            except Exception:
                pass


model_manager = ModelManager()