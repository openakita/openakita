"""Core data models for the v2 runtime.

This module is the leaf of the runtime layering described in ADR-0002:
no internal imports, no I/O, no async. Every type here is either a plain
dataclass or a ``StrEnum``. Higher-level modules (``state_graph``,
``supervisor``, ``messenger``, ``facade``) build on these.

Two flavours of dataclass live here:

* **Spec types** (``frozen=True``) describe the *declared shape* of an
  organization: what nodes exist, what they connect to, what defaults
  apply. Spec types are produced by template authors (ADR-0008) and
  serialised to JSON when stored.
* **Live types** (mutable) describe a *running* organization at a moment
  in time: their ``status`` mutates, their ``runtime_overrides`` may be
  patched, their internal counters move. The supervisor and node runtime
  read and write these.

Spec types and live types share field names where they overlap, so a
spec can be lifted into a live record by copy and a live record can be
distilled back into a spec by projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

OrgId = str
NodeId = str
EdgeId = str
CommandId = str


def _new_id(prefix: str) -> str:
    """Return a short, prefixed identifier suitable for v2 records.

    We use UUID4 hex truncated to 12 chars because the pre-v2 schema used
    similar widths and the runtime never needs lexicographic ordering on
    these ids (ULIDs are reserved for checkpoints — see ADR-0005).
    """
    return f"{prefix}_{uuid4().hex[:12]}"


def _utc_now() -> datetime:
    """Single source of truth for ``datetime.now`` calls in the runtime."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OrgStatus(StrEnum):
    """High-level lifecycle of a v2 organization.

    Refined from the pre-v2 ``OrgStatus`` to drop ``DORMANT`` (replaced by
    explicit pause/resume) and ``ARCHIVED`` (handled by the data
    migration policy in ADR-0010).
    """

    CREATED = "created"
    ACTIVE = "active"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class NodeType(StrEnum):
    """Discriminator for node implementations defined in ADR-0007."""

    LLM = "llm"
    WORKBENCH = "workbench"
    TOOL = "tool"
    CONDITION = "condition"
    HUMAN_REVIEW = "human_review"


class NodeStatus(StrEnum):
    """Per-node lifecycle state for the v2 runtime.

    Replaces the pre-v2 ``NodeStatus`` enum. ``SUSPECT`` is the new
    middle state introduced by the supervisor's stall detector
    (ADR-0004): a node that is busy but has not produced a stream
    update for the configured suspect window.
    """

    CREATED = "created"
    IDLE = "idle"
    BUSY = "busy"
    SUSPECT = "suspect"
    CANCELLED = "cancelled"
    ERROR = "error"
    OFFLINE = "offline"


class EdgeKind(StrEnum):
    """Edge semantics carried over from the pre-v2 schema, narrowed."""

    HIERARCHY = "hierarchy"
    COLLABORATE = "collaborate"
    ESCALATE = "escalate"
    CONSULT = "consult"
    ARTIFACT = "artifact"


class TaskLifecycleState(StrEnum):
    """Task-level lifecycle, independent of node status.

    Defined here so the supervisor (ADR-0004) and the messenger
    (ADR-0007 ``NodeContext``) share a single vocabulary.
    """

    RECEIVED = "received"
    PLANNING = "planning"
    WAITING_DEPS = "waiting_deps"
    EXECUTING = "executing"
    STALLED = "stalled"
    REPLANNING = "replanning"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Workbench binding (ADR-0009)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkbenchBinding:
    """Connector between a ``NodeSpec`` and a plugin's ``WORKBENCH`` manifest.

    A node with ``type=workbench`` MUST carry a binding. The
    ``capabilities`` field is an optional subset selector; when ``None``
    the node inherits every capability the manifest declares for the
    chosen mode.
    """

    plugin_id: str
    mode: str
    capabilities: tuple[str, ...] | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "mode": self.mode,
            "capabilities": list(self.capabilities) if self.capabilities is not None else None,
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> WorkbenchBinding:
        caps = data.get("capabilities")
        return cls(
            plugin_id=data["plugin_id"],
            mode=data["mode"],
            capabilities=tuple(caps) if caps is not None else None,
        )


# ---------------------------------------------------------------------------
# Runtime overrides
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeRuntimeOverrides:
    """Per-node runtime knobs supported by the v2 supervisor.

    The pre-v2 runtime had a free-form dict here; we replace it with a
    closed set of fields because that is the contract reviewed by the
    supervisor. Unknown keys deserialised from disk are *dropped*, which
    is intentional — it stops deprecated knobs like
    ``max_task_seconds`` from re-entering the system silently.
    """

    max_iterations: int | None = None  # caps reasoning loop turns
    max_turns: int | None = None  # supervisor inner-loop cap (per command)
    max_stalls: int | None = None  # per-command stall threshold
    suspect_secs: int | None = None  # node SUSPECT promotion window
    persona_overlay: str | None = None  # extra prompt fragment

    _ALLOWED_KEYS = frozenset(
        {
            "max_iterations",
            "max_turns",
            "max_stalls",
            "suspect_secs",
            "persona_overlay",
        }
    )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "max_iterations": self.max_iterations,
            "max_turns": self.max_turns,
            "max_stalls": self.max_stalls,
            "suspect_secs": self.suspect_secs,
            "persona_overlay": self.persona_overlay,
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any] | None) -> NodeRuntimeOverrides:
        if not data:
            return cls()
        clean = {k: v for k, v in data.items() if k in cls._ALLOWED_KEYS}
        return cls(**clean)


# ---------------------------------------------------------------------------
# Org-level defaults (echoed in template DefaultsSpec, ADR-0008)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DefaultsSpec:
    """Org-wide defaults applied to every command unless overridden.

    Mirrors the template-side ``DefaultsSpec`` from ADR-0008 so a
    template can be lifted into an org without translation.
    """

    max_turns: int = 30
    max_stalls: int = 3
    suspect_secs: int = 90
    stream_channels: tuple[str, ...] = (
        "values",
        "updates",
        "tasks",
        "checkpoints",
        "messages",
        "progress_ledger",
        "lifecycle",
    )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "max_stalls": self.max_stalls,
            "suspect_secs": self.suspect_secs,
            "stream_channels": list(self.stream_channels),
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any] | None) -> DefaultsSpec:
        if not data:
            return cls()
        return cls(
            max_turns=int(data.get("max_turns", 30)),
            max_stalls=int(data.get("max_stalls", 3)),
            suspect_secs=int(data.get("suspect_secs", 90)),
            stream_channels=tuple(
                data.get(
                    "stream_channels",
                    cls.__dataclass_fields__["stream_channels"].default,
                )
            ),
        )


# ---------------------------------------------------------------------------
# Live node / edge / org records
# ---------------------------------------------------------------------------


@dataclass
class NodeV2:
    """A live node within a v2 organization.

    The ``id`` is opaque, ``role`` is a human-readable function name
    (``art_director`` / ``image_artist`` / ``support_agent``), and
    ``label`` is the user-facing display name.

    ``last_seen`` and ``last_progress_at`` power the ``SUSPECT``
    promotion described in ADR-0007. They default to ``None`` for a
    freshly created node and are bumped by stream-emit hooks.
    """

    id: NodeId
    org_id: OrgId
    type: NodeType
    role: str
    label: str
    persona_prompt: str | None = None
    tool_subset: tuple[str, ...] | None = None
    workbench: WorkbenchBinding | None = None
    runtime_overrides: NodeRuntimeOverrides = field(default_factory=NodeRuntimeOverrides)
    parent_id: NodeId | None = None
    # Org-chart department (元数据 mirrored from the template's NodeSpec).
    # Defaults to "" so older persisted payloads keep deserialising.
    department: str = ""
    status: NodeStatus = NodeStatus.CREATED
    last_seen: datetime | None = None
    last_progress_at: datetime | None = None
    created_at: datetime = field(default_factory=_utc_now)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "type": self.type.value,
            "role": self.role,
            "label": self.label,
            "persona_prompt": self.persona_prompt,
            "tool_subset": list(self.tool_subset) if self.tool_subset is not None else None,
            "workbench": self.workbench.to_jsonable() if self.workbench else None,
            "runtime_overrides": self.runtime_overrides.to_jsonable(),
            "parent_id": self.parent_id,
            "department": self.department,
            "status": self.status.value,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "last_progress_at": (
                self.last_progress_at.isoformat() if self.last_progress_at else None
            ),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> NodeV2:
        ts = data.get("tool_subset")
        wb = data.get("workbench")
        return cls(
            id=data["id"],
            org_id=data["org_id"],
            type=NodeType(data["type"]),
            role=data["role"],
            label=data["label"],
            persona_prompt=data.get("persona_prompt"),
            tool_subset=tuple(ts) if ts is not None else None,
            workbench=WorkbenchBinding.from_jsonable(wb) if wb else None,
            runtime_overrides=NodeRuntimeOverrides.from_jsonable(data.get("runtime_overrides")),
            parent_id=data.get("parent_id"),
            department=data.get("department", ""),
            status=NodeStatus(data.get("status", NodeStatus.CREATED.value)),
            last_seen=datetime.fromisoformat(data["last_seen"]) if data.get("last_seen") else None,
            last_progress_at=(
                datetime.fromisoformat(data["last_progress_at"])
                if data.get("last_progress_at")
                else None
            ),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


@dataclass(frozen=True)
class EdgeV2:
    """A directed relationship between two nodes.

    Frozen because edges are part of an org's structure and rarely
    mutate; structural change is a deliberate operation that creates a
    new ``EdgeV2`` value.
    """

    id: EdgeId
    org_id: OrgId
    src: NodeId
    dst: NodeId
    kind: EdgeKind = EdgeKind.HIERARCHY
    binding: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind.value,
            "binding": dict(self.binding),
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> EdgeV2:
        return cls(
            id=data["id"],
            org_id=data["org_id"],
            src=data["src"],
            dst=data["dst"],
            kind=EdgeKind(data.get("kind", EdgeKind.HIERARCHY.value)),
            binding=dict(data.get("binding") or {}),
        )


@dataclass
class OrgV2:
    """A live v2 organization."""

    id: OrgId
    name: str
    template_id: str | None = None
    description: str | None = None
    nodes: list[NodeV2] = field(default_factory=list)
    edges: list[EdgeV2] = field(default_factory=list)
    defaults: DefaultsSpec = field(default_factory=DefaultsSpec)
    status: OrgStatus = OrgStatus.CREATED
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def get_node(self, node_id: NodeId) -> NodeV2 | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def root_nodes(self) -> list[NodeV2]:
        return [n for n in self.nodes if n.parent_id is None]

    def children_of(self, node_id: NodeId) -> list[NodeV2]:
        return [n for n in self.nodes if n.parent_id == node_id]

    # ------------------------------------------------------------------
    # JSON serialisation
    # ------------------------------------------------------------------

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "template_id": self.template_id,
            "description": self.description,
            "nodes": [n.to_jsonable() for n in self.nodes],
            "edges": [e.to_jsonable() for e in self.edges],
            "defaults": self.defaults.to_jsonable(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> OrgV2:
        return cls(
            id=data["id"],
            name=data["name"],
            template_id=data.get("template_id"),
            description=data.get("description"),
            nodes=[NodeV2.from_jsonable(n) for n in data.get("nodes", [])],
            edges=[EdgeV2.from_jsonable(e) for e in data.get("edges", [])],
            defaults=DefaultsSpec.from_jsonable(data.get("defaults")),
            status=OrgStatus(data.get("status", OrgStatus.CREATED.value)),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


# ---------------------------------------------------------------------------
# Identifier minting helpers (kept here to avoid sprinkling uuid imports)
# ---------------------------------------------------------------------------


def new_org_id() -> OrgId:
    return _new_id("org")


def new_node_id() -> NodeId:
    return _new_id("node")


def new_edge_id() -> EdgeId:
    return _new_id("edge")


def new_command_id() -> CommandId:
    return _new_id("cmd")
