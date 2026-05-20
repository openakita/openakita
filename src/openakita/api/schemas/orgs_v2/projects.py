"""Project-level wire shapes (P9.7a-2b skeleton).

Mirrors ``runtime.orgs.project_models.OrgProject`` / ``ProjectTask``;
tasks ride as opaque dicts in the project envelope.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Project",
    "ProjectCreate",
    "ProjectPatch",
    "ProjectStatus",
    "ProjectType",
    "TaskStatus",
]


class ProjectType(StrEnum):
    """Parity with ``project_models.ProjectType``."""

    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class ProjectStatus(StrEnum):
    """Parity with ``project_models.ProjectStatus``."""

    PLANNING = "planning"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    """Parity with ``project_models.TaskStatus``."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class Project(BaseModel):
    """Read shape for ``GET /api/v2/orgs/{id}/projects/{pid}``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    org_id: str
    name: str
    description: str = ""
    project_type: ProjectType = ProjectType.TEMPORARY
    status: ProjectStatus = ProjectStatus.PLANNING
    owner_node_id: str | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class ProjectCreate(BaseModel):
    """Body for ``POST /api/v2/orgs/{id}/projects`` -- ``name`` required."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = ""
    project_type: ProjectType = ProjectType.TEMPORARY
    owner_node_id: str | None = None


class ProjectPatch(BaseModel):
    """Body for ``PUT /api/v2/orgs/{id}/projects/{pid}`` -- partial merge."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    project_type: ProjectType | None = None
    status: ProjectStatus | None = None
    owner_node_id: str | None = None
