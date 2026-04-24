# 协议参考 / Protocol Reference

OpenAkita 定义了三个核心协议（Protocol），插件通过实现这些协议来扩展系统的数据管道。所有协议使用 `@runtime_checkable`，支持 `isinstance()` 检查。

OpenAkita defines three core protocols that plugins can implement to extend the system's data pipeline. All protocols are `@runtime_checkable` and support `isinstance()` checking.

```python
from openakita_plugin_sdk.protocols import (
    MemoryBackendProtocol,
    RetrievalSource,
    SearchBackend,
)
```

---

## MemoryBackendProtocol

替换或增强内置记忆系统。完全替换需要 `memory.replace` 权限（System 级）。

Replaces or augments the built-in memory system. Full replacement requires `memory.replace` permission (System tier).

### 接口定义 / Interface

```python
class MemoryBackendProtocol(Protocol):
    async def store(self, memory: dict) -> str:
        """存储一条记忆，返回 ID / Store a memory entry, return its ID."""

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """语义搜索记忆 / Semantic search over stored memories."""

    async def delete(self, memory_id: str) -> bool:
        """按 ID 删除记忆 / Delete a memory by ID."""

    async def get_injection_context(self, query: str, max_tokens: int) -> str:
        """构建用于 Prompt 注入的上下文字符串 / Build context string for prompt injection."""

    async def start_session(self, session_id: str) -> None:
        """会话开始时调用 / Called when a conversation session begins."""

    async def end_session(self) -> None:
        """会话结束时调用 / Called when a conversation session ends."""

    async def record_turn(self, role: str, content: str) -> None:
        """记录一轮对话 / Record a single conversation turn."""
```

### 实现示例 / Implementation Example

```python
class QdrantMemoryBackend:
    def __init__(self, url: str, collection: str):
        from qdrant_client import QdrantClient
        self.client = QdrantClient(url=url)
        self.collection = collection

    async def store(self, memory: dict) -> str:
        # 将记忆存入 Qdrant / Store memory in Qdrant
        point_id = str(uuid.uuid4())
        self.client.upsert(self.collection, points=[...])
        return point_id

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        # 向量搜索 / Vector search
        results = self.client.search(self.collection, query_vector=..., limit=limit)
        return [{"content": r.payload["content"], "id": r.id} for r in results]

    async def delete(self, memory_id: str) -> bool:
        self.client.delete(self.collection, points_selector=[memory_id])
        return True

    async def get_injection_context(self, query: str, max_tokens: int) -> str:
        results = await self.search(query, limit=5)
        return "\n".join(r["content"] for r in results)[:max_tokens]

    async def start_session(self, session_id: str) -> None:
        pass

    async def end_session(self) -> None:
        pass

    async def record_turn(self, role: str, content: str) -> None:
        await self.store({"role": role, "content": content, "type": "turn"})
```

### 注册 / Registration

```python
api.register_memory_backend(QdrantMemoryBackend(url="http://localhost:6333", collection="memory"))
```

---

## RetrievalSource

向多路检索管道（RAG）添加外部知识源。检索结果与内置源的结果合并后统一排序。

Adds an external knowledge source to the multi-way retrieval pipeline (RAG). Results are merged with built-in sources and ranked together.

### 接口定义 / Interface

```python
class RetrievalSource(Protocol):
    source_name: str  # 数据源名称 / Source identifier

    async def retrieve(self, query: str, limit: int = 5) -> list[dict]:
        """检索匹配的文档 / Return matching documents.

        每个 dict 应包含 / Each dict should contain:
        - "content": str — 文本内容 / text content
        - "score" / "relevance": float — 0.0~1.0 相关性分数 / relevance score
        - (可选 / optional) "id", "source" 等元数据 / metadata
        """
```

### 实现示例 / Implementation Example

```python
class ObsidianRetriever:
    source_name = "obsidian"

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)

    async def retrieve(self, query: str, limit: int = 5) -> list[dict]:
        results = []
        query_lower = query.lower()
        for md_file in self.vault_path.rglob("*.md"):
            text = md_file.read_text(encoding="utf-8", errors="ignore")
            if query_lower in text.lower():
                results.append({
                    "content": text[:500],
                    "score": 0.7,
                    "source": str(md_file),
                })
                if len(results) >= limit:
                    break
        return results
```

### 注册 / Registration

```python
api.register_retrieval_source(ObsidianRetriever("/path/to/vault"))
```

---

## SearchBackend

为记忆子系统提供向量/全文搜索后端。在设置界面中作为搜索引擎选项出现。

Provides a vector/full-text search backend for the memory subsystem. Appears as a search engine option in settings.

### 接口定义 / Interface

```python
class SearchBackend(Protocol):
    @property
    def available(self) -> bool:
        """后端是否可用 / Whether the backend is operational."""

    @property
    def backend_type(self) -> str:
        """后端类型标识 / Backend type identifier (e.g., 'pinecone')."""

    def search(
        self, query: str, limit: int = 10, filter_type: str | None = None
    ) -> list[tuple[str, float]]:
        """搜索，返回 (memory_id, score) 列表 / Search, return (memory_id, score) pairs."""

    def add(
        self, memory_id: str, content: str, metadata: dict | None = None
    ) -> bool:
        """索引一条记忆 / Index a memory entry."""

    def delete(self, memory_id: str) -> bool:
        """从索引中删除 / Remove from index."""

    def batch_add(self, items: list[dict]) -> int:
        """批量索引，返回成功数量 / Bulk index, return count of successfully added items."""
```

### 注册 / Registration

```python
api.register_search_backend("pinecone", PineconeBackend())
```

---

## 运行时类型检查 / Runtime Type Checking

所有协议都支持 `isinstance()` 检查：

All protocols support `isinstance()` checking:

```python
from openakita_plugin_sdk.protocols import MemoryBackendProtocol

assert isinstance(my_backend, MemoryBackendProtocol)
```

---

## 相关文档 / Related

- [api-reference.md](api-reference.md) — `register_memory_backend()`, `register_retrieval_source()`, `register_search_backend()` 方法说明
- [permissions.md](permissions.md) — `memory.replace`, `retrieval.register`, `search.register` 权限
- [examples/memory-plugin.md](examples/memory-plugin.md) — 完整的记忆后端插件示例
- [examples/rag-plugin.md](examples/rag-plugin.md) — 完整的 RAG 插件示例

