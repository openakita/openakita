"""
Unified capability metadata shared by skills, plugins, agent definitions, tools,
and scheduled task sources.

This module provides a small, stable schema so each extensibility surface can
answer the same questions:
- what is this capability?
- where did it come from?
- which namespace owns it?
- how should permission and UI layers describe it?
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class CapabilityKind(StrEnum):
    SKILL = "skill"
    PLUGIN = "plugin"
    AGENT_DEFINITION = "agent_definition"
    TOOL = "tool"
    TASK_SOURCE = "task_source"


class CapabilityOrigin(StrEnum):
    SYSTEM = "system"
    PROJECT = "project"
    USER = "user"
    PLUGIN = "plugin"
    MARKETPLACE = "marketplace"
    REMOTE = "remote"
    RUNTIME = "runtime"


class CapabilityVisibility(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Portable capability descriptor for registry, audit, and UI surfaces."""

    id: str
    kind: CapabilityKind
    origin: CapabilityOrigin
    namespace: str
    display_name: str
    description: str = ""
    version: str = ""
    visibility: CapabilityVisibility = CapabilityVisibility.PUBLIC
    permission_profile: str = ""
    source_ref: str = ""
    tags: list[str] = field(default_factory=list)
    i18n: dict[str, dict[str, str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["origin"] = self.origin.value
        data["visibility"] = self.visibility.value
        return data


def normalize_slug(value: str) -> str:
    raw = (value or "").strip().lower().replace("\\", "/")
    safe = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_", ".", "/", "@"}:
            safe.append(ch)
        elif ch in {" ", ":"}:
            safe.append("-")
    normalized = "".join(safe).strip("-./")
    return normalized or "unknown"


def build_namespace(
    origin: CapabilityOrigin | str,
    *,
    plugin_id: str = "",
    project_id: str = "",
) -> str:
    origin_value = CapabilityOrigin(origin).value if not isinstance(origin, CapabilityOrigin) else origin.value
    if origin_value == CapabilityOrigin.PLUGIN.value:
        return f"plugin:{normalize_slug(plugin_id or 'unknown')}"
    if origin_value == CapabilityOrigin.PROJECT.value:
        return f"project:{normalize_slug(project_id or 'default')}"
    return origin_value


def build_capability_id(
    kind: CapabilityKind | str,
    local_id: str,
    *,
    origin: CapabilityOrigin | str,
    plugin_id: str = "",
    project_id: str = "",
) -> str:
    kind_value = CapabilityKind(kind).value if not isinstance(kind, CapabilityKind) else kind.value
    namespace = build_namespace(origin, plugin_id=plugin_id, project_id=project_id)
    return f"{namespace}/{kind_value}:{normalize_slug(local_id)}"
