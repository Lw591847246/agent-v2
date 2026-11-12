# app/skills/anchor/service.py
"""Anchor 业务：IndexTTS → LivePortrait → 轮询 → 视频。"""
from __future__ import annotations

import json
import os
import time
import urllib.parse

import requests

from app.context.task_runner import task_runner
from app.contracts.result import build_output
from app.core.logger import logger
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.infrastructure.services.tts_service import tts_service
from app.skills.anchor.prompts import (
    DEFAULT_TEMPLATE_ID, LP_BASE_URL, POLL_INTERVAL, POLL_MAX_SECONDS,
    SKILL_NAME, _JUNK_PREFIX_RE, _PREFIX_CLEAN_RE, _QUOTE_RE,
)


def extract_speech_text(raw: str) -> str:
    """从用户输入中提取台词。有引号优先取引号内容，否则剥离指令词。"""
    if not raw:
        return ""
    raw = raw.strip()
    matches = _QUOTE_RE.findall(raw)
    if matches:
        cleaned = [m.strip() for m in matches if m.strip()]
        if cleaned:
            return "，".join(cleaned)
    result = raw.replace("《", "").replace("》", "")
    result = _PREFIX_CLEAN_RE.sub("", result)
    result = _JUNK_PREFIX_RE.sub("", result)
    return result.strip() or raw


class AnchorService:

    def submit(self, session_id: str, speech_text: str, user_id: int = 0) -> str:
        def _fn(task_id: str):
            self._run_pipeline(task_id, session_id, speech_text)

        return task_runner.submit(
            skill_name=SKILL_NAME,
            session_id=session_id,
            user_id=user_id,
            slots={"speech_text": speech_text},
            fn=_fn,
            provider="liveportrait",
        )

    def _run_pipeline(self, task_id: str, session_id: str, speech_text: str) -> None:
        start_time = time.time()
        steps = [
            {"label": "IndexTTS 音频合成", "status": "running"},
            {"label": "LivePortrait 口型生成", "status": "pending"},
        ]
        try:
            task_runner.progress(task_id, 20, "IndexTTS 合成音频中...", steps=steps)
            audio_path = os.path.abspath(tts_service.synthesize_wav(text=speech_text))

            steps[0]["status"] = "done"
            steps[1]["status"] = "running"
            task_runner.progress(task_id, 40, "提交 LivePortrait...", steps=steps)

            # 提交 LP 渲染任务
            r = requests.post(
                f"{LP_BASE_URL}/v1/render/by-template",
                json={
                    "template_id": DEFAULT_TEMPLATE_ID,
                    "overrides": {"tts": {"audio_path": audio_path}},
                    "preview": False,
                },
                timeout=30,
            )
            if r.status_code != 200:
                raise RuntimeError(f"LivePortrait 返回 {r.status_code}: {r.text[:300]}")
            resp = r.json()
            job_id = resp.get("job_id")
            if not job_id:
                raise RuntimeError(f"LivePortrait 返回无 job_id: {resp}")

            # 轮询 LP 状态
            deadline = time.time() + POLL_MAX_SECONDS
            output_path = None
            while time.time() < deadline:
                try:
                    jr = requests.get(f"{LP_BASE_URL}/v1/jobs/{job_id}", timeout=10)
                    if jr.status_code != 200:
                        raise RuntimeError(f"查询 LP 失败: {jr.status_code}")
                    job = jr.json()
                    lp_status = job.get("status", "pending")
                    lp_progress = job.get("progress", 0)
                    output_path = job.get("output_path")

                    if lp_status in ("done", "success"):
                        break
                    if lp_status == "failed":
                        raise RuntimeError(f"LivePortrait 失败: {job.get('error')}")

                    mapped = 50 + int(lp_progress * 0.49)
                    task_runner.progress(task_id, mapped, f"LivePortrait 生成中 {lp_progress}%")
                except requests.exceptions.RequestException as e:
                    logger.warning(f"[Anchor] 轮询 LP 异常: {e}")
                time.sleep(POLL_INTERVAL)
            else:
                raise RuntimeError(f"LivePortrait 超时 (>{POLL_MAX_SECONDS}s)")

            # 生成视频 URL
            if output_path and os.path.isfile(output_path):
                normalized = output_path.replace("\\", "/")
                encoded = urllib.parse.quote(normalized, safe=":/")
                video_url = f"/agent/v1/liveportrait/download?path={encoded}"
            else:
                video_url = f"/agent/v1/liveportrait/download?job_id={job_id}"

            duration_ms = int((time.time() - start_time) * 1000)
            final_parts = [
                {"type": "video", "url": video_url, "task_id": task_id},
                {"type": "text", "content": "孔子授课视频生成完毕（IndexTTS 音色）！"},
            ]
            outputs = [build_output(
                kind="video",
                filename=f"{task_id}.mp4",
                path=output_path or "",
                url=video_url,
                task_id=task_id,
            )]

            task_runner.finish(
                task_id=task_id, parts=final_parts, outputs=outputs,
                output_path=output_path if output_path and os.path.isfile(output_path) else None,
                duration_ms=duration_ms, session_id=session_id,
            )
            logger.info(f"[Anchor] 完成 task_id={task_id}")

        except Exception as e:
            logger.error(f"[Anchor] 失败 task_id={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)


anchor_service = AnchorService()