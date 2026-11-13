"""任务进度 + 视频下载（Range 支持）。返回标准 Result 协议。"""
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.core.logger import logger
from app.infrastructure.repositories.task_repo import task_repo

router = APIRouter(tags=["Job"])


@router.get("/agent/v1/job/{task_id}")
def get_job_status(task_id: str) -> dict:
    info = task_repo.get(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="task not found")

    # ⭐ 解析 steps / parts / outputs
    progress_data = task_repo.get_progress(task_id) or {}

    file_size = 0
    if info.output_path and os.path.isfile(info.output_path):
        try:
            file_size = os.stat(info.output_path).st_size
        except Exception:
            pass

    duration_ms = 0
    if info.created_at and info.finished_at:
        try:
            from datetime import datetime as _dt
            t0 = _dt.fromisoformat(info.created_at)
            t1 = _dt.fromisoformat(info.finished_at)
            duration_ms = max(0, int((t1 - t0).total_seconds() * 1000))
        except Exception:
            pass

    # ⭐ 返回标准 Result
    return {
        "task_id": task_id,
        "status": info.status,
        "progress": info.progress,
        "msg": progress_data.get("msg", ""),
        "steps": progress_data.get("steps", []),
        "parts": progress_data.get("parts", []),
        "outputs": progress_data.get("outputs", []),
        "error": info.error,
        "duration_ms": duration_ms,
        "file_size": file_size,
    }


@router.get("/agent/v1/job/{task_id}/video")
async def get_job_video(task_id: str, request: Request):
    info = task_repo.get(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="task not found")
    if info.status != "done":
        raise HTTPException(status_code=409, detail=f"task not ready: {info.status}")

    path = info.output_path
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="video file not found")

    with open(path, "rb") as f:
        content = f.read()
    total = len(content)

    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        try:
            parts = range_header[6:].split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else total - 1
            start = max(0, start)
            end = min(end, total - 1)
            if start > end:
                start, end = 0, total - 1
            chunk = content[start:end + 1]
            return Response(
                content=chunk, status_code=206, media_type="video/mp4",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(chunk)),
                },
            )
        except Exception as e:
            logger.warning(f"[Job] range parse failed: {e}")

    return Response(
        content=content, media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Content-Length": str(total)},
    )