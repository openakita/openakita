"""
Provider Registry

Retrieves model lists and capability info from various LLM providers.

┌──────────────────────────────────────────────────────────────┐
│  Data sources:                                               │
│  1. Built-in providers.json (same directory, shipped with    │
│     each release)                                            │
│  2. Workspace data/custom_providers.json (user-defined,      │
│     optional)                                                │
│                                                              │
│  Merge rule: built-in list is the base; workspace entries    │
│  override or append by slug. Users can add/remove/update     │
│  providers via AI tools (manage_provider action) or by       │
│  editing data/custom_providers.json directly.                │
│                                                              │
│  To add a new built-in provider:                             │
│  1. Write a new XxxRegistry class (subclass ProviderRegistry)│
│  2. Add an entry in providers.json with registry_class set   │
│     to the class name                                        │
│  3. Frontend syncs automatically (Vite imports JSON at build │
│     time)                                                    │
└──────────────────────────────────────────────────────────────┘
"""

import json
import logging
from importlib import import_module
from pathlib import Path

from .anthropic import AnthropicRegistry
from .base import ModelInfo, ProviderInfo, ProviderRegistry
from .dashscope import DashScopeRegistry
from .openrouter import OpenRouterRegistry
from .siliconflow import SiliconFlowRegistry

__all__ = [
    "AnthropicRegistry",
    "DashScopeRegistry",
    "ModelInfo",
    "OpenRouterRegistry",
    "ProviderInfo",
    "ProviderRegistry",
    "SiliconFlowRegistry",
]

_logger = logging.getLogger(__name__)

# ── Load built-in provider declarations from providers.json ──
_PROVIDERS_JSON = Path(__file__).parent / "providers.json"
_BUILTIN_ENTRIES: list[dict] = json.loads(_PROVIDERS_JSON.read_text(encoding="utf-8"))

# ── registry_class -> module mapping ──
_CLASS_MODULE_MAP: dict[str, str] = {
    "AnthropicRegistry": ".anthropic",
    "OpenAIRegistry": ".openai",
    "DashScopeRegistry": ".dashscope",
    "DashScopeInternationalRegistry": ".dashscope",
    "KimiChinaRegistry": ".kimi",
    "KimiInternationalRegistry": ".kimi",
    "MiniMaxChinaRegistry": ".minimax",
    "MiniMaxInternationalRegistry": ".minimax",
    "DeepSeekRegistry": ".deepseek",
    "OpenRouterRegistry": ".openrouter",
    "SiliconFlowRegistry": ".siliconflow",
    "SiliconFlowInternationalRegistry": ".siliconflow",
    "VolcEngineRegistry": ".volcengine",
    "ZhipuChinaRegistry": ".zhipu",
    "ZhipuInternationalRegistry": ".zhipu",
}


def _entry_to_provider_info(entry: dict) -> ProviderInfo:
    """Convert a JSON entry to ProviderInfo."""
    return ProviderInfo(
        name=entry["name"],
        slug=entry["slug"],
        api_type=entry["api_type"],
        default_base_url=entry["default_base_url"],
        api_key_env_suggestion=entry.get("api_key_env_suggestion", ""),
        supports_model_list=entry.get("supports_model_list", True),
        supports_capability_api=entry.get("supports_capability_api", False),
        requires_api_key=entry.get("requires_api_key", True),
        is_local=entry.get("is_local", False),
        coding_plan_base_url=entry.get("coding_plan_base_url"),
        coding_plan_api_type=entry.get("coding_plan_api_type"),
        note=entry.get("note"),
    )


# ── Workspace custom provider management ──


def _get_custom_providers_path() -> Path:
    """Return the path to the workspace custom providers file (sibling of llm_endpoints.json)."""
    from ..config import get_default_config_path

    return get_default_config_path().parent / "custom_providers.json"


def load_custom_providers() -> list[dict]:
    """Load custom provider list from workspace."""
    path = _get_custom_providers_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        _logger.warning(f"Failed to load custom providers from {path}: {e}")
        return []


def save_custom_providers(entries: list[dict]) -> None:
    """Save custom provider list to workspace."""
    path = _get_custom_providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _logger.info(f"Saved {len(entries)} custom providers to {path}")


def _merge_provider_entries() -> list[dict]:
    """Merge built-in and workspace custom providers.

    Custom entries override built-in entries by slug; new slugs are appended.
    """
    merged: dict[str, dict] = {}
    for entry in _BUILTIN_ENTRIES:
        merged[entry["slug"]] = entry

    custom = load_custom_providers()
    for entry in custom:
        slug = entry.get("slug", "")
        if not slug:
            continue
        if slug in merged:
            merged[slug] = {**merged[slug], **entry}
        else:
            merged[slug] = entry

    return list(merged.values())


def _build_registry_for_entry(entry: dict) -> ProviderRegistry | None:
    """Build a registry instance for a single provider entry."""
    cls_name = entry.get("registry_class", "")
    if not cls_name:
        api_type = entry.get("api_type", "openai")
        cls_name = "AnthropicRegistry" if api_type == "anthropic" else "OpenAIRegistry"

    mod_name = _CLASS_MODULE_MAP.get(cls_name)
    if mod_name is None:
        _logger.warning(
            f"registry_class '{cls_name}' not registered in _CLASS_MODULE_MAP, "
            f"skipping provider '{entry.get('name', '?')}'"
        )
        return None
    try:
        mod = import_module(mod_name, package=__package__)
        cls = getattr(mod, cls_name)
    except (ImportError, AttributeError) as e:
        _logger.warning(
            f"Failed to load registry '{cls_name}' (module={mod_name}), "
            f"skipping provider '{entry.get('name', '?')}': {e}"
        )
        return None

    instance = cls()
    instance.info = _entry_to_provider_info(entry)
    return instance


def _build_registries() -> list[ProviderRegistry]:
    """Build all registry instances from the merged provider list.

    A failure to load one registry does not affect other providers (logged as a warning and skipped).
    """
    registries: list[ProviderRegistry] = []
    for entry in _merge_provider_entries():
        reg = _build_registry_for_entry(entry)
        if reg is not None:
            registries.append(reg)
    return registries


ALL_REGISTRIES = _build_registries()

REGISTRY_BY_SLUG = {r.info.slug: r for r in ALL_REGISTRIES}


def reload_registries() -> int:
    """Reload provider registries (merge built-in + custom + plugins), return the count."""
    global ALL_REGISTRIES, REGISTRY_BY_SLUG
    ALL_REGISTRIES = _build_registries()

    try:
        from ...plugins import PLUGIN_REGISTRY_MAP

        for slug, reg in PLUGIN_REGISTRY_MAP.items():
            if slug not in {r.info.slug for r in ALL_REGISTRIES}:
                ALL_REGISTRIES.append(reg)
    except ImportError:
        pass

    REGISTRY_BY_SLUG = {r.info.slug: r for r in ALL_REGISTRIES}
    _logger.info(f"Reloaded {len(ALL_REGISTRIES)} provider registries")
    return len(ALL_REGISTRIES)


def get_registry(slug: str) -> ProviderRegistry:
    """Get a registry by slug."""
    if slug not in REGISTRY_BY_SLUG:
        raise ValueError(f"Unknown provider: {slug}")
    return REGISTRY_BY_SLUG[slug]


def list_providers() -> list[ProviderInfo]:
    """List all supported providers."""
    return [r.info for r in ALL_REGISTRIES]


__all__ = [
    "ProviderRegistry",
    "ProviderInfo",
    "ModelInfo",
    "ALL_REGISTRIES",
    "get_registry",
    "list_providers",
    "load_custom_providers",
    "save_custom_providers",
    "reload_registries",
]
