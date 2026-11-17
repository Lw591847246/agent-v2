# app/skills/image_to_video/service.py
"""图生视频业务：ComfyUI I2V → 可选 TTS + MuseTalk → 保存。"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Optional

from app.core.logger import logger
from app.infrastructure.clients.comfyui_i2v_client import comfyui_i2v_client
from app.core.gpu_lock import gpu_lock
from app.infrastructure.clients.model_manager import model_manager
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.infrastructure.services.lipsync_service import lipsync_service
from app.infrastructure.services.tts_service import tts_service
from app.skills.image_to_video.prompts import (
    _DEFAULT_DURATION, _DURATION_MAX, _PRESET_KEYWORDS,
    _VOICE_DEFAULT, _VOICE_MAP, DEFAULT_I2V_PROMPT,
)
from app.utils.file_registry import file_registry


VIDEO_OUTPUT_ROOT = "./data/videos"

_CN_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_SPLIT_RE = re.compile(
    r"(播报以下内容|播报内容|播报|说以下内容|说|讲以下内容|讲|"
    r"配音内容|语音内容|台词|旁白|念以下内容|念|具体内容是|要说的内容是)"
    r"\s*[：:]\s*", re.IGNORECASE,
)
_QUOTE_CHARS = "\"'“”‘’。\t\n"


class ImageToVideoService:

    # ---------- 参数解析 ----------
    @staticmethod
    def detect_preset(text: str) -> str:
        lower = (text or "").lower()
        for preset, kws in _PRESET_KEYWORDS:
            for kw in kws:
                if kw.lower() in lower:
                    return preset
        return "standard"

    @staticmethod
    def detect_duration(text: str) -> int:
        patterns = [
            re.compile(r"(\d+|[一二两三四五六七八九十])\s*[sS秒]"),
            re.compile(r"(\d+|[一二两三四五六七八九十])\s*seconds?", re.IGNORECASE),
        ]
        for pat in patterns:
            m = pat.search(text)
            if m:
                raw = m.group(1)
                try:
                    sec = int(raw) if raw.isdigit() else _CN_NUM.get(raw, 0)
                    if 1 <= sec <= _DURATION_MAX:
                        return sec
                except ValueError:
                    pass
        return _DEFAULT_DURATION

    @staticmethod
    def detect_voice(text: str) -> str:
        m = re.search(r"(?:音色|声音)\s*[:：]\s*([^\s，。；;]+)", text)
        if m:
            return _VOICE_MAP.get(m.group(1).strip(), m.group(1).strip())
        m = re.search(r"用(.+?)的声音", text)
        if m:
            return _VOICE_MAP.get(m.group(1).strip(), m.group(1).strip())
        return _VOICE_DEFAULT

    @staticmethod
    def split_text(text: str) -> tuple:
        """拆分为 (画面描述, 台词, 是否拆出)"""
        m = _SPLIT_RE.search(text)
        if not m:
            return text.strip(), "", False
        return text[:m.start()].strip(), text[m.end():].strip().strip(_QUOTE_CHARS), True

    @staticmethod
    def clean_i2v_prompt(raw: str) -> str:
        if not raw:
            return DEFAULT_I2V_PROMPT
        s = raw
        patterns = [
            r"将上传的图片", r"上传的图片", r"这张图片", r"这张图",
            r"生成一段\s*(\d+|[一二两三四五六七八九十]+)\s*[秒sS]?\s*的视频",
            r"生成视频", r"做成视频", r"然后", r"并且", r"请", r"帮我", r"给我",
        ]
        for p in patterns:
            s = re.sub(p, "", s, flags=re.IGNORECASE)
        s = s.strip("，。；、 \t\n")
        if not s:
            return DEFAULT_I2V_PROMPT
        if "说话" not in s and "口型" not in s:
            return f"{s}，{DEFAULT_I2V_PROMPT}"
        return f"{s}，写实风格，保持人物身份，画面稳定，面部清晰，不扭曲五官"

    @staticmethod
    def truncate_speech(text: str, duration: int) -> str:
        if not text:
            return ""
        max_chars = max(10, int((duration or _DEFAULT_DURATION) * 5))
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        for p in "，。；！？":
            idx = cut.rfind(p)
            if idx > max_chars * 0.6:
                cut = cut[:idx]
                break
        return cut.rstrip("，。；！？") + "…"

    # ---------- 执行 pipeline ----------
    def run_pipeline(
        self, task_id: str, session_id: str, user_id: int,
        image_file_id: str, prompt: str,
        preset: str = "standard", duration_seconds: int = 5,
        speech_text: str = "", voice: str = _VOICE_DEFAULT,
        progress_cb=None,
    ) -> str:
        start = time.time()
        steps = [
            {"label": "解析素材", "status": "done"},
            {"label": "生成画面", "status": "running"},
            {"label": "合成语音", "status": "pending" if speech_text else "done"},
        ]
        try:
            meta = file_registry.get(image_file_id)
            if not meta or not os.path.isfile(meta.get("path", "")):
                raise RuntimeError("图片文件丢失")

            if progress_cb:
                progress_cb(5, "准备素材...", steps)

            with open(meta["path"], "rb") as f:
                image_bytes = f.read()

            # 清理显存
            try:
                model_manager.unload_all_models()
                time.sleep(2)
            except Exception as e:
                logger.warning(f"[I2V-Service] 清理显存失败: {e}")

            # ComfyUI
            with gpu_lock.acquire(task_name="comfyui_i2v"):
                fps = comfyui_i2v_client.fps
                raw_length = duration_seconds * fps if duration_seconds else comfyui_i2v_client.default_length
                final_length = min(raw_length, comfyui_i2v_client.max_length)

                pid = comfyui_i2v_client.submit(
                    prompt=prompt, image_bytes=image_bytes,
                    preset=preset, length=final_length,
                )
                out_info = comfyui_i2v_client.wait_for(pid)
                content = comfyui_i2v_client.download_output(out_info)
                try:
                    comfyui_i2v_client.free()
                except Exception:
                    pass

            if progress_cb:
                progress_cb(60, "视频生成完成，正在保存...", steps)

            user_dir = os.path.join(VIDEO_OUTPUT_ROOT, str(user_id), session_id or "default")
            os.makedirs(user_dir, exist_ok=True)
            silent_path = os.path.join(user_dir, f"{task_id}_silent.mp4")
            with open(silent_path, "wb") as f:
                f.write(content)

            save_path = silent_path
            has_speech = False

            if speech_text:
                steps[2]["status"] = "running"
                if progress_cb:
                    progress_cb(70, "合成语音...", steps)
                with gpu_lock.acquire(task_name="tts"):
                    audio_path = tts_service.synthesize_wav(text=speech_text, voice=voice)

                if progress_cb:
                    progress_cb(85, "对口型中...", steps)
                with gpu_lock.acquire(task_name="musetalk"):
                    final_path = os.path.join(user_dir, f"{task_id}.mp4")
                    save_path = lipsync_service.generate(
                        video_path=silent_path, audio_path=audio_path,
                        output_path=final_path, task_id=task_id,
                    )
                has_speech = True

            if progress_cb:
                progress_cb(99, "完成", steps)
            logger.info(f"[I2V-Service] 完成 task={task_id} 耗时={int((time.time()-start)*1000)}ms")
            return save_path

        except Exception as e:
            logger.error(f"[I2V-Service] 失败 task={task_id}: {e}", exc_info=True)
            raise


image_to_video_service = ImageToVideoService()