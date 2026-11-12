# app/skills/code/skill.py
"""Code Skill：代码生成。"""
from __future__ import annotations

from typing import Iterator

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.code.service import code_service


class CodeSkill(BaseSkill):
    name = "code_generate"
    description = (
        "根据需求生成代码。适用于：'写一个函数/实现XX/改这段代码/用Python写XX'。"
        "不适用于：解释代码含义（用 chat）、执行代码（后续 code_execute）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "requirement": {"type": "string", "description": "代码需求描述"},
            "language": {"type": "string", "description": "编程语言，可空"},
        },
        "required": ["requirement"],
    }
    intent = "code_generation"
    category = "general"
    sync = True
    returns = ["text"]
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(self, requirement: str, language: str = "", **kwargs) -> Result:
        answer = code_service.generate(requirement, language)
        return Result(parts=[text_part(answer)])

    def stream(self, requirement: str, language: str = "", **kwargs) -> Iterator[str]:
        yield from code_service.generate_stream(requirement, language)


skill = CodeSkill()