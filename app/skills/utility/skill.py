# app/skills/utility/skill.py
"""Utility Skill：本地小工具组合。"""
from __future__ import annotations

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.utility.service import utility_service


class UtilitySkill(BaseSkill):
    name = "utility"
    description = (
        "本地小工具集合（不需要联网）。适用于：日期时间查询（今天几号/现在几点）、"
        "数学计算（123*456）、百分比、单位换算、温度换算、日期差、随机选择。"
        "不适用于：需要联网的实时数据（用 web_search）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "用户输入"},
        },
        "required": ["text"],
    }
    intent = "utility"
    category = "general"
    sync = True
    returns = ["text"]
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(self, text: str = "", **kwargs) -> Result:
        if not text:
            text = kwargs.get("query") or ""
        result = utility_service.handle(text)
        return Result(parts=[text_part(result.get("content", ""))])


skill = UtilitySkill()