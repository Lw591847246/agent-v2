# app/api/v1/word_controller.py
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.core.logger import logger
import os

router = APIRouter(tags=["Word"])
WORD_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/agent/v1/word/download/{session_id}/{filename}")
async def download_word(session_id: str, filename: str):
    file_path = os.path.join("./data/word_output", session_id, filename)
    if not os.path.exists(file_path):
        logger.warning(f"【Word下载】文件不存在: {file_path}")
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(path=file_path, media_type=WORD_MIME, filename=filename)