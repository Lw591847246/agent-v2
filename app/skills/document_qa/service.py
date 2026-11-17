"""文档问答业务：解析文档 + LLM 回答。"""
from __future__ import annotations

import os

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.document_qa.prompts import QA_SYSTEM_PROMPT
from app.utils.file_parser import extract_text_from_file
from app.utils.file_registry import file_registry


_MAX_DOC_CHARS = 12000


class DocumentQaService:

    def answer(self, file_id: str, question: str) -> str:
        logger.info(f"[DocumentQA] 开始处理 file_id={file_id}")
        text = self._read_doc(file_id)
        if not text:
            return "未能解析文档内容，请确认文件未损坏且格式受支持。"

        logger.info(f"[DocumentQA] 文档解析成功，长度={len(text)}")

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
        if not file_id:
            logger.warning("[DocumentQA] file_id 为空")
            return ""
        meta = file_registry.get(file_id)
        if not meta:
            logger.warning(f"[DocumentQA] file_registry 中找不到 file_id={file_id}")
            return ""
        logger.info(f"[DocumentQA] file_registry 命中: meta={meta}")

        path = meta.get("path")
        name = meta.get("name", "doc")
        if not path:
            logger.warning(f"[DocumentQA] meta 中没有 path 字段")
            return ""
        if not os.path.isfile(path):
            logger.warning(f"[DocumentQA] 本地文件不存在: {path}")
            return ""

        try:
            with open(path, "rb") as f:
                content = f.read()
            logger.info(f"[DocumentQA] 文件读取成功: {path}, size={len(content)} bytes")
            text = extract_text_from_file(content, name)
            logger.info(f"[DocumentQA] 解析完成: name={name}, 文本长度={len(text)}")
            return text
        except Exception as e:
            logger.error(f"[DocumentQA] 读取或解析 {name} 失败: {e}", exc_info=True)
            return ""


document_qa_service = DocumentQaService()