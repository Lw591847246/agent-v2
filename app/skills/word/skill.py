# app/skills/word/skill.py
"""Word Skill：生成 Word 文件，异步任务型。"""
from __future__ import annotations

from typing import List

from app.contracts.result import Result, card_part, text_part
from app.contracts.skill import BaseSkill
from app.skills.word.service import word_service


class WordSkill(BaseSkill):
    name = "generate_word"
    description = (
        "根据内容或要求生成 Word 文档。适用于：'生成Word/做Word/docx/导出文档/保存成文档'。"
        "不适用于：生成PPT（用 generate_ppt）、思维导图（用 mindmap）。"
        "支持'把上一轮的故事导出Word'这类引用。"
    )
    schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "生成 Word 所需内容或大纲"},
            "user_query": {"type": "string", "description": "额外要求，如字数、风格"},
            "history": {"type": "array", "description": "历史对话，可选"},
        },
        "required": ["content"],
    }
    intent = "word_generation"
    category = "content"
    sync = False
    returns = ["card", "file_download"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, content: str = "", user_query: str = "",
        session_id: str = "", user_id: int = 0,
        history: List[dict] = None, attachments: List[dict] = None,
        **kwargs,
    ) -> Result:
        if not content or not content.strip():
            return Result(parts=[text_part("缺少生成 Word 的内容")])

        task_id = word_service.submit(
            content=content, user_query=user_query,
            session_id=session_id, user_id=user_id,
            history=history or [],
            attachments=attachments or [],
        )

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在撰写 Word 文档",
                "description": "正在准备内容…",
                "progress": 10,
                "steps": [
                    {"label": "解析要求与上下文", "status": "running"},
                    {"label": "准备正文内容", "status": "pending"},
                    {"label": "排版并生成 Word 文件", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)


skill = WordSkill()