# app/skills/document_qa/service.py
"""文档问答业务：解析文档 + LLM 回答。"""
from __future__ import annotations

import os
from typing import Optional

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.document_qa.prompts import QA_SYSTEM_PROMPT
from app.utils.file_parser import extract_text_from_file
from app.utils.file_registry import file_registry


_MAX_DOC_CHARS = 12000


class DocumentQaService:

    def answer(self, file_id: str, question: str) -> str:
        text = self._read_doc(file_id)
        if not text:
            return "未能解析文档内容，请确认文件未损坏且格式受支持。"

        if len(text) > _MAX_DOC_CHARS:
            logger.info(f"[DocumentQA] 文档截断 {len(text)} → {_MAX_DOC_CHARS}")
            text = text[:_MAX_DOC_CHARS] + "\n...(文档过长已截断)"

        user_msg = f"文档内容：\n{text}\n\n问题：{question}"

        try:
            return ollama_client.chat(
                service="chat", model_key="chat",
                messages=[
                    {"role": "system", "content": QA_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
        except Exception as e:
            logger.error(f"[DocumentQA] 失败: {e}")
            return f"文档问答失败：{e}"

    @staticmethod
    def _read_doc(file_id: str) -> str:
        meta = file_registry.get(file_id) if file_id else None
        if not meta:
            return ""
        path = meta.get("path")
        name = meta.get("name", "doc")
        if not path or not os.path.isfile(path):
            return ""
        try:
            with open(path, "rb") as f:
                return extract_text_from_file(f.read(), name)
        except Exception as e:
            logger.error(f"[DocumentQA] 读取 {name} 失败: {e}")
            return ""


document_qa_service = DocumentQaService()