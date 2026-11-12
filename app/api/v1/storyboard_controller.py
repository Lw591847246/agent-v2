"""Storyboard 协议。"""
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.logger import logger
from app.skills.storyboard.service import STORYBOARD_ROOT, regenerate_storyboard_task

router = APIRouter(tags=["Storyboard"])


class RegenerateRequest(BaseModel):
    task_id: str
    extra_instruction: str = ""


@router.get("/agent/v1/storyboard/download")
async def download_storyboard(task_id: str = Query(...)):
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        raise HTTPException(status_code=400, detail="invalid task_id")

    file_path = os.path.join(STORYBOARD_ROOT, task_id, "final.mp4")
    abs_path = os.path.abspath(file_path)
    logger.info(f"[Storyboard-Download] task={task_id}, path={abs_path}")

    if not os.path.isfile(abs_path):
        logger.warning(f"[Storyboard-Download] 文件不存在: {abs_path}")
        raise HTTPException(status_code=404, detail=f"video not found: {task_id}")

    return FileResponse(abs_path, media_type="video/mp4")


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