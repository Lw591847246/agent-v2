# app/skills/face_swap/service.py
"""换脸任务服务：提交 → ComfyUI → 轮询 → 保存。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
from typing import Optional

from app.context.task_runner import task_runner
from app.core.logger import logger
from app.core.redis_client import redis_client
from app.infrastructure.clients.comfyui_face_swap_client import (
    FaceSwapException, comfyui_face_swap_client,
)
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.infrastructure.repositories.task_repo import task_repo
from app.application.file_upload_service import file_upload_service
from app.domain.models.task import TaskInfo
from app.utils.file_registry import file_registry
from app.infrastructure.clients.gpu_lock import gpu_lock

VIDEO_OUTPUT_ROOT = "./data/videos"
GROUP_KEY_PREFIX = "face_swap:group:"
GROUP_TTL = 60 * 60
SKILL_NAME = "face_swap"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _probe_video_codec(path: str) -> str:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        return (r.stdout or "").strip().lower()
    except Exception:
        return ""


def _finalize_video(content: bytes, task_id: str, original_video_path: Optional[str] = None) -> bytes:
    tmp_in = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    tmp_in.write(content)
    tmp_in.close()
    tmp_out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name

    try:
        v_codec = _probe_video_codec(tmp_in.name)
        if v_codec == "h264":
            v_args = ["-c:v", "copy"]
        else:
            v_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]

        use_original_audio = bool(original_video_path and os.path.isfile(original_video_path))

        if use_original_audio:
            cmd = [
                "ffmpeg", "-y", "-i", tmp_in.name, "-i", original_video_path,
                "-map", "0:v:0", "-map", "1:a:0?",
                *v_args, "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                "-shortest", "-movflags", "+faststart", tmp_out,
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-i", tmp_in.name,
                *v_args, "-movflags", "+faststart", tmp_out,
            ]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return content

        with open(tmp_out, "rb") as f:
            return f.read()
    except Exception:
        return content
    finally:
        try:
            os.remove(tmp_in.name)
        except Exception:
            pass
        try:
            os.remove(tmp_out)
        except Exception:
            pass


def _save_video(user_id, session_id, task_id, content, original_video_path=None):
    content = _finalize_video(content, task_id, original_video_path)
    user_dir = os.path.join(VIDEO_OUTPUT_ROOT, str(user_id), session_id or "default")
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, f"{task_id}.mp4")
    with open(path, "wb") as f:
        f.write(content)
    return path


class FaceSwapService:

    def submit_group(
        self, user_id: int, session_id: str,
        source_image_file_id: str, target_video_file_ids: list[str],
        prompt: str,
    ) -> dict:
        group_id = "g_" + uuid.uuid4().hex[:10]
        task_ids = []

        try:
            redis_client._client.setex(
                f"{GROUP_KEY_PREFIX}{group_id}:remaining",
                GROUP_TTL, len(target_video_file_ids),
            )
        except Exception as e:
            logger.warning(f"[FaceSwapService] set group counter failed: {e}")

        for vid in target_video_file_ids:
            task_id = "t_" + uuid.uuid4().hex[:10]
            slots = {
                "group_id": group_id,
                "source_image_file_id": source_image_file_id,
                "target_video_file_id": vid,
                "prompt": prompt,
            }
            info = TaskInfo(
                task_id=task_id, session_id=session_id, user_id=user_id,
                intent="face_swap", skill_name=SKILL_NAME,
                template_id=None, slots=slots, provider="comfyui",
                status="pending", progress=0, created_at=_now_iso(),
            )
            task_repo.create(info)
            task_ids.append(task_id)

            threading.Thread(
                target=self._run_one, args=(task_id,),
                name=f"face_swap-{task_id}", daemon=True,
            ).start()

        logger.info(f"[FaceSwapService] group={group_id} tasks={task_ids}")
        return {"group_id": group_id, "task_ids": task_ids}

    def _run_one(self, task_id: str) -> None:
        info = task_repo.get(task_id)
        if not info:
            return

        slots = info.slots or {}
        source_fid = slots.get("source_image_file_id")
        video_fid = slots.get("target_video_file_id")
        group_id = slots.get("group_id")
        session_id = info.session_id
        original_video_path = None

        steps = [
            {"label": "上传素材", "status": "running"},
            {"label": "ComfyUI 换脸", "status": "pending"},
            {"label": "保存视频", "status": "pending"},
        ]

        try:
            task_runner.progress(task_id, 5, "准备素材...", steps=steps)

            src_meta = file_registry.get(source_fid) if source_fid else None
            vid_meta = file_registry.get(video_fid) if video_fid else None

            if not src_meta or not vid_meta:
                raise FaceSwapException(f"素材已过期 src={source_fid} vid={video_fid}")

            original_video_path = vid_meta.get("path")

            with open(src_meta["path"], "rb") as f:
                src_bytes = f.read()
            with open(vid_meta["path"], "rb") as f:
                vid_bytes = f.read()

            src_name = comfyui_face_swap_client.upload_file(
                src_bytes, f"face_{task_id}.{src_meta['name'].rsplit('.', 1)[-1]}",
            )
            vid_name = comfyui_face_swap_client.upload_file(
                vid_bytes, f"video_{task_id}.{vid_meta['name'].rsplit('.', 1)[-1]}",
            )

            steps[0]["status"] = "done"
            steps[1]["status"] = "running"
            task_runner.progress(task_id, 20, "提交 ComfyUI 换脸...", steps=steps)

            with gpu_lock.acquire(task_name="comfyui_face_swap", timeout=600):
                prompt_id = comfyui_face_swap_client.submit(
                    source_image_name=src_name, video_name=vid_name,
                    prefix=f"face_swap_{task_id}",
                )

            task_repo.update(
                task_id, provider_job_id=prompt_id, status="running", progress=30,
            )

            output_info = comfyui_face_swap_client.wait_for(prompt_id)
            task_runner.progress(task_id, 90, "下载并保存视频...", steps=steps)

            content = comfyui_face_swap_client.download_output(output_info)
            save_path = _save_video(
                info.user_id, info.session_id, task_id, content,
                original_video_path=original_video_path,
            )

            duration_ms = 0
            try:
                if info.created_at:
                    t0 = datetime.fromisoformat(info.created_at)
                    t1 = datetime.now()
                    duration_ms = int((t1 - t0).total_seconds() * 1000)
            except Exception:
                pass

            video_name = vid_meta.get("name") or "face_swap.mp4"
            video_url = f"/agent/v1/job/{task_id}/video"
            final_parts = [
                {"type": "video", "url": video_url, "task_id": task_id},
                {"type": "text", "content": f"换脸视频生成完毕（{video_name}）"},
            ]
            outputs = [{
                "kind": "video",
                "filename": f"{task_id}.mp4",
                "path": save_path,
                "url": video_url,
                "thumb": "",
                "task_id": task_id,
            }]
            task_repo.finish(
                task_id, parts=final_parts, duration_ms=duration_ms,
                output_path=save_path, outputs=outputs,
            )

            # 新增一条 video 消息（不是更新进度卡）
            self._save_video_message(
                session_id, task_id, save_path, video_url,
                video_name=video_name,
                prompt=slots.get("prompt") or "AI 换脸视频",
                duration_ms=duration_ms,
            )

        except Exception as e:
            logger.error(f"[FaceSwapService] task failed {task_id}: {e}", exc_info=True)
            task_repo.fail(task_id, str(e))
            self._save_failed_message(session_id, task_id, str(e))
        finally:
            if video_fid:
                try:
                    file_upload_service.delete_asset(video_fid)
                except Exception:
                    pass
            if group_id:
                self._decr_group_and_cleanup(group_id, source_fid, session_id)

    def _save_video_message(
        self, session_id, task_id, save_path, video_url,
        video_name, prompt, duration_ms=0,
    ):
        try:
            from app.application.conversation_service import conversation_service
            file_size = os.path.getsize(save_path) if save_path and os.path.isfile(save_path) else 0
            payload = {
                "task_id": task_id, "video_name": video_name,
                "video_url": video_url, "file_size": file_size,
                "summary": prompt or "AI 换脸视频",
                "duration_ms": duration_ms,
            }
            conversation_service.save_assistant_message(
                session_id=session_id,
                content=json.dumps(payload, ensure_ascii=False),
                message_type="video",
                outputs=[{
                    "kind": "video",
                    "filename": f"{task_id}.mp4",
                    "path": save_path,
                    "url": video_url,
                    "thumb": "",
                    "task_id": task_id,
                }],
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.warning(f"[FaceSwapService] save video message failed: {e}")

    def _save_failed_message(self, session_id, task_id, error):
        try:
            from app.application.conversation_service import conversation_service
            card = {
                "card_id": f"err_{task_id}", "card_type": "error",
                "title": "换脸失败", "description": error or "任务执行失败，请重试",
                "items": [],
            }
            conversation_service.save_assistant_message(
                session_id=session_id,
                content=json.dumps(card, ensure_ascii=False),
                message_type="card",
            )
        except Exception as e:
            logger.warning(f"[FaceSwapService] save error card failed: {e}")

    def _decr_group_and_cleanup(self, group_id, source_fid, session_id):
        key = f"{GROUP_KEY_PREFIX}{group_id}:remaining"
        try:
            remaining = redis_client._client.decr(key)
        except Exception:
            return

        if remaining > 0:
            return

        try:
            redis_client._client.delete(key)
        except Exception:
            pass

        if source_fid:
            try:
                file_upload_service.delete_asset(source_fid)
            except Exception:
                pass

        if session_id:
            try:
                from app.context.flow_state_store import flow_state_store
                flow_state_store.clear_active("face_swap", session_id)
            except Exception:
                pass


face_swap_service = FaceSwapService()