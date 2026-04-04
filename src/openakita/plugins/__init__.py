"""OpenAkita Plugin System — unified extensibility for all core modules."""

from .api import PluginAPI, PluginBase
from .catalog import PluginCatalog
from .errors import PluginError, PluginErrorCode
from .hooks import HookRegistry
from .manager import PluginManager
from .manifest import PluginManifest, parse_manifest
from .protocols import MemoryBackendProtocol, RetrievalSource, SearchBackend
from .sandbox import PluginErrorTracker, safe_call, safe_call_sync
from .state import PluginState

__all__ = [
    "HookRegistry",
    "MemoryBackendProtocol",
    "PLUGIN_PROVIDER_MAP",
    "PLUGIN_REGISTRY_MAP",
    "PluginAPI",
    "PluginBase",
    "PluginCatalog",
    "PluginError",
    "PluginErrorCode",
    "PluginManager",
    "PluginManifest",
    "PluginState",
    "RetrievalSource",
    "SearchBackend",
    "parse_manifest",
    "safe_call",
    "PluginErrorTracker",
    "safe_call_sync",
]

PLUGIN_PROVIDER_MAP: dict[str, type] = {}
PLUGIN_REGISTRY_MAP: dict[str, object] = {}
