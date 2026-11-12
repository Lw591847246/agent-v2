# app/context/conversation_orchestrator.py
"""回合后置处理：feedback 记录 + summary 触发。"""
from __future__ import annotations

from typing import Optional

from app.context.intent_feedback_service import intent_feedback_service
from app.application.conversation_service import conversation_service
from app.contracts.context import RequestContext
from app.core.logger import logger


class ConversationOrchestrator:
    def after_turn(
        self,
        ctx: RequestContext,
        routing=None,
        skill_name: str = "",
    ) -> None:
        # 1. 记录意图反馈
        if routing and ctx.session_id:
            try:
                intent_feedback_service.record(
                    session_id=ctx.session_id,
                    user_id=ctx.user_id,
                    text=ctx.text,
                    attachment_types=[a.type for a in ctx.attachments],
                    routing=routing,
                )
            except Exception as e:
                logger.warning(f"[Orchestrator] feedback failed: {e}")

        # 2. 触发 summary
        if ctx.session_id:
            try:
                conversation_service.maybe_generate_summary(ctx.session_id)
            except Exception as e:
                logger.warning(f"[Orchestrator] summary failed: {e}")


conversation_orchestrator = ConversationOrchestrator()