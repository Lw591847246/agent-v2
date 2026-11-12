"""PPT 下载接口"""
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.core.logger import logger

router = APIRouter(tags=["PPT"])

PPT_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PPT_OUTPUT_ROOT = "./data/ppt_output"


@router.get("/agent/v1/ppt/download/{session_id}/{filename}")
async def download_ppt(session_id: str, filename: str):
    # 路径安全校验
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    file_path = os.path.join(PPT_OUTPUT_ROOT, session_id, filename)
    if not os.path.isfile(file_path):
        logger.warning(f"【PPT】文件不存在: {file_path}")
        raise HTTPException(status_code=404, detail="PPT 文件不存在")

    logger.info(f"【PPT】下载: {file_path}")
    return FileResponse(path=file_path, media_type=PPT_MIME, filename=filename)