"""CostTracker — runtime cost accounting (reserve / reconcile / refund).

Complements :mod:`cost_estimator` (which produces a *pre-flight* preview)
by tracking what actually happens during execution:

* ``reserve(key, amount)`` — freeze a slice of budget *before* a costly
  call (e.g. before launching an LLM batch).  Multiple reservations under
  the same ``key`` are rejected so retries do not double-count.
* ``reconcile(key, actual)`` — replace the reservation with the real
  amount once the call finishes (returns ``Adjustment`` so the caller can
  log the delta).
* ``refund(key)`` — drop the reservation entirely when the call fails or
  is cancelled.
* ``commit(key, amount)`` — record an unreserved actual charge (handy for
  cheap, post-hoc fees).
* ``requires_approval(amount)`` — single-call gate; lets the host pause
  and ask the user "this one call costs ¥12.40 — proceed?".
* ``snapshot()`` — produce a ``CostSnapshot`` that :mod:`checkpoint` can
  persist before a high-cost stage so we can roll back cleanly on error.

Design choices:

* **Pure Python, no extra deps**.  All state is in memory; persistence
  is the caller's responsibility (the ``snapshot()`` dict is JSON-safe).
* **asyncio-safe**: every public coroutine takes an internal
  ``asyncio.Lock`` so concurrent reservations under the same tracker
  cannot race.  Sync helpers (``requires_approval`` / ``snapshot`` /
  ``summary``) are read-only and lock-free.
* **No multi-path**: this file is the single canonical home; never copy
  reserve/reconcile semantics into individual plugins.
* **Vendor-agnostic units**: the tracker stores raw floats in a single
  ``currency`` (CNY / USD / "credit").  Unit conversion is handled by
  :func:`cost_estimator.to_human_units`.

Inspired by OpenMontage's ``cost_tracker.reserve / reconcile`` pattern
(``cost_tracker.py:40-175``), simplified for the plugin SDK context.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Adjustment",
    "ApprovalRequired",
    "CostEntry",
    "CostSnapshot",
    "CostSummary",
    "CostTracker",
    "DuplicateReservation",
    "InsufficientBudget",
    "ReservationNotFound",
]


class CostTrackerError(Exception):
    """Base class for cost-tracker errors so callers can ``except`` once."""


class DuplicateReservation(CostTrackerError):
    """Raised when ``reserve`` is called twice with the same ``key``."""


class ReservationNotFound(CostTrackerError):
    """Raised when ``reconcile`` / ``refund`` references an unknown key."""


class InsufficientBudget(CostTrackerError):
    """Raised when a reservation would push committed+reserved past budget."""


class ApprovalRequired(CostTrackerError):
    """Raised when a single charge exceeds ``single_call_threshold``.

    Callers are expected to surface this to the user (UI prompt or
    chat message) and only retry after explicit confirmation.
    """


@dataclass(frozen=True)
class CostEntry:
    """One row of the ledger (reservation OR commit)."""

    key: str
    amount: float
    label: str
    state: str          # "reserved" | "committed" | "refunded"
    created_at: float   # epoch seconds
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "amount": round(self.amount, 6),
            "label": self.label,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Adjustment:
    """Returned by ``reconcile`` so callers can log the delta cleanly."""

    key: str
    reserved: float
    actual: float
    delta: float          # actual - reserved (positive = over-spent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "reserved": round(self.reserved, 6),
            "actual": round(self.actual, 6),
            "delta": round(self.delta, 6),
        }


@dataclass(frozen=True)
class CostSummary:
    """Aggregate view (read from ``CostTracker.summary()``)."""

    currency: str
    reserved: float
    committed: float
    refunded: float
    total_budget: float | None
    remaining: float | None
    entry_count: int

    @property
    def in_flight(self) -> float:
        """Reserved + committed (i.e. money already on the line)."""
        return self.reserved + self.committed

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "reserved": round(self.reserved, 6),
            "committed": round(self.committed, 6),
            "refunded": round(self.refunded, 6),
            "in_flight": round(self.in_flight, 6),
            "total_budget": (
                round(self.total_budget, 6)
                if self.total_budget is not None
                else None
            ),
            "remaining": (
                round(self.remaining, 6) if self.remaining is not None else None
            ),
            "entry_count": self.entry_count,
        }


@dataclass(frozen=True)
class CostSnapshot:
    """Point-in-time copy of the ledger — used by :mod:`checkpoint`.

    Round-trippable via ``CostTracker.restore(snapshot)``: the receiving
    tracker drops its current state and replays the snapshot, so a
    failed high-cost stage can be rolled back without leaking
    reservations.
    """

    taken_at: float
    currency: str
    total_budget: float | None
    single_call_threshold: float
    entries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "taken_at": self.taken_at,
            "currency": self.currency,
            "total_budget": self.total_budget,
            "single_call_threshold": self.single_call_threshold,
            "entries": [dict(e) for e in self.entries],
        }


class CostTracker:
    """Reserve → reconcile / refund / commit ledger with optional budget cap.

    Example::

        tracker = CostTracker(
            currency="CNY",
            single_call_threshold=10.0,   # per-call approval prompt
            total_budget=200.0,           # session-wide cap
        )

        if tracker.requires_approval(estimated):
            ...prompt user...

        await tracker.reserve("llm-batch-1", estimated, label="Seedance 720p")
        try:
            actual = await run_call()
            adj = await tracker.reconcile("llm-batch-1", actual)
        except Exception:
            await tracker.refund("llm-batch-1")
            raise
    """

    def __init__(
        self,
        *,
        currency: str = "CNY",
        single_call_threshold: float = 0.0,
        total_budget: float | None = None,
    ) -> None:
        if single_call_threshold < 0:
            raise ValueError("single_call_threshold must be >= 0")
        if total_budget is not None and total_budget < 0:
            raise ValueError("total_budget must be >= 0 or None")
        self.currency = currency
        self.single_call_threshold = float(single_call_threshold)
        self.total_budget = (
            float(total_budget) if total_budget is not None else None
        )
        # Two ledgers: live (reserved | committed) vs archive (refunded).
        # Splitting prevents an O(N) scan on every summary() call.
        self._live: dict[str, CostEntry] = {}
        self._refunded: list[CostEntry] = []
        self._lock = asyncio.Lock()

    # ── inspection (sync, lock-free) ─────────────────────────────────

    def requires_approval(self, amount: float) -> bool:
        """``True`` if ``amount`` is over the per-call approval threshold.

        ``single_call_threshold == 0`` (default) disables the gate.  Negative
        amounts also return ``False`` so callers do not mis-prompt on a
        refund-style negative delta.
        """
        if self.single_call_threshold <= 0:
            return False
        return float(amount) > self.single_call_threshold

    def summary(self) -> CostSummary:
        reserved = sum(
            e.amount for e in self._live.values() if e.state == "reserved"
        )
        committed = sum(
            e.amount for e in self._live.values() if e.state == "committed"
        )
        refunded = sum(e.amount for e in self._refunded)
        remaining: float | None = None
        if self.total_budget is not None:
            remaining = self.total_budget - reserved - committed
        return CostSummary(
            currency=self.currency,
            reserved=reserved,
            committed=committed,
            refunded=refunded,
            total_budget=self.total_budget,
            remaining=remaining,
            entry_count=len(self._live) + len(self._refunded),
        )

    def get_entry(self, key: str) -> CostEntry | None:
        return self._live.get(key)

    def list_entries(self) -> list[CostEntry]:
        """Snapshot copy — safe to iterate while async ops continue."""
        return list(self._live.values()) + list(self._refunded)

    # ── snapshot / restore (sync, lock-free reads) ───────────────────

    def snapshot(self) -> CostSnapshot:
        """Capture a JSON-safe ledger snapshot for :mod:`checkpoint`."""
        return CostSnapshot(
            taken_at=time.time(),
            currency=self.currency,
            total_budget=self.total_budget,
            single_call_threshold=self.single_call_threshold,
            entries=[e.to_dict() for e in self.list_entries()],
        )

    async def restore(self, snapshot: CostSnapshot) -> None:
        """Replace internal state with ``snapshot``.

        Used by :mod:`checkpoint.restore_from_snapshot` to roll back after
        a failed high-cost stage.  Resets currency / budget / threshold
        too so a snapshot taken with different settings replays cleanly.
        """
        async with self._lock:
            self.currency = snapshot.currency
            self.total_budget = snapshot.total_budget
            self.single_call_threshold = snapshot.single_call_threshold
            self._live.clear()
            self._refunded.clear()
            for raw in snapshot.entries:
                entry = CostEntry(
                    key=str(raw["key"]),
                    amount=float(raw["amount"]),
                    label=str(raw.get("label", "")),
                    state=str(raw["state"]),
                    created_at=float(raw.get("created_at", time.time())),
                    updated_at=float(raw.get("updated_at", time.time())),
                )
                if entry.state == "refunded":
                    self._refunded.append(entry)
                else:
                    self._live[entry.key] = entry

    # ── mutation (async, lock-protected) ─────────────────────────────

    async def reserve(
        self,
        key: str,
        amount: float,
        *,
        label: str = "",
    ) -> CostEntry:
        """Freeze ``amount`` against the budget under ``key``.

        Raises:
            DuplicateReservation: ``key`` already has a live entry.
            InsufficientBudget: would push reserved+committed past
                ``total_budget``.  ``total_budget=None`` disables this check.
        """
        if amount < 0:
            raise ValueError("amount must be >= 0")
        async with self._lock:
            if key in self._live:
                raise DuplicateReservation(
                    f"reservation key already in use: {key!r}"
                )
            if self.total_budget is not None:
                projected = (
                    sum(e.amount for e in self._live.values()) + float(amount)
                )
                if projected > self.total_budget:
                    raise InsufficientBudget(
                        f"reserve {amount} would exceed budget "
                        f"{self.total_budget} (projected={projected})"
                    )
            now = time.time()
            entry = CostEntry(
                key=key,
                amount=float(amount),
                label=label,
                state="reserved",
                created_at=now,
                updated_at=now,
            )
            self._live[key] = entry
            return entry

    async def reconcile(self, key: str, actual: float) -> Adjustment:
        """Convert a reservation into a committed actual charge.

        Returns an ``Adjustment`` (positive ``delta`` = over-spent) so the
        caller can log the difference.  Going over ``total_budget`` after
        reconciliation is **allowed** (vendor already charged us); we just
        record it.  A future ``reserve`` will then fail closed.
        """
        if actual < 0:
            raise ValueError("actual must be >= 0")
        async with self._lock:
            existing = self._live.get(key)
            if existing is None or existing.state != "reserved":
                raise ReservationNotFound(
                    f"no live reservation for key {key!r}"
                )
            adjustment = Adjustment(
                key=key,
                reserved=existing.amount,
                actual=float(actual),
                delta=float(actual) - existing.amount,
            )
            self._live[key] = CostEntry(
                key=key,
                amount=float(actual),
                label=existing.label,
                state="committed",
                created_at=existing.created_at,
                updated_at=time.time(),
            )
            return adjustment

    async def refund(self, key: str) -> CostEntry:
        """Drop the reservation entirely (call on failure/cancel)."""
        async with self._lock:
            existing = self._live.pop(key, None)
            if existing is None:
                raise ReservationNotFound(
                    f"no live reservation for key {key!r}"
                )
            refunded = CostEntry(
                key=existing.key,
                amount=existing.amount,
                label=existing.label,
                state="refunded",
                created_at=existing.created_at,
                updated_at=time.time(),
            )
            self._refunded.append(refunded)
            return refunded

    async def commit(
        self,
        key: str,
        amount: float,
        *,
        label: str = "",
    ) -> CostEntry:
        """Record an *unreserved* actual charge (e.g. small post-hoc fees).

        Raises:
            DuplicateReservation: ``key`` is already in the live ledger.
        """
        if amount < 0:
            raise ValueError("amount must be >= 0")
        async with self._lock:
            if key in self._live:
                raise DuplicateReservation(
                    f"key already in use: {key!r}"
                )
            now = time.time()
            entry = CostEntry(
                key=key,
                amount=float(amount),
                label=label,
                state="committed",
                created_at=now,
                updated_at=now,
            )
            self._live[key] = entry
            return entry

    async def reset(self) -> None:
        """Drop all entries — useful between user sessions."""
        async with self._lock:
            self._live.clear()
            self._refunded.clear()
