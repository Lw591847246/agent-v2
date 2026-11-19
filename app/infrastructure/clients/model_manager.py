# app/infrastructure/clients/model_manager.py
import requests
from app.config.loader import config
from app.core.gpu_lock import gpu_lock
from app.core.logger import logger
from typing import List, Dict


class ModelManager:
    def chat(self, task_type: str, messages: List[Dict], format: str = ""):
        keep_alive = config.get(f"ollama.keep_alive.{task_type}", "-1")

        with gpu_lock.acquire(task_name=f"LLM-{task_type}", timeout=300):
            logger.info(f"【ModelManager】调用模型: {task_type}, keep_alive={keep_alive}")

            from app.infrastructure.clients.ollama_client import ollama_client

            return ollama_client.chat(
                service="chat",
                model_key=task_type,
                messages=messages,
                keep_alive=keep_alive,
                format=format
            )

    def unload_all_models(self):
        """在调用 ComfyUI 等其它重负载前，清理 Ollama 显存。
        不再硬编码 localhost：从 ollama.hosts.* 与 ollama.base_url 解析全部源，
        对每个源、每个模型广播 keep_alive=0；打到没有该模型的源是无害 no-op。
        """
        models = config.get("ollama.models", {}) or {}
        names = {m for m in models.values() if m}
        if not names:
            return

        from app.infrastructure.clients.ollama_client import ollama_client
        hosts = ollama_client.get_unload_hosts()
        timeout = config.get("ollama.timeout.unload", 3)

        for host in hosts:
            url = f"{host}/api/generate"
            for name in names:
                try:
                    requests.post(url, json={"model": name, "keep_alive": 0}, timeout=timeout)
                    logger.info(f"【显存清理】已通知 {host} 卸载模型: {name}")
                except Exception:
                    # 卸载是 best-effort：源不可达 / 模型本就未加载，静默跳过即可
                    pass


model_manager = ModelManager()
