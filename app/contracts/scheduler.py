# app/contracts/scheduler.py
"""Scheduler + ParameterProvider 契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Optional

from app.contracts.action import ActionRequest
from app.contracts.context import RequestContext
from app.contracts.skill import BaseSkill


class BaseScheduler(ABC):
    """调度器：决定调哪个 Skill、填什么参数。"""

    @abstractmethod
    def route(self, ctx: RequestContext) -> "RoutingDecision":
        ...

    def route_action(self, action: ActionRequest) -> "RoutingDecision":
        raise NotImplementedError


class BaseParameterProvider(ABC):
    """参数提供器：把 ctx/args/params 归一化为 kwargs。"""

    @abstractmethod
    def provide(self, source: Any, skill: BaseSkill, raw: dict) -> dict:
        ...


@dataclass
class RoutingDecision:
    """调度决策。"""
    skill: Optional[BaseSkill] = None
    kwargs: dict = field(default_factory=dict)
    plan: List[dict] = field(default_factory=list)
    is_plan: bool = False
    intent: str = ""
    confidence: float = 0.0
    reason: str = ""