"""文生图业务：调 Qwen Image 客户端生成 + 保存。"""
from __future__ import annotations

import json
from typing import Optional

from app.core.logger import logger
from app.infrastructure.clients.gpu_lock import gpu_lock
from app.infrastructure.clients.qwen_Image_client import qwen_image_client


class ImageGenerationService:

    def generate(
        self, prompt: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> dict:
        """返回 {"image_base64": "...", "prompt": "..."}"""
        try:
            # ⭐ 关键：从 Ollama 切换到 ComfyUI 时，会先卸载 Ollama 释放显存
            with gpu_lock.acquire(task_name="comfyui_image_gen", timeout=600):
                logger.info(f"[ImageGenerationService] GPU 锁已获取，prompt={prompt[:40]}")
                return qwen_image_client.generate(
                    prompt=prompt, width=width, height=height,
                )
        except Exception as e:
            logger.error(f"[ImageGenerationService] 生成失败: {e}")
            raise

    @staticmethod
    def extract_params(user_text: str) -> dict:
        """小模型抽参数，失败降级到正则。"""
        try:
            from app.infrastructure.clients.model_manager import model_manager
            from app.skills.image_generation.prompts import PARAM_EXTRACT_PROMPT
            raw = model_manager.chat(
                task_type="intent",
                messages=[
                    {"role": "system", "content": PARAM_EXTRACT_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                format="json",
            )
            raw = raw.strip()
            if raw.startswith("```json"):
                raw = raw[7:-3]
            elif raw.startswith("```"):
                raw = raw[3:-3]
            return json.loads(raw.strip())
        except Exception as e:
            logger.warning(f"[ImageGenerationService] 参数提取降级: {e}")
            return {"prompt": user_text, "width": None, "height": None, "style": None}


image_generation_service = ImageGenerationService()