"""TTS 业务：短文本同步 + 长文本异步。"""
from __future__ import annotations

import os
import shutil

from app.core.logger import logger
from app.infrastructure.services.tts_service import tts_service


TTS_CACHE_DIR = "./data/tts"
os.makedirs(TTS_CACHE_DIR, exist_ok=True)

# 同步阈值：小于此字符数同步返回，否则走异步任务
SYNC_MAX_CHARS = 100


class TtsServiceWrapper:

    def synthesize_sync(self, text: str, voice: str = "") -> dict:
        """
        同步合成。返回 {path, filename, url}
        """
        path = tts_service.synthesize_wav(text, voice)
        if not path or not os.path.isfile(path):
            raise RuntimeError(f"TTS 未生成有效文件: {path}")

        filename = os.path.basename(path)

        # ⭐ 核心修复：把文件复制到 ./data/tts/，保证 /agent/v1/tts/{filename} 能访问到
        dst = os.path.join(TTS_CACHE_DIR, filename)
        if os.path.abspath(path) != os.path.abspath(dst):
            shutil.copy2(path, dst)
            logger.info(f"[TTS] 已复制到缓存目录: {dst}")

        return {
            "path": dst,
            "filename": filename,
            "url": f"/agent/v1/tts/{filename}",
        }

    def should_async(self, text: str) -> bool:
        return len(text or "") >= SYNC_MAX_CHARS


tts_wrapper = TtsServiceWrapper()