"""
Prompt Retriever (v2) — 从记忆系统检索相关片段

v2 改动:
- 委托给 RetrievalEngine.retrieve() (多路召回 + 重排序)
- 保留 _get_core_memory() 和简单版本向后兼容
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory import MemoryManager

logger = logging.getLogger(__name__)


def retrieve_memory(
    query: str,
    memory_manager: "MemoryManager",
    max_tokens: int = 400,
    max_items: int = 5,
    min_importance: float = 0.5,
) -> str:
    """
    从记忆系统检索与查询相关的片段

    v2: 优先使用 RetrievalEngine, 回退到旧路径。
    """
    retrieval_engine = getattr(memory_manager, "retrieval_engine", None)
    if retrieval_engine and query and query.strip():
        try:
            recent = getattr(memory_manager, "_recent_messages", None)
            return retrieval_engine.retrieve(
                query=query,
                recent_messages=recent,
                max_tokens=max_tokens,
            )
        except Exception as e:
            logger.warning(f"[Retriever] RetrievalEngine failed, using legacy path: {e}")

    lines: list[str] = []

    core_memory = _get_core_memory(memory_manager, max_chars=max_tokens * 2)
    if core_memory:
        lines.append("## 核心记忆\n")
        lines.append(core_memory)

    if query and query.strip():
        related, used_vector = _search_related_memories(
            query=query,
            memory_manager=memory_manager,
            max_items=max_items,
            min_importance=min_importance,
        )
        if related:
            search_type = "语义匹配" if used_vector else "关键词匹配"
            lines.append(f"\n## 相关记忆（{search_type}）\n")
            lines.append(related)

    result = "\n".join(lines)
    max_chars = max_tokens * 4

    if len(result) > max_chars:
        result = result[:max_chars]
        last_newline = result.rfind("\n")
        if last_newline > max_chars * 0.8:
            result = result[:last_newline]
        result += "\n...(记忆已截断)"

    return result


def _get_core_memory(memory_manager: "MemoryManager", max_chars: int = 800) -> str:
    memory_path = getattr(memory_manager, "memory_md_path", None)
    if not memory_path or not memory_path.exists():
        return ""
    try:
        content = memory_path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        if len(content) > max_chars:
            lines = content.split("\n")
            result_lines: list[str] = []
            current_len = 0
            for line in reversed(lines):
                if current_len + len(line) + 1 > max_chars:
                    break
                result_lines.insert(0, line)
                current_len += len(line) + 1
            return "\n".join(result_lines)
        return content
    except Exception as e:
        logger.warning(f"Failed to read MEMORY.md: {e}")
        return ""


def _search_related_memories(
    query: str,
    memory_manager: "MemoryManager",
    max_items: int = 5,
    min_importance: float = 0.5,
) -> tuple[str, bool]:
    """Legacy: search via VectorStore or keyword, used as fallback."""
    vector_store = getattr(memory_manager, "vector_store", None)

    if vector_store and getattr(vector_store, "enabled", False):
        try:
            results = vector_store.search(
                query=query,
                limit=max_items,
                min_importance=min_importance,
            )
            if results:
                memories = getattr(memory_manager, "_memories", {})
                lines = []
                for memory_id, _distance in results:
                    memory = memories.get(memory_id)
                    if memory:
                        content = getattr(memory, "content", str(memory))
                        lines.append(f"- {content}")
                if lines:
                    return "\n".join(lines), True
        except Exception as e:
            logger.warning(f"Vector memory search failed: {e}")

    keyword_search = getattr(memory_manager, "_keyword_search", None)
    if keyword_search:
        try:
            results = keyword_search(query, max_items)
            if results:
                lines = [f"- {getattr(m, 'content', str(m))}" for m in results]
                return "\n".join(lines), False
        except Exception as e:
            logger.warning(f"Keyword memory search failed: {e}")

    return "", False


async def async_search_related_memories(
    query: str,
    memory_manager: "MemoryManager",
    max_items: int = 5,
    min_importance: float = 0.5,
) -> tuple[str, bool]:
    """Async version (backward compatible)."""
    retrieval_engine = getattr(memory_manager, "retrieval_engine", None)
    if retrieval_engine and query:
        try:
            recent = getattr(memory_manager, "_recent_messages", None)
            result = retrieval_engine.retrieve(
                query=query, recent_messages=recent, max_tokens=400,
            )
            if result:
                return result, True
        except Exception as e:
            logger.warning(f"Async RetrievalEngine failed: {e}")

    return _search_related_memories(query, memory_manager, max_items, min_importance)


def retrieve_memory_simple(
    memory_md_path: Path,
    max_chars: int = 800,
) -> str:
    """直接读取 MEMORY.md (不使用向量搜索)"""
    if not memory_md_path.exists():
        return ""
    try:
        content = memory_md_path.read_text(encoding="utf-8").strip()
        if len(content) > max_chars:
            lines = content.split("\n")
            result_lines: list[str] = []
            current_len = 0
            for line in reversed(lines):
                if current_len + len(line) + 1 > max_chars:
                    break
                result_lines.insert(0, line)
                current_len += len(line) + 1
            return "\n".join(result_lines)
        return content
    except Exception as e:
        logger.warning(f"Failed to read {memory_md_path}: {e}")
        return ""
