# app/api/v1/code_workspace_controller.py
"""Code skill 工作区 HTTP 适配层。

只做：鉴权依赖、会话归属、参数解析、调 skill service。
业务逻辑全部在 app/skills/code/workspace/。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import CurrentUser, assert_session_owned, uid_of
from app.skills.code.workspace.scan_service import code_scan_service
from app.skills.code.workspace.service import code_workspace_service
from app.core.exceptions import BadRequestException
from app.core.logger import logger

router = APIRouter(tags=["代码工作区"])


@router.post("/agent/v1/workspace/upload")
async def upload_batch(
    session_id: str = Form(...),
    batch_id: str = Form(...),
    paths: str = Form(...),          # JSON 数组，与 files 顺序一致
    scope: str = Form("all"),
    module_id: str = Form(""),
    files: List[UploadFile] = File(...),
    user=CurrentUser,
):
    uid = uid_of(user)
    sid = assert_session_owned(session_id, uid)

    # 1) 参数解析
    try:
        rel_paths = json.loads(paths)
    except Exception:
        raise HTTPException(status_code=400, detail="paths 必须是 JSON 数组")
    if not isinstance(rel_paths, list):
        raise HTTPException(status_code=400, detail="paths 必须是 JSON 数组")
    if len(rel_paths) != len(files):
        raise HTTPException(status_code=400, detail="paths 与 files 数量不一致")

    # 2) 读取上传流 -> (relpath, bytes)
    pairs = []
    for rel, f in zip(rel_paths, files):
        try:
            content = await f.read()
        except Exception as e:
            logger.warning(f"读取上传文件失败 rel={rel}: {e}")
            raise HTTPException(status_code=400, detail=f"读取文件失败: {rel}")
        pairs.append((rel, content))

    # 3) 调 service：业务异常交全局 handler，未知异常才转 500
    try:
        result = code_workspace_service.upload_batch(
            uid, sid, batch_id, pairs, scope=scope, module_id=module_id,
        )
    except BadRequestException:
        # 整批级校验失败（如超单批上限/总文件数超限），交全局 handler 返回 400
        raise
    except Exception as e:
        logger.exception(
            "workspace upload_batch unexpected, session=%s, batch=%s, files=%d",
            sid, batch_id, len(files),
        )
        raise HTTPException(status_code=500, detail="服务端内部错误")

    return JSONResponse(result)


@router.post("/agent/v1/workspace/scan")
async def start_scan(payload: dict, user=CurrentUser):
    uid = uid_of(user)
    sid = assert_session_owned(payload.get("session_id", ""), uid)
    job_id = code_scan_service.start_scan(
        uid, sid,
        scope=payload.get("scope", "all"),
        module_id=payload.get("module_id", ""),
    )
    return {"job_id": job_id, "session_id": sid}


@router.get("/agent/v1/workspace/scan_progress")
async def scan_progress(
    session_id: str,
    job_id: str = "",
    stream: bool = False,
    user=CurrentUser,
):
    uid = uid_of(user)
    sid = assert_session_owned(session_id, uid)

    if not stream:
        p = code_scan_service.get_progress(uid, sid, job_id or None)
        return JSONResponse(p.model_dump() if p else {})

    async def gen():
        last = ""
        while True:
            p = await asyncio.to_thread(code_scan_service.get_progress, uid, sid, job_id or None)
            if p:
                data = p.model_dump()
                body = json.dumps(data, ensure_ascii=False)
                if body != last:
                    yield f"data: {body}\n\n"
                    last = body
                if p.status in ("done", "failed", "cancelled"):
                    yield "data: [DONE]\n\n"
                    break
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/agent/v1/workspace/status")
async def status(session_id: str, user=CurrentUser):
    uid = uid_of(user)
    sid = assert_session_owned(session_id, uid)
    return JSONResponse(code_workspace_service.status(uid, sid))


@router.get("/agent/v1/workspace/map")
async def get_map(
    session_id: str,
    scope: str = "all",
    module_id: str = "",
    user=CurrentUser,
):
    uid = uid_of(user)
    sid = assert_session_owned(session_id, uid)
    cmap = code_workspace_service.get_map(uid, sid, scope=scope, module_id=module_id)
    return JSONResponse(cmap.model_dump() if cmap else {})


@router.post("/agent/v1/workspace/clear")
async def clear(payload: dict, user=CurrentUser):
    uid = uid_of(user)
    sid = assert_session_owned(payload.get("session_id", ""), uid)
    return JSONResponse(code_workspace_service.clear(uid, sid))