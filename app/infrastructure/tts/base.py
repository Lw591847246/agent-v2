# app/application/tts/base.py
"""TTS 引擎抽象接口"""
from abc import ABC, abstractmethod
from typing import Dict, List


class BaseTTSEngine(ABC):
    """所有 TTS 引擎的统一接口"""

    @abstractmethod
    def synthesize_wav(self, text: str, voice: str = "") -> str:
        """
        同步生成音频，返回本地绝对路径。
        用于：MuseTalk 对口型、视频合成。
        """
        pass

    @abstractmethod
    async def synthesize(self, text: str, voice: str = "") -> dict:
        """
        异步生成音频，返回 {"audio_url": "/xxx.wav"}。
        用于：纯 TTS 消息播放。
        """
        pass

    @abstractmethod
    async def synthesize_with_timeline(
        self, text: str, voice: str = "", speed: float = 1.0,
        task_id: str = "", tts_params: dict = None
    ) -> dict:
        """
        异步生成音频，返回带时间轴的信息。
        用于：漫剧/广播剧对齐。
        """
        pass

    @abstractmethod
    def list_voices(self) -> List[Dict]:
        """返回支持的音色列表"""
        pass

    @abstractmethod
    def get_audio_path(self, filename: str) -> str:
        """根据文件名返回本地路径（用于静态文件服务）"""
        pass