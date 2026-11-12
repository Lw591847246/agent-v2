# app/skills/image_edit/service.py
"""图生图业务：调 Qwen Image 客户端做 img2img。"""
from __future__ import annotations

import base64
import os
from typing import Optional

from app.core.logger import logger
from app.infrastructure.clients.qwen_Image_client import qwen_image_client
from app.utils.file_registry import file_registry


class ImageEditService:

    def edit(
        self, prompt: str, image_bytes: bytes,
        width: Optional[int] = None,
        height: Optional[int] = None,
        denoise: float = 1.0,
    ) -> dict:
        try:
            return qwen_image_client.generate_img2img(
                prompt=prompt, image_bytes=image_bytes,
                width=width, height=height, denoise=denoise,
            )
        except Exception as e:
            logger.error(f"[ImageEditService] 失败: {e}")
            raise

    @staticmethod
    def resolve_image_b64(
        image_file_id: str = "", session_id: str = "", user_id: int = 0,
    ) -> str:
        """从 file_id 或最近一张图解析 base64，失败返回空串。"""
        # 1. 从 file_registry 读
        if image_file_id:
            meta = file_registry.get(image_file_id)
            if meta and meta.get("path") and os.path.isfile(meta["path"]):
                try:
                    with open(meta["path"], "rb") as f:
                        return base64.b64encode(f.read()).decode("utf-8")
                except Exception as e:
                    logger.warning(f"[ImageEdit] 读 {image_file_id} 失败: {e}")

        # 2. 从最近一张生成的图读
        if session_id and user_id:
            try:
                from app.utils.image_storage import get_latest_filename, load_original
                latest = get_latest_filename(user_id, session_id)
                if latest:
                    raw = load_original(user_id, session_id, latest)
                    if raw:
                        logger.info(f"[ImageEdit] 使用最近一张图: {latest}")
                        return base64.b64encode(raw).decode("utf-8")
            except Exception as e:
                logger.warning(f"[ImageEdit] 读最近一张失败: {e}")

        return ""


image_edit_service = ImageEditService()