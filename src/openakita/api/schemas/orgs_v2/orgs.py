"""Org-level wire shapes (P9.7a-2b skeleton).

Mirrors the wire-stable subset of ``openakita.orgs.models.Organization``;
nested nodes / edges ride as opaque dicts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Org", "OrgCreate", "OrgPatch", "OrgStatus"]


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

    name: str = Field(..., min_length=1)
    description: str = ""
    icon: str = ""
    core_business: str = ""
    workspace_dir: str = ""
    tags: list[str] = Field(default_factory=list)


class OrgPatch(BaseModel):
    """Body for ``PUT /api/v2/orgs/{id}`` (``None`` means "leave alone")."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    status: OrgStatus | None = None
    core_business: str | None = None
    workspace_dir: str | None = None
    tags: list[str] | None = None
