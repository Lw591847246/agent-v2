# app/api/v1/word_controller.py
"""Word 文档下载（路径穿越加固 + 授权 + 会话归属）。

- 路径校验统一到 app/utils/safe_path.py；
- 授权 = 签名有效 或 登录态+会话归属（download_router，端点内授权）。
"""
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import OptionalUser, assert_session_owned, authorize_download
from app.core.logger import logger
from app.utils.safe_path import data_root, safe_join
from app.utils.signed_url import verify_url

router = APIRouter(tags=["Word"])
download_router = APIRouter(tags=["Word"])

WORD_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_WORD_ROOT = data_root("word_output")


@download_router.get("/agent/v1/word/download/{session_id}/{filename}")
async def download_word(
    session_id: str,
    filename: str,
    exp: str = "",
    sig: str = "",
    user=OptionalUser,
):
    ident = f"{session_id}/{filename}"

    authorize_download(
        sig_ok=verify_url("word", ident, exp, sig),
        user=user,
        check_owner=lambda uid: assert_session_owned(session_id, uid),
    )

    file_path = safe_join(_WORD_ROOT, session_id, filename)

    if not os.path.isfile(file_path):
        logger.warning(f"【Word下载】文件不存在: {file_path}")
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(path=file_path, media_type=WORD_MIME, filename=filename)