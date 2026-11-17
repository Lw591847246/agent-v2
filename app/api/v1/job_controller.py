"""任务进度 + 视频下载（Range 支持）。返回标准 Result 协议。

⚠️ 本文件不做 GPU 锁 —— 只读 task 状态和磁盘文件，
   GPU 串行的加锁点在媒体任务提交侧（comfyui_client / liveportrait 提交处）。
"""
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.core.logger import logger
from app.infrastructure.repositories.task_repo import task_repo

router = APIRouter(tags=["Job"])

_CHUNK = 1024 * 1024  # 1MB


@router.get("/agent/v1/job/{task_id}")
def get_job_status(task_id: str) -> dict:
    info = task_repo.get(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="task not found")

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

    total = os.path.getsize(path)
    if total == 0:
        raise HTTPException(status_code=404, detail="video file is empty")

    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        try:
            spec = range_header[6:].strip()
            left, _, right = spec.partition("-")
            left = left.strip()
            right = right.strip()

            if left:
                # 普通范围 bytes=START-[END]
                start = int(left)
                end = int(right) if right else total - 1
            else:
                # 后缀范围 bytes=-N（最后 N 字节）
                n = int(right) if right else 0
                if n <= 0:
                    start, end = 0, total - 1
                else:
                    start = max(0, total - n)
                    end = total - 1

            if start < 0:
                start = 0
            if end >= total:
                end = total - 1

            if start > end or start >= total:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{total}"},
                )

            length = end - start + 1
            with open(path, "rb") as f:
                f.seek(start)
                chunk = f.read(length)

            return Response(
                content=chunk, status_code=206, media_type="video/mp4",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(chunk)),
                    "Cache-Control": "public, max-age=3600",
                },
            )
        except ValueError as e:
            logger.warning(f"[Job] range 解析失败: {e} (header={range_header!r})")
            # 解析失败 → 回退到全量
        except Exception as e:
            logger.warning(f"[Job] range 处理异常: {e}")

    # 无 Range：流式返回，避免大视频整体读入内存
    def _iter_file():
        with open(path, "rb") as f:
            while True:
                buf = f.read(_CHUNK)
                if not buf:
                    break
                yield buf

    return StreamingResponse(
        _iter_file(), media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(total),
            "Cache-Control": "public, max-age=3600",
        },
    )