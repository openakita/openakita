"""Plugin workbench manifest data classes.

Implements the manifest shape declared in ADR-0009. A plugin opts
into the workbench protocol by exposing a top-level ``WORKBENCH``
``dict`` constant in its plugin module. This module contains the
parser/validator that :class:`WorkbenchNode` uses; the node itself
lives in :mod:`runtime.nodes.workbench_node` so the data layer can
be imported without dragging in the node and its dependencies.

Validation rules (subset of ADR-0009 enforceable without a tool
registry; the rest happens at plugin load time in Phase 6):

* ``id`` is a non-empty string.
* ``modes`` is a non-empty list of mode dicts.
* Every mode has a non-empty ``id`` and a non-empty ``tools`` list.
* Mode ids are unique.
* ``default_mode`` must reference an existing mode.
* ``ui.url``, ``ui.icon``, ``ui.min_width`` are optional but typed
  if present.
* ``capabilities`` is an optional list of strings.
* ``version`` is an optional positive int (default 1).

Tool registry validation (every tool listed in ``modes[*].tools``
must be registered) is the Phase 6 plugin loader's job, since
running it here would create a tight coupling between the runtime
and the legacy plugin tool registry. The :class:`WorkbenchNode`
constructor accepts a callable ``tool_runner`` exactly so plugins
can plug in whatever runtime they have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "WorkbenchManifest",
    "WorkbenchManifestError",
    "WorkbenchMode",
    "WorkbenchUI",
]


class WorkbenchManifestError(ValueError):
    """Raised when a ``WORKBENCH`` constant fails validation."""


@dataclass(frozen=True)
class WorkbenchUI:
    url: str | None = None
    min_width: int | None = None
    icon: str | None = None

    @classmethod
    def parse(cls, raw: Any) -> WorkbenchUI:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise WorkbenchManifestError("`ui` must be a dict if provided")
        url = raw.get("url")
        if url is not None and not isinstance(url, str):
            raise WorkbenchManifestError("`ui.url` must be a string if provided")
        icon = raw.get("icon")
        if icon is not None and not isinstance(icon, str):
            raise WorkbenchManifestError("`ui.icon` must be a string if provided")
        min_width = raw.get("min_width")
        if min_width is not None and not isinstance(min_width, int):
            raise WorkbenchManifestError(
                "`ui.min_width` must be an int if provided"
            )
        return cls(url=url, min_width=min_width, icon=icon)


@dataclass(frozen=True)
class WorkbenchMode:
    id: str
    label: str
    tools: tuple[str, ...]
    description: str = ""
    system_prompt_override: str | None = None
    guardrails: tuple[dict[str, Any], ...] = ()
    ui_panel: str | None = None

    @classmethod
    def parse(cls, raw: Any) -> WorkbenchMode:
        if not isinstance(raw, dict):
            raise WorkbenchManifestError("each mode must be a dict")
        mode_id = raw.get("id")
        if not isinstance(mode_id, str) or not mode_id:
            raise WorkbenchManifestError("mode.id must be a non-empty string")
        label = raw.get("label") or mode_id
        if not isinstance(label, str):
            raise WorkbenchManifestError(
                f"mode {mode_id!r}.label must be a string"
            )
        tools = raw.get("tools")
        if (
            not isinstance(tools, list)
            or not tools
            or not all(isinstance(t, str) and t for t in tools)
        ):
            raise WorkbenchManifestError(
                f"mode {mode_id!r}.tools must be a non-empty list of strings"
            )
        description = raw.get("description") or ""
        if not isinstance(description, str):
            raise WorkbenchManifestError(
                f"mode {mode_id!r}.description must be a string"
            )
        system_prompt = raw.get("system_prompt_override")
        if system_prompt is not None and not isinstance(system_prompt, str):
            raise WorkbenchManifestError(
                f"mode {mode_id!r}.system_prompt_override must be a string"
            )
        guardrails_raw = raw.get("guardrails") or []
        if not isinstance(guardrails_raw, list) or not all(
            isinstance(g, dict) for g in guardrails_raw
        ):
            raise WorkbenchManifestError(
                f"mode {mode_id!r}.guardrails must be a list of dicts"
            )
        ui_panel = raw.get("ui_panel")
        if ui_panel is not None and not isinstance(ui_panel, str):
            raise WorkbenchManifestError(
                f"mode {mode_id!r}.ui_panel must be a string if provided"
            )
        return cls(
            id=mode_id,
            label=label,
            tools=tuple(tools),
            description=description,
            system_prompt_override=system_prompt,
            guardrails=tuple(dict(g) for g in guardrails_raw),
            ui_panel=ui_panel,
        )


@dataclass(frozen=True)
class WorkbenchManifest:
    id: str
    title: str
    modes: tuple[WorkbenchMode, ...]
    default_mode: str
    description: str = ""
    version: int = 1
    capabilities: tuple[str, ...] = ()
    ui: WorkbenchUI = field(default_factory=WorkbenchUI)

    def mode(self, mode_id: str) -> WorkbenchMode:
        """Look up a mode by id. Raises ``KeyError`` for unknown ids."""
        for m in self.modes:
            if m.id == mode_id:
                return m
        raise KeyError(mode_id)

    @classmethod
    def parse(cls, raw: Any) -> WorkbenchManifest:
        """Validate and convert a raw ``WORKBENCH`` dict to typed form."""
        if not isinstance(raw, dict):
            raise WorkbenchManifestError("WORKBENCH must be a dict")
        manifest_id = raw.get("id")
        if not isinstance(manifest_id, str) or not manifest_id:
            raise WorkbenchManifestError("`id` must be a non-empty string")
        title = raw.get("title") or manifest_id
        if not isinstance(title, str):
            raise WorkbenchManifestError("`title` must be a string")
        description = raw.get("description") or ""
        if not isinstance(description, str):
            raise WorkbenchManifestError("`description` must be a string")
        version = raw.get("version", 1)
        if not isinstance(version, int) or version < 1:
            raise WorkbenchManifestError("`version` must be a positive int")
        capabilities = raw.get("capabilities") or []
        if not isinstance(capabilities, list) or not all(
            isinstance(c, str) for c in capabilities
        ):
            raise WorkbenchManifestError(
                "`capabilities` must be a list of strings"
            )
        ui = WorkbenchUI.parse(raw.get("ui"))
        modes_raw = raw.get("modes")
        if not isinstance(modes_raw, list) or not modes_raw:
            raise WorkbenchManifestError("`modes` must be a non-empty list")
        modes = tuple(WorkbenchMode.parse(m) for m in modes_raw)
        seen: set[str] = set()
        for m in modes:
            if m.id in seen:
                raise WorkbenchManifestError(
                    f"duplicate mode id {m.id!r} in WORKBENCH"
                )
            seen.add(m.id)
        default_mode = raw.get("default_mode") or modes[0].id
        if not isinstance(default_mode, str):
            raise WorkbenchManifestError("`default_mode` must be a string")
        if default_mode not in seen:
            raise WorkbenchManifestError(
                f"default_mode {default_mode!r} is not in modes; "
                f"valid: {sorted(seen)}"
            )
        return cls(
            id=manifest_id,
            title=title,
            description=description,
            version=version,
            modes=modes,
            default_mode=default_mode,
            capabilities=tuple(capabilities),
            ui=ui,
        )
