from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse
from typing import List

from app.infrastructure.services.knowledge_service import knowledge_service

from app.utils.file_parser import extract_text_from_file
from app.core.logger import logger

router = APIRouter(tags=["知识库"])


@router.post("/agent/v1/knowledge/add")
async def add_knowledge(
    doc_id: str = Form(...),
    files: List[UploadFile] = File(...),
):
    """将文档加入知识库"""
    total = 0
    for f in files:
        content = await f.read()
        text = extract_text_from_file(content, f.filename)
        if text:
            n = knowledge_service.add_document(f"{doc_id}_{f.filename}", text)
            total += n
    return JSONResponse({"type": "text", "content": f"已添加 {total} 个片段"})


@router.get("/agent/v1/knowledge/query")
def query_knowledge(question: str):
    answer = knowledge_service.ask(question)
    return JSONResponse({"type": "text", "content": answer})


@router.get("/agent/v1/knowledge/count")
def count():
    return JSONResponse({"count": knowledge_service.count()})