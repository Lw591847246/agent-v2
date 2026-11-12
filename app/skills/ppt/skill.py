# app/skills/ppt/skill.py
"""Ppt Skill：生成 PPT 文件，异步任务型。"""
from __future__ import annotations

from app.contracts.result import Result, card_part, text_part
from app.contracts.skill import BaseSkill
from app.skills.ppt.service import ppt_service


class PptSkill(BaseSkill):
    name = "generate_ppt"
    description = (
        "根据文本内容或要求生成 PPT 文件。适用于：'生成PPT/做PPT/演示文稿/slides'。"
        "不适用于：生成Word（用 generate_word）、思维导图（用 mindmap）。"
        "如果用户先要'总结文档再导出PPT'，先调 document_summarize，再调本 Skill。"
    )
    schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "生成 PPT 所需的核心内容、大纲或要求"},
            "user_query": {"type": "string", "description": "额外风格要求，如'科技感、8页'"},
        },
        "required": ["content"],
    }
    intent = "ppt_generation"
    category = "content"
    sync = False
    returns = ["card", "file_download"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, content: str = "", user_query: str = "",
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not content or not content.strip():
            return Result(parts=[text_part("缺少生成 PPT 的内容")])

        task_id = ppt_service.submit(
            content=content, user_query=user_query,
            session_id=session_id, user_id=user_id,
        )

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在制作 PPT",
                "description": "Agent 正在为您定制大纲与设计版式…",
                "progress": 10,
                "steps": [
                    {"label": "解析要求与上下文", "status": "running"},
                    {"label": "智能提炼大纲与文档", "status": "pending"},
                    {"label": "渲染背景图", "status": "pending"},
                    {"label": "组装 PPT 并生成文件", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)


skill = PptSkill()