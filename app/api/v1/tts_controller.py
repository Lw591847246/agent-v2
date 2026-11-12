# app/api/v1/tts_controller.py
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.infrastructure.services.tts_service import tts_service

from app.middleware.auth import auth_interceptor

router = APIRouter(tags=["语音"])

class TtsRequest(BaseModel):
    text: str
    voice: str = ""  # 留空则使用 IndexTTS2 的默认参考音频

@router.post("/agent/v1/tts")
async def tts(payload: TtsRequest, user=Depends(auth_interceptor)):
    """
    合成语音并返回音频 URL
    """
    try:
        return await tts_service.synthesize(payload.text, payload.voice)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/agent/v1/tts/{filename}")
async def get_tts_file(filename: str):
    """
    播放音频文件
    """
    path = tts_service.get_audio_path(filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="音频文件不存在")

    if filename.endswith(".mp3"):
        media_type = "audio/mpeg"
    elif filename.endswith(".wav"):
        media_type = "audio/wav"
    else:
        media_type = "application/octet-stream"

    return FileResponse(path, media_type=media_type)