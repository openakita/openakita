"""Plugin manifest (plugin.json) parsing and validation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {"id", "name", "version", "type"}
VALID_TYPES = {"python", "mcp", "skill"}

BASIC_PERMISSIONS = frozenset({
    "tools.register",
    "hooks.basic",
    "config.read",
    "config.write",
    "data.own",
    "log",
    "skill",
})

ADVANCED_PERMISSIONS = frozenset({
    "memory.read",
    "memory.write",
    "channel.register",
    "channel.send",
    "hooks.message",
    "hooks.retrieve",
    "retrieval.register",
    "search.register",
    "routes.register",
    "brain.access",
    "vector.access",
    "settings.read",
    "llm.register",
})

SYSTEM_PERMISSIONS = frozenset({
    "hooks.all",
    "memory.replace",
    "system.config.write",  # reserved: will gate writes to global settings in P1
})

ALL_PERMISSIONS = BASIC_PERMISSIONS | ADVANCED_PERMISSIONS | SYSTEM_PERMISSIONS


@dataclass
class PluginManifest:
    """Parsed plugin.json manifest."""

    id: str
    name: str
    version: str
    plugin_type: str  # "python" | "mcp" | "skill"
    entry: str = "plugin.py"
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    permissions: list[str] = field(default_factory=list)
    requires: dict[str, Any] = field(default_factory=dict)
    provides: dict[str, Any] = field(default_factory=dict)
    replaces: list[str] = field(default_factory=list)  # reserved: plugins this one supersedes
    conflicts: list[str] = field(default_factory=list)
    category: str = ""
    tags: list[str] = field(default_factory=list)
    icon: str = ""
    load_timeout: float = 10.0
    hook_timeout: float = 5.0
    retrieve_timeout: float = 3.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def basic_permissions(self) -> list[str]:
        return [p for p in self.permissions if p in BASIC_PERMISSIONS]

    @property
    def advanced_permissions(self) -> list[str]:
        return [p for p in self.permissions if p in ADVANCED_PERMISSIONS]

    @property
    def system_permissions(self) -> list[str]:
        return [p for p in self.permissions if p in SYSTEM_PERMISSIONS]

    @property
    def max_permission_level(self) -> str:
        if self.system_permissions:
            return "system"
        if self.advanced_permissions:
            return "advanced"
        return "basic"


class ManifestError(Exception):
    """Raised when plugin.json is invalid."""


def parse_manifest(plugin_dir: Path) -> PluginManifest:
    """Parse and validate a plugin.json file from a plugin directory."""
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        raise ManifestError(f"Missing plugin.json in {plugin_dir}")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ManifestError(f"Invalid JSON in {manifest_path}: {e}") from e

    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise ManifestError(
            f"Missing required fields in {manifest_path}: {missing}"
        )

    plugin_type = raw.get("type", "")
    if plugin_type not in VALID_TYPES:
        raise ManifestError(
            f"Invalid plugin type '{plugin_type}' in {manifest_path}, "
            f"must be one of {VALID_TYPES}"
        )

    permissions = raw.get("permissions", [])
    if not isinstance(permissions, list):
        raise ManifestError(
            f"'permissions' must be a list in {manifest_path}, "
            f"got {type(permissions).__name__}"
        )

    unknown = set(permissions) - ALL_PERMISSIONS
    if unknown:
        logger.warning(
            "Plugin '%s' declares unknown permissions: %s (ignored)",
            raw.get("id", "?"),
            unknown,
        )
        permissions = [p for p in permissions if p in ALL_PERMISSIONS]

    return PluginManifest(
        id=raw["id"],
        name=raw["name"],
        version=raw["version"],
        plugin_type=plugin_type,
        entry=raw.get("entry", _default_entry(plugin_type)),
        description=raw.get("description", ""),
        author=raw.get("author", ""),
        license=raw.get("license", ""),
        homepage=raw.get("homepage", ""),
        permissions=permissions,
        requires=raw.get("requires", {}),
        provides=raw.get("provides", {}),
        replaces=raw.get("replaces", []),
        conflicts=raw.get("conflicts", []),
        category=raw.get("category", ""),
        tags=raw.get("tags", []),
        icon=raw.get("icon", ""),
        load_timeout=_safe_float(raw.get("load_timeout", 10), 10.0, "load_timeout", manifest_path),
        hook_timeout=_safe_float(raw.get("hook_timeout", 5), 5.0, "hook_timeout", manifest_path),
        retrieve_timeout=_safe_float(raw.get("retrieve_timeout", 3), 3.0, "retrieve_timeout", manifest_path),
        raw=raw,
    )


def _safe_float(value: Any, default: float, field_name: str, path: Path) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid %s value %r in %s, using default %.1f",
            field_name, value, path, default,
        )
        return default


def _default_entry(plugin_type: str) -> str:
    if plugin_type == "python":
        return "plugin.py"
    if plugin_type == "mcp":
        return "mcp_config.json"
    if plugin_type == "skill":
        return "SKILL.md"
    return "plugin.py"
