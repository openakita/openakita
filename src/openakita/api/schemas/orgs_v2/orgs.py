"""Org-level wire shapes (P9.7a-2b skeleton).

Mirrors the wire-stable subset of ``openakita.orgs.models.Organization``;
nested nodes / edges ride as opaque dicts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Org", "OrgCreate", "OrgPatch", "OrgStatus"]

# v10 #7 / v11 follow-up: cap free-form text inputs so a stuck client
# (or a crafted payload) cannot persist multi-megabyte ``name`` /
# ``description`` strings that the editor and IM bridges have no way
# to render. Only validated on inbound write — read-back paths leave
# legacy oversize records untouched so existing data stays loadable.
_NAME_MAX = 200
_DESCRIPTION_MAX = 1000
_SHORT_TEXT_MAX = 200
_LONG_TEXT_MAX = 1000


class OrgStatus(StrEnum):
    """Byte-for-byte parity with ``orgs.models.OrgStatus``."""

    DORMANT = "dormant"
    ACTIVE = "active"
    RUNNING = "running"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Org(BaseModel):
    """Read shape for ``GET /api/v2/orgs/{id}``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    icon: str = ""
    status: OrgStatus = OrgStatus.DORMANT
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    core_business: str = ""
    workspace_dir: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class OrgCreate(BaseModel):
    """Body for ``POST /api/v2/orgs`` -- only ``name`` is required."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=_NAME_MAX)
    description: str = Field("", max_length=_DESCRIPTION_MAX)
    icon: str = Field("", max_length=_SHORT_TEXT_MAX)
    core_business: str = Field("", max_length=_LONG_TEXT_MAX)
    workspace_dir: str = Field("", max_length=_LONG_TEXT_MAX)
    tags: list[str] = Field(default_factory=list)


class OrgPatch(BaseModel):
    """Body for ``PUT /api/v2/orgs/{id}`` (``None`` means "leave alone").

    smoke-B3: the v2 frontend ``OrgEditorView`` posts a full org
    snapshot on every save (see ``apps/setup-center/src/views/
    OrgEditorView.tsx`` ``buildSavePayload``), so this schema must
    accept the wire-stable subset of every editable Organization
    field, not just the 7 read-back metadata fields the original
    P9.7a-2b skeleton shipped.  Keeping ``extra="forbid"`` preserves
    the existing "typo guard" invariant exercised by
    ``test_b11_update_org_422_on_extra_field``.

    ``user_persona`` / ``nodes`` / ``edges`` ride as opaque
    ``dict[str, Any]`` / ``list[dict[str, Any]]`` containers --
    ``OrgManager.update`` already coerces them via
    ``UserPersona.from_dict`` / ``OrgNode.from_dict`` / ``OrgEdge
    .from_dict`` and validates the workbench-leaf invariant.
    """

    model_config = ConfigDict(extra="forbid")

    # Original 7 metadata fields (kept exactly as before).
    name: str | None = Field(None, min_length=1, max_length=_NAME_MAX)
    description: str | None = Field(None, max_length=_DESCRIPTION_MAX)
    icon: str | None = Field(None, max_length=_SHORT_TEXT_MAX)
    status: OrgStatus | None = None
    core_business: str | None = Field(None, max_length=_LONG_TEXT_MAX)
    workspace_dir: str | None = Field(None, max_length=_LONG_TEXT_MAX)
    tags: list[str] | None = None

    # smoke-B3 -- editable org-level configuration the frontend sends
    # on every save.  All fields are optional; ``None`` means the
    # caller wants to leave the existing value alone.
    user_persona: dict[str, Any] | None = None
    operation_mode: str | None = None
    layout_locked: bool | None = None
    auto_persist_final_answer: bool | None = None
    watchdog_enabled: bool | None = None
    watchdog_interval_s: int | None = None
    watchdog_stuck_threshold_s: int | None = None
    watchdog_silence_threshold_s: int | None = None
    runtime_overrides: dict[str, Any] | None = None
    heartbeat_enabled: bool | None = None
    heartbeat_interval_s: int | None = None
    standup_enabled: bool | None = None

    # smoke-B3 -- nested graph (nodes + edges) full-replace shape.
    # OrgManager.update merges these key-wise: existing node ids keep
    # their identity, missing ids are inserted, dangling ids are
    # dropped.  Edges go through OrgEdge.from_dict with a
    # ``source != target`` guard.
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
