# RAG 知识库插件示例 / RAG Knowledge Base Plugin Example

向检索管道添加外部知识源（如 Obsidian、Notion、本地文件等），让 AI 能在回复中引用这些知识。

Adds an external knowledge source (e.g., Obsidian, Notion, local files) to the retrieval pipeline, enabling the AI to reference this knowledge in responses.

**权限级别 / Permission Level:** Advanced（需要用户确认 / requires user approval）

---

## 目录结构 / Directory Structure

```
obsidian-kb/
  plugin.json
  plugin.py
  config_schema.json
  README.md
```

## plugin.json

```json
{
  "id": "obsidian-kb",
  "name": "Obsidian Knowledge Base",
  "version": "1.0.0",
  "description": "从 Obsidian 知识库检索内容用于 RAG / RAG retrieval from Obsidian vault",
  "author": "Community",
  "license": "MIT",
  "type": "python",
  "entry": "plugin.py",
  "permissions": [
    "tools.register",
    "hooks.retrieve",
    "retrieval.register",
    "config.read",
    "config.write"
  ],
  "provides": {
    "tools": ["search_obsidian"],
    "retrieval_sources": ["obsidian"],
    "hooks": ["on_retrieve"],
    "config_schema": "config_schema.json"
  },
  "category": "productivity",
  "tags": ["obsidian", "knowledge-base", "rag", "markdown"]
}
```

## config_schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "title": "Obsidian KB Settings",
  "properties": {
    "vault_path": {
      "type": "string",
      "description": "Obsidian 知识库路径 / Path to Obsidian vault"
    },
    "max_results": {
      "type": "integer",
      "description": "最大返回结果数 / Maximum search results",
      "default": 10
    },
    "file_extensions": {
      "type": "array",
      "description": "搜索的文件扩展名 / File extensions to search",
      "default": [".md"]
    }
  },
  "required": ["vault_path"]
}
```

## plugin.py

```python
from __future__ import annotations

from pathlib import Path

from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.tools import tool_definition

TOOLS = [
    tool_definition(
        name="search_obsidian",
        description="搜索用户的 Obsidian 知识库 / Search the user's Obsidian knowledge base",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词 / Search query"},
                "limit": {"type": "integer", "description": "最大结果数 / Max results", "default": 5},
            },
            "required": ["query"],
        },
    ),
]


class ObsidianRetriever:
    """实现 RetrievalSource 协议，从 Obsidian vault 检索笔记。
    Implements RetrievalSource protocol, retrieves notes from Obsidian vault.
    """

    source_name = "obsidian"

    def __init__(self, vault_path: str, extensions: list[str] | None = None):
        self.vault_path = Path(vault_path)
        self.extensions = extensions or [".md"]

    async def retrieve(self, query: str, limit: int = 5) -> list[dict]:
        results = []
        query_lower = query.lower()

        for ext in self.extensions:
            for file_path in self.vault_path.rglob(f"*{ext}"):
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue

                if query_lower in text.lower():
                    results.append({
                        "content": text[:500],
                        "score": 0.7,
                        "source": str(file_path.relative_to(self.vault_path)),
                        "id": str(file_path),
                    })
                    if len(results) >= limit:
                        return results

        return results


class Plugin(PluginBase):
    def __init__(self):
        self.api: PluginAPI | None = None
        self.retriever: ObsidianRetriever | None = None

    def on_load(self, api: PluginAPI) -> None:
        self.api = api
        config = api.get_config()
        vault_path = config.get("vault_path", "")

        if vault_path and Path(vault_path).is_dir():
            extensions = config.get("file_extensions", [".md"])
            self.retriever = ObsidianRetriever(vault_path, extensions)
            api.register_retrieval_source(self.retriever)
            api.register_hook("on_retrieve", self._on_retrieve)
            api.log(f"Obsidian vault loaded: {vault_path}")
        else:
            api.log("vault_path not configured or not found, tool-only mode", "warning")

        api.register_tools(TOOLS, self._handle_tool)
        api.log("Obsidian KB plugin loaded")

    async def _on_retrieve(self, **kwargs) -> None:
        """钩子：检索后记录日志 / Hook: log after retrieval."""
        query = kwargs.get("query", "")
        candidates = kwargs.get("candidates", [])
        self.api.log(f"on_retrieve: query='{query[:50]}', candidates={len(candidates)}")

    async def _handle_tool(self, tool_name: str, arguments: dict) -> str:
        if tool_name != "search_obsidian":
            return f"Unknown tool: {tool_name}"

        if self.retriever is None:
            return "Obsidian vault not configured. Set vault_path in plugin config."

        results = await self.retriever.retrieve(
            arguments["query"],
            arguments.get("limit", 5),
        )
        if not results:
            return "没有找到匹配的笔记 / No matching notes found."

        return "\n\n---\n\n".join(
            f"**{r.get('source', 'unknown')}**\n{r['content']}"
            for r in results
        )

    def on_unload(self) -> None:
        self.retriever = None
```

## 测试 / Test

```python
import tempfile
from pathlib import Path
from openakita_plugin_sdk.testing import MockPluginAPI
from plugin import Plugin

def test_plugin_with_vault():
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试笔记 / Create test notes
        (Path(tmpdir) / "note1.md").write_text("# Python\nPython is a programming language.")
        (Path(tmpdir) / "note2.md").write_text("# Rust\nRust is fast and safe.")

        api = MockPluginAPI()
        api.config = {"vault_path": tmpdir}
        plugin = Plugin()
        plugin.on_load(api)

        assert "search_obsidian" in api.registered_tools
        assert len(api.registered_retrieval_sources) == 1
        assert "on_retrieve" in api.registered_hooks

def test_plugin_without_vault():
    api = MockPluginAPI()
    plugin = Plugin()
    plugin.on_load(api)

    assert "search_obsidian" in api.registered_tools
    assert len(api.registered_retrieval_sources) == 0
```

---

## 关键要点 / Key Points

- 实现 `RetrievalSource` 协议（需要 `source_name` 属性和 `retrieve()` 方法）/ Implement `RetrievalSource` protocol
- 注册检索源后，结果自动与内置检索合并排序 / After registration, results are auto-merged with built-in retrieval
- `on_retrieve` 钩子可以观察和修改检索结果 / `on_retrieve` hook can observe and modify candidates
- 配合 `config_schema.json` 提供用户友好的配置界面 / Use `config_schema.json` for user-friendly settings
- 优雅降级：vault 路径未配置时仍提供工具（返回提示信息）/ Graceful degradation when vault path is not set

