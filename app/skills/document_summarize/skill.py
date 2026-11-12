# app/skills/document_summarize/skill.py
"""DocumentSummarize Skill：文档概括/提取/整理。"""
from __future__ import annotations

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.document_summarize.service import document_summarize_service


class DocumentSummarizeSkill(BaseSkill):
    name = "document_summarize"
    description = (
        "概括、提取、整理用户上传文档的内容。"
        "适用于：'总结这份文档'、'提取要点'、'整理成报告'。"
        "不适用于：具体问题提问（用 document_qa）、知识库查询（用 kb_ask）。"
        "如果用户要'总结后导出Word/PPT'，本 Skill 只输出文本，导出由 generate_word/generate_ppt 完成。"
    )
    schema = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "文档 file_id"},
            "file_ids": {
                "type": "array",
                "description": "多文档 file_id 列表（批量场景），与 file_id 二选一",
            },
            "user_query": {"type": "string", "description": "概括要求，如'提取核心数据'"},
        },
        "required": [],
    }
    intent = "document_summarize"
    category = "content"
    sync = True
    returns = ["text"]
    requires_attachments = ["doc"]
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, file_id: str = "", file_ids: list = None,
        user_query: str = "提取核心信息", **kwargs,
    ) -> Result:
        file_ids = file_ids or []
        if file_ids and len(file_ids) > 1:
            result = document_summarize_service.summarize_batch(file_ids, user_query)
        elif file_id:
            result = document_summarize_service.summarize(file_id, user_query)
        elif file_ids:
            result = document_summarize_service.summarize(file_ids[0], user_query)
        else:
            return Result(parts=[text_part("未提供文档")])
        return Result(parts=[text_part(result)])


skill = DocumentSummarizeSkill()