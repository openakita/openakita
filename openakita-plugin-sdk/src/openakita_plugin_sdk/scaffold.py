"""Plugin scaffold generator — create a complete plugin directory from a template.

Usage::

    from openakita_plugin_sdk.scaffold import scaffold_plugin

    scaffold_plugin(
        target_dir="./my-plugin",
        plugin_id="my-plugin",
        plugin_name="My Plugin",
        plugin_type="tool",      # tool | channel | rag | memory | llm | hook | skill | mcp | ui
        author="Your Name",
    )

Or from the command line::

    python -m openakita_plugin_sdk.scaffold --id my-plugin --type tool --dir ./my-plugin
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from .version import MIN_OPENAKITA_VERSION, PLUGIN_API_VERSION, PLUGIN_UI_API_VERSION, SDK_VERSION

PLUGIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "tool": {
        "permissions": ["tools.register"],
        "provides": {"tools": ["example_tool"]},
        "category": "tool",
        "code": textwrap.dedent('''\
            """Example tool plugin for OpenAkita."""

            from __future__ import annotations

            from openakita_plugin_sdk import PluginBase, PluginAPI
            from openakita_plugin_sdk.tools import tool_definition

            TOOLS = [
                tool_definition(
                    name="example_tool",
                    description="An example tool — replace with your own",
                    parameters={
                        "type": "object",
                        "properties": {
                            "input": {"type": "string", "description": "Input text"},
                        },
                        "required": ["input"],
                    },
                ),
            ]


            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    async def handler(tool_name: str, arguments: dict) -> str:
                        if tool_name == "example_tool":
                            return f"Processed: {arguments.get('input', '')}"
                        return f"Unknown tool: {tool_name}"

                    api.register_tools(TOOLS, handler)
                    api.log("Example tool plugin loaded")

                def on_unload(self) -> None:
                    pass
        '''),
    },
    "channel": {
        "permissions": ["channel.register", "hooks.basic"],
        "provides": {"channels": ["example_channel"]},
        "category": "channel",
        "code": textwrap.dedent('''\
            """Example channel plugin for OpenAkita."""

            from __future__ import annotations

            from openakita_plugin_sdk import PluginBase, PluginAPI
            from openakita_plugin_sdk.channel import ChannelAdapter


            class ExampleAdapter(ChannelAdapter):
                """Replace with your real channel adapter."""

                async def start(self) -> None:
                    pass

                async def stop(self) -> None:
                    pass

                async def send_message(self, message) -> None:
                    pass

                async def send_text(self, chat_id: str, text: str, **kwargs) -> None:
                    print(f"[ExampleChannel] -> {chat_id}: {text}")


            def _adapter_factory(creds, *, channel_name, bot_id, agent_profile_id):
                return ExampleAdapter()


            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    api.register_channel("example_channel", _adapter_factory)
                    api.log("Example channel plugin loaded")

                def on_unload(self) -> None:
                    pass
        '''),
    },
    "rag": {
        "permissions": ["tools.register", "retrieval.register", "hooks.basic"],
        "provides": {"tools": ["example_search"]},
        "category": "knowledge",
        "code": textwrap.dedent('''\
            """Example RAG plugin for OpenAkita."""

            from __future__ import annotations

            from openakita_plugin_sdk import PluginBase, PluginAPI, RetrievalSource
            from openakita_plugin_sdk.tools import tool_definition

            TOOLS = [
                tool_definition(
                    name="example_search",
                    description="Search the example knowledge base",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                        },
                        "required": ["query"],
                    },
                ),
            ]


            class ExampleRetriever:
                """Replace with your real retrieval source (Obsidian, Notion, etc.)."""

                source_name = "example_kb"

                async def retrieve(self, query: str, limit: int = 5) -> list[dict]:
                    return [{"content": f"Result for: {query}", "score": 1.0}]


            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    retriever = ExampleRetriever()
                    api.register_retrieval_source(retriever)

                    async def handler(tool_name: str, arguments: dict) -> str:
                        if tool_name == "example_search":
                            results = await retriever.retrieve(arguments.get("query", ""))
                            return str(results)
                        return f"Unknown tool: {tool_name}"

                    api.register_tools(TOOLS, handler)
                    api.log("Example RAG plugin loaded")

                def on_unload(self) -> None:
                    pass
        '''),
    },
    "memory": {
        "permissions": ["memory.replace"],
        "category": "memory",
        "code": textwrap.dedent('''\
            """Example memory backend plugin for OpenAkita."""

            from __future__ import annotations

            from openakita_plugin_sdk import PluginBase, PluginAPI


            class ExampleMemoryBackend:
                """Replace with your real memory backend (Qdrant, Pinecone, etc.)."""

                async def store(self, memory: dict) -> str:
                    return "mem_id_placeholder"

                async def search(self, query: str, limit: int = 10) -> list[dict]:
                    return []

                async def delete(self, memory_id: str) -> bool:
                    return True

                async def get_injection_context(self, query: str, max_tokens: int) -> str:
                    return ""

                async def start_session(self, session_id: str) -> None:
                    pass

                async def end_session(self) -> None:
                    pass

                async def record_turn(self, role: str, content: str) -> None:
                    pass


            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    backend = ExampleMemoryBackend()
                    api.register_memory_backend(backend)
                    api.log("Example memory backend plugin loaded")

                def on_unload(self) -> None:
                    pass
        '''),
    },
    "llm": {
        "permissions": ["llm.register"],
        "category": "ai",
        "code": textwrap.dedent('''\
            """Example LLM provider plugin for OpenAkita."""

            from __future__ import annotations

            from openakita_plugin_sdk import PluginBase, PluginAPI
            from openakita_plugin_sdk.llm import LLMProvider, ProviderRegistry, ProviderRegistryInfo


            class ExampleProvider(LLMProvider):
                """Replace with your real LLM API implementation."""

                def __init__(self, config) -> None:
                    self.config = config

                async def chat(self, messages: list[dict], **kwargs):
                    return {"content": "This is a placeholder response"}

                async def chat_stream(self, messages: list[dict], **kwargs):
                    yield {"content": "This is a placeholder streaming response"}


            class ExampleRegistry(ProviderRegistry):
                def list_models(self) -> list[dict]:
                    return [{"id": "example-model", "name": "Example Model"}]


            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    api.register_llm_provider("example_api", ExampleProvider)
                    registry = ExampleRegistry(ProviderRegistryInfo(
                        slug="example-llm",
                        name="Example LLM",
                        api_type="example_api",
                        default_base_url="http://localhost:11434",
                        api_key_env="EXAMPLE_API_KEY",
                    ))
                    api.register_llm_registry("example-llm", registry)
                    api.log("Example LLM provider plugin loaded")

                def on_unload(self) -> None:
                    pass
        '''),
    },
    "hook": {
        "permissions": ["hooks.basic", "hooks.message"],
        "category": "utility",
        "code": textwrap.dedent('''\
            """Example hook plugin for OpenAkita."""

            from __future__ import annotations

            from openakita_plugin_sdk import PluginBase, PluginAPI


            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    self.api = api

                    async def on_init(**kwargs):
                        api.log("System initialized!")

                    async def on_message_received(**kwargs):
                        text = kwargs.get("text", "")
                        api.log(f"Incoming message: {text[:50]}")

                    api.register_hook("on_init", on_init)
                    api.register_hook("on_message_received", on_message_received)
                    api.log("Example hook plugin loaded")

                def on_unload(self) -> None:
                    pass
        '''),
    },
    "skill": {
        "permissions": [],
        "category": "skill",
        "skill_content": textwrap.dedent("""\
            ---
            name: example-skill
            description: An example skill — replace with your own guidance.
            ---

            # Example Skill

            When the user asks about [your topic], follow these guidelines:

            1. First, understand the user's intent
            2. Then, provide a structured response
            3. Use relevant tools if available
        """),
    },
    "mcp": {
        "permissions": [],
        "category": "tool",
        "mcp_config": {
            "command": "npx",
            "args": ["-y", "@example/mcp-server"],
            "env": {},
            "transport": "stdio",
        },
    },
    "ui": {
        "permissions": ["tools.register", "config.read", "config.write", "data.own", "api_routes.register"],
        "provides": {"routes": True},
        "category": "app",
        "ui_config": {
            "entry": "ui/dist/index.html",
            "title": "My App",
            "sidebar_group": "apps",
        },
        "code": textwrap.dedent('''\
            """Example full-stack UI plugin for OpenAkita (Plugin 2.0)."""

            from __future__ import annotations

            from fastapi import APIRouter
            from openakita_plugin_sdk import PluginBase, PluginAPI

            router = APIRouter()


            @router.get("/status")
            async def status():
                return {"ok": True, "message": "Plugin is running"}


            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    self.api = api
                    api.register_api_routes(router)
                    api.log("UI plugin loaded")

                def on_unload(self) -> None:
                    pass
        '''),
        "html": textwrap.dedent('''\
            <!DOCTYPE html>
            <html lang="en">
            <head>
              <meta charset="UTF-8">
              <meta name="viewport" content="width=device-width, initial-scale=1.0">
              <title>My Plugin</title>
              <style>
                body { font-family: system-ui, sans-serif; margin: 0; padding: 24px; }
                h1 { font-size: 1.5rem; }
              </style>
            </head>
            <body>
              <h1>My Plugin</h1>
              <p id="status">Loading...</p>
              <script>
                const API_BASE = window._ctx?.apiBase || '';
                fetch(`${API_BASE}/status`)
                  .then(r => r.json())
                  .then(d => document.getElementById('status').textContent = d.message)
                  .catch(e => document.getElementById('status').textContent = 'Error: ' + e);
              </script>
            </body>
            </html>
        '''),
    },
}


def scaffold_plugin(
    target_dir: str | Path,
    plugin_id: str,
    plugin_name: str,
    plugin_type: str = "tool",
    author: str = "",
    description: str = "",
    version: str = "1.0.0",
) -> Path:
    """Create a complete plugin directory from a template.

    Args:
        target_dir: Where to create the plugin directory.
        plugin_id: Stable plugin identifier (e.g. ``my-plugin``).
        plugin_name: Human-readable name.
        plugin_type: One of: tool, channel, rag, memory, llm, hook, skill, mcp, ui.
        author: Author name.
        description: Short description.
        version: Semver version string.

    Returns:
        Path to the created plugin directory.
    """
    if plugin_type not in PLUGIN_TEMPLATES:
        raise ValueError(
            f"Unknown plugin type '{plugin_type}'. "
            f"Choose from: {', '.join(sorted(PLUGIN_TEMPLATES))}"
        )

    template = PLUGIN_TEMPLATES[plugin_type]
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    is_skill = plugin_type == "skill"
    is_mcp = plugin_type == "mcp"
    is_ui = plugin_type == "ui"

    type_map = {"skill": "skill", "mcp": "mcp"}
    entry_map = {"skill": "SKILL.md", "mcp": "mcp_config.json"}

    manifest: dict[str, Any] = {
        "id": plugin_id,
        "name": plugin_name,
        "version": version,
        "description": description or f"A {plugin_type} plugin for OpenAkita",
        "author": author,
        "license": "MIT",
        "type": type_map.get(plugin_type, "python"),
        "entry": entry_map.get(plugin_type, "plugin.py"),
        "permissions": template.get("permissions", []),
        "category": template.get("category", ""),
        "tags": [plugin_type, "example"],
    }
    manifest["requires"] = {
        "openakita": f">={MIN_OPENAKITA_VERSION}",
        "plugin_api": f"~{PLUGIN_API_VERSION.split('.')[0]}",
        "sdk": f">={SDK_VERSION}",
        "python": ">=3.11",
    }
    if is_ui:
        manifest["requires"]["plugin_ui_api"] = f"~{PLUGIN_UI_API_VERSION.split('.')[0]}"
        manifest["ui"] = template.get("ui_config", {})
    if "provides" in template:
        manifest["provides"] = template["provides"]

    (target / "plugin.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if is_skill:
        skill_content = template.get("skill_content", "")
        skill_content = skill_content.replace("example-skill", plugin_id)
        (target / "SKILL.md").write_text(skill_content, encoding="utf-8")
    elif is_mcp:
        (target / "mcp_config.json").write_text(
            json.dumps(template.get("mcp_config", {}), indent=2) + "\n",
            encoding="utf-8",
        )
    elif is_ui:
        (target / "plugin.py").write_text(template["code"], encoding="utf-8")
        ui_dir = target / "ui" / "dist"
        ui_dir.mkdir(parents=True, exist_ok=True)
        (ui_dir / "index.html").write_text(template["html"], encoding="utf-8")
    else:
        (target / "plugin.py").write_text(template["code"], encoding="utf-8")

    readme = f"# {plugin_name}\n\n{description or manifest['description']}\n"
    (target / "README.md").write_text(readme, encoding="utf-8")

    return target


def main() -> None:
    """CLI entry point for scaffolding."""
    import argparse

    parser = argparse.ArgumentParser(description="Scaffold an OpenAkita plugin")
    parser.add_argument("--id", required=True, help="Plugin ID (e.g. my-plugin)")
    parser.add_argument("--name", help="Plugin display name")
    parser.add_argument(
        "--type", default="tool",
        choices=sorted(PLUGIN_TEMPLATES),
        help="Plugin type template",
    )
    parser.add_argument("--dir", default=".", help="Target directory")
    parser.add_argument("--author", default="", help="Author name")
    parser.add_argument("--description", default="", help="Short description")
    args = parser.parse_args()

    name = args.name or args.id.replace("-", " ").title()
    target = Path(args.dir) / args.id
    result = scaffold_plugin(
        target_dir=target,
        plugin_id=args.id,
        plugin_name=name,
        plugin_type=args.type,
        author=args.author,
        description=args.description,
    )
    print(f"Plugin scaffolded at: {result}")


if __name__ == "__main__":
    main()
