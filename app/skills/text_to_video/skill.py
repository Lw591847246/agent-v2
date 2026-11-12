# app/skills/text_to_video/skill.py
"""TextToVideo Skill：文生视频，异步任务型。"""
from __future__ import annotations

from typing import Optional

from app.context.task_runner import task_runner
from app.contracts.result import (
    Result, build_output, card_part, text_part, video_part,
)
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.text_to_video.service import text_to_video_service


class TextToVideoSkill(BaseSkill):
    name = "text_to_video"
    description = (
        "根据文本描述生成视频（文生视频）。适用于：'生成视频/做视频/做一段视频/生成一段XX秒的视频'。"
        "不适用于：图片动起来（用 image_to_video）、数字人口播（用 custom_avatar）、"
        "漫剧/短剧（用 storyboard）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "视频画面描述"},
            "duration_seconds": {"type": "integer", "description": "时长（秒），可空，默认 3s，上限 20s"},
            "preset": {"type": "string", "description": "清晰度档位：standard / hd"},
            "negative_prompt": {"type": "string", "description": "负面提示词，可空"},
        },
        "required": ["prompt"],
    }
    intent = "text_to_video"
    category = "video"
    sync = False
    returns = ["card", "video"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, prompt: str = "", duration_seconds: Optional[int] = None,
        preset: str = "standard", negative_prompt: str = "",
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not prompt or not prompt.strip():
            return Result(parts=[text_part("缺少视频内容描述")])

        # 参数提取
        if preset == "standard":
            preset = text_to_video_service.detect_preset(prompt)
        if duration_seconds is None:
            duration_seconds = text_to_video_service.detect_duration(prompt)

        info = text_to_video_service.submit(
            prompt=prompt, negative_prompt=negative_prompt,
            preset=preset, duration_seconds=duration_seconds,
        )

        task_id = task_runner.submit(
            skill_name="text_to_video",
            session_id=session_id,
            user_id=user_id,
            slots={"prompt": prompt[:500], "duration_seconds": duration_seconds},
            fn=lambda tid: self._run_async(tid, session_id, user_id, info),
            provider="comfyui",
        )

        preset_label = {"standard": "标准清晰度", "hd": "高清"}.get(preset, "标准清晰度")
        duration_label = f" · {duration_seconds}s" if duration_seconds else " · 3s"

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "视频生成中",
                "description": f"{preset_label}{duration_label}，处理中…",
                "progress": 10,
                "steps": [
                    {"label": "解析参数", "status": "done"},
                    {"label": "生成视频", "status": "running"},
                    {"label": "保存文件", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)

    def _run_async(self, task_id: str, session_id: str, user_id: int, info: dict) -> None:
        try:
            def progress_cb(pct: int, msg: str, steps: list):
                task_runner.progress(task_id, pct, msg, steps=steps)

            save_path = text_to_video_service.run_pipeline(
                task_id=task_id,
                session_id=session_id,
                user_id=user_id,
                prompt=info["prompt"],
                negative_prompt=info["negative_prompt"],
                preset=info["preset"],
                length=info["length"],
                progress_cb=progress_cb,
            )

            parts = [
                video_part(f"/agent/v1/job/{task_id}/video", task_id),
                {"type": "text", "content": "视频已生成完毕"},
            ]
            outputs = [build_output(
                kind="video",
                filename=f"{task_id}.mp4",
                path=save_path,
                url=f"/agent/v1/job/{task_id}/video",
                task_id=task_id,
            )]
            task_runner.finish(
                task_id=task_id, parts=parts, outputs=outputs,
                output_path=save_path, session_id=session_id,
            )
            logger.info(f"[T2V-Skill] 完成 task={task_id}")
        except Exception as e:
            logger.error(f"[T2V-Skill] 失败 task={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)


skill = TextToVideoSkill()