"""Command-level wire shapes (P9.7a-2b skeleton).

Matches ``runtime.orgs.command_service.OrgCommandService`` surface;
``source`` / ``forward_to`` ride as opaque dicts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "CancelRequest",
    "CommandSnapshot",
    "CommandSubmit",
    "OrgCommandSurface",
    "OrgOutputScope",
]


class OrgCommandSurface(StrEnum):
    """Parity with ``runtime.orgs.command_models.OrgCommandSurface``."""

    ORG_CONSOLE = "org_console"
    DESKTOP_CHAT = "desktop_chat"
    IM = "im"


class OrgOutputScope(StrEnum):
    """Parity with ``runtime.orgs.command_models.OrgOutputScope``."""

    INTERNAL = "internal"
    CONSOLE_FULL = "console_full"
    CHAT_SUMMARY = "chat_summary"
    IM_SUMMARY = "im_summary"
    FINAL_ONLY = "final_only"


# Frontend clients (OrgEditorView, PixelOfficeView) historically POSTed
# `origin_surface=desktop` or `web` against this endpoint. The canonical
# enum values are `desktop_chat` / `org_console` / `im`; without an alias
# layer those legacy payloads dead-end at 422. The alias map normalizes
# the common short forms before enum coercion so the wire contract stays
# canonical while older callers keep working.
_ORIGIN_SURFACE_ALIASES: dict[str, str] = {
    "desktop": OrgCommandSurface.DESKTOP_CHAT.value,
    "web": OrgCommandSurface.DESKTOP_CHAT.value,
    "console": OrgCommandSurface.ORG_CONSOLE.value,
}


class CommandSubmit(BaseModel):
    """Body for ``POST /api/v2/orgs/{id}/command`` -- ``content`` required."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1)
    target_node_id: str | None = None
    source: dict[str, Any] | None = None
    origin_surface: OrgCommandSurface = OrgCommandSurface.ORG_CONSOLE
    # exploratory v12 §10.1: callers that omit ``output_scope`` (mobile,
    # CLI, IM bridge default body) used to land in ``command_service``
    # with a ``None``, which crashed on ``.value``. We default to
    # ``INTERNAL`` here because v12 §7 E5 verified 5 concurrent
    # internal-scope submits return 200. The field is intentionally
    # *not* ``Optional`` so an explicit ``null`` is now a 422 (was a
    # latent 500), which is the safer contract.
    output_scope: OrgOutputScope = OrgOutputScope.INTERNAL
    replace_existing: bool = False
    continue_previous: bool = False
    forward_to: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("origin_surface", mode="before")
    @classmethod
    def _normalize_origin_surface(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _ORIGIN_SURFACE_ALIASES.get(v.lower(), v)
        return v


class CommandSnapshot(BaseModel):
    """Read shape for ``GET /api/v2/orgs/{id}/commands/{cid}``."""

    model_config = ConfigDict(extra="forbid")

    command_id: str
    org_id: str
    root_node_id: str = ""
    status: str
    content: str = ""
    origin_surface: str = ""
    output_scope: str = ""
    created_at: str = ""
    updated_at: str = ""
    delivered_to: list[dict[str, Any]] = Field(default_factory=list)


class CancelRequest(BaseModel):
    """Body for ``POST .../commands/{cid}/cancel``."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
