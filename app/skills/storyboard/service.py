# app/skills/storyboard/service.py
"""Storyboard 业务：拆场景 → TTS → 出图 → FFmpeg 合成。"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import time

from app.config.loader import config
from app.context.task_runner import task_runner
from app.contracts.result import build_output
from app.core.logger import logger
from app.infrastructure.clients.comfyui_i2v_client import comfyui_i2v_client  # noqa: F401
from app.core.gpu_lock import gpu_lock
from app.infrastructure.clients.model_manager import model_manager
from app.infrastructure.clients.qwen_Image_client import qwen_image_client
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.infrastructure.services.tts_service import tts_service
from app.skills.storyboard.composer import build_storyboard_video
from app.skills.storyboard.splitter import (
    STYLE_PROFILES, split_story_to_scenes,
)


# 项目根（app/skills/storyboard/service.py → ../../../../ = agent-v2）
_THIS_FILE = os.path.abspath(__file__)
_SKILL_DIR = os.path.dirname(_THIS_FILE)
_SKILLS_DIR = os.path.dirname(_SKILL_DIR)
_APP_DIR = os.path.dirname(_SKILLS_DIR)
_PROJECT_ROOT = os.path.dirname(_APP_DIR)

STORYBOARD_ROOT = os.path.join(_PROJECT_ROOT, "data", "storyboard")
os.makedirs(STORYBOARD_ROOT, exist_ok=True)


def _log_gpu(label: str):
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            used, total = r.stdout.strip().split(",")
            logger.info(f"🔍 [显存] {label}: {used.strip()}MB / {total.strip()}MB")
    except Exception:
        pass


def _force_release_ollama():
    try:
        model_manager.unload_all_models()
        time.sleep(3)
    except Exception as e:
        logger.warning(f"[Storyboard] 清理显存失败: {e}")


def _adjust_audio_speed(audio_path, actual_duration, expected_duration, workdir):
    if actual_duration <= 0 or expected_duration <= 0:
        return audio_path, actual_duration
    if actual_duration >= expected_duration * 0.6:
        return audio_path, actual_duration

    ratio = actual_duration / expected_duration
    adjusted_path = os.path.join(workdir, "audio_adjusted.wav")
    try:
        if ratio >= 0.5:
            cmd = ["ffmpeg", "-y", "-i", audio_path,
                   "-filter:a", f"atempo={ratio:.3f}",
                   "-c:a", "pcm_s16le", adjusted_path]
        else:
            cmd = ["ffmpeg", "-y", "-i", audio_path,
                   "-filter:a", f"atempo=0.5,atempo={ratio / 0.5:.3f}",
                   "-c:a", "pcm_s16le", adjusted_path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return audio_path, actual_duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", adjusted_path],
            capture_output=True, text=True, timeout=10,
        )
        return adjusted_path, float(probe.stdout.strip())
    except Exception:
        return audio_path, actual_duration


def _save_manifest(workdir, manifest):
    try:
        path = os.path.join(workdir, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[Storyboard] manifest 保存失败: {e}")


class StoryboardService:

    def submit(
        self, session_id: str, story: str, style: str, user_id: int = 0,
    ) -> str:
        """提交任务，返回 task_id。"""
        def _fn(task_id: str):
            workdir = os.path.join(STORYBOARD_ROOT, task_id)
            os.makedirs(workdir, exist_ok=True)
            self._run_pipeline(task_id, session_id, story, style, workdir)

        task_id = task_runner.submit(
            skill_name="storyboard",
            session_id=session_id,
            user_id=user_id,
            slots={"story": story[:500], "style": style},
            fn=_fn,
            provider="ffmpeg",
        )
        return task_id

    def _run_pipeline(
        self, task_id: str, session_id: str, story: str, style: str, workdir: str,
    ) -> None:
        start_time = time.time()
        steps = [
            {"label": "拆解故事场景", "status": "running"},
            {"label": "合成旁白音频", "status": "pending"},
            {"label": "生成画面", "status": "pending"},
            {"label": "合成视频", "status": "pending"},
        ]
        try:
            mode = config.get("storyboard.mode", "single")
            img_w = int(config.get("storyboard.image_width", 720))
            img_h = int(config.get("storyboard.image_height", 1080))
            zoom_max = float(config.get("storyboard.zoom_max", 1.15))
            max_scenes = int(config.get("storyboard.max_scenes", 6))

            # Step 1: 拆场景
            task_runner.progress(task_id, 5, "拆解故事场景...", steps=steps)
            outline = split_story_to_scenes(story, style=style, max_scenes=max_scenes)
            scenes = outline["scenes"]
            total_scenes = len(scenes)

            steps[0]["status"] = "done"
            steps[1]["status"] = "running"

            # Step 2: TTS
            task_runner.progress(task_id, 15, "合成旁白音频...", steps=steps)
            style_cfg = STYLE_PROFILES.get(style, STYLE_PROFILES["realistic_cinema"])
            tts_params = style_cfg.get("tts", {})

            _force_release_ollama()

            loop = asyncio.new_event_loop()
            try:
                with gpu_lock.acquire(task_name="tts", timeout=1800):
                    tts_res = loop.run_until_complete(
                        tts_service.synthesize_with_timeline(
                            story, voice=tts_params.get("ref_audio", ""),
                            speed=tts_params.get("speed", 1.0),
                            task_id=task_id, tts_params=tts_params,
                        )
                    )
            finally:
                loop.close()

            audio_path = tts_res["audio_path"]
            audio_duration = tts_res["duration"]
            expected_duration = len(story.strip()) / 4.5
            audio_path, audio_duration = _adjust_audio_speed(
                audio_path, audio_duration, expected_duration, workdir,
            )

            steps[1]["status"] = "done"
            steps[2]["status"] = "running"

            # Step 3: 出图
            task_runner.progress(task_id, 20, "生成画面...", steps=steps)
            style_prefix = style_cfg["prompt_prefix"]
            image_paths = []

            with gpu_lock.acquire(task_name="comfyui_i2v", timeout=1800):
                if mode == "single":
                    overview_hint = (
                        outline.get("overview_visual_hint") or scenes[0]["visual_hint"]
                    )
                    img_prompt = f"{style_prefix}, {overview_hint}"
                    img_res = qwen_image_client.generate(
                        prompt=img_prompt, width=img_w, height=img_h,
                    )
                    if not img_res.get("image_base64"):
                        raise RuntimeError("单图生成失败")
                    img_path = os.path.join(workdir, "overview.png")
                    with open(img_path, "wb") as f:
                        f.write(base64.b64decode(img_res["image_base64"]))
                    image_paths.append(img_path)
                else:
                    for i, scene in enumerate(scenes):
                        img_prompt = f"{style_prefix}, {scene['visual_hint']}"
                        img_res = qwen_image_client.generate(
                            prompt=img_prompt, width=img_w, height=img_h,
                        )
                        if not img_res.get("image_base64"):
                            raise RuntimeError(f"场景 {i + 1} 生图失败")
                        img_path = os.path.join(workdir, f"scene_{i:03d}.png")
                        with open(img_path, "wb") as f:
                            f.write(base64.b64decode(img_res["image_base64"]))
                        image_paths.append(img_path)
                        p = 20 + int(50 * (i + 1) / total_scenes)
                        task_runner.progress(task_id, p)

            steps[2]["status"] = "done"
            steps[3]["status"] = "running"

            # Step 4: 合成视频
            task_runner.progress(task_id, 75, "合成视频...", steps=steps)
            final_path = os.path.join(workdir, "final.mp4")
            build_storyboard_video(
                images=image_paths, audio_path=audio_path,
                audio_duration=audio_duration, output_path=final_path,
                workdir=workdir, zoom_max=zoom_max,
            )

            duration_ms = int((time.time() - start_time) * 1000)
            title = outline.get("title", "AI 漫剧")
            video_url = f"/agent/v1/storyboard/download?task_id={task_id}"

            final_parts = [
                {"type": "video", "url": video_url, "task_id": task_id},
                {"type": "text", "content": f"《{title}》生成完毕，共 {total_scenes} 个场景。"},
            ]
            outputs = [build_output(
                kind="video",
                filename="final.mp4",
                path=final_path,
                url=video_url,
                task_id=task_id,
            )]

            _save_manifest(workdir, {
                "task_id": task_id, "session_id": session_id, "title": title,
                "style": style, "mode": mode, "story": story, "scenes": scenes,
                "overview_visual_hint": outline.get("overview_visual_hint", ""),
                "audio_path": audio_path, "audio_duration": audio_duration,
                "image_paths": image_paths, "final_video": final_path,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            })

            task_runner.finish(
                task_id=task_id, parts=final_parts, outputs=outputs,
                output_path=final_path, duration_ms=duration_ms,
                session_id=session_id,
            )
            logger.info(f"[Storyboard] 完成 task_id={task_id}")

        except Exception as e:
            logger.error(f"[Storyboard] 失败 task_id={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)

    # ---------- regenerate ----------
    def regenerate(self, original_task_id: str, extra_instruction: str = "") -> str:
        """基于原任务的 manifest 重新生成，返回新 task_id。"""
        manifest_path = os.path.join(STORYBOARD_ROOT, original_task_id, "manifest.json")
        if not os.path.isfile(manifest_path):
            raise FileNotFoundError(f"找不到原任务 manifest: {original_task_id}")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        story = manifest.get("story", "")
        style = manifest.get("style", "realistic_cinema")
        session_id = manifest.get("session_id", "")
        if not story:
            raise ValueError("原任务 manifest 中缺少 story")
        if extra_instruction:
            story = f"{story}\n\n【修改要求】{extra_instruction}"
        return self.submit(session_id=session_id, story=story, style=style)


# 兼容旧名（controller 用）
from datetime import datetime  # noqa: E402

storyboard_service = StoryboardService()


def submit_storyboard_task(session_id: str, story: str, style: str = "realistic_cinema", user_id: int = 0) -> str:
    return storyboard_service.submit(session_id=session_id, story=story, style=style, user_id=user_id)


def regenerate_storyboard_task(original_task_id: str, extra_instruction: str = "") -> str:
    return storyboard_service.regenerate(original_task_id, extra_instruction)