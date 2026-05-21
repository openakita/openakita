"""Project + task models for the v2 ProjectStore (P-RC-9 P9.2).

Duplicates v1 ``openakita.orgs.models.OrgProject`` /
``ProjectTask`` (+ ``ProjectType`` / ``ProjectStatus`` /
``TaskStatus`` enums) under the v2 namespace so
``runtime/orgs/project_store.py`` has zero ``openakita.orgs.*``
imports (P-RC-9-PLAN section 0.3 invariant) and v1 can be
deleted wholesale at P9.9. Parity is enforced byte-for-byte
via ``to_dict()`` round-trip; the dataclass type identity
intentionally differs across the namespace split.

ID minting switches from v1''s ``uuid.uuid4().hex[:12]`` to a
ULID-style prefix (``<13-digit ms timestamp>_<10 hex random>``)
so v2 IDs are loosely chronologically sortable. v1 vs v2
parity tests therefore ignore the ``id`` / ``project_id`` /
``parent_task_id`` fields and assert structural equality
(P-RC-9-PLAN section 5.2 ProjectStore ignore set).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

__all__ = [
    "OrgProject",
    "ProjectStatus",
    "ProjectTask",
    "ProjectType",
    "TaskStatus",
    "new_project_id",
    "new_task_id",
    "now_iso",
]


def now_iso() -> str:
    """ISO-8601 UTC timestamp string; mirrors v1 ``_now_iso``."""
    return datetime.now(UTC).isoformat()


def _ulid_like(prefix: str) -> str:
    """Mint a ULID-style id: ``<prefix><13-digit ms>_<10 hex>``.

    Loosely sortable across runs (timestamp prefix) and unique
    within a millisecond (10 hex chars = 40 random bits ->
    collision probability << 1e-9 per ms even at 10k inserts/s).
    The prefix-bearing format matches v1''s convention of
    ``<kind>_<random>`` so opaque-id callers see no change.
    """
    ts_ms = int(time.time() * 1000)
    rand = secrets.token_hex(5)  # 10 hex chars
    return f"{prefix}{ts_ms:013d}_{rand}"


def new_project_id() -> str:
    """Mint a fresh project id."""
    return _ulid_like("proj_")


def new_task_id() -> str:
    """Mint a fresh task id."""
    return _ulid_like("task_")


class ProjectType(StrEnum):
    """Project lifetime kind; matches v1 enum values verbatim."""

    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class ProjectStatus(StrEnum):
    """Project lifecycle status; matches v1 enum values verbatim."""

    PLANNING = "planning"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    """Task lifecycle status; matches v1 enum values verbatim."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class ProjectTask:
    """v2 project task; ``to_dict`` shape matches v1 byte-for-byte."""

    id: str = field(default_factory=new_task_id)
    project_id: str = ""
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    assignee_node_id: str | None = None
    delegated_by: str | None = None
    chain_id: str | None = None
    parent_task_id: str | None = None
    depth: int = 0
    plan_steps: list = field(default_factory=list)
    execution_log: list = field(default_factory=list)
    priority: int = 0
    progress_pct: int = 0
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    delivered_at: str | None = None
    completed_at: str | None = None
    deliverable_content: str = ""
    delivery_summary: str = ""
    file_attachments: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        st = self.status.value if hasattr(self.status, "value") else str(self.status)
        d: dict = {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "status": st,
            "assignee_node_id": self.assignee_node_id,
            "delegated_by": self.delegated_by,
            "chain_id": self.chain_id,
            "parent_task_id": self.parent_task_id,
            "depth": self.depth,
            "plan_steps": list(self.plan_steps) if self.plan_steps else [],
            "execution_log": list(self.execution_log) if self.execution_log else [],
            "priority": self.priority,
            "progress_pct": self.progress_pct,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "delivered_at": self.delivered_at,
            "completed_at": self.completed_at,
            "deliverable_content": self.deliverable_content,
            "delivery_summary": self.delivery_summary,
        }
        if self.file_attachments:
            d["file_attachments"] = list(self.file_attachments)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ProjectTask:
        d = dict(d)
        if "status" in d and isinstance(d["status"], str):
            raw = d["status"]
            if "." in raw:
                raw = raw.rsplit(".", 1)[-1].lower()
            try:
                d["status"] = TaskStatus(raw)
            except ValueError:
                d["status"] = TaskStatus.TODO
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OrgProject:
    """v2 project; ``to_dict`` shape matches v1 byte-for-byte."""

    id: str = field(default_factory=new_project_id)
    org_id: str = ""
    name: str = ""
    description: str = ""
    project_type: ProjectType = ProjectType.TEMPORARY
    status: ProjectStatus = ProjectStatus.PLANNING
    owner_node_id: str | None = None
    tasks: list[ProjectTask] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    completed_at: str | None = None

    def to_dict(self) -> dict:
        def _enum_val(v: object) -> str:
            if hasattr(v, "value"):
                return v.value
            if isinstance(v, str) and "." in v:
                return v.rsplit(".", 1)[-1].lower()
            return str(v)

        return {
            "id": self.id,
            "org_id": self.org_id,
            "name": self.name,
            "description": self.description,
            "project_type": _enum_val(self.project_type),
            "status": _enum_val(self.status),
            "owner_node_id": self.owner_node_id,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrgProject:
        d = dict(d)
        if "project_type" in d and isinstance(d["project_type"], str):
            raw_pt = d["project_type"]
            if "." in raw_pt:
                raw_pt = raw_pt.rsplit(".", 1)[-1].lower()
            try:
                d["project_type"] = ProjectType(raw_pt)
            except ValueError:
                d["project_type"] = ProjectType.TEMPORARY
        if "status" in d and isinstance(d["status"], str):
            raw_st = d["status"]
            if "." in raw_st:
                raw_st = raw_st.rsplit(".", 1)[-1].lower()
            try:
                d["status"] = ProjectStatus(raw_st)
            except ValueError:
                d["status"] = ProjectStatus.PLANNING
        raw_tasks = d.pop("tasks", [])
        proj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        proj.tasks = [ProjectTask.from_dict(t) for t in raw_tasks]
        return proj
