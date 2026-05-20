"""Command-level wire shapes (P9.7a-2b skeleton).

Matches ``runtime.orgs.command_service.OrgCommandService`` surface;
``source`` / ``forward_to`` ride as opaque dicts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


class CommandSubmit(BaseModel):
    """Body for ``POST /api/v2/orgs/{id}/command`` -- ``content`` required."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1)
    target_node_id: str | None = None
    source: dict[str, Any] | None = None
    origin_surface: OrgCommandSurface = OrgCommandSurface.ORG_CONSOLE
    output_scope: OrgOutputScope | None = None
    replace_existing: bool = False
    continue_previous: bool = False
    forward_to: list[dict[str, Any]] = Field(default_factory=list)


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
