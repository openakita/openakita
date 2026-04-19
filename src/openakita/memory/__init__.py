"""
OpenAkita Memory System (v2)

Architecture:
- UnifiedStore: SQLite (primary storage + FTS5) + SearchBackend (pluggable search)
- RetrievalEngine: multi-path recall + reranking
- LifecycleManager: consolidation + decay + deduplication
- MemoryExtractor: AI extraction (v2: tool-aware / entity-attribute)

Memory types:
- SemanticMemory: semantic memory (entity-attribute structure)
- Episode: episodic memory (full interaction stories)
- Scratchpad: working-memory scratchpad (persisted across sessions)
"""

from .consolidator import MemoryConsolidator
from .extractor import MemoryExtractor
from .manager import MemoryManager
from .retrieval import RetrievalEngine
from .search_backends import (
    APIEmbeddingBackend,
    ChromaDBBackend,
    FTS5Backend,
    SearchBackend,
    create_search_backend,
)
from .types import (
    ActionNode,
    Attachment,
    AttachmentDirection,
    ConversationTurn,
    Episode,
    Memory,
    MemoryPriority,
    MemoryType,
    Scratchpad,
    SemanticMemory,
    SessionSummary,
)
from .unified_store import UnifiedStore

__all__ = [
    "MemoryManager",
    "MemoryExtractor",
    "MemoryConsolidator",
    "UnifiedStore",
    "RetrievalEngine",
    # Search backends
    "SearchBackend",
    "FTS5Backend",
    "ChromaDBBackend",
    "APIEmbeddingBackend",
    "create_search_backend",
    # Types
    "Memory",
    "SemanticMemory",
    "MemoryType",
    "MemoryPriority",
    "ConversationTurn",
    "SessionSummary",
    "Episode",
    "ActionNode",
    "Scratchpad",
    "Attachment",
    "AttachmentDirection",
]
