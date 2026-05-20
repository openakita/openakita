"""Node-level wire shapes (P9.7a-2b skeleton).

Mirrors the wire-stable subset of ``openakita.orgs.models.OrgNode``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Node", "NodeRegister", "NodeStatus"]


class NodeStatus(StrEnum):
    """Parity with ``orgs.models.NodeStatus``."""

    IDLE = "idle"
    BUSY = "busy"
    WAITING = "waiting"
    ERROR = "error"
    OFFLINE = "offline"
    FROZEN = "frozen"


class Node(BaseModel):
    """Read shape for ``GET /api/v2/orgs/{id}/nodes/{node_id}/status``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    role_title: str = ""
    role_goal: str = ""
    role_backstory: str = ""
    agent_profile_id: str | None = None
    department: str = ""
    level: int = 0
    avatar: str | None = None
    skills: list[str] = Field(default_factory=list)
    external_tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    status: NodeStatus = NodeStatus.IDLE
    position: dict[str, Any] = Field(default_factory=dict)
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class NodeRegister(BaseModel):
    """Body for POST node-create endpoints; only ``role_title`` required."""

    model_config = ConfigDict(extra="forbid")

    role_title: str = Field(..., min_length=1)
    role_goal: str = ""
    role_backstory: str = ""
    agent_profile_id: str | None = None
    department: str = ""
    level: int = 0
    avatar: str | None = None
    skills: list[str] = Field(default_factory=list)
    external_tools: list[str] = Field(default_factory=list)
