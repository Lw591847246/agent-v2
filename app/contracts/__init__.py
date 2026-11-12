# app/contracts/__init__.py
"""契约层：所有跨层共享的数据结构和接口。"""
from app.contracts.action import ActionRequest
from app.contracts.context import RequestContext
from app.contracts.errors import (
    NotSupportedError, NotFoundError, SchedulerError,
    SkillError, SkillExecutionError, ValidationError,
)
from app.contracts.result import (
    Result, StreamResult, audio_part, build_output, card_part,
    error_part, file_part, image_part, text_part, video_part,
)
from app.contracts.scheduler import (
    BaseParameterProvider, BaseScheduler, RoutingDecision,
)
from app.contracts.skill import BaseSkill

__all__ = [
    "ActionRequest", "RequestContext",
    "SkillError", "ValidationError", "NotFoundError",
    "SkillExecutionError", "SchedulerError", "NotSupportedError",
    "Result", "StreamResult",
    "text_part", "image_part", "video_part", "audio_part",
    "card_part", "file_part", "error_part", "build_output",
    "BaseSkill", "BaseScheduler", "BaseParameterProvider", "RoutingDecision",
]