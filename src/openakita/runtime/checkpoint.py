"""Checkpoint contract for the v2 runtime.

Implements ADR-0005: a single transactional snapshot taken at well
defined points (end of each supervisor turn, on accepted deliverable,
on cancel) that lets us resume execution exactly where it left off,
including TaskLedger, ProgressLedger history, stall counter, channel
state, and per-node state.

This module hosts only the *protocol* and supporting record types. The
backends live next to it under ``runtime/backends/`` so each backend
can own its own dependencies.

The checkpoint envelope is **versioned canonical JSON**:

    {
        "$schema_version": 1,
        "supervisor": {...},
        "channels":   {...},
        "nodes":      {...}
    }

Backends store the envelope verbatim; loaders dispatch on
``$schema_version`` and refuse unknown versions with a clear diagnosis.
"""

from __future__ import annotations

import json
import zlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "BaseCheckpointer",
    "Checkpoint",
    "CheckpointMetadata",
    "CheckpointStatus",
    "CheckpointSchemaError",
    "CheckpointId",
    "CommandId",
    "CHECKPOINT_SCHEMA_VERSION",
    "encode_state",
    "decode_state",
]


CheckpointId = str  # ULID, lexicographically sortable
CommandId = str

CHECKPOINT_SCHEMA_VERSION = 1


class CheckpointStatus(StrEnum):
    """Status field on every checkpoint metadata record."""

    RUNNING = "running"
    INTERRUPTED = "interrupted"
    DONE = "done"
    OUT_OF_STEPS = "out_of_steps"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CheckpointSchemaError(Exception):
    """Raised when a stored envelope's $schema_version cannot be loaded."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckpointMetadata:
    checkpoint_id: CheckpointId
    parent_id: CheckpointId | None
    command_id: CommandId
    org_id: str
    superstep: int
    status: CheckpointStatus
    n_stalls: int
    n_turns: int
    created_at: datetime

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "parent_id": self.parent_id,
            "command_id": self.command_id,
            "org_id": self.org_id,
            "superstep": self.superstep,
            "status": self.status.value,
            "n_stalls": self.n_stalls,
            "n_turns": self.n_turns,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> CheckpointMetadata:
        return cls(
            checkpoint_id=data["checkpoint_id"],
            parent_id=data.get("parent_id"),
            command_id=data["command_id"],
            org_id=data["org_id"],
            superstep=int(data["superstep"]),
            status=CheckpointStatus(data["status"]),
            n_stalls=int(data["n_stalls"]),
            n_turns=int(data["n_turns"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


@dataclass(frozen=True)
class Checkpoint:
    """A snapshot of runtime state at a supervisor turn boundary."""

    metadata: CheckpointMetadata
    state: dict[str, Any]
    pending_writes: list[dict[str, Any]] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_jsonable(),
            "state": dict(self.state),
            "pending_writes": list(self.pending_writes),
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> Checkpoint:
        return cls(
            metadata=CheckpointMetadata.from_jsonable(data["metadata"]),
            state=dict(data.get("state", {})),
            pending_writes=list(data.get("pending_writes", [])),
        )


# ---------------------------------------------------------------------------
# Envelope encoding
# ---------------------------------------------------------------------------


def _ensure_envelope(state: dict[str, Any]) -> dict[str, Any]:
    """Add $schema_version if absent; pass other envelopes through."""
    if "$schema_version" not in state:
        return {"$schema_version": CHECKPOINT_SCHEMA_VERSION, **state}
    return state


def encode_state(state: dict[str, Any]) -> bytes:
    """Serialise ``state`` into a compact, optionally compressed JSON blob.

    Used by the SQLite backend; exposed as a public helper so other
    backends and tests can produce identical bytes.
    """
    envelope = _ensure_envelope(state)
    raw = json.dumps(envelope, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return zlib.compress(raw, level=6)


def decode_state(blob: bytes) -> dict[str, Any]:
    """Reverse :func:`encode_state`."""
    raw = zlib.decompress(blob)
    payload = json.loads(raw.decode("utf-8"))
    version = payload.get("$schema_version")
    if version is None:
        raise CheckpointSchemaError(
            "checkpoint envelope is missing $schema_version; "
            "this store is from a pre-v2 runtime and cannot be loaded"
        )
    if version != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointSchemaError(
            f"checkpoint envelope has $schema_version={version}; "
            f"this runtime supports {CHECKPOINT_SCHEMA_VERSION}. "
            "Run scripts/migrate_checkpoints.py to upgrade."
        )
    return payload


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class BaseCheckpointer(ABC):
    """Abstract checkpoint storage protocol.

    Backends implement the four async methods plus
    :meth:`adelete_command`. A backend MUST be safe to share across
    multiple concurrent commands; per-command serialisation is the
    supervisor's responsibility (one supervisor per command, see
    ADR-0004).
    """

    @abstractmethod
    async def aput(self, checkpoint: Checkpoint) -> CheckpointMetadata: ...

    @abstractmethod
    async def aget(self, checkpoint_id: CheckpointId) -> Checkpoint | None: ...

    @abstractmethod
    async def aget_latest(self, command_id: CommandId) -> Checkpoint | None: ...

    @abstractmethod
    def alist(
        self,
        command_id: CommandId,
        *,
        limit: int = 64,
    ) -> AsyncIterator[CheckpointMetadata]: ...

    @abstractmethod
    async def adelete_command(self, command_id: CommandId) -> int: ...

    # ------------------------------------------------------------------
    # Default close hook; concrete backends may override.
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# In-memory backend (tests + dev)
# ---------------------------------------------------------------------------


class MemoryCheckpointer(BaseCheckpointer):
    """In-process dict backend.

    Used by tests, smoke runs, and the parity harness. It validates
    envelopes the same way the SQLite backend does so test failures
    catch schema regressions before they can hit production storage.
    """

    def __init__(self) -> None:
        self._by_id: dict[CheckpointId, Checkpoint] = {}
        self._by_command: dict[CommandId, list[CheckpointId]] = {}
        self._counter = 0

    async def aput(self, checkpoint: Checkpoint) -> CheckpointMetadata:
        # Round-trip the state through encode/decode so we exercise the
        # same schema validation path as the SQLite backend, and store
        # the *normalised* envelope (with $schema_version) so reads
        # match the persistent backends byte for byte.
        normalised_state = decode_state(encode_state(checkpoint.state))
        normalised = Checkpoint(
            metadata=checkpoint.metadata,
            state=normalised_state,
            pending_writes=list(checkpoint.pending_writes),
        )
        self._by_id[checkpoint.metadata.checkpoint_id] = normalised
        self._by_command.setdefault(
            checkpoint.metadata.command_id, []
        ).append(checkpoint.metadata.checkpoint_id)
        self._counter += 1
        return checkpoint.metadata

    async def aget(self, checkpoint_id: CheckpointId) -> Checkpoint | None:
        return self._by_id.get(checkpoint_id)

    async def aget_latest(self, command_id: CommandId) -> Checkpoint | None:
        ids = self._by_command.get(command_id, [])
        if not ids:
            return None
        return self._by_id.get(ids[-1])

    async def alist(
        self,
        command_id: CommandId,
        *,
        limit: int = 64,
    ) -> AsyncIterator[CheckpointMetadata]:
        ids = list(reversed(self._by_command.get(command_id, [])))[:limit]
        for cid in ids:
            ck = self._by_id.get(cid)
            if ck is not None:
                yield ck.metadata

    async def adelete_command(self, command_id: CommandId) -> int:
        ids = self._by_command.pop(command_id, [])
        for cid in ids:
            self._by_id.pop(cid, None)
        return len(ids)

    # Convenience for tests
    def total(self) -> int:
        return len(self._by_id)


# ---------------------------------------------------------------------------
# Helpers used by backends
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_checkpoint_id() -> CheckpointId:
    """Return a ULID-like, lexicographically sortable id.

    We avoid pulling in a third-party ULID dependency at this layer.
    The id is ``<unix_ms_padded_13>_<uuid4_hex_8>`` which sorts by time
    while remaining unique under high contention.
    """
    from uuid import uuid4

    ms = int(datetime.now(UTC).timestamp() * 1000)
    return f"{ms:013d}_{uuid4().hex[:8]}"
