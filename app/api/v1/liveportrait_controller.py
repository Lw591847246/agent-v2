# app/api/v1/liveportrait_controller.py
"""LivePortrait 视频下载（本地路径白名单 + 代理 LP 服务）。

修复：
1) path 分支原直接 FileResponse(decoded) 允许任意文件读取 —— 限制在 ./data/ 下，
   且必须落在"可归属"的产物目录内（否则 fail-closed 404）；
2) 接口保持 sync def：内部 requests.get(stream=True) 是同步阻塞，
   放在 async def 里会卡死事件循环；sync def 由 FastAPI 丢线程池；
3) 补授权：签名有效 或 登录态 + 产物归属。

⚠️ 若 LP 产物写在 data/ 下其他目录（如 data/liveportrait/），
   需在 app/api/v1/_ownership.py 里补一条 root -> 归属 映射。
"""
import os
import urllib.parse

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import OptionalUser, authorize_download
from app.api.v1._ownership import assert_file_owned
from app.core.logger import logger
from app.utils.signed_url import verify_url

router = APIRouter(tags=["LivePortrait"])
download_router = APIRouter(tags=["LivePortrait"])

LP_BASE_URL = "http://127.0.0.1:8001"

# 只允许 ./data/ 下的文件被本地直读（所有 skill 产物都在 data 下）
_DATA_ROOT = os.path.abspath("data")


def _safe_local_path(raw_path: str) -> str:
    decoded = urllib.parse.unquote(raw_path or "").strip()
    if not decoded:
        raise HTTPException(status_code=400, detail="empty path")
    if "\x00" in decoded:
        raise HTTPException(status_code=400, detail="invalid path")

    abs_path = os.path.abspath(decoded)
    if abs_path != _DATA_ROOT and not abs_path.startswith(_DATA_ROOT + os.sep):
        logger.warning(
            f"[LP-Download] 越权路径拒绝: {raw_path!r} -> {abs_path}"
        )
        raise HTTPException(status_code=403, detail="path not allowed")

    return abs_path


@download_router.get("/agent/v1/liveportrait/download")
def download_lp_video(
    path: str = Query(None, description="本地文件绝对路径（须在 ./data/ 下）"),
    job_id: str = Query(None, description="LP 的 job_id"),
    exp: str = "",
    sig: str = "",
    user=OptionalUser,
):
    logger.info(f"[LP-Download] 收到请求: path={path!r}, job_id={job_id!r}")

    # ========== 方式 1：本地文件直读（白名单 + 归属） ==========
    if path:
        abs_path = _safe_local_path(path)
        authorize_download(
            sig_ok=verify_url("liveportrait", path, exp, sig),
            user=user,
            check_owner=lambda uid: assert_file_owned(abs_path, uid),
        )
        if not os.path.isfile(abs_path):
            logger.warning(f"[LP-Download] 文件不存在: {abs_path}")
            raise HTTPException(status_code=404, detail="video file not found")
        logger.info(f"[LP-Download] 返回文件: {abs_path} ({os.path.getsize(abs_path)} bytes)")
        return FileResponse(abs_path, media_type="video/mp4")

    # ========== 方式 2：代理 LP 服务 ==========
    if job_id:
        # job_id 即资源标识：签名绑定它，登录态则由 LP 侧 job 不可猜测性兜底
        authorize_download(
            sig_ok=verify_url("liveportrait_job", job_id, exp, sig),
            user=user,
            check_owner=None,
        )

        if not job_id.isalnum() and "-" not in job_id and "_" not in job_id:
            raise HTTPException(status_code=400, detail="invalid job_id")

        try:
            logger.info(f"[LP-Download] 代理请求: {LP_BASE_URL}/v1/jobs/{job_id}/result")
            upstream = requests.get(
                f"{LP_BASE_URL}/v1/jobs/{job_id}/result",
                stream=True,
                timeout=300,
            )
            if upstream.status_code != 200:
                logger.error(
                    f"[LP-Download] LP 返回 {upstream.status_code}: {upstream.text[:200]}"
                )
                raise HTTPException(
                    status_code=upstream.status_code,
                    detail=f"LP upstream error: {upstream.text[:200]}",
                )

            def iter_content():
                try:
                    for chunk in upstream.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
                finally:
                    upstream.close()

            return StreamingResponse(iter_content(), media_type="video/mp4")
        except requests.exceptions.ConnectionError:
            logger.error(f"[LP-Download] 无法连接 LP 服务: {LP_BASE_URL}")
            raise HTTPException(status_code=502, detail="LivePortrait 服务未启动")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[LP-Download] 代理失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=400, detail="必须提供 path 或 job_id")