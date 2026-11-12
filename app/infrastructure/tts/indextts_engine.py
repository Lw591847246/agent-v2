# app/application/tts/indextts_engine.py
"""IndexTTS2 引擎（HTTP 调用）"""
import asyncio
import hashlib
import os
import shutil
import time
import requests
from typing import Dict, List

from app.core.logger import logger
from app.infrastructure.tts.base import BaseTTSEngine

TTS_CACHE_DIR = "./data/tts"
os.makedirs(TTS_CACHE_DIR, exist_ok=True)


class IndexTTSEngine(BaseTTSEngine):

    def __init__(self, base_url: str, timeout: int = 1800, default_ref_audio: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.default_ref_audio = os.path.abspath(default_ref_audio) if default_ref_audio else ""

        self._voice_map = {
            "云健": "./data/voices/anime_narrator11.wav",
            "晓晓": "./data/voices/anime_narrator11.wav",
        }

    def _get_ref_audio(self, voice: str) -> str:
        if voice and voice in self._voice_map:
            path = os.path.abspath(self._voice_map[voice])
            if os.path.isfile(path):
                return path
        return self.default_ref_audio

    def _call(self, text: str, voice: str = "") -> dict:
        if not text or not text.strip():
            raise ValueError("TTS 文本为空")

        ref_audio = self._get_ref_audio(voice)
        if not os.path.isfile(ref_audio):
            raise FileNotFoundError(f"参考音频不存在: {ref_audio}")

        payload = {
            "text": text,
            "ref_audio": ref_audio,
            "lang": "ZH",
            "duration_factor": 1.0,
            "max_segment_tokens": 500,
            "interval_silence": 100,
            "more_segment_before": 8,
            "text_normalization": True,
            "stream_return": True,
        }

        logger.info(f"【IndexTTS2】请求 {self.base_url}/synthesize, {len(text)} 字")

        try:
            r = requests.post(
                f"{self.base_url}/synthesize",
                json=payload, timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("audio_path") or not os.path.isfile(data["audio_path"]):
                raise RuntimeError(f"IndexTTS2 返回音频不存在: {data.get('audio_path')}")
            logger.info(f"【IndexTTS2】✅ 完成 dur={data.get('duration', 0):.1f}s")
            return data
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"IndexTTS2 服务未启动: {self.base_url}")

    def synthesize_wav(self, text: str, voice: str = "") -> str:
        return self._call(text, voice)["audio_path"]

    async def synthesize(self, text: str, voice: str = "") -> dict:
        return await asyncio.to_thread(self._synthesize_sync, text, voice)

    def _synthesize_sync(self, text: str, voice: str) -> dict:
        data = self._call(text, voice)
        key = hashlib.sha256(f"{voice}|{text}".encode()).hexdigest()[:16]
        dst = os.path.join(TTS_CACHE_DIR, f"{key}.wav")
        shutil.copy2(data["audio_path"], dst)
        return {"audio_url": f"/agent/v1/tts/{key}.wav"}

    async def synthesize_with_timeline(
        self, text: str, voice: str = "", speed: float = 1.0,
        task_id: str = "", tts_params: dict = None
    ) -> dict:
        return await asyncio.to_thread(self._call, text, voice)

    def list_voices(self) -> List[Dict]:
        return [
            {"id": "anime_narrator", "name": "动漫旁白", "gender": "female"},
            {"id": "zm_yunjian", "name": "云健", "gender": "male"},
        ]

    def get_audio_path(self, filename: str) -> str:
        return os.path.join(TTS_CACHE_DIR, filename)