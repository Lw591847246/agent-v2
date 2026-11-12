# app/skills/image_understanding/skill.py
"""ImageUnderstanding Skill：图片描述/OCR/解题。"""
from __future__ import annotations

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.image_understanding.service import image_understanding_service


class ImageUnderstandingSkill(BaseSkill):
    name = "image_understand"
    description = (
        "理解图片内容：描述图片、OCR 提取文字、解答题目。"
        "适用于：用户上传图片并问'这是什么/描述/提取文字/解这道题'。"
        "不适用于：修改图片（用 edit_image）、纯文本问题。"
    )
    schema = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "图片 file_id"},
            "question": {"type": "string", "description": "用户问题，可空"},
            "mode": {
                "type": "string",
                "enum": ["describe", "ocr", "solve"],
                "description": "模式：describe=描述（默认）、ocr=提取文字、solve=解题",
            },
        },
        "required": ["file_id"],
    }
    intent = "image_understanding"
    category = "image"
    sync = True
    returns = ["text"]
    requires_attachments = ["image"]
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, file_id: str, question: str = "", mode: str = "", **kwargs,
    ) -> Result:
        answer = image_understanding_service.understand(file_id, question, mode)
        return Result(parts=[text_part(answer)])


skill = ImageUnderstandingSkill()