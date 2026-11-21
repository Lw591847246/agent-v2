"""PPT 下载接口。

修复（P0-3）：session_id 与 filename 两段都过白名单校验 + realpath 前缀断言；
原实现只校验 filename，session_id=".." 或 "..%5C.."（Windows 反斜杠）
即可读到 ./data 之外甚至任意路径。

授权（P0-2）：下载走 download_router，端点内授权 =
签名有效 或 登录态+会话归属。
"""
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import OptionalUser, assert_session_owned, authorize_download
from app.core.logger import logger
from app.utils.safe_path import data_root, safe_join
from app.utils.signed_url import verify_url

router = APIRouter(tags=["PPT"])
download_router = APIRouter(tags=["PPT"])

PPT_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PPT_OUTPUT_ROOT = data_root("ppt_output")


@download_router.get("/agent/v1/ppt/download/{session_id}/{filename}")
async def download_ppt(
    session_id: str,
    filename: str,
    exp: str = "",
    sig: str = "",
    user=OptionalUser,
):
    ident = f"{session_id}/{filename}"

    authorize_download(
        sig_ok=verify_url("ppt", ident, exp, sig),
        user=user,
        check_owner=lambda uid: assert_session_owned(session_id, uid),
    )

    # 归属通过后再做路径安全：两段都校验 + realpath 断言
    file_path = safe_join(PPT_OUTPUT_ROOT, session_id, filename)

    if not os.path.isfile(file_path):
        logger.warning(f"【PPT】文件不存在: {file_path}")
        raise HTTPException(status_code=404, detail="PPT 文件不存在")

    logger.info(f"【PPT】下载: {file_path}")
    return FileResponse(path=file_path, media_type=PPT_MIME, filename=filename)