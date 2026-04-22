"""Checkpoint — capture/restore state before high-cost stages.

When a plugin is about to enter an expensive stage (multi-LLM generation,
ffmpeg encode batch, vendor video render), we want to:

1. **Snapshot** — capture cost ledger + free-form per-stage extras *before*
   the stage starts.  Cheap (no I/O), JSON-safe.
2. **Persist** (optional) — caller serialises ``snapshot.to_dict()`` to its
   own task DB row so a process restart can resume.
3. **Restore** — on stage failure, replay the snapshot back into the cost
   tracker so future ``reserve()`` calls do not see leaked reservations.

Pairing rationale:

* Without checkpointing, a partial failure leaves the cost ledger in a
  hybrid state (some calls reserved-but-never-reconciled, some
  committed).  Future budget gates then mis-fire.
* Without an explicit *high-cost stage* concept, the user has no clean
  way to see "we just spent ¥4.20 on the generate step" and decide
  whether to retry vs. abort.

Design rules:

* Pure stdlib + asyncio.  No persistence layer here — that is the
  caller's job (and they can use any DB they like).
* Single canonical home for snapshot/restore wiring; never copy.
* Inter-op only with :class:`cost_tracker.CostTracker`; ``extra`` is an
  opaque dict so plugins can carry their own per-stage state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .cost_tracker import CostSnapshot, CostTracker

__all__ = [
    "Checkpoint",
    "restore_from_snapshot",
    "take_checkpoint",
]


@dataclass(frozen=True)
class Checkpoint:
    """Point-in-time bundle: stage label + cost snapshot + extras.

    ``extra`` is for caller-defined per-stage state that should round-trip
    through persistence (e.g. shot list IDs already committed, partial
    output paths).  Keep it JSON-serialisable.
    """

    name: str
    taken_at: float
    cost_snapshot: CostSnapshot | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "taken_at": self.taken_at,
            "cost_snapshot": (
                self.cost_snapshot.to_dict() if self.cost_snapshot else None
            ),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Checkpoint:
        """Round-trip helper; tolerates older payloads with missing keys."""
        snapshot_raw = raw.get("cost_snapshot")
        snapshot: CostSnapshot | None = None
        if snapshot_raw:
            snapshot = CostSnapshot(
                taken_at=float(snapshot_raw.get("taken_at", time.time())),
                currency=str(snapshot_raw.get("currency", "CNY")),
                total_budget=snapshot_raw.get("total_budget"),
                single_call_threshold=float(
                    snapshot_raw.get("single_call_threshold", 0.0)
                ),
                entries=list(snapshot_raw.get("entries") or []),
            )
        return cls(
            name=str(raw.get("name", "")),
            taken_at=float(raw.get("taken_at", time.time())),
            cost_snapshot=snapshot,
            extra=dict(raw.get("extra") or {}),
        )


def take_checkpoint(
    name: str,
    *,
    cost_tracker: CostTracker | None = None,
    extra: dict[str, Any] | None = None,
) -> Checkpoint:
    """Capture a checkpoint right before entering a high-cost stage.

    Synchronous (read-only against the tracker) — safe to call from inside
    ``async`` workers without awaiting.  The returned ``Checkpoint`` is
    JSON-safe via ``to_dict()``.

    Args:
        name: Stage name (e.g. ``"generate-images"``, ``"render-video"``).
            Surfaces in error reports so users can tell which stage failed.
        cost_tracker: Optional tracker to snapshot.  Pass ``None`` if the
            plugin does not use cost accounting yet.
        extra: Free-form per-stage state.  Must be JSON-serialisable for
            ``to_dict()`` to round-trip.
    """
    if not name:
        raise ValueError("checkpoint name must be non-empty")
    snapshot = cost_tracker.snapshot() if cost_tracker is not None else None
    return Checkpoint(
        name=name,
        taken_at=time.time(),
        cost_snapshot=snapshot,
        extra=dict(extra or {}),
    )


async def restore_from_snapshot(
    checkpoint: Checkpoint,
    *,
    cost_tracker: CostTracker | None = None,
) -> None:
    """Replay ``checkpoint`` back into the supplied tracker.

    Used in ``except`` blocks of high-cost stages to undo any leaked
    reservations.  No-op when the checkpoint carried no cost snapshot or
    no tracker is supplied.

    Args:
        checkpoint: A previously-taken :class:`Checkpoint`.  Re-loaded
            from JSON via ``Checkpoint.from_dict`` works too.
        cost_tracker: The tracker to restore.  Typically the same
            instance passed to :func:`take_checkpoint`.
    """
    if cost_tracker is None or checkpoint.cost_snapshot is None:
        return
    await cost_tracker.restore(checkpoint.cost_snapshot)
