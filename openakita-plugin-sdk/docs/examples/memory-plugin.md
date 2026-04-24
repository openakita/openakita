# 记忆后端插件示例 / Memory Backend Plugin Example

用外部向量数据库替换内置记忆系统。这是最高权限级别的插件类型。

Replaces the built-in memory system with an external vector database. This is the highest permission level plugin type.

**权限级别 / Permission Level:** System（需要手动确认 / requires manual approval）

---

## 目录结构 / Directory Structure

```
qdrant-memory/
  plugin.json
  plugin.py
  config_schema.json
  README.md
```

## plugin.json

```json
{
  "id": "qdrant-memory",
  "name": "Qdrant Memory Backend",
  "version": "1.0.0",
  "description": "使用 Qdrant 向量数据库替换内置记忆系统 / Replace built-in memory with Qdrant vector DB",
  "author": "Community",
  "license": "MIT",
  "type": "python",
  "entry": "plugin.py",
  "permissions": ["memory.replace", "config.read", "config.write"],
  "requires": {
    "openakita": ">=1.5.0",
    "pip": ["qdrant-client>=1.7.0"]
  },
  "provides": { "memory_backend": "qdrant" },
  "replaces": ["builtin-memory"],
  "category": "memory",
  "tags": ["qdrant", "vector-db", "memory"]
}
```

## config_schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "title": "Qdrant Memory Settings",
  "properties": {
    "qdrant_url": {
      "type": "string",
      "description": "Qdrant 服务地址 / Qdrant server URL",
      "default": "http://localhost:6333"
    },
    "collection_name": {
      "type": "string",
      "description": "集合名称 / Collection name",
      "default": "openakita_memory"
    },
    "embedding_model": {
      "type": "string",
      "description": "嵌入模型 / Embedding model for vectorization",
      "default": "text-embedding-3-small"
    }
  },
  "required": ["qdrant_url"]
}
```

## plugin.py

```python
from __future__ import annotations

import uuid
from typing import Any

from openakita_plugin_sdk import PluginBase, PluginAPI


class QdrantMemoryBackend:
    """实现 MemoryBackendProtocol，使用 Qdrant 作为向量存储。
    Implements MemoryBackendProtocol using Qdrant as vector storage.

    生产环境中需要:
    In production, requires:
        pip install qdrant-client
    """

    def __init__(self, url: str, collection: str):
        self.url = url
        self.collection = collection
        # 实际使用时取消注释 / Uncomment for real usage:
        # from qdrant_client import QdrantClient
        # self.client = QdrantClient(url=url)
        self._store: dict[str, dict] = {}  # 演示用内存存储 / In-memory for demo

    async def store(self, memory: dict) -> str:
        memory_id = str(uuid.uuid4())
        self._store[memory_id] = memory
        return memory_id

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        # 实际实现应使用向量搜索 / Real impl should use vector search
        results = []
        query_lower = query.lower()
        for mid, mem in self._store.items():
            content = mem.get("content", "")
            if query_lower in content.lower():
                results.append({"id": mid, "content": content, "score": 0.8})
                if len(results) >= limit:
                    break
        return results

    async def delete(self, memory_id: str) -> bool:
        return self._store.pop(memory_id, None) is not None

    async def get_injection_context(self, query: str, max_tokens: int) -> str:
        results = await self.search(query, limit=5)
        context = "\n".join(r.get("content", "") for r in results)
        return context[:max_tokens]

    async def start_session(self, session_id: str) -> None:
        pass

    async def end_session(self) -> None:
        pass

    async def record_turn(self, role: str, content: str) -> None:
        await self.store({
            "role": role,
            "content": content,
            "type": "conversation_turn",
        })


class Plugin(PluginBase):
    def __init__(self):
        self.backend: QdrantMemoryBackend | None = None

    def on_load(self, api: PluginAPI) -> None:
        config = api.get_config()
        url = config.get("qdrant_url", "http://localhost:6333")
        collection = config.get("collection_name", "openakita_memory")

        self.backend = QdrantMemoryBackend(url, collection)
        api.register_memory_backend(self.backend)
        api.log(f"Qdrant memory backend registered: {url}/{collection}")

    def on_unload(self) -> None:
        self.backend = None
```

## 测试 / Test

```python
import pytest
from openakita_plugin_sdk.testing import MockPluginAPI
from plugin import Plugin

def test_plugin_loads():
    api = MockPluginAPI()
    api.config = {"qdrant_url": "http://localhost:6333"}
    plugin = Plugin()
    plugin.on_load(api)

    assert len(api.registered_memory_backends) == 1
    assert not any(level == "error" for level, _ in api.logs)

@pytest.mark.asyncio
async def test_store_and_search():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)

    backend = plugin.backend
    mid = await backend.store({"content": "Python is great", "type": "note"})
    assert mid

    results = await backend.search("python")
    assert len(results) >= 1
    assert "Python" in results[0]["content"]

@pytest.mark.asyncio
async def test_delete():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)

    mid = await plugin.backend.store({"content": "temp"})
    assert await plugin.backend.delete(mid) is True
    assert await plugin.backend.delete("nonexistent") is False

def test_unload():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)
    plugin.on_unload()
    assert plugin.backend is None
```

---

## 关键要点 / Key Points

- 实现 `MemoryBackendProtocol` 的全部 7 个方法 / Implement all 7 methods of `MemoryBackendProtocol`
- `memory.replace` 是 System 级权限，用户必须手动确认 / `memory.replace` is System-level, requires manual approval
- 如果插件加载失败，内置记忆系统继续正常工作 / If plugin fails to load, built-in memory continues working
- 使用 `config_schema.json` 让用户通过 UI 配置连接参数 / Use `config_schema.json` for UI-configurable settings
- 建议在 `requires.pip` 中声明依赖 / Declare dependencies in `requires.pip`
- 测试时可使用内存存储替代真实数据库 / Use in-memory storage for testing without real database

