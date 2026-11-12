# app/skills/anchor/skill.py
"""Anchor Skill：孔子画像授课视频。"""
from __future__ import annotations

from app.contracts.result import Result, card_part, text_part
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.anchor.service import anchor_service, extract_speech_text


class AnchorSkill(BaseSkill):
    name = "generate_standard_anchor"
    description = (
        "使用孔子画像和预设模板，快速生成一段 AI 授课视频（适用于成语故事、文言文领读）。"
        "适用于：'孔子/画像/讲课/模板/数字人口播'。"
        "不适用于：定制数字人（用 custom_avatar）、图生视频（用 image_to_video）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "speech_text": {
                "type": "string",
                "description": "孔子要讲授的课文内容或台词，可空；为空时从 user_text 提取",
            },
            "user_text": {
                "type": "string",
                "description": "用户原始输入（用于提取台词）",
            },
        },
        "required": [],
    }
    intent = "portrait_animation"
    category = "video"
    sync = False
    returns = ["card", "video"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, speech_text: str = "", user_text: str = "",
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not speech_text:
            speech_text = user_text or kwargs.get("text") or ""

        final_speech = extract_speech_text(speech_text)
        if not final_speech:
            return Result(parts=[text_part("请提供孔子要讲授的内容。")])

        logger.info(f"[AnchorSkill] session={session_id} speech={final_speech[:60]}")

        task_id = anchor_service.submit(
            session_id=session_id, speech_text=final_speech, user_id=user_id,
        )

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "孔子上课啦",
                "description": "正在生成授课视频...",
                "progress": 10,
                "steps": [
                    {"label": "台词解析", "status": "running"},
                    {"label": "语音合成", "status": "pending"},
                    {"label": "动作与口型生成", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)


skill = AnchorSkill()