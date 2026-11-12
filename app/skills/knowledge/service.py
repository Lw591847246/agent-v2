# app/skills/knowledge/service.py
"""知识库业务：query / ask / count。"""
from __future__ import annotations

from typing import Dict, List

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.infrastructure.vectorstore.chroma_client import chroma_client
from app.skills.knowledge.prompts import QA_SYSTEM_PROMPT


class KnowledgeService:

    def query(self, question: str, top_k: int = 5) -> List[Dict]:
        try:
            emb = ollama_client.embed(question)
            result = chroma_client.query(emb, top_k)
        except Exception as e:
            logger.error(f"[KnowledgeService] query 失败: {e}")
            return []

        docs = []
        if result and result.get("documents"):
            for i, doc in enumerate(result["documents"][0]):
                docs.append({
                    "content": doc,
                    "metadata": result["metadatas"][0][i] if result.get("metadatas") else {},
                    "distance": result["distances"][0][i] if result.get("distances") else None,
                })
        return docs

    def ask(self, question: str, top_k: int = 5) -> str:
        docs = self.query(question, top_k)
        if not docs:
            return "知识库中未找到相关内容。"

        # 组装上下文
        context_lines = []
        for i, d in enumerate(docs, 1):
            meta = d.get("metadata") or {}
            doc_id = meta.get("doc_id", "")
            chunk_idx = meta.get("chunk_index", "")
            source = f"{doc_id}#{chunk_idx}" if doc_id else f"片段{i}"
            context_lines.append(f"[{i}] ({source})\n{d['content']}")
        context = "\n\n".join(context_lines)

        user_msg = f"""资料：
{context}

问题：{question}

请基于资料回答。"""

        try:
            return ollama_client.chat(
                service="chat", model_key="chat",
                messages=[
                    {"role": "system", "content": QA_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
        except Exception as e:
            logger.error(f"[KnowledgeService] ask 失败: {e}")
            return f"知识库问答失败：{e}"

    def count(self) -> int:
        try:
            return chroma_client.count()
        except Exception as e:
            logger.warning(f"[KnowledgeService] count 失败: {e}")
            return 0


knowledge_service = KnowledgeService()