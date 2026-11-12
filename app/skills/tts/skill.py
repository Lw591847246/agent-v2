# app/skills/tts/skill.py
"""TTS Skill：文本转语音，短同步、长异步。"""
from __future__ import annotations

import os
import time

from app.context.task_runner import task_runner
from app.contracts.result import (
    Result, audio_part, build_output, card_part, text_part,
)
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.tts.service import tts_wrapper


class TtsSkill(BaseSkill):
    name = "tts_synthesize"
    description = (
        "将文本合成为语音，返回音频 URL。适用于：'朗读/配音/生成语音/转成语音'。"
        "不适用于：视频配音（用 image_to_video/custom_avatar 的对口型流程）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要合成的文本"},
            "voice": {"type": "string", "description": "音色，可空，用默认音色"},
        },
        "required": ["text"],
    }
    intent = "tts"
    category = "general"
    sync = True
    returns = ["audio", "card"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, text: str, voice: str = "", session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not text or not text.strip():
            return Result(parts=[text_part("请输入要合成的文本")])

        if tts_wrapper.should_async(text):
            return self._submit_async(text, voice, session_id, user_id)
        return self._run_sync(text, voice)

    def _run_sync(self, text: str, voice: str) -> Result:
        try:
            info = tts_wrapper.synthesize_sync(text, voice)
        except Exception as e:
            logger.error(f"[TtsSkill] 同步合成失败: {e}")
            return Result(parts=[text_part(f"语音合成失败：{e}")])

        output = build_output(
            kind="audio",
            filename=info["filename"],
            path=info["path"],
            url=info["url"],
        )
        return Result(
            parts=[audio_part(info["url"])],
            outputs=[output],
        )

    def _submit_async(
        self, text: str, voice: str, session_id: str, user_id: int,
    ) -> Result:
        task_id = task_runner.submit(
            skill_name="tts",
            session_id=session_id,
            user_id=user_id,
            slots={"text": text[:200], "voice": voice, "length": len(text)},
            fn=lambda tid: self._run_async(tid, text, voice, session_id),
        )
        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在合成语音",
                "description": f"共 {len(text)} 字，异步合成中",
                "progress": 10,
                "steps": [
                    {"label": "提交任务", "status": "done"},
                    {"label": "合成语音", "status": "running"},
                    {"label": "保存文件", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)

    def _run_async(self, task_id: str, text: str, voice: str, session_id: str) -> None:
        start = time.time()
        try:
            task_runner.progress(task_id, 30, "TTS 合成中...")
            info = tts_wrapper.synthesize_sync(text, voice)
            duration_ms = int((time.time() - start) * 1000)

            output = build_output(
                kind="audio",
                filename=info["filename"],
                path=info["path"],
                url=info["url"],
            )
            parts = [audio_part(info["url"])]
            task_runner.finish(
                task_id=task_id,
                parts=parts,
                outputs=[output],
                output_path=info["path"],
                duration_ms=duration_ms,
                session_id=session_id,
            )
            logger.info(f"[TtsSkill] 异步完成 task_id={task_id} duration={duration_ms}ms")
        except Exception as e:
            logger.error(f"[TtsSkill] 异步失败 task_id={task_id}: {e}")
            task_runner.fail(task_id, str(e), session_id=session_id)


skill = TtsSkill()