# app/api/v1/liveportrait_controller.py
import os
import urllib.parse
import requests
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from app.core.logger import logger

router = APIRouter(tags=["LivePortrait"])

LP_BASE_URL = "http://127.0.0.1:8001"


@router.get("/agent/v1/liveportrait/download")
async def download_lp_video(
        path: str = Query(None, description="本地文件绝对路径"),
        job_id: str = Query(None, description="LP 的 job_id"),
):
    logger.info(f"[LP-Download] 收到请求: path={path!r}, job_id={job_id!r}")

    # ========== 方式 1：直接读本地文件 ==========
    if path:
        # URL 解码
        decoded = urllib.parse.unquote(path)
        # Windows 路径兼容：/ -> \
        if os.name == "nt":
            decoded = decoded.replace("/", "\\")
        # 去除首尾空白
        decoded = decoded.strip()

        logger.info(f"[LP-Download] 解码后路径: {decoded!r}")

        if not os.path.isfile(decoded):
            logger.warning(f"[LP-Download] 文件不存在: {decoded}")
            raise HTTPException(status_code=404, detail=f"video file not found: {decoded}")

        logger.info(f"[LP-Download] 返回文件: {decoded} ({os.path.getsize(decoded)} bytes)")
        return FileResponse(decoded, media_type="video/mp4")

    # ========== 方式 2：代理 LP 服务 ==========
    if job_id:
        try:
            logger.info(f"[LP-Download] 代理请求: {LP_BASE_URL}/v1/jobs/{job_id}/result")
            upstream = requests.get(
                f"{LP_BASE_URL}/v1/jobs/{job_id}/result",
                stream=True,
                timeout=300,
            )
            if upstream.status_code != 200:
                logger.error(f"[LP-Download] LP 返回 {upstream.status_code}: {upstream.text[:200]}")
                raise HTTPException(
                    status_code=upstream.status_code,
                    detail=f"LP upstream error: {upstream.text[:200]}",
                )

            def iter_content():
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk

            return StreamingResponse(iter_content(), media_type="video/mp4")
        except requests.exceptions.ConnectionError:
            logger.error(f"[LP-Download] 无法连接 LP 服务: {LP_BASE_URL}")
            raise HTTPException(status_code=502, detail="LivePortrait 服务未启动")
        except Exception as e:
            logger.error(f"[LP-Download] 代理失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=400, detail="必须提供 path 或 job_id")