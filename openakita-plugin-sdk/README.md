# OpenAkita Plugin SDK

Build plugins for [OpenAkita](https://github.com/openakita/openakita) without installing the full runtime.

## Install

```bash
pip install openakita-plugin-sdk
```

For development from source:

```bash
pip install -e ./openakita-plugin-sdk
```

## 30-Second Quick Start

### Option A: Scaffold a plugin (recommended)

```bash
python -m openakita_plugin_sdk.scaffold --id my-tool --type tool --dir ./plugins
```

This creates a complete plugin directory with `plugin.json`, `plugin.py`, and `README.md`.

Available types: `tool`, `channel`, `rag`, `memory`, `llm`, `hook`, `skill`, `mcp`.

### Option B: Use decorators

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.decorators import tool, hook, auto_register

@tool(name="greet", description="Greet someone by name")
async def greet(tool_name: str, arguments: dict) -> str:
    return f"Hello, {arguments['name']}!"

@hook("on_message_received")
async def log_msg(**kwargs):
    print(f"Got: {kwargs.get('text', '')[:50]}")

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        auto_register(api)
```

### Option C: Manual registration

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.tools import tool_definition

TOOLS = [
    tool_definition(
        name="hello",
        description="Say hello",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
]

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        async def handler(tool_name: str, arguments: dict) -> str:
            return f"Hello, {arguments['name']}!"

        api.register_tools(TOOLS, handler)
```

## Plugin Types at a Glance

| Type | What it does | Key API |
|------|-------------|---------|
| **Tool** | Add tools the AI can call | `api.register_tools()` |
| **Channel** | Add IM channels (WhatsApp, Matrix...) | `api.register_channel()` |
| **RAG** | Add knowledge sources (Obsidian, Notion...) | `api.register_retrieval_source()` |
| **Memory** | Replace the built-in memory system | `api.register_memory_backend()` |
| **LLM** | Add LLM providers (Ollama, custom API...) | `api.register_llm_provider()` |
| **Hook** | React to lifecycle events | `api.register_hook()` |
| **Skill** | Inject prompt guidance (SKILL.md) | Declarative (no code) |
| **MCP** | Wrap an MCP server as a managed plugin | JSON config only |
| **Full-Stack UI** | Plugin with dedicated frontend page (2.0) | `api.register_api_routes()` + Bridge SDK |

## Testing

```python
from openakita_plugin_sdk.testing import MockPluginAPI, assert_plugin_loads

def test_my_plugin():
    plugin = Plugin()
    api = assert_plugin_loads(plugin)
    assert "greet" in api.registered_tools
```

## Documentation

| Doc | What it covers |
|-----|---------------|
| [Getting Started](docs/getting-started.md) | Full walkthrough from zero to running plugin |
| [API Reference](docs/api-reference.md) | All `PluginAPI` methods and signatures |
| [Permissions](docs/permissions.md) | Three-tier permission model |
| [Hooks](docs/hooks.md) | All 14 lifecycle hooks with callback signatures |
| [Protocols](docs/protocols.md) | Memory, Retrieval, Search interfaces |
| [plugin.json](docs/plugin-json.md) | Manifest schema reference |
| [Testing](docs/testing.md) | MockPluginAPI and test patterns |
| [Cross-Ecosystem](docs/cross-ecosystem.md) | Compatibility with Claude/Cursor/Codex |

## SDK Modules

```python
from openakita_plugin_sdk import PluginBase, PluginAPI, tool_definition
from openakita_plugin_sdk.decorators import tool, hook, auto_register
from openakita_plugin_sdk.scaffold import scaffold_plugin
from openakita_plugin_sdk.testing import MockPluginAPI, assert_plugin_loads
from openakita_plugin_sdk.hooks import HOOK_NAMES, HOOK_SIGNATURES
from openakita_plugin_sdk.channel import ChannelAdapter, ChannelPluginMixin
from openakita_plugin_sdk.llm import LLMProvider, ProviderRegistry
from openakita_plugin_sdk.protocols import MemoryBackendProtocol, RetrievalSource, SearchBackend
from openakita_plugin_sdk.config import config_schema, config_property
from openakita_plugin_sdk.types import UnifiedMessage, OutgoingMessage, ToolCall
```
