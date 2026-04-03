"""Plugin manifest (plugin.json) parsing and validation."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from ..core.capabilities import (
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityOrigin,
    CapabilityVisibility,
    build_capability_id,
    build_namespace,
)

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {"id", "name", "version", "type"}
VALID_TYPES = {"python", "mcp", "skill"}

_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-_.]{0,128}$")

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
    "system.config.write",  # reserved: will gate writes to global settings
})

ALL_PERMISSIONS = BASIC_PERMISSIONS | ADVANCED_PERMISSIONS | SYSTEM_PERMISSIONS


class PluginManifest(BaseModel):
    """Parsed plugin.json manifest with strict validation."""

    model_config = {"extra": "allow", "frozen": False, "populate_by_name": True}

    id: str
    name: str
    version: str
    plugin_type: str = Field("python", alias="type")
    entry: str = "plugin.py"
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    permissions: list[str] = Field(default_factory=list)
    requires: dict[str, Any] = Field(default_factory=dict)
    provides: dict[str, Any] = Field(default_factory=dict)
    replaces: list[str] = Field(default_factory=list)  # reserved: plugins this one supersedes
    conflicts: list[str] = Field(default_factory=list)
    depends: list[str] = Field(default_factory=list)  # reserved: inter-plugin dependency
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    icon: str = ""
    display_name_zh: str = ""
    display_name_en: str = ""
    description_i18n: dict[str, str] = Field(default_factory=dict)
    review_status: str = "unreviewed"
    load_timeout: float = 10.0
    hook_timeout: float = 5.0
    retrieve_timeout: float = 3.0
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _PLUGIN_ID_RE.match(v):
            raise ValueError(
                f"Plugin ID '{v}' is invalid — must match {_PLUGIN_ID_RE.pattern}"
            )
        return v

    @field_validator("plugin_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError(f"Invalid plugin type '{v}', must be one of {VALID_TYPES}")
        return v

    @field_validator("entry")
    @classmethod
    def _validate_entry(cls, v: str) -> str:
        if ".." in v:
            raise ValueError(f"Plugin entry '{v}' must not contain '..'")
        return v

    @field_validator("load_timeout", "hook_timeout", "retrieve_timeout", mode="before")
    @classmethod
    def _coerce_timeout(cls, v: Any, info: ValidationInfo) -> float:
        try:
            return float(v)
        except (ValueError, TypeError):
            defaults = {"load_timeout": 10.0, "hook_timeout": 5.0, "retrieve_timeout": 3.0}
            return defaults.get(info.field_name, 10.0)

    @field_validator("permissions", mode="before")
    @classmethod
    def _validate_permissions(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            raise ValueError(f"'permissions' must be a list, got {type(v).__name__}")
        return v

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

    @property
    def origin(self) -> str:
        return CapabilityOrigin.PLUGIN.value

    @property
    def namespace(self) -> str:
        return build_namespace(CapabilityOrigin.PLUGIN, plugin_id=self.id)

    @property
    def capability_id(self) -> str:
        return build_capability_id(
            CapabilityKind.PLUGIN,
            self.id,
            origin=CapabilityOrigin.PLUGIN,
            plugin_id=self.id,
        )

    @property
    def permission_profile(self) -> str:
        return self.max_permission_level

    def to_capability_descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            id=self.capability_id,
            kind=CapabilityKind.PLUGIN,
            origin=CapabilityOrigin.PLUGIN,
            namespace=self.namespace,
            display_name=self.name,
            description=self.description,
            version=self.version,
            visibility=CapabilityVisibility.PUBLIC,
            permission_profile=self.permission_profile,
            i18n={
                "name": {
                    "zh": self.display_name_zh or self.name,
                    "en": self.display_name_en or self.name,
                },
                "description": dict(self.description_i18n),
            },
            metadata={
                "plugin_type": self.plugin_type,
                "permissions": list(self.permissions),
                "review_status": self.review_status,
                "category": self.category,
                "tags": list(self.tags),
            },
        )


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

    if not isinstance(raw, dict):
        raise ManifestError(f"plugin.json must be a JSON object in {manifest_path}")

    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise ManifestError(
            f"Missing required fields in {manifest_path}: {missing}"
        )

    permissions = raw.get("permissions", [])
    if isinstance(permissions, list):
        unknown = set(permissions) - ALL_PERMISSIONS
        if unknown:
            logger.warning(
                "Plugin '%s' declares unknown permissions: %s (ignored)",
                raw.get("id", "?"),
                unknown,
            )
            raw = {**raw, "permissions": [p for p in permissions if p in ALL_PERMISSIONS]}

    entry = raw.get("entry")
    if entry is None:
        raw = {**raw, "entry": _default_entry(raw.get("type", "python"))}

    try:
        manifest = PluginManifest.model_validate(raw)
    except Exception as e:
        raise ManifestError(f"Manifest validation failed in {manifest_path}: {e}") from e

    manifest.raw = raw
    return manifest


def _default_entry(plugin_type: str) -> str:
    if plugin_type == "python":
        return "plugin.py"
    if plugin_type == "mcp":
        return "mcp_config.json"
    if plugin_type == "skill":
        return "SKILL.md"
    return "plugin.py"
