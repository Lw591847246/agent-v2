# app/api/v1/custom_avatar_controller.py
import os
import urllib.parse
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["CustomAvatar"])


@router.get("/agent/v1/custom_avatar/download")
async def download_custom_avatar(path: str = Query(...)):
    decoded = urllib.parse.unquote(path).strip()
    if os.name == "nt":
        decoded = decoded.replace("/", "\\")

    if not os.path.isfile(decoded):
        raise HTTPException(status_code=404, detail="video not found")

    return FileResponse(decoded, media_type="video/mp4")