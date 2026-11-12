# app/application/tts_service.py
"""TTS 服务：配置驱动，支持 IndexTTS2 / CosyVoice2 平滑切换"""
import os

from app.config.loader import config
from app.core.logger import logger
from app.infrastructure.tts.base import BaseTTSEngine
from app.infrastructure.tts.indextts_engine import IndexTTSEngine
from app.infrastructure.tts.cosyvoice_engine import CosyVoiceEngine


class TtsService:

    def __init__(self):
        self._engine: BaseTTSEngine = None
        self._init_engine()

    def _init_engine(self):
        engine_name = (config.get("tts.engine", "indextts2") or "indextts2").lower()
        default_ref_audio = os.path.abspath(
            config.get("tts.default_ref_audio", "./data/voices/anime_narrator11.wav")
        )

        if engine_name == "cosyvoice2":
            base_url = config.get("tts.cosyvoice2.base_url", "http://127.0.0.1:9011")
            timeout = int(config.get("tts.cosyvoice2.timeout", "600"))
            self._engine = CosyVoiceEngine(
                base_url=base_url,
                timeout=timeout,
                default_ref_audio=default_ref_audio,
            )
            logger.info(f"[TtsService] ✅ 使用 CosyVoice2 引擎 ({base_url})")
        else:
            base_url = config.get("tts.indextts2.base_url", "http://127.0.0.1:9010")
            timeout = int(config.get("tts.indextts2.timeout", "1800"))
            self._engine = IndexTTSEngine(
                base_url=base_url,
                timeout=timeout,
                default_ref_audio=default_ref_audio,
            )
            logger.info(f"[TtsService] ✅ 使用 IndexTTS2 引擎 ({base_url})")

    # ============================================================
    # 对外接口 —— 与原来完全一致（Agent 端零改动）
    # ============================================================

    def synthesize_wav(self, text: str, voice: str = "") -> str:
        return self._engine.synthesize_wav(text, voice)

    async def synthesize(self, text: str, voice: str = ""):
        return await self._engine.synthesize(text, voice)

    async def synthesize_with_timeline(
        self, text: str, voice: str = "", speed: float = 1.05,
        task_id: str = "", tts_params: dict = None,
    ):
        return await self._engine.synthesize_with_timeline(
            text, voice, speed, task_id, tts_params
        )

    def list_voices(self):
        return self._engine.list_voices()

    def get_audio_path(self, filename: str) -> str:
        return self._engine.get_audio_path(filename)

    def warmup(self) -> None:
        pass


tts_service = TtsService()