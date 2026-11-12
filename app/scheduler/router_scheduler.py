# app/scheduler/router_scheduler.py
"""Router 调度器：L0/L1/L2 → intent → Skill + kwargs。"""
from __future__ import annotations

from typing import Optional

from app.contracts.context import RequestContext
from app.contracts.scheduler import BaseScheduler, RoutingDecision
from app.core.logger import logger
from app.registry.skill_registry import SkillRegistry, registry
from app.scheduler.intent_router import IntentRouter, intent_router
from app.scheduler.router_provider import RouterParameterProvider, router_parameter_provider


class RouterScheduler(BaseScheduler):

    def __init__(
        self,
        router: Optional[IntentRouter] = None,
        skill_registry: Optional[SkillRegistry] = None,
        provider: Optional[RouterParameterProvider] = None,
    ):
        self.router = router or intent_router
        self.registry = skill_registry or registry
        self.provider = provider or router_parameter_provider

    def route(self, ctx: RequestContext) -> RoutingDecision:
        # 1. L0/L1/L2
        routing = self.router.route(
            text=ctx.text,
            has_attachment=bool(ctx.attachments),
            attachment_types=[a.type for a in ctx.attachments],
            recent_intents=ctx.recent_intents,
        )

        # 2. intent → skill
        skill = self.registry.by_intent(routing.intent.value) if routing.intent else None
        if not skill:
            logger.warning(f"[RouterScheduler] intent={routing.intent} 无对应 Skill，降级 chat")
            skill = self.registry.by_intent("chat")
            if not skill:
                return RoutingDecision(
                    skill=None,
                    kwargs={},
                    intent="chat",
                    confidence=0.0,
                    reason="no_skill_for_intent",
                )

        # 3. 参数映射
        try:
            kwargs = self.provider.provide(ctx, skill, routing.params or {})
        except Exception as e:
            logger.warning(f"[RouterScheduler] 参数映射失败: {e}，降级 chat")
            skill = self.registry.by_intent("chat") or skill
            kwargs = self.provider.provide(ctx, skill, {})

        # 4. 决策
        return RoutingDecision(
            skill=skill,
            kwargs=kwargs,
            is_plan=False,
            intent=routing.intent.value if routing.intent else "",
            confidence=routing.confidence,
            reason=routing.reasoning,
        )


router_scheduler = RouterScheduler()