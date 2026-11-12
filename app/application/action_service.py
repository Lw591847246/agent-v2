# app/application/action_service.py
"""卡片点击 action 处理。"""
from __future__ import annotations

from app.context.flow_state_store import flow_state_store
from app.contracts.action import ActionRequest
from app.contracts.result import Result, error_part
from app.core.logger import logger
from app.executor.executor import executor
from app.registry.skill_registry import registry


class ActionService:

    def handle(self, req: ActionRequest) -> Result:
        if not req.session_id or not req.card_id:
            return Result(parts=[error_part("参数缺失")])

        state = flow_state_store.load(req.session_id, req.card_id)
        if not state:
            logger.warning(f"[ActionService] state not found: {req.session_id}/{req.card_id}")
            return Result(parts=[error_part("该操作已过期，请重新发起")])

        skill = registry.by_name(state.skill_name) if state.skill_name else None
        if not skill:
            skill = registry.by_intent(state.intent)
        if not skill:
            logger.warning(f"[ActionService] 未知 Skill: {state.skill_name}")
            return Result(parts=[error_part("该操作对应的能力不存在")])

        return executor.run_action(skill, req)


action_service = ActionService()