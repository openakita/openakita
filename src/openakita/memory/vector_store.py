"""
向量存储 - 基于 ChromaDB

提供语义搜索能力:
- 记忆向量化存储
- 语义相似度搜索
- 支持按类型过滤
- 支持多源下载 (HuggingFace / hf-mirror / ModelScope)
"""

import asyncio
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 延迟导入，避免未安装依赖时报错
_sentence_transformers_available = None
_chromadb = None


def _lazy_import():
    """延迟导入依赖"""
    global _sentence_transformers_available, _chromadb

    if _sentence_transformers_available is None:
        try:
            import sentence_transformers  # noqa: F401

            _sentence_transformers_available = True
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("sentence_transformers")
            logger.warning(f"向量搜索不可用: {hint}")
            _sentence_transformers_available = False
            return False

    if not _sentence_transformers_available:
        return False

    if _chromadb is None:
        try:
            import chromadb

            _chromadb = chromadb
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("chromadb")
            logger.warning(f"ChromaDB 不可用: {hint}")
            return False

    return True


class VectorStore:
    """
    向量存储 - 基于 ChromaDB

    使用本地 embedding 模型，无需 API 调用。
    支持多下载源 (HuggingFace / hf-mirror / ModelScope)。
    """

    # 默认使用中文优化的 embedding 模型
    DEFAULT_MODEL = "shibing624/text2vec-base-chinese"

    def __init__(
        self,
        data_dir: Path,
        model_name: str | None = None,
        device: str = "cpu",
        download_source: str = "auto",
    ):
        """
        初始化向量存储

        Args:
            data_dir: 数据目录
            model_name: embedding 模型名称 (默认 shibing624/text2vec-base-chinese)
            device: 设备 (cpu 或 cuda)
            download_source: 下载源 ("auto" | "huggingface" | "hf-mirror" | "modelscope")
        """
        self.data_dir = Path(data_dir)
        self.model_name = model_name or self.DEFAULT_MODEL
        self.device = device
        self.download_source = download_source

        self._model = None
        self._client = None
        self._collection = None
        self._enabled = False

        # 延迟初始化
        self._initialized = False
        self._lock = threading.RLock()

    def _ensure_initialized(self) -> bool:
        """确保已初始化"""
        with self._lock:
            if self._initialized:
                return self._enabled

            self._initialized = True

            if not _lazy_import():
                self._enabled = False
                return False

            try:
                # 初始化 embedding 模型（支持多源下载）
                from .model_hub import load_embedding_model

                logger.info(
                    f"Loading embedding model: {self.model_name} "
                    f"(source={self.download_source})"
                )
                self._model = load_embedding_model(
                    model_name=self.model_name,
                    source=self.download_source,
                    device=self.device,
                )

                # 初始化 ChromaDB
                chromadb_dir = self.data_dir / "chromadb"
                chromadb_dir.mkdir(parents=True, exist_ok=True)

                from chromadb.config import Settings

                self._client = _chromadb.PersistentClient(
                    path=str(chromadb_dir),
                    settings=Settings(anonymized_telemetry=False),
                )

                # 获取或创建 collection
                self._collection = self._client.get_or_create_collection(
                    name="memories",
                    metadata={"hnsw:space": "cosine"},
                )

                self._enabled = True
                logger.info(f"VectorStore initialized with {self._collection.count()} memories")
                return True

            except Exception as e:
                logger.error(f"Failed to initialize VectorStore: {e}")
                self._enabled = False
                return False

    @property
    def enabled(self) -> bool:
        """是否可用"""
        return self._ensure_initialized()

    def add_memory(
        self,
        memory_id: str,
        content: str,
        memory_type: str,
        priority: str,
        importance: float,
        tags: list[str] = None,
    ) -> bool:
        """
        添加记忆到向量库

        Args:
            memory_id: 记忆 ID
            content: 记忆内容
            memory_type: 记忆类型 (fact/preference/skill/error/rule/context)
            priority: 优先级 (transient/short_term/long_term/permanent)
            importance: 重要性评分 (0-1)
            tags: 标签列表

        Returns:
            是否成功
        """
        if not self._ensure_initialized():
            return False

        try:
            with self._lock:
                # 计算 embedding
                embedding = self._model.encode(content).tolist()

                # 存储到 ChromaDB
                self._collection.add(
                    ids=[memory_id],
                    embeddings=[embedding],
                    documents=[content],
                    metadatas=[
                        {
                            "type": memory_type,
                            "priority": priority,
                            "importance": importance,
                            "tags": ",".join(tags) if tags else "",
                        }
                    ],
                )

            logger.debug(f"Added memory to vector store: {memory_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to add memory to vector store: {e}")
            return False

    def search(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
        min_importance: float = 0.0,
    ) -> list[tuple[str, float]]:
        """
        语义搜索

        Args:
            query: 搜索查询
            limit: 返回数量
            filter_type: 过滤类型 (可选)
            min_importance: 最小重要性 (可选)

        Returns:
            [(memory_id, distance), ...] 距离越小越相似
        """
        if not self._ensure_initialized():
            return []

        try:
            with self._lock:
                # 计算查询 embedding
                query_embedding = self._model.encode(query).tolist()

                # 构建过滤条件
                where = None
                if filter_type:
                    where = {"type": filter_type}

                # 搜索
                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=limit,
                    where=where,
                )

            if not results["ids"] or not results["ids"][0]:
                return []

            # 返回 (id, distance) 列表
            ids = results["ids"][0]
            distances = results["distances"][0] if results.get("distances") else [0] * len(ids)

            # 过滤低重要性
            if min_importance > 0 and results.get("metadatas"):
                filtered = []
                for i, (mid, dist) in enumerate(zip(ids, distances, strict=False)):
                    meta = results["metadatas"][0][i]
                    if meta.get("importance", 0) >= min_importance:
                        filtered.append((mid, dist))
                return filtered

            return list(zip(ids, distances, strict=False))

        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

    async def async_search(
        self,
        query: str,
        limit: int = 10,
        filter_type: str | None = None,
        min_importance: float = 0.0,
    ) -> list[tuple[str, float]]:
        """
        异步语义搜索（将 CPU 密集的 encode 操作放到线程池，避免阻塞事件循环）

        参数和返回值与 search() 完全相同。
        """
        return await asyncio.to_thread(
            self.search, query, limit, filter_type, min_importance
        )

    def delete_memory(self, memory_id: str) -> bool:
        """
        删除记忆

        Args:
            memory_id: 记忆 ID

        Returns:
            是否成功
        """
        if not self._ensure_initialized():
            return False

        try:
            with self._lock:
                self._collection.delete(ids=[memory_id])
            logger.debug(f"Deleted memory from vector store: {memory_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete memory: {e}")
            return False

    def update_memory(
        self,
        memory_id: str,
        content: str,
        memory_type: str,
        priority: str,
        importance: float,
        tags: list[str] = None,
    ) -> bool:
        """
        更新记忆

        Args:
            memory_id: 记忆 ID
            content: 新内容
            memory_type: 记忆类型
            priority: 优先级
            importance: 重要性
            tags: 标签

        Returns:
            是否成功
        """
        if not self._ensure_initialized():
            return False

        try:
            with self._lock:
                # 计算新 embedding
                embedding = self._model.encode(content).tolist()

                # 更新
                self._collection.update(
                    ids=[memory_id],
                    embeddings=[embedding],
                    documents=[content],
                    metadatas=[
                        {
                            "type": memory_type,
                            "priority": priority,
                            "importance": importance,
                            "tags": ",".join(tags) if tags else "",
                        }
                    ],
                )

            logger.debug(f"Updated memory in vector store: {memory_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update memory: {e}")
            return False

    def get_stats(self) -> dict:
        """获取统计信息"""
        if not self._ensure_initialized():
            return {"enabled": False, "count": 0}

        with self._lock:
            return {
                "enabled": True,
                "count": self._collection.count(),
                "model": self.model_name,
                "device": self.device,
            }

    def clear(self) -> bool:
        """清空所有记忆"""
        if not self._ensure_initialized():
            return False

        try:
            with self._lock:
                # 删除并重新创建 collection
                self._client.delete_collection("memories")
                self._collection = self._client.get_or_create_collection(
                    name="memories",
                    metadata={"hnsw:space": "cosine"},
                )
            logger.info("Cleared all memories from vector store")
            return True
        except Exception as e:
            logger.error(f"Failed to clear vector store: {e}")
            return False

    def batch_add(
        self,
        memories: list[dict],
    ) -> int:
        """
        批量添加记忆

        Args:
            memories: [{"id": ..., "content": ..., "type": ..., "priority": ..., "importance": ..., "tags": ...}, ...]

        Returns:
            成功添加的数量
        """
        if not self._ensure_initialized():
            return 0

        if not memories:
            return 0

        try:
            with self._lock:
                # 批量计算 embedding
                contents = [m["content"] for m in memories]
                embeddings = self._model.encode(contents).tolist()

                # 准备数据
                ids = [m["id"] for m in memories]
                metadatas = [
                    {
                        "type": m.get("type", "fact"),
                        "priority": m.get("priority", "short_term"),
                        "importance": m.get("importance", 0.5),
                        "tags": ",".join(m.get("tags", [])),
                    }
                    for m in memories
                ]

                # 批量添加
                self._collection.add(
                    ids=ids,
                    embeddings=embeddings,
                    documents=contents,
                    metadatas=metadatas,
                )

            logger.info(f"Batch added {len(memories)} memories to vector store")
            return len(memories)

        except Exception as e:
            logger.error(f"Batch add failed: {e}")
            return 0
