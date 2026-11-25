# app/skills/document_qa/service.py
"""文档问答业务：解析文档 + LLM 回答。

本版改动（不依赖向量库路线 P0）
--------------------------------
旧实现对超过 12000 字的文档做**头部暴力截断**，导致上传整库代码（dump 85 万字）时
模型只看到开头目录壳，于是"未找到相关内容"。改为：
1. 取消固定 12000 头部截断，改为**按 token 预算的头尾保留**（保住开头结构与结尾结论）；
2. 预算从配置 agent.context.qa_doc_max_tokens 读取，缺省 14000；
3. 在 prompt 里声明"文档可能已按预算压缩"，避免模型把省略当成"内容不存在"而误判。
真正的"整库按需理解"由 code 链路的项目地图 + 多轮追问完成（见 skills/code）。
"""
from __future__ import annotations

import os

from app.config.loader import config
from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.document_qa.prompts import QA_SYSTEM_PROMPT
from app.utils.context_budget import fit
from app.utils.file_parser import extract_text_from_file
from app.utils.file_registry import file_registry


class DocumentQaService:

    def answer(self, file_id: str, question: str) -> str:
        logger.info(f"[DocumentQA] 开始处理 file_id={file_id}")
        text = self._read_doc(file_id)
        if not text:
            return "未能解析文档内容，请确认文件未损坏且格式受支持。"

        logger.info(f"[DocumentQA] 文档解析成功，长度={len(text)}")

        max_tokens = int(config.get("agent.context.qa_doc_max_tokens", 14000) or 14000)
        before = len(text)
        text = fit(text, max_tokens, keep="head_tail",
                   marker="\n...[文档过长，已按上下文预算压缩中间 {omitted} 字；"
                          "如需被省略部分请点名提问]...\n")
        if len(text) != before:
            logger.info(f"[DocumentQA] 文档按预算压缩 {before} 字 -> {len(text)} 字"
                        f"（预算 {max_tokens} token）")

        user_msg = (
            "文档内容如下（超长时中间可能被省略；若问题恰好落在被省略部分，"
            f"请回答'该部分未在当前上下文内'而不是臆测）：\n{text}\n\n问题：{question}"
        )

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
        path = meta.get("path")
        name = meta.get("name", "doc")
        if not path:
            logger.warning("[DocumentQA] meta 中没有 path 字段")
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
