"""通用文件上传接口"""
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

from app.middleware.auth import auth_interceptor
from app.application.file_upload_service import file_upload_service
from app.core.logger import logger

router = APIRouter(prefix="/agent/v1/upload", tags=["上传"])


@router.post("/asset")
async def upload_asset(
    file: UploadFile = File(...),
    session_id: str = Form(""),
    user: dict = Depends(auth_interceptor),
):
    user_id = user.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="userId missing")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    try:
        meta = file_upload_service.save_asset(
            content=content,
            filename=file.filename or "unnamed",
            user_id=user_id,
            session_id=session_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[Upload] save failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="upload failed")

    return {
        "file_id": meta["file_id"],
        "type": meta["type"],
        "name": meta["name"],
        "size": meta["size"],
        "duration": meta.get("duration"),
        "thumb_url": (
            f"/agent/v1/upload/thumb/{meta['file_id']}"
            if meta.get("thumb_path") else None
        ),
    }


@router.get("/thumb/{file_id}")
def get_thumb(file_id: str):
    data = file_upload_service.get_thumb_bytes(file_id)
    if not data:
        raise HTTPException(status_code=404, detail="thumb not found")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )