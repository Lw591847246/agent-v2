"""Storyboard Skill：故事 → 漫剧/短剧/有声绘本。"""
from __future__ import annotations

from typing import List

from app.contracts.result import Result, card_part, text_part
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.storyboard.extractor import detect_style, extract_story
from app.skills.storyboard.service import storyboard_service


class StoryboardSkill(BaseSkill):
    name = "generate_storyboard"
    description = (
        "把一段故事文本改编成漫剧/有声绘本/广播剧（多场景视频拼接 + 旁白配音）。"
        "适用于：'做成漫剧/短剧/有声绘本/广播剧/分镜视频'、'把上一轮的故事做成视频'。"
        "不适用于：纯文本生视频（用 text_to_video）、图片动起来（用 image_to_video）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "story": {
                "type": "string",
                "description": "故事文本，可空；为空时从 user_text + history 提取",
            },
            "style": {
                "type": "string",
                "description": "视觉风格：realistic_cinema（写实电影，默认）/ anime_drama（漫剧）/ ink_wash（水墨）",
                "enum": ["realistic_cinema", "anime_drama", "ink_wash"],
            },
            "user_text": {
                "type": "string",
                "description": "用户原始输入（用于从 history 提取故事）",
            },
            "history": {
                "type": "array",
                "description": "历史对话（用于提取故事）",
            },
        },
        "required": [],
    }
    intent = "storyboard"
    category = "video"
    sync = False
    returns = ["card", "video"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, story: str = "", style: str = "realistic_cinema",
        user_text: str = "", history: List[dict] = None,
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        history = history or []

        # 没传 story 时，从 user_text + history 提取
        if not story:
            user_text = user_text or kwargs.get("text") or ""
            story, source_type = extract_story(user_text, history)
            logger.info(f"[Storyboard] 提取结果: source={source_type}, len={len(story)}")
            if not story:
                return Result(parts=[text_part(
                    "未找到故事内容。\n你可以：\n1. 直接粘贴故事\n2. 引用上一轮的故事"
                )])
            if style == "realistic_cinema":
                style = detect_style(user_text)

        if len(story) < 100:
            return Result(parts=[text_part(f"故事太短（{len(story)} 字），建议至少 100 字。")])

        task_id = storyboard_service.submit(
            session_id=session_id, story=story,
            style=style, user_id=user_id,
        )

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在制作漫剧",
                "description": "拆解故事 → 生成画面 → 拼接成片...",
                "progress": 5,
                "steps": [
                    {"label": "拆解故事场景", "status": "running"},
                    {"label": "合成旁白音频", "status": "pending"},
                    {"label": "生成画面", "status": "pending"},
                    {"label": "合成视频", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)


skill = StoryboardSkill()