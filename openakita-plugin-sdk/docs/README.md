# OpenAkita Plugin SDK

Build plugins for [OpenAkita](https://github.com/openakita/openakita) without installing the full runtime.

## Install

```bash
pip install openakita-plugin-sdk
```

For development from source (from the [main repository](https://github.com/openakita/openakita)):

```bash
git clone https://github.com/openakita/openakita.git
pip install -e openakita/openakita-plugin-sdk
```

## 30-Second Quick Start

### Option A: Scaffold a plugin (recommended)

```bash
python -m openakita_plugin_sdk.scaffold --id my-tool --type tool --dir ./plugins
```

This creates a complete plugin directory with `plugin.json`, `plugin.py`, and `README.md`.

Available types: `tool`, `channel`, `rag`, `memory`, `llm`, `hook`, `skill`, `mcp`, `ui`.

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
| **Full-Stack UI** 🆕 | Plugin with dedicated frontend page | `api.register_api_routes()` + Bridge SDK |

### Full-Stack UI Plugin (Quick Start)

```bash
# Create a plugin with frontend UI
my-ui-plugin/
├── plugin.json          # Add "ui" section (see plugin-ui.md)
├── plugin.py            # Backend: register routes + tools
└── ui/dist/index.html   # Frontend: inline Bridge SDK + your app
```

```json
// plugin.json — key addition
{
  "ui": {
    "entry": "ui/dist/index.html",
    "title": "My App",
    "sidebar_group": "apps"
  },
  "requires": { "plugin_ui_api": "~1" }
}
```

See [UI Plugin Guide](plugin-ui.md) for the full Bridge SDK template and protocol reference.

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
| [Getting Started](getting-started.md) | Full walkthrough from zero to running plugin |
| [API Reference](api-reference.md) | All `PluginAPI` methods and signatures |
| [**UI Plugin Guide**](plugin-ui.md) | 🆕 Full-stack UI plugin development (Plugin 2.0) |
| [Permissions](permissions.md) | Three-tier permission model |
| [Hooks](hooks.md) | All 14 lifecycle hooks with callback signatures |
| [Protocols](protocols.md) | Memory, Retrieval, Search interfaces |
| [plugin.json](plugin-json.md) | Manifest schema reference |
| [**REST API**](rest-api.md) | Plugin management HTTP endpoints |
| [Testing](testing.md) | MockPluginAPI and test patterns |
| [Cross-Ecosystem](cross-ecosystem.md) | Compatibility with Claude/Cursor/Codex |
| **Examples** | |
| [Tool Plugin](examples/tool-plugin.md) | Register AI-callable tools |
| [Channel Plugin](examples/channel-plugin.md) | Add IM channel adapters |
| [MCP Plugin](examples/mcp-plugin.md) | Wrap MCP server (JSON only) |
| [Skill Plugin](examples/skill-plugin.md) | Inject prompt guidance (no code) |
| [UI Plugin](examples/ui-plugin.md) | Full-stack UI with Bridge SDK |
| [Hook Plugin](examples/hook-plugin.md) | React to lifecycle events |
| [Memory Plugin](examples/memory-plugin.md) | Custom memory backend |
| [LLM Plugin](examples/llm-plugin.md) | Custom LLM provider |
| [RAG Plugin](examples/rag-plugin.md) | Custom retrieval source |

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
