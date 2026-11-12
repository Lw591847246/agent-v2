# app/application/tts/cosyvoice_engine.py
"""CosyVoice2 引擎（HTTP 调用）"""
import asyncio
import hashlib
import os
import shutil
import requests
from typing import Dict, List

from app.core.logger import logger
from app.infrastructure.tts.base import BaseTTSEngine

TTS_CACHE_DIR = "./data/tts"
os.makedirs(TTS_CACHE_DIR, exist_ok=True)


class CosyVoiceEngine(BaseTTSEngine):

    def __init__(self, base_url: str, timeout: int = 600, default_ref_audio: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.default_ref_audio = os.path.abspath(default_ref_audio) if default_ref_audio else ""

        # ⭐ 参考音频 → 参考文本（必须一一对应！）
        self._ref_text_map = {
            "anime_narrator11.wav": "这个呀，就是我们精心制作准备的纪念品，大家可以看到这个色泽和这个材质啊，哎呀多么的光彩照人。",
        }
        self._default_ref_text = "这个呀，就是我们精心制作准备的纪念品，大家可以看到这个色泽和这个材质啊，哎呀多么的光彩照人。"

    def _get_ref_audio(self, voice: str) -> str:
        if voice and voice in ("云健", "晓晓"):
            # CosyVoice 目前只用同一个参考音频
            return self.default_ref_audio
        return self.default_ref_audio

    def _get_ref_text(self, ref_audio: str) -> str:
        basename = os.path.basename(ref_audio)
        return self._ref_text_map.get(basename, self._default_ref_text)

    def _call(self, text: str, voice: str = "") -> dict:
        if not text or not text.strip():
            raise ValueError("TTS 文本为空")

        ref_audio = self._get_ref_audio(voice)
        if not os.path.isfile(ref_audio):
            raise FileNotFoundError(f"参考音频不存在: {ref_audio}")

        ref_text = self._get_ref_text(ref_audio)

        payload = {
            "text": text,
            "ref_audio": ref_audio,
            # ⭐ CosyVoice2 需要 ref_text，但 api_server 内部自己维护了映射，
            # 所以这里不用传（如果未来 api_server 支持就加上）
        }

        logger.info(f"【CosyVoice2】请求 {self.base_url}/synthesize, {len(text)} 字")
        logger.info(f"【CosyVoice2】参考音频: {ref_audio}")
        logger.info(f"【CosyVoice2】参考文本: {ref_text[:40]}...")

        try:
            r = requests.post(
                f"{self.base_url}/synthesize",
                json=payload, timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("audio_path") or not os.path.isfile(data["audio_path"]):
                raise RuntimeError(f"CosyVoice2 返回音频不存在: {data.get('audio_path')}")
            logger.info(f"【CosyVoice2】✅ 完成 dur={data.get('duration', 0):.1f}s")
            return data
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"CosyVoice2 服务未启动: {self.base_url}")

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
        ]

    def get_audio_path(self, filename: str) -> str:
        return os.path.join(TTS_CACHE_DIR, filename)