# app/skills/image_edit/skill.py
"""ImageEdit Skill：图生图 / 续改，异步任务型。"""
from __future__ import annotations

import base64
import time
from typing import Optional

from app.context.task_runner import task_runner
from app.contracts.result import (
    Result, build_output, card_part, image_part, text_part,
)
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.skills.image_edit.service import image_edit_service
from app.utils.image_storage import save_image


class ImageEditSkill(BaseSkill):
    name = "edit_image"
    description = (
        "基于已有图片进行修改（图生图）。适用于：'把这张图改成XX/去掉XX/换风格/变暗/变亮'、"
        "'把上一张图改一下'。不适用于：纯文本生图（用 generate_image）、图片理解（用 image_understand）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "修改要求"},
            "image_file_id": {"type": "string", "description": "输入图片 file_id，可空，为空则用最近一张图"},
            "denoise": {"type": "number", "description": "修改幅度 0-1，可空，默认 1.0"},
        },
        "required": ["prompt"],
    }
    intent = "image_edit"
    category = "image"
    sync = False
    returns = ["card", "image"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, prompt: str, image_file_id: str = "",
        denoise: float = None,
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not prompt or not prompt.strip():
            return Result(parts=[text_part("请描述你要的修改")])

        image_b64 = image_edit_service.resolve_image_b64(
            image_file_id=image_file_id,
            session_id=session_id,
            user_id=user_id,
        )
        if not image_b64:
            return Result(parts=[text_part("未找到可编辑的图片，请先上传或生成一张图")])

        task_id = task_runner.submit(
            skill_name="image_edit",
            session_id=session_id,
            user_id=user_id,
            slots={
                "prompt": prompt[:500],
                "image_file_id": image_file_id,
                "denoise": denoise,
            },
            fn=lambda tid: self._run_async(tid, prompt, image_b64, denoise, session_id, user_id),
            provider="comfyui",
        )

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在修改图片",
                "description": "图生图处理中...",
                "progress": 10,
                "steps": [
                    {"label": "读取原图", "status": "done"},
                    {"label": "生成修改", "status": "running"},
                    {"label": "保存图片", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)

    def _run_async(
        self, task_id: str, prompt: str, image_b64: str,
        denoise: Optional[float], session_id: str, user_id: int,
    ) -> None:
        start = time.time()
        try:
            task_runner.progress(task_id, 20, "处理原图...")
            image_bytes = base64.b64decode(image_b64)

            task_runner.progress(task_id, 40, "生成修改中...")
            result = image_edit_service.edit(
                prompt=prompt, image_bytes=image_bytes,
                denoise=denoise if denoise is not None else 1.0,
            )
            if not result.get("image_base64"):
                raise RuntimeError("图生图返回空数据")

            task_runner.progress(task_id, 80, "保存图片中...")
            storage = save_image(user_id, session_id, result["image_base64"])
            duration_ms = int((time.time() - start) * 1000)

            msg_id = conversation_repo.get_message_id_by_task_id(session_id, task_id)

            if msg_id:
                image_url = f"/agent/v1/image/{msg_id}"
                thumb_url = f"/agent/v1/thumb/{msg_id}"
                parts = [image_part(image_url, thumb_url)]
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
                logger.info(f"[ImageEdit] 完成 task_id={task_id} msg_id={msg_id}")
            else:
                parts = [text_part("图片已修改，但消息未找到")]
                task_runner.finish(
                    task_id=task_id, parts=parts,
                    duration_ms=duration_ms, session_id=session_id,
                )
        except Exception as e:
            logger.error(f"[ImageEdit] 失败 task_id={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)


skill = ImageEditSkill()