"""Skill 产出文件下载（路径白名单校验）。"""
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.core.logger import logger

router = APIRouter(tags=["SkillDownload"])


# 允许下载的根目录白名单（相对项目根）
ALLOWED_ROOTS = [
    "./data/mindmap_output",
    "./data/prototype_output",
    "./data/ppt_output",
    "./data/word_output",
    "./data/storyboard",
    "./data/custom_avatar",
]

_MIME_MAP = {
    ".md": "text/markdown",
    ".html": "text/html",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".mp4": "video/mp4",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@router.get("/agent/v1/skill/download")
async def download_skill_output(path: str = Query(..., description="相对路径")):
    # 1. 基础安全校验
    if not path or ".." in path or path.startswith("/") or path.startswith("\\"):
        raise HTTPException(status_code=400, detail="invalid path")

    abs_path = os.path.abspath(path)

    # 2. 白名单校验
    allowed = [os.path.abspath(r) for r in ALLOWED_ROOTS]
    is_allowed = any(
        abs_path == root or abs_path.startswith(root + os.sep)
        for root in allowed
    )
    if not is_allowed:
        logger.warning(f"[SkillDownload] 路径越权: {path} -> {abs_path}")
        raise HTTPException(status_code=403, detail="path not allowed")

    # 3. 文件存在
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="file not found")

    # 4. MIME
    ext = os.path.splitext(abs_path)[1].lower()
    media_type = _MIME_MAP.get(ext, "application/octet-stream")

    logger.info(f"[SkillDownload] 下载: {abs_path} ({media_type})")
    return FileResponse(abs_path, media_type=media_type)