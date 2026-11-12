# app/skills/document_qa/skill.py
"""DocumentQA Skill：针对上传文档提问。"""
from __future__ import annotations

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.document_qa.service import document_qa_service


class DocumentQaSkill(BaseSkill):
    name = "document_qa"
    description = (
        "针对用户上传的单个文档回答问题、解释章节、查找内容。"
        "适用于：'这份文档里XX是什么'、'第三章讲了什么'、'解释文档里的YY'。"
        "不适用于：概括/总结文档（用 document_summarize）、知识库查询（用 kb_ask）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "文档 file_id"},
            "question": {"type": "string", "description": "用户问题"},
        },
        "required": ["file_id", "question"],
    }
    intent = "document_qa"
    category = "content"
    sync = True
    returns = ["text"]
    requires_attachments = ["doc"]
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(self, file_id: str, question: str = "", **kwargs) -> Result:
        if not question:
            question = "请介绍一下这份文档的主要内容"
        answer = document_qa_service.answer(file_id, question)
        return Result(parts=[text_part(answer)])


skill = DocumentQaSkill()