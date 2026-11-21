# app/skills/face_swap/service.py
"""换脸任务服务：提交 → ComfyUI → 轮询 → 保存。

本版改动（架构复核 P0-4/P1）
--------------------------
1. **纳入 task_runner 统一治理**：原来是裸 `threading.Thread`，绕过了全局并发闸
   （一个请求带 N 个视频就 N 个线程），且没有每用户配额。现在走
   `task_runner.submit(...)`，与其它 skill 一致地被闸住；
2. 失败不再自己写错误卡片：异常向上抛，由 `task_runner.fail()` 统一落库/落卡
   （避免两处写同一张卡、格式不一致）；
3. 视频地址带签名（`job_video`），写入 parts/outputs 与 video 消息；
4. 落盘路径走 `safe_join`（user_id / session_id / task_id 全部先校验）。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from app.context.task_runner import task_runner
from app.core.gpu_lock import gpu_lock
from app.core.logger import logger
from app.core.redis_client import redis_client
from app.infrastructure.clients.comfyui_face_swap_client import (
    FaceSwapException, comfyui_face_swap_client,
)
from app.infrastructure.repositories.task_repo import task_repo
from app.application.file_upload_service import file_upload_service
from app.utils.file_registry import file_registry
from app.utils.safe_path import data_root, safe_join
from app.utils.signed_url import sign_url

VIDEO_OUTPUT_ROOT = data_root("videos")
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
    # 纵深防御：三段都先校验再拼路径（safe_join 内含 realpath 前缀断言）
    path = safe_join(
        VIDEO_OUTPUT_ROOT,
        str(int(user_id)),
        (session_id or "default"),
        f"{task_id}.mp4",
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def face_swap_video_url(task_id: str) -> str:
    """换脸视频地址（带签名，<video src> 可直接播放）。"""
    return f"/agent/v1/job/{task_id}/video?{sign_url('job_video', task_id)}"


class FaceSwapService:

    # ============================================================
    # 提交（走 task_runner，受全局闸 + 每用户配额约束）
    # ============================================================
    def submit_group(
        self, user_id: int, session_id: str,
        source_image_file_id: str, target_video_file_ids: List[str],
        prompt: str,
    ) -> Dict:
        group_id = "g_" + uuid.uuid4().hex[:10]
        total = len(target_video_file_ids or [])
        group_key = f"{GROUP_KEY_PREFIX}{group_id}:remaining"

        try:
            redis_client._client.setex(group_key, GROUP_TTL, total)
        except Exception as e:
            logger.warning(f"[FaceSwapService] set group counter failed: {e}")

        task_ids: List[str] = []
        for vid in target_video_file_ids or []:
            slots = {
                "group_id": group_id,
                "source_image_file_id": source_image_file_id,
                "target_video_file_id": vid,
                "prompt": prompt,
            }
            try:
                task_id = task_runner.submit(
                    skill_name=SKILL_NAME,
                    session_id=session_id,
                    user_id=user_id,
                    slots=slots,
                    fn=self._run_one,
                    provider="comfyui",
                )
                task_ids.append(task_id)
            except Exception as e:
                logger.warning(
                    f"[FaceSwapService] 提交被拒 group={group_id} vid={vid}: {e}"
                )
                # 一个都没提交成功：清掉组计数并把错误抛给上层（用户能看到"任务过多"）
                if not task_ids:
                    try:
                        redis_client._client.delete(group_key)
                    except Exception:
                        pass
                    raise
                # 部分成功：把未提交的名额从计数里扣掉，避免组永远不清零
                remain = total - len(task_ids)
                try:
                    redis_client._client.decrby(group_key, remain)
                except Exception:
                    pass
                break

        logger.info(
            f"[FaceSwapService] group={group_id} submitted={len(task_ids)}/{total}"
        )
        return {"group_id": group_id, "task_ids": task_ids}

    # ============================================================
    # 单个任务（由 task_runner 在子线程调用）
    # ============================================================
    def _run_one(self, task_id: str) -> None:
        """注意：异常不要在这里吞掉 —— 交给 task_runner.fail 统一落库/落卡。"""
        info = task_repo.get(task_id)
        if not info:
            logger.warning(f"[FaceSwapService] task not found: {task_id}")
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
            video_url = face_swap_video_url(task_id)
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
            task_runner.finish(
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
            # 进度卡能被 task_runner.fail 定位到就交给它统一落卡；
            # 定位不到才自己补一条错误卡（避免两张重复卡片）。
            logger.error(f"[FaceSwapService] task failed {task_id}: {e}", exc_info=True)
            try:
                if not self._progress_msg_id(task_id, session_id):
                    self._save_failed_message(session_id, task_id, str(e))
            except Exception:
                pass
            raise

        finally:
            if video_fid:
                try:
                    file_upload_service.delete_asset(video_fid)
                except Exception:
                    pass
            if group_id:
                self._decr_group_and_cleanup(group_id, source_fid, session_id)

    # ============================================================
    # 失败兜底落卡
    # ============================================================
    def _progress_msg_id(self, task_id: str, session_id: str) -> Optional[int]:
        """找到该任务对应的进度卡消息 id（显式外键优先，LIKE 兜底）。"""
        try:
            msg_id = task_repo.get_msg_id(task_id)
        except Exception:
            msg_id = None
        if msg_id:
            return int(msg_id)
        try:
            from app.infrastructure.repositories.conversation_repo import conversation_repo
            mid = conversation_repo.get_message_id_by_task_id(session_id, task_id)
            return int(mid) if mid else None
        except Exception:
            return None

    def _save_failed_message(self, session_id, task_id, error):
        """仅当进度卡找不到时才写一条独立的错误卡，避免和 task_runner.fail 重复。"""
        try:
            from app.application.conversation_service import conversation_service
            card = {
                "card_id": f"err_{task_id}", "card_type": "error",
                "title": "换脸失败", "description": (error or "任务执行失败，请重试")[:500],
                "items": [],
            }
            conversation_service.save_assistant_message(
                session_id=session_id,
                content=json.dumps(card, ensure_ascii=False),
                message_type="card",
            )
        except Exception as e:
            logger.warning(f"[FaceSwapService] save error card failed: {e}")

    # ============================================================
    # 落库 / 清理
    # ============================================================
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