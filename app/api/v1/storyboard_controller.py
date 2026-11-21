"""Storyboard 协议。

修复（P0-2）：
- download 走 `download_router`：签名有效 或 登录态+任务归属
  （<video src> 带不了 Authorization 头，必须支持签名 URL）；
- regenerate 走 `router`（需登录）：只允许针对自己的任务，且把 user_id
  透传给新任务（原来任何人可反复触发烧 GPU，且新任务归属丢失）。
"""
import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import OptionalUser, authorize_download, get_owned_task, uid_of
from app.api.v1._ownership import assert_storyboard_task
from app.core.logger import logger
from app.skills.storyboard.service import STORYBOARD_ROOT, regenerate_storyboard_task
from app.utils.safe_path import safe_join, safe_segment
from app.utils.signed_url import verify_url

router = APIRouter(tags=["Storyboard"])
download_router = APIRouter(tags=["Storyboard"])


class RegenerateRequest(BaseModel):
    task_id: str
    extra_instruction: str = ""


@download_router.get("/agent/v1/storyboard/download")
async def download_storyboard(
    task_id: str = Query(...),
    exp: str = Query(""),
    sig: str = Query(""),
    user=OptionalUser,
):
    task_id = safe_segment(task_id, "task_id")

    authorize_download(
        sig_ok=verify_url("storyboard", task_id, exp, sig),
        user=user,
        check_owner=lambda uid: assert_storyboard_task(task_id, uid),
    )

    abs_path = safe_join(STORYBOARD_ROOT, task_id, "final.mp4")
    logger.info(f"[Storyboard-Download] task={task_id}, path={abs_path}")

    if not os.path.isfile(abs_path):
        logger.warning(f"[Storyboard-Download] 文件不存在: {abs_path}")
        raise HTTPException(status_code=404, detail=f"video not found: {task_id}")

    return FileResponse(abs_path, media_type="video/mp4")


@router.post("/agent/v1/storyboard/regenerate")
async def regenerate_storyboard(payload: RegenerateRequest, request: Request, user=OptionalUser):
    task_id = safe_segment(payload.task_id, "task_id")

    # 重跑属于"写操作"，必须登录 + 归属校验（不接受纯签名 URL 触发）
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    uid = uid_of(user)
    get_owned_task(task_id, uid)

    try:
        new_task_id = regenerate_storyboard_task(
            original_task_id=task_id,
            user_id=uid,
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