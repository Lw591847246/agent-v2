# app/skills/knowledge/skill.py
"""Knowledge Skill：知识库查询 + 问答。"""
from __future__ import annotations

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.knowledge.service import knowledge_service


class KnowledgeSkill(BaseSkill):
    name = "kb_ask"
    description = (
        "查询本地知识库并生成带引用的答案。适用于：用户问'知识库里关于XX的内容'、"
        "'查知识库'、'根据知识库回答XX'。不适用于：问上传的文档（用 document_qa）、通用问答（用 chat）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "用户问题"},
            "top_k": {"type": "integer", "description": "检索片段数，默认 5"},
        },
        "required": ["question"],
    }
    intent = "knowledge_query"
    category = "content"
    sync = True
    returns = ["text"]
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(self, question: str, top_k: int = 5, **kwargs) -> Result:
        # 先查有没有内容
        docs = knowledge_service.query(question, top_k)
        if not docs:
            # 知识库空 / 未命中 → 降级 chat
            try:
                from app.skills.chat.service import chat_service
                answer = chat_service.chat(question)
                return Result(parts=[text_part(answer)])
            except Exception:
                return Result(parts=[text_part("知识库中未找到相关内容。")])

        answer = knowledge_service.ask(question, top_k)
        return Result(parts=[text_part(answer)])


skill = KnowledgeSkill()