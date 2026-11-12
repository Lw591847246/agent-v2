# app/skills/ppt/hooks.py
"""PPT 背景图生成钩子。"""
from typing import Callable, Optional

from app.core.logger import logger

BgGeneratorHook = Callable[[str, str], Optional[str]]

_bg_generator: Optional[BgGeneratorHook] = None


def register_bg_generator(func: BgGeneratorHook):
    global _bg_generator
    _bg_generator = func
    logger.info(f"【PPT插件】成功注册文生图引擎: {func.__name__}")


def get_bg_generator() -> Optional[BgGeneratorHook]:
    return _bg_generator