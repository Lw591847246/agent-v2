# app/scheduler/planner_scheduler.py
"""Planner 调度器：LLM 生成多步计划，返回 is_plan=True。"""
from __future__ import annotations

from app.contracts.context import RequestContext
from app.contracts.scheduler import BaseScheduler, RoutingDecision
from app.core.logger import logger
from app.scheduler.intents import get_domain


class PlannerScheduler(BaseScheduler):

    def route(self, ctx: RequestContext) -> RoutingDecision:
        domain = "general"
        try:
            # 简单场景判断 domain：由调用方通过 ctx 传入
            domain = ctx.params.get("domain", "general") if ctx.params else "general"
        except Exception:
            pass

        try:
            from app.scheduler.planner import generate_plan
        except ImportError:
            logger.error("[PlannerScheduler] planner.generate_plan 未实现")
            return RoutingDecision(is_plan=False, reason="planner_not_implemented")

        plan_data = generate_plan(
            user_text=ctx.text,
            domain=domain,
            attachments=[{"name": a.name, "type": a.type} for a in ctx.attachments],
            history=ctx.history,
            summary=ctx.summary,
        )
        plan = plan_data.get("plan") or []
        if not plan:
            logger.warning("[PlannerScheduler] 计划为空，降级")
            return RoutingDecision(is_plan=False, reason="empty_plan")

        return RoutingDecision(
            is_plan=True,
            plan=plan,
            reason=plan_data.get("reasoning", ""),
        )


planner_scheduler = PlannerScheduler()