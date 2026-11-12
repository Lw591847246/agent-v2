# app/registry/skill_registry.py
"""Skill 注册中心。"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.contracts.skill import BaseSkill
from app.core.logger import logger


class SkillRegistry:
    def __init__(self):
        self._by_name: Dict[str, BaseSkill] = {}
        self._by_intent: Dict[str, BaseSkill] = {}

    # ---------- 注册 ----------
    def register(self, skill: BaseSkill) -> None:
        if not skill.name:
            raise ValueError(f"Skill {skill.__class__.__name__} missing name")
        if skill.name in self._by_name:
            logger.warning(f"[SkillRegistry] {skill.name} 已存在，覆盖")
        self._by_name[skill.name] = skill
        if skill.intent:
            if skill.intent in self._by_intent:
                logger.warning(
                    f"[SkillRegistry] intent={skill.intent} 已绑定 "
                    f"{self._by_intent[skill.intent].name}，改为 {skill.name}"
                )
            self._by_intent[skill.intent] = skill
        logger.info(
            f"[SkillRegistry] 注册 {skill.name} "
            f"intent={skill.intent} category={skill.category}"
        )

    # ---------- 查询 ----------
    def by_name(self, name: str) -> Optional[BaseSkill]:
        return self._by_name.get(name)

    def by_intent(self, intent: str) -> Optional[BaseSkill]:
        return self._by_intent.get(intent)

    def all(self) -> List[BaseSkill]:
        return list(self._by_name.values())

    def names(self) -> List[str]:
        return list(self._by_name.keys())

    def intents(self) -> List[str]:
        return list(self._by_intent.keys())

    # ---------- schema 导出 ----------
    def get_schemas(self, category: Optional[str] = None) -> List[dict]:
        """导出为 OpenAI tools 格式。"""
        result = []
        for skill in self._by_name.values():
            if category and skill.category != category and skill.category != "general":
                continue
            if not skill.visible_to_planner:
                continue
            result.append(skill.to_fc_tool())
        return result

    def get_l2_skills(self) -> List[BaseSkill]:
        return [s for s in self._by_name.values() if s.visible_to_l2]

    # ---------- 统计 ----------
    def stats(self) -> dict:
        return {
            "total": len(self._by_name),
            "with_intent": len(self._by_intent),
            "categories": self._categories(),
        }

    def _categories(self) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for s in self._by_name.values():
            c[s.category] = c.get(s.category, 0) + 1
        return c


registry = SkillRegistry()