# app/skills/image_understanding/service.py
"""图片理解业务：描述/OCR/解题。"""
from __future__ import annotations

import base64
import os
from typing import Optional

import requests

from app.config.loader import config
from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.image_understanding.prompts import (
    DESCRIBE_PROMPT, OCR_PROMPT, SOLVE_PROMPT,
)
from app.utils.file_registry import file_registry


_OCR_KEYWORDS = ("提取文字", "识别文字", "ocr", "OCR", "读出文字")
_SOLVE_KEYWORDS = ("计算", "解答", "求解", "答案", "解题", "这道题", "怎么做", "求值")


class ImageUnderstandingService:

    def understand(
        self, file_id: str, question: str = "", mode: str = "",
    ) -> str:
        image_b64 = self._load_image_b64(file_id)
        if not image_b64:
            return "图片不存在或已过期"

        if not mode:
            mode = self._detect_mode(question)

        if mode == "ocr":
            prompt = OCR_PROMPT
            model_key = "ocr"
        elif mode == "solve":
            prompt = f"{SOLVE_PROMPT}\n\n用户要求：{question}" if question else SOLVE_PROMPT
            model_key = "chat"
        else:
            prompt = f"用户要求：{question}" if question else DESCRIBE_PROMPT
            model_key = "chat"

        logger.info(f"[ImageUnderstanding] mode={mode} model={model_key}")
        return self._call_vlm(prompt, image_b64, model_key)

    @staticmethod
    def _detect_mode(question: str) -> str:
        q = (question or "").lower()
        if any(k in question for k in _SOLVE_KEYWORDS):
            return "solve"
        if any(k.lower() in q for k in _OCR_KEYWORDS):
            return "ocr"
        return "describe"

    @staticmethod
    def _load_image_b64(file_id: str) -> str:
        meta = file_registry.get(file_id) if file_id else None
        if not meta or not meta.get("path"):
            return ""
        path = meta["path"]
        if not os.path.isfile(path):
            return ""
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.warning(f"[ImageUnderstanding] 读图失败: {e}")
            return ""

    @staticmethod
    def _call_vlm(prompt: str, image_b64: str, model_key: str = "chat") -> str:
        # 优先用 ollama_client 扩展方法（旧项目有）
        if hasattr(ollama_client, "chat_with_image"):
            try:
                return ollama_client.chat_with_image(prompt, image_b64, model_key=model_key)
            except Exception as e:
                logger.warning(f"[ImageUnderstanding] chat_with_image 失败，回退: {e}")

        # 回退：直接用 requests 调 Ollama /api/chat
        try:
            host = config.get("ollama.hosts.chat", "http://localhost:11434")
            model = config.get(
                f"ollama.models.{model_key}",
                config.get("ollama.models.chat"),
            )
            payload = {
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }],
                "stream": False,
            }
            r = requests.post(f"{host}/api/chat", json=payload, timeout=300)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "")
        except Exception as e:
            logger.error(f"[ImageUnderstanding] 调用失败: {e}")
            return f"图片理解失败：{e}"


image_understanding_service = ImageUnderstandingService()