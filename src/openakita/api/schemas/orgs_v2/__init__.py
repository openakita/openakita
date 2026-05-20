"""``orgs_v2`` Pydantic shapes skeleton (P-RC-9 P9.7a-2b).

7 enums + 12 models across 4 sub-modules. All models pin
``model_config = ConfigDict(extra="forbid")`` so unknown fields
raise 422 (v1 silently dropped them; charter R5 mitigation).
Endpoint bodies in P9.7a-3+ consume these shapes; the skeleton
is intentionally narrow (only wire-stable fields), with nested
nodes / edges / tasks left as ``dict[str, Any]`` so the
frontend payload format is unchanged. ADR refs: ADR-0011 (D-3
layer separation), ADR-0012 (no shim under v1).
"""

from __future__ import annotations

from .commands import CancelRequest, CommandSnapshot, CommandSubmit
from .nodes import Node, NodeRegister, NodeStatus
from .orgs import Org, OrgCreate, OrgPatch, OrgStatus
from .projects import (
    Project,
    ProjectCreate,
    ProjectPatch,
    ProjectStatus,
    ProjectType,
    TaskStatus,
)

__all__ = [
    "CancelRequest",
    "CommandSnapshot",
    "CommandSubmit",
    "Node",
    "NodeRegister",
    "NodeStatus",
    "Org",
    "OrgCreate",
    "OrgPatch",
    "OrgStatus",
    "Project",
    "ProjectCreate",
    "ProjectPatch",
    "ProjectStatus",
    "ProjectType",
    "TaskStatus",
]
