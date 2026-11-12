# app/scheduler/fc_scheduler.py
"""FC 调度器（stub）。硬件升级后实现。"""
from __future__ import annotations

from app.contracts.context import RequestContext
from app.contracts.scheduler import BaseScheduler, RoutingDecision
from app.core.logger import logger


class FCScheduler(BaseScheduler):

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def route(self, ctx: RequestContext) -> RoutingDecision:
        if not self.enabled:
            raise NotImplementedError("FC scheduler not enabled")
        # 将来实现：LLM + tools → tool_calls → skill + arguments
        raise NotImplementedError("FC scheduler not implemented")


fc_scheduler = FCScheduler(enabled=False)