# app/infrastructure/clients/comfyui_client.py
"""ComfyUI 客户端：提交 workflow、轮询、下载产物。

关键：从提交到任务完成，全程持有 COMFYUI 租约。
8G 卡上 ComfyUI 一旦加载模型就占 6G+，期间不能有别资源进来。
"""
from __future__ import annotations

import time
import uuid
from typing import Dict, Optional

import requests

from app.config.loader import config
from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.logger import logger


class ComfyUIClient:

    def __init__(self):
        self.base = (config.get("comfyui.base_url", "http://127.0.0.1:8188") or "").rstrip("/")

    def submit(self, workflow: Dict, timeout: int = 1800) -> Dict:
        """提交 workflow 并等待完成，全程持锁。

        返回 {"task_id": ..., "outputs": [...], "duration_ms": ...}
        """
        client_id = uuid.uuid4().hex
        with gpu_lock.lease(GPUResource.COMFYUI, task="comfyui-run", timeout=timeout):
            # 1. 提交
            r = requests.post(
                f"{self.base}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30,
            )
            r.raise_for_status()
            prompt_id = r.json()["prompt_id"]
            logger.info(f"[ComfyUI] 提交 prompt_id={prompt_id}")

            # 2. 轮询
            deadline = time.time() + timeout
            history = None
            while time.time() < deadline:
                time.sleep(2)
                hr = requests.get(f"{self.base}/history/{prompt_id}", timeout=10)
                if hr.status_code == 200:
                    data = hr.json()
                    if prompt_id in data:
                        history = data[prompt_id]
                        break
            if history is None:
                raise TimeoutError(f"[ComfyUI] prompt {prompt_id} 超时未完成")

            # 3. 提取产物
            outputs = []
            for node_id, node_out in (history.get("outputs") or {}).items():
                for key in ("images", "gifs", "videos"):
                    for item in node_out.get(key, []) or []:
                        outputs.append({
                            "filename": item.get("filename"),
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                        })
            return {"prompt_id": prompt_id, "outputs": outputs}

    def download(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        r = requests.get(
            f"{self.base}/view",
            params={"filename": filename, "subfolder": subfolder, "type": folder_type},
            timeout=60,
        )
        r.raise_for_status()
        return r.content

    def free(self):
        """主动释放（锁内部切换时也会调，这里是手动兜底）。"""
        try:
            requests.post(
                f"{self.base}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=15,
            )
        except Exception as e:
            logger.warning(f"[ComfyUI] free 失败: {e}")


comfyui_client = ComfyUIClient()