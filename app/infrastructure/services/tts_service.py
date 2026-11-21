# app/infrastructure/services/tts_service.py
"""TTS 服务：配置驱动，支持 IndexTTS2 / CosyVoice2 平滑切换。

GPU 锁说明：
- 同步方法用 `with gpu_lock.lease(...)`
- 异步方法用 `async with gpu_lock.async_lease(...)`（不阻塞事件循环）
- 锁粒度 = 一次合成请求（含上游推理 + 落盘）
- 资源类型按 config.tts.engine 自动选择 INDEXTTS2 / COSYVOICE2
"""
import os
import re

from app.config.loader import config
from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.logger import logger
from app.infrastructure.tts.base import BaseTTSEngine
from app.infrastructure.tts.indextts_engine import IndexTTSEngine
from app.infrastructure.tts.cosyvoice_engine import CosyVoiceEngine


_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


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

    def _resource(self) -> GPUResource:
        name = (config.get("tts.engine", "indextts2") or "indextts2").lower()
        return GPUResource.COSYVOICE2 if name == "cosyvoice2" else GPUResource.INDEXTTS2

    def _timeout(self) -> int:
        name = (config.get("tts.engine", "indextts2") or "indextts2").lower()
        if name == "cosyvoice2":
            return int(config.get("tts.cosyvoice2.timeout", "6000"))
        return int(config.get("tts.indextts2.timeout", "6000"))

    # ============================================================
    # 对外接口
    # ============================================================

    def synthesize_wav(self, text: str, voice: str = "") -> str:
        with gpu_lock.lease(
            self._resource(), task="tts-wav", timeout=self._timeout(),
        ):
            return self._engine.synthesize_wav(text, voice)

    async def synthesize(self, text: str, voice: str = ""):
        async with gpu_lock.async_lease(
            self._resource(), task="tts", timeout=self._timeout(),
        ):
            return await self._engine.synthesize(text, voice)

    async def synthesize_with_timeline(
        self, text: str, voice: str = "", speed: float = 1.05,
        task_id: str = "", tts_params: dict = None,
    ):
        async with gpu_lock.async_lease(
            self._resource(), task="tts-timeline", timeout=self._timeout(),
        ):
            return await self._engine.synthesize_with_timeline(
                text, voice, speed, task_id, tts_params
            )

    # ---- 以下不占 GPU，不加锁 ----

    def list_voices(self):
        return self._engine.list_voices()

    def get_audio_path(self, filename: str) -> str:
        """获取音频文件路径（文件名硬校验：只允许纯文件名，杜绝路径穿越）。

        - 拒绝任何分隔符、..、NUL
        - 再走 basename 兜底
        - 结果路径不在 engine 目录内视为异常返回 ""
        """
        if not filename or "\x00" in filename:
            logger.warning(f"[TtsService] 非法 filename: {filename!r}")
            return ""
        if "/" in filename or "\\" in filename or ".." in filename:
            logger.warning(f"[TtsService] 非法 filename: {filename!r}")
            return ""
        if not _SAFE_FILENAME_RE.match(filename):
            logger.warning(f"[TtsService] 非法 filename: {filename!r}")
            return ""
        name = os.path.basename(filename)
        return self._engine.get_audio_path(name)

    def warmup(self) -> None:
        pass


tts_service = TtsService()