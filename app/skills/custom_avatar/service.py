# app/skills/custom_avatar/service.py
"""CustomAvatar 业务：ComfyUI I2V → TTS → MuseTalk → ffmpeg 合音。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

from app.context.task_runner import task_runner
from app.contracts.result import build_output
from app.core.logger import logger
from app.infrastructure.clients.comfyui_i2v_client import comfyui_i2v_client
from app.core.gpu_lock import gpu_lock
from app.infrastructure.services.lipsync_service import lipsync_service
from app.infrastructure.services.tts_service import tts_service
from app.skills.custom_avatar.prompts import (
    QUOTE_RE, _ACTION_KEYWORDS, _STOPWORDS,
)
from app.utils.file_registry import file_registry


OUTPUT_DIR = "./data/custom_avatar"
os.makedirs(OUTPUT_DIR, exist_ok=True)
SKILL_NAME = "custom_avatar"


def _merge_audio_video(video_path: str, audio_path: str, out_path: str) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-shortest", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg 合并失败: {r.stderr[-500:]}")


def parse_user_input(raw: str) -> tuple:
    """解析用户输入 → (prompt, speech_text)。"""
    if not raw:
        return "人物自然说话，动作生动", "大家好，我是 AI 数字人。"

    raw = raw.strip()
    quote_matches = re.findall(QUOTE_RE, raw)
    speech_text = "，".join([m.strip() for m in quote_matches if m.strip()]) if quote_matches else ""
    text_wo_quotes = re.sub(QUOTE_RE, "", raw).strip()

    action_hits = [kw for kw in _ACTION_KEYWORDS if kw in text_wo_quotes]
    if action_hits:
        prompt = "，".join(action_hits) + "，写实风格，面部清晰，画面稳定"
    else:
        prompt = "人物自然说话，动作生动，面部清晰"

    if not speech_text:
        cleaned = text_wo_quotes
        for sw in _STOPWORDS:
            cleaned = cleaned.replace(sw, "")
        cleaned = re.sub(r'[，。！？、；：\s]+', ' ', cleaned).strip()
        speech_text = cleaned or "大家好，我是 AI 数字人。"

    return prompt, speech_text


class CustomAvatarService:

    def submit(
        self, session_id: str, image_path: str, prompt: str,
        speech_text: str, user_id: int = 0,
    ) -> str:
        def _fn(task_id: str):
            self._run_pipeline(task_id, session_id, image_path, prompt, speech_text)

        return task_runner.submit(
            skill_name=SKILL_NAME,
            session_id=session_id,
            user_id=user_id,
            slots={
                "image_path": image_path,
                "prompt": prompt,
                "speech_text": speech_text,
            },
            fn=_fn,
            provider="comfyui",
        )

    def _run_pipeline(
        self, task_id: str, session_id: str,
        image_path: str, prompt: str, speech_text: str,
    ) -> None:
        start_time = time.time()
        steps = [
            {"label": "ComfyUI 生成画面", "status": "running"},
            {"label": "IndexTTS 音频合成", "status": "pending"},
            {"label": "MuseTalk 对口型", "status": "pending"},
        ]
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()

            # ---- Step 1: ComfyUI I2V ----
            task_runner.progress(task_id, 20, "ComfyUI 正在生成动作画面...", steps=steps)
            with gpu_lock.acquire(task_name="comfyui_i2v", timeout=600):
                pid = comfyui_i2v_client.submit(prompt=prompt, image_bytes=image_bytes)
                out_info = comfyui_i2v_client.wait_for(pid)
                content = comfyui_i2v_client.download_output(out_info)
                try:
                    comfyui_i2v_client.free()
                except Exception:
                    pass

            silent_path = os.path.join(OUTPUT_DIR, f"{task_id}_silent.mp4")
            with open(silent_path, "wb") as f:
                f.write(content)

            steps[0]["status"] = "done"
            steps[1]["status"] = "running"
            task_runner.progress(task_id, 60, "画面生成完毕，正在合成音频...", steps=steps)

            # ---- Step 2: TTS ----
            audio_path = os.path.abspath(tts_service.synthesize_wav(text=speech_text))

            steps[1]["status"] = "done"
            steps[2]["status"] = "running"
            task_runner.progress(task_id, 80, "MuseTalk 开始对口型...", steps=steps)

            lip_path = os.path.join(OUTPUT_DIR, f"{task_id}_lip.mp4")

            def _muse_progress(pct: int, msg: str):
                task_runner.progress(task_id, pct, msg)

            # 清理显存给 MuseTalk
            try:
                import requests as _req
                _req.post("http://127.0.0.1:9010/unload", timeout=5)
            except Exception:
                pass
            try:
                comfyui_i2v_client.free()
            except Exception:
                pass
            time.sleep(3)

            # ---- Step 3: MuseTalk ----
            with gpu_lock.acquire(task_name="musetalk", timeout=600):
                lipsync_service.generate(
                    video_path=silent_path, audio_path=audio_path,
                    output_path=lip_path, task_id=task_id,
                    progress_callback=_muse_progress,
                )

            # ---- Step 4: 合音 ----
            final_path = os.path.abspath(os.path.join(OUTPUT_DIR, f"{task_id}_final.mp4"))
            _merge_audio_video(lip_path, audio_path, final_path)

            duration_ms = int((time.time() - start_time) * 1000)
            video_url = f"/agent/v1/custom_avatar/download?path={final_path}"

            final_parts = [
                {"type": "video", "url": video_url, "task_id": task_id},
                {"type": "text", "content": "定制数字人视频生成完毕！"},
            ]
            outputs = [build_output(
                kind="video",
                filename=f"{task_id}_final.mp4",
                path=final_path,
                url=video_url,
                task_id=task_id,
            )]

            task_runner.finish(
                task_id=task_id, parts=final_parts, outputs=outputs,
                output_path=final_path, duration_ms=duration_ms,
                session_id=session_id,
            )
            logger.info(f"[CustomAvatar] 完成 task_id={task_id}")

        except Exception as e:
            logger.error(f"[CustomAvatar] 失败 task_id={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)


custom_avatar_service = CustomAvatarService()