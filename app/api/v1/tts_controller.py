# app/api/v1/tts_controller.py
"""TTS 合成 + 音频下载（filename 加固 + 授权）。

- filename 只允许 [A-Za-z0-9_.-]，不允许分隔符 / ..；
- 下载走 download_router：签名有效 或 登录态（音频文件本身无归属元数据，
  靠 128 bit 级不可猜文件名 + 登录/签名双通道保护）。
"""
import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import CurrentUser, OptionalUser, authorize_download
from app.infrastructure.services.tts_service import tts_service
from app.utils.signed_url import verify_url

router = APIRouter(tags=["语音"])
download_router = APIRouter(tags=["语音"])

_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class TtsRequest(BaseModel):
    text: str
    voice: str = ""


@router.post("/agent/v1/tts")
async def tts(payload: TtsRequest, user=CurrentUser):
    try:
        return await tts_service.synthesize(payload.text, payload.voice)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@download_router.get("/agent/v1/tts/{filename}")
async def get_tts_file(
    filename: str,
    exp: str = "",
    sig: str = "",
    user=OptionalUser,
):
    authorize_download(
        sig_ok=verify_url("tts", filename, exp, sig),
        user=user,
        check_owner=None,
    )

    if not filename or not _FILENAME_RE.match(filename) or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    path = tts_service.get_audio_path(filename)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="音频文件不存在")

    if filename.endswith(".mp3"):
        media_type = "audio/mpeg"
    elif filename.endswith(".wav"):
        media_type = "audio/wav"
    else:
        media_type = "application/octet-stream"

    return FileResponse(path, media_type=media_type)