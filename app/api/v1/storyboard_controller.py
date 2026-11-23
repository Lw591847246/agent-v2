"""Storyboard 协议。"""
import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.core.logger import logger
from app.skills.storyboard.service import STORYBOARD_ROOT, regenerate_storyboard_task

# 1. 普通路由（需要登录态）
router = APIRouter(tags=["Storyboard"])

# 2. 下载路由（前端使用带签名的 URL，无需 Token，由 router.py 统一挂载）
download_router = APIRouter(tags=["Storyboard"])

_CHUNK = 1024 * 1024  # 1MB


class RegenerateRequest(BaseModel):
    task_id: str
    extra_instruction: str = ""


# ⭐ 注意：挂载在 download_router 上
@download_router.get("/agent/v1/storyboard/download")
async def download_storyboard(task_id: str = Query(...), request: Request = None):
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        raise HTTPException(status_code=400, detail="invalid task_id")

    file_path = os.path.join(STORYBOARD_ROOT, task_id, "final.mp4")
    abs_path = os.path.abspath(file_path)
    logger.info(f"[Storyboard-Download] task={task_id}, path={abs_path}")

    if not os.path.isfile(abs_path):
        logger.warning(f"[Storyboard-Download] 文件不存在: {abs_path}")
        raise HTTPException(status_code=404, detail=f"video not found: {task_id}")

    total = os.path.getsize(abs_path)
    if total == 0:
        raise HTTPException(status_code=404, detail="video file is empty")

    # ⭐ 核心修复：手动处理 Range 请求，避免 FileResponse 在 Windows 下的大文件锁冲突
    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        try:
            spec = range_header[6:].strip()
            left, _, right = spec.partition("-")
            left = left.strip()
            right = right.strip()

            if left:
                start = int(left)
                end = int(right) if right else total - 1
            else:
                n = int(right) if right else 0
                start, end = (max(0, total - n), total - 1) if n > 0 else (0, total - 1)

            start = max(0, start)
            end = min(end, total - 1)

            if start > end or start >= total:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{total}"},
                )

            length = end - start + 1
            with open(abs_path, "rb") as f:
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
        except Exception as e:
            logger.warning(f"[Storyboard-Download] range 解析失败: {e}")
            # 解析失败回退到全量流式

    # 无 Range：流式返回，避免大文件读入内存
    def _iter_file():
        with open(abs_path, "rb") as f:
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


# ⭐ 注意：重新生成接口需要用户身份，挂载在普通 router 上
@router.post("/agent/v1/storyboard/regenerate")
async def regenerate_storyboard(payload: RegenerateRequest):
    try:
        new_task_id = regenerate_storyboard_task(
            original_task_id=payload.task_id,
            extra_instruction=payload.extra_instruction,
        )
        return {
            "ok": True,
            "new_task_id": new_task_id,
            "message": (
                "重新生成任务已创建" if not payload.extra_instruction
                else "已带修改意见重新生成"
            ),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[Storyboard-Regenerate] 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))