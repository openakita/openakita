"""OpenAkita Plugin SDK — build plugins without installing the full runtime.

Quick start::

    from openakita_plugin_sdk import PluginBase, PluginAPI
    from openakita_plugin_sdk.tools import tool_definition
    from openakita_plugin_sdk.decorators import tool, hook, auto_register
    from openakita_plugin_sdk.testing import MockPluginAPI, assert_plugin_loads
    from openakita_plugin_sdk.scaffold import scaffold_plugin

The SDK is intentionally minimal — it gives plugins a typed entrypoint,
a host-facing API surface, and a testing harness, nothing more.  Any
opinionated AI-media scaffolding (task DB, vendor client, error coach,
cost preview, render pipeline, intent verifier, …) belongs to the
plugin that needs it; see ``plugins-archive/_shared/`` for examples
that used to ship under ``openakita_plugin_sdk.contrib`` (removed in
0.7.0).

See ``docs/getting-started.md`` for the full walkthrough.
"""

from .core import PluginAPI, PluginBase, PluginManifest
from .hooks import HOOK_NAMES, HOOK_SIGNATURES
from .protocols import MemoryBackendProtocol, RetrievalSource, SearchBackend
from .tools import ToolHandler, tool_definition
from .version import (
    MIN_OPENAKITA_VERSION,
    PLUGIN_API_VERSION,
    PLUGIN_UI_API_VERSION,
    SDK_VERSION,
)

__version__ = SDK_VERSION

__all__ = [
    "HOOK_NAMES",
    "HOOK_SIGNATURES",
    "MemoryBackendProtocol",
    "MIN_OPENAKITA_VERSION",
    "PLUGIN_API_VERSION",
    "PLUGIN_UI_API_VERSION",
    "PluginAPI",
    "PluginBase",
    "PluginManifest",
    "RetrievalSource",
    "SDK_VERSION",
    "SearchBackend",
    "ToolHandler",
    "tool_definition",
]
