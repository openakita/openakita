"""hello-tool: registers a single LLM-callable tool."""

from __future__ import annotations

from openakita.plugins.api import PluginAPI, PluginBase


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": "hello_world",
                    "description": "Returns a friendly greeting for the given name.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Name to greet",
                            },
                        },
                        "required": ["name"],
                    },
                },
            },
        ]

        def handle(tool_name: str, params: dict) -> str:
            if tool_name != "hello_world":
                return ""
            name = str(params.get("name", "world"))
            return f"Hello, {name}!"

        api.register_tools(definitions, handle)

    def on_unload(self) -> None:
        pass
