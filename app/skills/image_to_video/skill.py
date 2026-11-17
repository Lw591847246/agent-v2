# app/skills/image_to_video/skill.py
"""ImageToVideo Skill：图生视频，可选配音对口型。"""
from __future__ import annotations

from typing import Optional

from app.context.task_runner import task_runner
from app.contracts.result import (
    Result, build_output, card_part, text_part, video_part,
)
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.image_to_video.service import image_to_video_service
from app.skills.image_to_video.prompts import _VOICE_DEFAULT


class ImageToVideoSkill(BaseSkill):
    name = "image_to_video"
    description = (
        "根据图片和提示词生成视频，支持配音对口型。适用于：'让这张图动起来/加动作'、"
        "'让这张图说话/对口型/有台词'。不适用于：纯文本生视频（用 text_to_video）、"
        "定制数字人（用 custom_avatar）、模板数字人（用 portrait_animation）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "image_file_id": {"type": "string", "description": "图片 file_id"},
            "prompt": {"type": "string", "description": "视频画面动作提示词"},
            "speech_text": {"type": "string", "description": "可选，角色要说的台词"},
            "duration_seconds": {"type": "integer", "description": "时长（秒），可选，默认 5s，上限 5s"},
            "preset": {"type": "string", "description": "清晰度档位：standard / hd / ultra"},
            "voices": {"type": "string", "description": "音色，可选，默认 zf_xiaoxiao"},
        },
        "required": ["image_file_id", "prompt"],
    }
    intent = "image_to_video"
    category = "video"
    sync = False
    returns = ["card", "video"]
    requires_attachments = ["image"]
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, image_file_id: str = "", prompt: str = "",
        speech_text: str = "", duration_seconds: int = 5,
        preset: str = "standard", voice: str = _VOICE_DEFAULT,
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not image_file_id:
            image_file_id = (kwargs.get("file_id") or kwargs.get("image") or "")
        if not prompt:
            prompt = (kwargs.get("text") or kwargs.get("action")
                      or kwargs.get("content") or "主播面对镜头自然说话")

        if not image_file_id:
            return Result(parts=[text_part("❌ 未检测到图片，无法生成图生视频")])
        if not prompt.strip():
            return Result(parts=[text_part("缺少视频动作描述")])

        # 参数解析
        raw_text = prompt
        i2v_raw, split_speech, _ = image_to_video_service.split_text(raw_text)
        final_duration = image_to_video_service.detect_duration(raw_text) if "duration" not in kwargs else duration_seconds
        final_voice = image_to_video_service.detect_voice(raw_text) if voice == _VOICE_DEFAULT else voice
        final_preset = image_to_video_service.detect_preset(raw_text) if preset == "standard" else preset
        i2v_prompt = image_to_video_service.clean_i2v_prompt(i2v_raw)
        final_speech = image_to_video_service.truncate_speech(
            split_speech or speech_text, final_duration,
        )

        task_id = task_runner.submit(
            skill_name="image_to_video",
            session_id=session_id,
            user_id=user_id,
            slots={
                "image_file_id": image_file_id, "prompt": i2v_prompt[:500],
                "speech_text": final_speech[:500], "duration_seconds": final_duration,
            },
            fn=lambda tid: self._run_async(
                tid, session_id, user_id,
                image_file_id, i2v_prompt, final_preset,
                final_duration, final_speech, final_voice,
            ),
            provider="comfyui",
        )

        desc = f"画质:{final_preset} · 时长:{final_duration}s"
        if final_speech:
            desc += " · 含配音"

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在生成视频",
                "description": desc,
                "progress": 10,
                "steps": [
                    {"label": "解析素材", "status": "done"},
                    {"label": "生成画面", "status": "running"},
                    {"label": "合成语音", "status": "pending" if final_speech else "done"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)

    def _run_async(
        self, task_id: str, session_id: str, user_id: int,
        image_file_id: str, prompt: str, preset: str,
        duration_seconds: int, speech_text: str, voice: str,
    ) -> None:
        try:
            def progress_cb(pct: int, msg: str, steps: list):
                task_runner.progress(task_id, pct, msg, steps=steps)

            save_path = image_to_video_service.run_pipeline(
                task_id=task_id, session_id=session_id, user_id=user_id,
                image_file_id=image_file_id, prompt=prompt,
                preset=preset, duration_seconds=duration_seconds,
                speech_text=speech_text, voice=voice,
                progress_cb=progress_cb,
            )

            has_speech = bool(speech_text)
            parts = [
                video_part(f"/agent/v1/job/{task_id}/video", task_id),
                {"type": "text", "content": f"视频生成完毕。{('包含配音' if has_speech else '无声视频')}"},
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
            logger.info(f"[I2V-Skill] 完成 task={task_id}")
        except Exception as e:
            logger.error(f"[I2V-Skill] 失败 task={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)


skill = ImageToVideoSkill()