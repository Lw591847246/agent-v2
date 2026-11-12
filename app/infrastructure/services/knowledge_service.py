"""知识库服务（RAG）"""
from typing import List, Dict
from app.infrastructure.clients.ollama_client import ollama_client
from app.infrastructure.vectorstore.chroma_client import chroma_client
from app.utils.text_utils import split_text
from app.config.settings import vector_store_settings
from app.core.logger import logger


class KnowledgeService:

    def add_document(self, doc_id: str, text: str):
        """将文档切块、向量化、存入向量库"""
        chunks = split_text(
            text,
            chunk_size=vector_store_settings.chunk_size,
            overlap=vector_store_settings.chunk_overlap,
        )
        if not chunks:
            return 0

        embeddings = ollama_client.embed_batch(chunks)
        ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        metadatas = [{"doc_id": doc_id, "chunk_index": i} for i in range(len(chunks))]

        chroma_client.add(ids, embeddings, chunks, metadatas)
        return len(chunks)

    def query(self, question: str, top_k: int = None) -> List[Dict]:
        """检索相关片段"""
        emb = ollama_client.embed(question)
        result = chroma_client.query(emb, top_k)

        docs = []
        if result and result.get("documents"):
            for i, doc in enumerate(result["documents"][0]):
                docs.append({
                    "content": doc,
                    "metadata": result["metadatas"][0][i] if result.get("metadatas") else {},
                    "distance": result["distances"][0][i] if result.get("distances") else None,
                })
        return docs

    def ask(self, question: str, top_k: int = None) -> str:
        """检索 + 生成答案"""
        docs = self.query(question, top_k)
        if not docs:
            return "未在知识库中找到相关内容。"

        context = "\n\n".join([d["content"] for d in docs])
        prompt = f"""请根据以下资料回答用户问题。如果资料中没有相关信息，直接说明。

资料：
{context}

问题：{question}

回答："""
        return ollama_client.chat(
            service="chat", model_key="chat",
            messages=[{"role": "user", "content": prompt}],
        )

    def count(self) -> int:
        return chroma_client.count()


knowledge_service = KnowledgeService()