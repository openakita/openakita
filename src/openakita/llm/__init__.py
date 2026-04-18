"""
Unified LLM invocation layer.

Keep package exports lazy so importing lightweight modules such as
`openakita.llm.types` does not automatically initialize the full client stack.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapter import LegacyContext, LegacyResponse, LLMAdapter, think
    from .client import LLMClient, chat, get_default_client
    from .config import get_default_config_path, load_endpoints_config
    from .types import (
        ContentBlock,
        EndpointConfig,
        ImageContent,
        LLMRequest,
        LLMResponse,
        Message,
        StopReason,
        TextBlock,
        Tool,
        ToolResultBlock,
        ToolUseBlock,
        Usage,
        VideoContent,
    )

__all__ = [
    "LLMRequest",
    "LLMResponse",
    "EndpointConfig",
    "ContentBlock",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ImageContent",
    "VideoContent",
    "Message",
    "Tool",
    "Usage",
    "StopReason",
    "LLMClient",
    "get_default_client",
    "chat",
    "load_endpoints_config",
    "get_default_config_path",
    "LLMAdapter",
    "LegacyResponse",
    "LegacyContext",
    "think",
]

_LAZY_IMPORTS = {
    # Types
    "LLMRequest": (".types", "LLMRequest"),
    "LLMResponse": (".types", "LLMResponse"),
    "EndpointConfig": (".types", "EndpointConfig"),
    "ContentBlock": (".types", "ContentBlock"),
    "TextBlock": (".types", "TextBlock"),
    "ToolUseBlock": (".types", "ToolUseBlock"),
    "ToolResultBlock": (".types", "ToolResultBlock"),
    "ImageContent": (".types", "ImageContent"),
    "VideoContent": (".types", "VideoContent"),
    "Message": (".types", "Message"),
    "Tool": (".types", "Tool"),
    "Usage": (".types", "Usage"),
    "StopReason": (".types", "StopReason"),
    # Client
    "LLMClient": (".client", "LLMClient"),
    "get_default_client": (".client", "get_default_client"),
    "chat": (".client", "chat"),
    # Config
    "load_endpoints_config": (".config", "load_endpoints_config"),
    "get_default_config_path": (".config", "get_default_config_path"),
    # Adapter
    "LLMAdapter": (".adapter", "LLMAdapter"),
    "LegacyResponse": (".adapter", "LegacyResponse"),
    "LegacyContext": (".adapter", "LegacyContext"),
    "think": (".adapter", "think"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
