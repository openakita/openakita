"""
Vector store - based on ChromaDB

Provides semantic search capabilities:
- Vectorized memory storage
- Semantic similarity search
- Filtering by type
- Multi-source download support (HuggingFace / hf-mirror / ModelScope)
"""

import asyncio
import logging
import threading
from pathlib import Path

from .types import normalize_tags

logger = logging.getLogger(__name__)

# Lazy imports to avoid errors when dependencies are not installed
_sentence_transformers_available = None
_chromadb = None


def _lazy_import():
    """Lazily import dependencies"""
    global _sentence_transformers_available, _chromadb

    if _sentence_transformers_available is None:
        # The module may be installed while the service is running, with the path
        # not yet injected into sys.path. Try refreshing once before importing
        # (idempotent; does not re-add existing paths).
        import sys

        if "sentence_transformers" not in sys.modules:
            try:
                from openakita.runtime_env import inject_module_paths_runtime

                inject_module_paths_runtime()
            except Exception:
                pass

        try:
            import sentence_transformers  # noqa: F401

            _sentence_transformers_available = True
        except ImportError as e:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("sentence_transformers")
            logger.info(f"[VectorStore] Vector search not enabled: {hint}")
            logger.debug(f"sentence_transformers ImportError details: {e}", exc_info=True)
            _sentence_transformers_available = False
            return False

    if not _sentence_transformers_available:
        return False

    if _chromadb is None:
        try:
            # Disable chromadb telemetry before import to avoid ImportError from missing posthog.
            # chromadb checks these environment variables at import time.
            import os

            os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
            os.environ.setdefault("CHROMA_TELEMETRY", "False")

            import chromadb

            _chromadb = chromadb
        except ImportError as e:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("chromadb")
            logger.info(f"[VectorStore] ChromaDB not enabled: {hint}")
            logger.debug(f"chromadb ImportError details: {e}", exc_info=True)
            return False

    return True


class VectorStore:
    """
    Vector store - based on ChromaDB

    Uses a local embedding model, no API calls required.
    Supports multiple download sources (HuggingFace / hf-mirror / ModelScope).

    Initialization strategy:
    - Model download runs in a background thread and never blocks backend startup
    - Until download completes, all operations gracefully degrade (return empty results)
    - Cooldown-based retry mechanism after download failures
    """

    # Default to a Chinese-optimized embedding model
    DEFAULT_MODEL = "shibing624/text2vec-base-chinese"

    def __init__(
        self,
        data_dir: Path,
        model_name: str | None = None,
        device: str = "cpu",
        download_source: str = "auto",
    ):
        """
        Initialize vector store

        Args:
            data_dir: Data directory
            model_name: Embedding model name (default shibing624/text2vec-base-chinese)
            device: Device (cpu or cuda)
            download_source: Download source ("auto" | "huggingface" | "hf-mirror" | "modelscope")
        """
        self.data_dir = Path(data_dir)
        self.model_name = model_name or self.DEFAULT_MODEL
        self.device = device
        self.download_source = download_source

        self._model = None
        self._client = None
        self._collection = None
        self._enabled = False

        # Initialization state machine
        self._init_state = "idle"  # idle → loading → ready / failed
        self._init_failed = False
        self._init_fail_time: float = 0.0
        self._init_retry_cooldown: float = 300.0  # 5-minute cooldown after failure before retrying
        self._retry_count: int = 0
        self._import_missing: bool = False  # Dependency missing (ImportError)
        self._lock = threading.RLock()

        # Start background initialization immediately (does not block caller)
        self._start_background_init()

    def _start_background_init(self) -> None:
        """Start initialization in a background thread; does not block the caller."""
        t = threading.Thread(
            target=self._do_initialize,
            name="VectorStore-init",
            daemon=True,
        )
        t.start()
        logger.info("[VectorStore] Background initialization started (does not block backend startup)")

    def _do_initialize(self) -> None:
        """Actually perform initialization (runs in background thread)."""
        with self._lock:
            if self._init_state == "loading":
                return  # Another thread is already initializing
            self._init_state = "loading"

        try:
            self._do_initialize_inner()
        except Exception:
            pass  # Errors are already handled in inner

    def _do_initialize_inner(self) -> None:
        """Core initialization logic, including model download and ChromaDB init."""
        import time as _time

        # ── Key: configure HF_ENDPOINT BEFORE importing sentence_transformers ──
        # Importing sentence_transformers triggers huggingface_hub import,
        # and huggingface_hub caches HF_ENDPOINT at module level.
        # If not set in advance, the cached value will be https://huggingface.co,
        # and later changes to os.environ will have no effect.
        try:
            from .model_hub import _apply_source_env, _resolve_source

            resolved = _resolve_source(self.download_source)
            if resolved.value == "auto":
                from .model_hub import detect_best_source

                resolved = detect_best_source()
            _apply_source_env(resolved)
            logger.info(f"[VectorStore] Pre-configured HF_ENDPOINT (source={resolved.value})")
        except Exception as e:
            logger.debug(f"[VectorStore] Pre-configuring HF_ENDPOINT failed (non-fatal): {e}")

        if not _lazy_import():
            with self._lock:
                self._enabled = False
                self._init_state = "failed"
                self._init_failed = True
                self._import_missing = True
                self._init_fail_time = _time.monotonic()
                self._retry_count += 1
            return

        try:
            # Initialize embedding model (supports multi-source download)
            from .model_hub import load_embedding_model

            logger.info(
                f"[VectorStore] Loading embedding model: {self.model_name} "
                f"(source={self.download_source})"
            )
            model = load_embedding_model(
                model_name=self.model_name,
                source=self.download_source,
                device=self.device,
            )

            # Initialize ChromaDB
            chromadb_dir = self.data_dir / "chromadb"
            chromadb_dir.mkdir(parents=True, exist_ok=True)

            from chromadb.config import Settings

            client = _chromadb.PersistentClient(
                path=str(chromadb_dir),
                settings=Settings(anonymized_telemetry=False),
            )

            # Get or create collection
            collection = client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )

            # All succeeded; set state atomically
            with self._lock:
                self._model = model
                self._client = client
                self._collection = collection
                self._enabled = True
                self._init_state = "ready"
                self._init_failed = False
                self._import_missing = False
                self._retry_count = 0

            logger.info(f"[VectorStore] ✓ Initialization complete, loaded {collection.count()} memories")

        except Exception as e:
            err_msg = str(e)
            if "posthog" in err_msg:
                logger.warning(
                    f"VectorStore initialization failed (chromadb telemetry dependency missing, does not affect core features): {e}"
                )
            elif "chromadb" in err_msg.lower():
                logger.warning(
                    f"VectorStore initialization failed (chromadb internal module missing, "
                    f"please try reinstalling the vector-memory module): {e}"
                )
            else:
                logger.error(f"[VectorStore] Initialization failed: {e}")

            with self._lock:
                self._enabled = False
                self._init_state = "failed"
                self._init_failed = True
                self._init_fail_time = _time.monotonic()
                self._retry_count += 1

    def _ensure_initialized(self) -> bool:
        """Check whether initialization is ready.

        Design principle: **never block the caller**.
        - Ready → return True
        - Loading → return False (caller degrades gracefully)
        - Failed and cooldown elapsed → trigger background retry, return False
        - Dependency missing (ImportError) → exponential backoff, capped at 1 hour
        """
        global _sentence_transformers_available, _chromadb

        with self._lock:
            if self._init_state == "ready" and self._enabled:
                return True

            if self._init_state == "loading":
                return False

            if self._init_failed:
                import time as _time

                # Exponential backoff on missing dependency: 300s → 600s → 1200s → … capped at 3600s
                if self._import_missing:
                    cooldown = min(
                        self._init_retry_cooldown * (2 ** (self._retry_count - 1)),
                        3600.0,
                    )
                else:
                    cooldown = self._init_retry_cooldown

                elapsed = _time.monotonic() - self._init_fail_time
                if elapsed < cooldown:
                    return False
                logger.info(
                    f"[VectorStore] {elapsed:.0f}s since last init failure "
                    f"(retry #{self._retry_count + 1}), retrying in background..."
                )
                self._init_failed = False
                # Reset global import cache to allow retrying the import
                # (the module may have been installed via setup center during cooldown)
                _sentence_transformers_available = None
                _chromadb = None

        self._start_background_init()
        return False

    @property
    def enabled(self) -> bool:
        """Whether available"""
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
        Add a memory to the vector store

        Args:
            memory_id: Memory ID
            content: Memory content
            memory_type: Memory type (fact/preference/skill/error/rule/context)
            priority: Priority (transient/short_term/long_term/permanent)
            importance: Importance score (0-1)
            tags: Tag list

        Returns:
            Whether successful
        """
        if not self._ensure_initialized():
            return False

        try:
            embedding = self._model.encode(content).tolist()

            with self._lock:
                self._collection.add(
                    ids=[memory_id],
                    embeddings=[embedding],
                    documents=[content],
                    metadatas=[
                        {
                            "type": memory_type,
                            "priority": priority,
                            "importance": importance,
                            "tags": ",".join(normalize_tags(tags)),
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
        Semantic search

        Args:
            query: Search query
            limit: Number of results to return
            filter_type: Filter type (optional)
            min_importance: Minimum importance (optional)

        Returns:
            [(memory_id, distance), ...] smaller distance means more similar
        """
        if not self._ensure_initialized():
            return []

        try:
            query_embedding = self._model.encode(query).tolist()

            with self._lock:
                where = None
                if filter_type:
                    where = {"type": filter_type}

                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=limit,
                    where=where,
                )

            if not results["ids"] or not results["ids"][0]:
                return []

            # Return list of (id, distance)
            ids = results["ids"][0]
            distances = results["distances"][0] if results.get("distances") else [0] * len(ids)

            # Filter out low importance
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
        Async semantic search (offloads the CPU-intensive encode operation to a thread pool to avoid blocking the event loop)

        Parameters and return value are identical to search().
        """
        return await asyncio.to_thread(self.search, query, limit, filter_type, min_importance)

    def delete_memory(self, memory_id: str) -> bool:
        """
        Delete a memory

        Args:
            memory_id: Memory ID

        Returns:
            Whether successful
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
        Update a memory

        Args:
            memory_id: Memory ID
            content: New content
            memory_type: Memory type
            priority: Priority
            importance: Importance
            tags: Tags

        Returns:
            Whether successful
        """
        if not self._ensure_initialized():
            return False

        try:
            embedding = self._model.encode(content).tolist()

            with self._lock:
                self._collection.update(
                    ids=[memory_id],
                    embeddings=[embedding],
                    documents=[content],
                    metadatas=[
                        {
                            "type": memory_type,
                            "priority": priority,
                            "importance": importance,
                            "tags": ",".join(normalize_tags(tags)),
                        }
                    ],
                )

            logger.debug(f"Updated memory in vector store: {memory_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update memory: {e}")
            return False

    def get_stats(self) -> dict:
        """Get statistics"""
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
        """Clear all memories"""
        if not self._ensure_initialized():
            return False

        try:
            with self._lock:
                # Delete and recreate collection
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
        Batch add memories

        Args:
            memories: [{"id": ..., "content": ..., "type": ..., "priority": ..., "importance": ..., "tags": ...}, ...]

        Returns:
            Number of memories successfully added
        """
        if not self._ensure_initialized():
            return 0

        if not memories:
            return 0

        try:
            contents = [m["content"] for m in memories]
            embeddings = self._model.encode(contents).tolist()

            ids = [m["id"] for m in memories]
            metadatas = [
                {
                    "type": m.get("type", "fact"),
                    "priority": m.get("priority", "short_term"),
                    "importance": m.get("importance", 0.5),
                    "tags": ",".join(normalize_tags(m.get("tags"))),
                }
                for m in memories
            ]

            with self._lock:
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
