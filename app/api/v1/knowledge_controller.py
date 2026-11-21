# app/api/v1/knowledge_controller.py
"""知识库接口。

修复（P0-2）：原来三个端点完全没有鉴权 —— 任何人可写入知识库（污染全站 RAG）
并直接检索内容。现补登录鉴权。

注意：知识库目前是全局单库（无 user_id 维度），这里只解决"未授权访问"；
如果要做到"每个用户独立知识库"，需要给 chroma collection 加 user 维度，
属于后续功能改造。
"""
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, uid_of
from app.core.logger import logger
from app.infrastructure.services.knowledge_service import knowledge_service
from app.utils.file_parser import extract_text_from_file

router = APIRouter(tags=["知识库"])


@router.post("/agent/v1/knowledge/add")
async def add_knowledge(
    doc_id: str = Form(...),
    files: List[UploadFile] = File(...),
    user=CurrentUser,
):
    """将文档加入知识库"""
    uid = uid_of(user)
    doc_id = (doc_id or "").strip()
    if not doc_id:
        raise HTTPException(status_code=400, detail="invalid doc_id")

    total = 0
    for f in files:
        content = await f.read()
        text = extract_text_from_file(content, f.filename)
        if text:
            n = knowledge_service.add_document(f"{doc_id}_{f.filename}", text)
            total += n
    logger.info(f"[Knowledge] user={uid} doc={doc_id} 新增 {total} 个片段")
    return JSONResponse({"type": "text", "content": f"已添加 {total} 个片段"})


@router.get("/agent/v1/knowledge/query")
def query_knowledge(question: str, user=CurrentUser):
    uid = uid_of(user)
    logger.info(f"[Knowledge] user={uid} query={question[:60]!r}")
    answer = knowledge_service.ask(question)
    return JSONResponse({"type": "text", "content": answer})


@router.get("/agent/v1/knowledge/count")
def count(user=CurrentUser):
    return JSONResponse({"count": knowledge_service.count()})