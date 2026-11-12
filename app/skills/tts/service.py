# app/skills/tts/service.py
"""TTS 业务：短文本同步 + 长文本异步。"""
from __future__ import annotations

import os

from app.core.logger import logger
from app.infrastructure.services.tts_service import tts_service


# 同步阈值：小于此字符数同步返回，否则走异步任务
SYNC_MAX_CHARS = 100


class TtsServiceWrapper:

    def synthesize_sync(self, text: str, voice: str = "") -> dict:
        """
        同步合成。返回 {path, filename, url, duration_ms}
        """
        path = tts_service.synthesize_wav(text, voice)
        if not path or not os.path.isfile(path):
            raise RuntimeError(f"TTS 未生成有效文件: {path}")
        filename = os.path.basename(path)
        return {
            "path": path,
            "filename": filename,
            "url": f"/agent/v1/tts/{filename}",
        }

    def should_async(self, text: str) -> bool:
        return len(text or "") >= SYNC_MAX_CHARS


tts_wrapper = TtsServiceWrapper()