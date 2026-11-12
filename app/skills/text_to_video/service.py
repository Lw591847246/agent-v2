# app/skills/text_to_video/service.py
"""文生视频业务：ComfyUI 提交 → 轮询 → 下载 → 保存。"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from typing import Optional

from app.core.logger import logger
from app.infrastructure.clients.comfyui_t2v_client import comfyui_t2v_client


VIDEO_OUTPUT_ROOT = "./data/videos"
_MAX_DURATION = 20


def _convert_to_mp4(content: bytes, task_id: str) -> bytes:
    """如果不是 mp4/mov，用 ffmpeg 转成 mp4。"""
    tmp_in = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    tmp_in.write(content)
    tmp_in.close()
    try:
        fmt = ""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
                 "-of", "csv=p=0", tmp_in.name],
                capture_output=True, text=True, timeout=15,
            )
            fmt = (r.stdout or "").strip().lower()
        except Exception:
            pass
        if "mp4" in fmt or "mov" in fmt:
            return content

        tmp_out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_in.name,
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart", tmp_out],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                return content
            with open(tmp_out, "rb") as f:
                return f.read()
        finally:
            try:
                os.remove(tmp_out)
            except Exception:
                pass
    finally:
        try:
            os.remove(tmp_in.name)
        except Exception:
            pass


class TextToVideoService:

    def submit(
        self, prompt: str, negative_prompt: str = "",
        preset: str = "standard", duration_seconds: Optional[int] = None,
        provider: str = "comfyui",
    ) -> dict:
        """提交参数，返回 {prompt, negative_prompt, preset, duration_seconds, length}"""
        length = None
        if duration_seconds and duration_seconds > 0:
            fps = comfyui_t2v_client.fps
            raw_length = duration_seconds * fps
            length = min(raw_length, comfyui_t2v_client.max_length)
        return {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "preset": preset,
            "duration_seconds": duration_seconds,
            "length": length,
        }

    def run_pipeline(
        self, task_id: str, session_id: str, user_id: int,
        prompt: str, negative_prompt: str = "",
        preset: str = "standard", length: Optional[int] = None,
        progress_cb=None,
    ) -> str:
        """执行生成，返回保存路径。progress_cb(pct, msg, steps)"""
        start = time.time()
        steps = [
            {"label": "解析参数", "status": "done"},
            {"label": "生成视频", "status": "running"},
            {"label": "保存文件", "status": "pending"},
        ]
        if progress_cb:
            progress_cb(5, "提交任务...", steps)

        prompt_id = comfyui_t2v_client.submit(
            prompt=prompt, negative_prompt=negative_prompt,
            preset=preset, length=length, prefix=f"t2v_{task_id}",
        )
        logger.info(f"[T2V] 提交成功 task={task_id} prompt_id={prompt_id}")

        if progress_cb:
            progress_cb(10, "生成中...", steps)

        output_info = comfyui_t2v_client.wait_for(prompt_id)

        if progress_cb:
            progress_cb(90, "下载并转换中...", steps)

        content = comfyui_t2v_client.download_output(output_info)
        content = _convert_to_mp4(content, task_id)

        user_dir = os.path.join(VIDEO_OUTPUT_ROOT, str(user_id), session_id or "default")
        os.makedirs(user_dir, exist_ok=True)
        save_path = os.path.join(user_dir, f"{task_id}.mp4")
        with open(save_path, "wb") as f:
            f.write(content)

        if progress_cb:
            progress_cb(99, "完成", steps)
        logger.info(f"[T2V] 完成 task={task_id} 耗时={int((time.time()-start)*1000)}ms")
        return save_path

    # ---------- 参数提取 ----------
    @staticmethod
    def detect_preset(text: str) -> str:
        lower = (text or "").lower()
        for preset, kws in _PRESET_KEYWORDS:
            for kw in kws:
                if kw.lower() in lower:
                    return preset
        return "standard"

    @staticmethod
    def detect_duration(text: str) -> Optional[int]:
        if not text:
            return None
        patterns = [
            re.compile(r"(\d+)\s*[sS秒]"),
            re.compile(r"(\d+)\s*seconds?", re.IGNORECASE),
        ]
        for pat in patterns:
            m = pat.search(text)
            if m:
                try:
                    sec = int(m.group(1))
                    if 1 <= sec <= _MAX_DURATION:
                        return sec
                except ValueError:
                    pass
        return None


# 延迟导入，避免循环
from app.skills.text_to_video.prompts import _PRESET_KEYWORDS  # noqa: E402

text_to_video_service = TextToVideoService()