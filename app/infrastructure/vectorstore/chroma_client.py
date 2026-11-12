"""ChromaDB 向量库客户端"""
import chromadb
from typing import List, Dict
from app.config.loader import config
from app.core.logger import logger


class ChromaClient:

    def __init__(self):
        self._client = chromadb.PersistentClient(path=config.get("vector_store.path"))
        self._collection = self._client.get_or_create_collection(
            name=config.get("vector_store.collection")
        )

    def add(self, ids: List[str], embeddings: List[List[float]],
            documents: List[str], metadatas: List[Dict] = None):
        self._collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas or [{} for _ in ids],
        )
        logger.info(f"向量库写入 {len(ids)} 条")

    def query(self, query_embedding: List[float], top_k: int = None) -> Dict:
        top_k = top_k or config.get("vector_store.top_k", 5)
        return self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )

    def count(self) -> int:
        return self._collection.count()

    def clear(self):
        self._client.delete_collection(config.get("vector_store.collection"))
        self._collection = self._client.get_or_create_collection(
            name=config.get("vector_store.collection")
        )


chroma_client = ChromaClient()