"""Convenience decorators for plugin development.

These decorators collect tool and hook registrations declaratively,
then apply them all at once during ``on_load`` via ``auto_register()``.

Usage::

    from openakita_plugin_sdk import PluginBase, PluginAPI
    from openakita_plugin_sdk.decorators import tool, hook, auto_register

    @tool(
        name="search_notes",
        description="Search user notes by keyword",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
            },
            "required": ["query"],
        },
    )
    async def search_notes(tool_name: str, arguments: dict) -> str:
        query = arguments["query"]
        return f"Results for: {query}"

    @hook("on_message_received")
    async def log_message(**kwargs):
        print(f"Got message: {kwargs.get('text', '')}")

    class Plugin(PluginBase):
        def on_load(self, api: PluginAPI) -> None:
            auto_register(api)   # registers all @tool and @hook in this module
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from .tools import tool_definition

_TOOL_REGISTRY: list[tuple[dict, Callable]] = []
_HOOK_REGISTRY: list[tuple[str, Callable]] = []


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
) -> Callable:
    """Decorator that marks an async function as a tool handler.

    The decorated function must accept ``(tool_name: str, arguments: dict)``
    and return a ``str``.

    Example::

        @tool(name="greet", description="Say hello")
        async def greet(tool_name: str, arguments: dict) -> str:
            return f"Hello, {arguments.get('name', 'world')}!"
    """
    defn = tool_definition(name, description, parameters)

    def decorator(fn: Callable) -> Callable:
        fn.__openakita_tool__ = defn  # type: ignore[attr-defined]
        _TOOL_REGISTRY.append((defn, fn))
        return fn

    return decorator


def hook(hook_name: str) -> Callable:
    """Decorator that marks a function as a lifecycle hook callback.

    Example::

        @hook("on_init")
        async def setup(**kwargs):
            print("Plugin initialized!")

    Valid hook names: on_init, on_shutdown, on_schedule, on_config_change,
    on_error, on_message_received, on_message_sending, on_session_start,
    on_session_end, on_retrieve, on_tool_result, on_before_tool_use,
    on_after_tool_use, on_prompt_build.
    """
    from .hooks import HOOK_NAMES

    if hook_name not in HOOK_NAMES:
        raise ValueError(
            f"Unknown hook '{hook_name}'. Valid hooks: {sorted(HOOK_NAMES)}"
        )

    def decorator(fn: Callable) -> Callable:
        fn.__openakita_hook__ = hook_name  # type: ignore[attr-defined]
        _HOOK_REGISTRY.append((hook_name, fn))
        return fn

    return decorator


def auto_register(api: Any, module: Any = None) -> None:
    """Register all ``@tool`` and ``@hook`` decorated functions with the API.

    If ``module`` is provided, only functions defined in that module are
    registered. Otherwise, uses the global registries (all decorated
    functions across all imported modules).

    Typical usage in ``on_load``::

        def on_load(self, api):
            auto_register(api)
    """
    if module is not None:
        _register_from_module(api, module)
    else:
        _register_from_globals(api)


def _register_from_module(api: Any, module: Any) -> None:
    """Scan a module for decorated functions and register them."""
    tools: list[tuple[dict, Callable]] = []
    handlers: dict[str, Callable] = {}

    for _name, obj in inspect.getmembers(module, callable):
        defn = getattr(obj, "__openakita_tool__", None)
        if defn is not None:
            tool_name = defn.get("function", {}).get("name", defn.get("name", ""))
            tools.append((defn, obj))
            handlers[tool_name] = obj

        hook_name = getattr(obj, "__openakita_hook__", None)
        if hook_name is not None:
            api.register_hook(hook_name, obj)

    if tools:
        defs = [t[0] for t in tools]

        async def dispatch(tool_name: str, arguments: dict) -> str:
            fn = handlers.get(tool_name)
            if fn is None:
                return f"Unknown tool: {tool_name}"
            return await fn(tool_name, arguments)

        api.register_tools(defs, dispatch)


def _register_from_globals(api: Any) -> None:
    """Register from global decorator registries."""
    if _TOOL_REGISTRY:
        defs = [t[0] for t in _TOOL_REGISTRY]
        handler_map: dict[str, Callable] = {}
        for defn, fn in _TOOL_REGISTRY:
            name = defn.get("function", {}).get("name", defn.get("name", ""))
            handler_map[name] = fn

        async def dispatch(tool_name: str, arguments: dict) -> str:
            fn = handler_map.get(tool_name)
            if fn is None:
                return f"Unknown tool: {tool_name}"
            return await fn(tool_name, arguments)

        api.register_tools(defs, dispatch)

    for hook_name, fn in _HOOK_REGISTRY:
        api.register_hook(hook_name, fn)


def clear_registries() -> None:
    """Clear global decorator registries. Useful between tests."""
    _TOOL_REGISTRY.clear()
    _HOOK_REGISTRY.clear()

