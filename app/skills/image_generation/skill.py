"""ImageGeneration Skill：文生图，异步任务型。"""
from __future__ import annotations

import time
from typing import Optional

from app.context.task_runner import task_runner
from app.contracts.result import (
    Result, build_output, card_part, image_part, text_part,
)
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.skills.image_generation.service import image_generation_service
from app.utils.image_storage import save_image


class ImageGenerationSkill(BaseSkill):
    name = "generate_image"
    description = (
        "根据文本描述生成图片（文生图）。适用于：'画一张/画一个/生成图片/生成一张图/帮我画'。"
        "不适用于：修改已有图片（用 edit_image）、图片理解（用 image_understand）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "画面描述"},
            "width": {"type": "integer", "description": "宽度，可空"},
            "height": {"type": "integer", "description": "高度，可空"},
        },
        "required": ["prompt"],
    }
    intent = "image_generation"
    category = "image"
    sync = False
    returns = ["card", "image"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, prompt: str, width: int = None, height: int = None,
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not prompt or not prompt.strip():
            return Result(parts=[text_part("请描述你要生成的图片")])

        task_id = task_runner.submit(
            skill_name="image_generation",
            session_id=session_id,
            user_id=user_id,
            slots={"prompt": prompt[:500], "width": width, "height": height},
            fn=lambda tid: self._run_async(tid, prompt, width, height, session_id, user_id),
            provider="comfyui",
        )

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在生成图片",
                "description": "Qwen Image 处理中...",
                "progress": 10,
                "steps": [
                    {"label": "解析参数", "status": "done"},
                    {"label": "生成图片", "status": "running"},
                    {"label": "保存图片", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)

    def _run_async(
        self, task_id: str, prompt: str,
        width: Optional[int], height: Optional[int],
        session_id: str, user_id: int,
    ) -> None:
        start = time.time()
        current_steps = [
            {"label": "解析参数", "status": "done"},
            {"label": "生成图片", "status": "running"},
            {"label": "保存图片", "status": "pending"},
        ]
        try:
            task_runner.progress(task_id, 20, "生成图片中...", steps=current_steps)

            result = image_generation_service.generate(prompt, width, height)
            if not result.get("image_base64"):
                raise RuntimeError("生图返回空数据")

            current_steps[1]["status"] = "done"
            current_steps[2]["status"] = "running"
            task_runner.progress(task_id, 80, "保存图片中...", steps=current_steps)

            storage = save_image(user_id, session_id, result["image_base64"])
            duration_ms = int((time.time() - start) * 1000)

            if not session_id:
                from app.infrastructure.repositories.task_repo import task_repo
                info = task_repo.get(task_id)
                if info and info.session_id:
                    session_id = info.session_id

            msg_id = conversation_repo.get_message_id_by_task_id(session_id, task_id)

            if msg_id:
                image_url = f"/agent/v1/image/{msg_id}"
                thumb_url = f"/agent/v1/thumb/{msg_id}"
                # ⭐ 核心：添加 text_part，实现“上方图片，下方文字”
                parts = [
                    image_part(image_url, thumb_url),
                    text_part("图片已生成"),
                ]
                outputs = [build_output(
                    kind="image",
                    filename=storage["filename"],
                    path=storage["image_path"],
                    url=image_url,
                    thumb=thumb_url,
                    task_id=task_id,
                )]
                task_runner.finish(
                    task_id=task_id,
                    parts=parts,
                    outputs=outputs,
                    output_path=storage["image_path"],
                    duration_ms=duration_ms,
                    session_id=session_id,
                    message_extra={
                        "image_filename": storage["filename"],
                        "thumb_filename": storage["thumb_filename"],
                        "message_type": "image",
                    },
                )
                logger.info(f"[ImageGeneration] 完成 task_id={task_id} msg_id={msg_id}")
            else:
                logger.warning(f"[ImageGeneration] 找不到 msg_id task_id={task_id} session_id={session_id}")
                parts = [text_part("图片已生成，但消息未找到")]
                task_runner.finish(
                    task_id=task_id, parts=parts,
                    duration_ms=duration_ms, session_id=session_id,
                )
        except Exception as e:
            logger.error(f"[ImageGeneration] 失败 task_id={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)


skill = ImageGenerationSkill()