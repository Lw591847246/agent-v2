# app/skills/custom_avatar/skill.py
"""CustomAvatar Skill：定制数字人（图 + 动作 + 台词）。"""
from __future__ import annotations

from app.contracts.result import Result, card_part, text_part
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.custom_avatar.service import custom_avatar_service, parse_user_input
from app.utils.file_registry import file_registry


class CustomAvatarSkill(BaseSkill):
    name = "generate_custom_avatar"
    description = (
        "根据图片和提示词生成带有特定动作和口型的短视频。适用于用户需要定制动作、特殊背景的 AI 播报。"
        "适用于：'让这张图说话/对口型/定制数字人/加上挥手动作'。"
        "不适用于：模板数字人（用 portrait_animation）、图生视频无台词（用 image_to_video）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "image_file_id": {"type": "string", "description": "人物图片 file_id"},
            "raw_text": {"type": "string", "description": "用户原始输入（含动作描述和台词），可空"},
            "prompt": {"type": "string", "description": "画面动作描述，可空"},
            "speech_text": {"type": "string", "description": "台词，可空"},
            "user_text": {"type": "string", "description": "用户原始输入（同 raw_text）"},
        },
        "required": ["image_file_id"],
    }
    intent = "custom_avatar"
    category = "video"
    sync = False
    returns = ["card", "video"]
    requires_attachments = ["image"]
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, image_file_id: str = "", raw_text: str = "",
        prompt: str = "", speech_text: str = "",
        user_text: str = "", session_id: str = "", user_id: int = 0,
        **kwargs,
    ) -> Result:
        if not image_file_id:
            return Result(parts=[text_part("请上传一张图片，我才能生成定制播报视频。")])

        meta = file_registry.get(image_file_id)
        if not meta or not meta.get("path"):
            return Result(parts=[text_part("❌ 找不到上传的图片")])

        raw = raw_text or user_text or kwargs.get("text") or ""
        if raw:
            parsed_prompt, parsed_speech = parse_user_input(raw)
            prompt = parsed_prompt or prompt or "人物自然说话"
            speech_text = parsed_speech or speech_text or "大家好，我是 AI 数字人。"

        logger.info(f"[CustomAvatar] img={image_file_id} text={raw[:60]}")

        task_id = custom_avatar_service.submit(
            session_id=session_id,
            image_path=meta["path"],
            prompt=prompt,
            speech_text=speech_text,
            user_id=user_id,
        )

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在定制数字人视频",
                "description": "正在执行画面生成 → 音频合成 → 口型对齐…",
                "progress": 10,
                "steps": [
                    {"label": "ComfyUI 生成画面", "status": "running"},
                    {"label": "IndexTTS 音频合成", "status": "pending"},
                    {"label": "MuseTalk 对口型", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)


skill = CustomAvatarSkill()