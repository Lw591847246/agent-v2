# app/skills/document_summarize/service.py
"""文档概括业务：单文档 + 批量。"""
from __future__ import annotations

import os
from typing import Dict, List

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.document_summarize.prompts import (
    COMPRESS_PROMPT, FINAL_PROMPT, SUMMARIZE_SYSTEM_PROMPT,
)
from app.utils.file_parser import extract_text_from_file
from app.utils.file_registry import file_registry


_MAX_DOC_CHARS = 12000


class DocumentSummarizeService:

    def summarize(self, file_id: str, user_query: str = "提取核心信息") -> str:
        text = self._read_doc(file_id)
        if not text:
            return "未能解析文档内容。"

        if len(text) > _MAX_DOC_CHARS:
            text = text[:_MAX_DOC_CHARS] + "\n...(已截断)"

        user_msg = f"用户要求：{user_query}\n\n文档：\n{text}\n\n请按要求处理，输出 Markdown。"

        try:
            return ollama_client.chat(
                service="chat", model_key="chat",
                messages=[
                    {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
        except Exception as e:
            logger.error(f"[DocumentSummarize] 失败: {e}")
            return f"文档概括失败：{e}"

    def summarize_batch(self, file_ids: List[str], user_query: str = "提取核心信息") -> str:
        """批量：map-reduce。"""
        if not file_ids:
            return "未提供文档。"

        summaries = []
        for fid in file_ids:
            meta = file_registry.get(fid)
            name = meta.get("name", fid) if meta else fid
            text = self._read_doc(fid)
            if not text:
                continue
            if len(text) > 5000:
                text = text[:5000] + "...(截断)"
            try:
                s = ollama_client.chat(
                    service="chat", model_key="chat",
                    messages=[{
                        "role": "user",
                        "content": f"请提取该文档的核心信息与要点。直接输出摘要文本。\n文档名：{name}\n内容：\n{text}",
                    }],
                )
                summaries.append(f"【{name}】\n{s.strip()}")
            except Exception as e:
                logger.warning(f"[DocumentSummarize] {name} 失败: {e}")

        if not summaries:
            return "未能从文档中提取到有效内容。"

        combined = "\n\n".join(summaries)
        try:
            return ollama_client.chat(
                service="chat", model_key="chat",
                messages=[{
                    "role": "user",
                    "content": FINAL_PROMPT.format(summaries=combined, query=user_query),
                }],
            )
        except Exception as e:
            logger.error(f"[DocumentSummarize] reduce 失败: {e}")
            return combined

    @staticmethod
    def _read_doc(file_id: str) -> str:
        if not file_id:
            logger.warning("[DocumentSummarize] file_id 为空")
            return ""
        meta = file_registry.get(file_id)
        if not meta:
            logger.warning(f"[DocumentSummarize] file_registry 中找不到 file_id={file_id}")
            return ""
        logger.info(f"[DocumentSummarize] file_registry 命中: meta={meta}")

        path = meta.get("path")
        name = meta.get("name", "doc")
        if not path:
            logger.warning(f"[DocumentSummarize] meta 中没有 path 字段")
            return ""
        if not os.path.isfile(path):
            logger.warning(f"[DocumentSummarize] 本地文件不存在: {path}")
            return ""

        try:
            with open(path, "rb") as f:
                content = f.read()
            logger.info(f"[DocumentSummarize] 文件读取成功: {path}, size={len(content)} bytes")
            text = extract_text_from_file(content, name)
            logger.info(f"[DocumentSummarize] 解析完成: name={name}, 文本长度={len(text)}")
            return text
        except Exception as e:
            logger.error(f"[DocumentSummarize] 读取或解析 {name} 失败: {e}", exc_info=True)
            return ""


document_summarize_service = DocumentSummarizeService()