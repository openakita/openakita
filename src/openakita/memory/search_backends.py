"""
搜索后端抽象层

三种可插拔的搜索后端:
- FTS5Backend: SQLite FTS5 全文搜索 (默认, 零外部依赖)
- ChromaDBBackend: 本地向量搜索 (可选, 需要 chromadb + sentence-transformers)
- APIEmbeddingBackend: 在线 Embedding API (可选, 需要 API key)

用法:
    backend = create_search_backend("fts5", storage=storage)
    results = backend.search("代码风格", limit=10)
"""

from __future__ import annotations

import hashlib
import logging
import struct
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# =========================================================================
# Abstract Protocol
# =========================================================================


@runtime_checkable
class SearchBackend(Protocol):
    """搜索后端抽象接口"""

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
    ) -> list[tuple[str, float]]:
        """搜索, 返回 [(memory_id, score), ...], score 越高越相关"""
        ...

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> bool:
        ...

    def delete(self, memory_id: str) -> bool:
        ...

    def batch_add(self, items: list[dict]) -> int:
        ...

    @property
    def available(self) -> bool:
        ...

    @property
    def backend_type(self) -> str:
        ...


# =========================================================================
# FTS5 Backend (default)
# =========================================================================


class FTS5Backend:
    """
    SQLite FTS5 全文搜索后端 (默认)

    - jieba 分词: 写入前将中文文本分词为空格分隔 tokens
    - BM25 排序: SQLite FTS5 内置 bm25() 函数
    - 零外部依赖 (jieba 是纯 Python, ~15MB)
    - 零初始化延迟
    """

    backend_type = "fts5"

    def __init__(self, storage: Any) -> None:
        self._storage = storage
        self._jieba = None
        self._jieba_available: bool | None = None

    @property
    def available(self) -> bool:
        return True

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
    ) -> list[tuple[str, float]]:
        segmented = self._segment(query)
        results = self._storage.search_fts(segmented, limit=limit * 2)

        if filter_type:
            results = [r for r in results if r.get("type", "").upper() == filter_type.upper()]

        output: list[tuple[str, float]] = []
        for r in results[:limit]:
            rank = abs(r.get("rank", 0))
            score = 1.0 / (1.0 + rank) if rank else 1.0
            output.append((r["id"], score))
        return output

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> bool:
        return True

    def delete(self, memory_id: str) -> bool:
        return True

    def batch_add(self, items: list[dict]) -> int:
        return len(items)

    def _segment(self, text: str) -> str:
        """jieba 中文分词, 回退到原文"""
        if self._jieba_available is None:
            try:
                import jieba
                self._jieba = jieba
                self._jieba_available = True
                self._jieba.setLogLevel(logging.WARNING)
            except ImportError:
                self._jieba_available = False
                logger.info("[FTS5] jieba not installed, using raw text for search")

        if self._jieba_available and self._jieba is not None:
            return " ".join(self._jieba.cut_for_search(text))
        return text


# =========================================================================
# ChromaDB Backend (optional)
# =========================================================================


class ChromaDBBackend:
    """
    ChromaDB 向量搜索后端 (可选)

    封装现有 VectorStore, 适配 SearchBackend 接口。
    """

    backend_type = "chromadb"

    def __init__(self, vector_store: Any) -> None:
        self._vs = vector_store

    @property
    def available(self) -> bool:
        return self._vs.enabled

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
    ) -> list[tuple[str, float]]:
        results = self._vs.search(
            query=query,
            limit=limit,
            filter_type=filter_type.lower() if filter_type else None,
        )
        output: list[tuple[str, float]] = []
        for memory_id, distance in results:
            score = max(0.0, 1.0 - distance)
            output.append((memory_id, score))
        return output

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> bool:
        meta = metadata or {}
        return self._vs.add_memory(
            memory_id=memory_id,
            content=content,
            memory_type=meta.get("type", "fact"),
            priority=meta.get("priority", "short_term"),
            importance=meta.get("importance", 0.5),
            tags=meta.get("tags", []),
        )

    def delete(self, memory_id: str) -> bool:
        return self._vs.delete_memory(memory_id)

    def batch_add(self, items: list[dict]) -> int:
        return self._vs.batch_add(items)


# =========================================================================
# API Embedding Backend (optional)
# =========================================================================


class APIEmbeddingBackend:
    """
    在线 Embedding API 搜索后端 (可选)

    支持 DashScope (text-embedding-v3) 和 OpenAI (text-embedding-3-small)。
    embedding 结果缓存到 SQLite embedding_cache 表。
    """

    backend_type = "api_embedding"

    def __init__(
        self,
        storage: Any,
        provider: str = "dashscope",
        api_key: str = "",
        model: str = "",
        dimensions: int = 1024,
    ) -> None:
        self._storage = storage
        self._provider = provider
        self._api_key = api_key
        self._model = model or self._default_model(provider)
        self._dimensions = dimensions
        self._httpx = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def _default_model(provider: str) -> str:
        defaults = {
            "dashscope": "text-embedding-v3",
            "openai": "text-embedding-3-small",
        }
        return defaults.get(provider, "text-embedding-v3")

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
    ) -> list[tuple[str, float]]:
        query_emb = self._get_embedding(query)
        if query_emb is None:
            return []

        memories = self._storage.query(
            memory_type=filter_type, limit=200
        )
        if not memories:
            return []

        scored: list[tuple[str, float]] = []
        for mem in memories:
            mem_emb = self._get_embedding(mem.get("content", ""))
            if mem_emb is None:
                continue
            sim = self._cosine_similarity(query_emb, mem_emb)
            scored.append((mem["id"], sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> bool:
        self._get_embedding(content)
        return True

    def delete(self, memory_id: str) -> bool:
        return True

    def batch_add(self, items: list[dict]) -> int:
        for item in items:
            self._get_embedding(item.get("content", ""))
        return len(items)

    def _get_embedding(self, text: str) -> list[float] | None:
        if not text.strip():
            return None

        content_hash = hashlib.sha256(
            f"{self._model}:{text}".encode()
        ).hexdigest()

        cached = self._storage.get_cached_embedding(content_hash)
        if cached is not None:
            return self._bytes_to_floats(cached)

        embedding = self._call_api(text)
        if embedding is not None:
            blob = self._floats_to_bytes(embedding)
            self._storage.save_cached_embedding(
                content_hash, blob, self._model, len(embedding)
            )
        return embedding

    def _call_api(self, text: str) -> list[float] | None:
        try:
            if self._httpx is None:
                import httpx
                self._httpx = httpx

            if self._provider == "dashscope":
                return self._call_dashscope(text)
            elif self._provider == "openai":
                return self._call_openai(text)
            else:
                logger.warning(f"Unknown embedding provider: {self._provider}")
                return None
        except Exception as e:
            logger.error(f"Embedding API call failed: {e}")
            return None

    def _call_dashscope(self, text: str) -> list[float] | None:
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": [text],
            "encoding_format": "float",
        }
        if self._dimensions:
            payload["dimensions"] = self._dimensions

        resp = self._httpx.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]

    def _call_openai(self, text: str) -> list[float] | None:
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": text,
        }
        if self._dimensions:
            payload["dimensions"] = self._dimensions

        resp = self._httpx.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _floats_to_bytes(floats: list[float]) -> bytes:
        return struct.pack(f"{len(floats)}f", *floats)

    @staticmethod
    def _bytes_to_floats(data: bytes) -> list[float]:
        n = len(data) // 4
        return list(struct.unpack(f"{n}f", data))


# =========================================================================
# Factory
# =========================================================================


def create_search_backend(
    backend_type: str = "fts5",
    *,
    storage: Any = None,
    vector_store: Any = None,
    api_provider: str = "",
    api_key: str = "",
    api_model: str = "",
    api_dimensions: int = 1024,
) -> SearchBackend:
    """Create a search backend by type, with automatic fallback to FTS5."""

    if backend_type == "chromadb" and vector_store is not None:
        backend = ChromaDBBackend(vector_store)
        if backend.available:
            logger.info("[SearchBackend] Using ChromaDB backend")
            return backend
        logger.warning("[SearchBackend] ChromaDB not available, falling back to FTS5")

    if backend_type == "api_embedding" and api_key:
        backend = APIEmbeddingBackend(
            storage=storage,
            provider=api_provider,
            api_key=api_key,
            model=api_model,
            dimensions=api_dimensions,
        )
        if backend.available:
            logger.info(f"[SearchBackend] Using API Embedding backend ({api_provider})")
            return backend
        logger.warning("[SearchBackend] API Embedding not available, falling back to FTS5")

    if storage is None:
        raise ValueError("FTS5Backend requires a storage instance")

    logger.info("[SearchBackend] Using FTS5 backend (default)")
    return FTS5Backend(storage)
