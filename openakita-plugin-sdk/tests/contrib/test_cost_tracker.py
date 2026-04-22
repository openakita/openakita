"""Tests for openakita_plugin_sdk.contrib.cost_tracker."""

from __future__ import annotations

import asyncio

import pytest
from openakita_plugin_sdk.contrib import (
    CostSnapshot,
    CostTracker,
    DuplicateReservation,
    InsufficientBudget,
    ReservationNotFound,
)

# ── construction ────────────────────────────────────────────────────


def test_default_construction() -> None:
    t = CostTracker()
    s = t.summary()
    assert s.currency == "CNY"
    assert s.reserved == 0.0
    assert s.committed == 0.0
    assert s.refunded == 0.0
    assert s.total_budget is None
    assert s.remaining is None
    assert s.entry_count == 0


def test_negative_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="single_call_threshold"):
        CostTracker(single_call_threshold=-1)


def test_negative_budget_rejected() -> None:
    with pytest.raises(ValueError, match="total_budget"):
        CostTracker(total_budget=-0.01)


def test_zero_budget_allowed() -> None:
    t = CostTracker(total_budget=0)
    assert t.total_budget == 0


# ── requires_approval ────────────────────────────────────────────────


def test_approval_disabled_by_default() -> None:
    t = CostTracker()
    assert not t.requires_approval(1_000_000)


def test_approval_threshold_excludes_equal() -> None:
    t = CostTracker(single_call_threshold=10.0)
    assert not t.requires_approval(10.0)
    assert t.requires_approval(10.01)


def test_approval_ignores_negative() -> None:
    t = CostTracker(single_call_threshold=10.0)
    assert not t.requires_approval(-100)


# ── reserve / reconcile / refund ────────────────────────────────────


@pytest.mark.asyncio
async def test_reserve_and_reconcile_happy_path() -> None:
    t = CostTracker()
    await t.reserve("k1", 5.0, label="seedance")
    s = t.summary()
    assert s.reserved == 5.0
    assert s.committed == 0.0

    adj = await t.reconcile("k1", 4.5)
    assert adj.reserved == 5.0
    assert adj.actual == 4.5
    assert adj.delta == pytest.approx(-0.5)
    s = t.summary()
    assert s.reserved == 0.0
    assert s.committed == 4.5


@pytest.mark.asyncio
async def test_reserve_duplicate_key() -> None:
    t = CostTracker()
    await t.reserve("k1", 1.0)
    with pytest.raises(DuplicateReservation):
        await t.reserve("k1", 2.0)


@pytest.mark.asyncio
async def test_reserve_negative_amount_rejected() -> None:
    t = CostTracker()
    with pytest.raises(ValueError, match="amount"):
        await t.reserve("k", -1.0)


@pytest.mark.asyncio
async def test_reserve_exceeds_budget() -> None:
    t = CostTracker(total_budget=10.0)
    await t.reserve("a", 6.0)
    with pytest.raises(InsufficientBudget, match="exceed budget"):
        await t.reserve("b", 5.0)
    s = t.summary()
    assert s.reserved == 6.0
    assert s.remaining == 4.0


@pytest.mark.asyncio
async def test_reserve_at_exact_budget_ok() -> None:
    t = CostTracker(total_budget=10.0)
    await t.reserve("a", 10.0)
    assert t.summary().remaining == 0.0


@pytest.mark.asyncio
async def test_reconcile_unknown_key() -> None:
    t = CostTracker()
    with pytest.raises(ReservationNotFound):
        await t.reconcile("missing", 1.0)


@pytest.mark.asyncio
async def test_reconcile_after_commit_rejected() -> None:
    t = CostTracker()
    await t.reserve("k", 5.0)
    await t.reconcile("k", 5.0)
    with pytest.raises(ReservationNotFound):
        # already committed → no live reservation
        await t.reconcile("k", 5.0)


@pytest.mark.asyncio
async def test_reconcile_negative_amount_rejected() -> None:
    t = CostTracker()
    await t.reserve("k", 5.0)
    with pytest.raises(ValueError, match="actual"):
        await t.reconcile("k", -1)


@pytest.mark.asyncio
async def test_refund_drops_reservation() -> None:
    t = CostTracker(total_budget=10.0)
    await t.reserve("k", 5.0)
    await t.refund("k")
    s = t.summary()
    assert s.reserved == 0.0
    assert s.refunded == 5.0
    assert s.remaining == 10.0  # budget freed


@pytest.mark.asyncio
async def test_refund_unknown_key() -> None:
    t = CostTracker()
    with pytest.raises(ReservationNotFound):
        await t.refund("nope")


# ── commit (no reservation) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_commit_records_unreserved_charge() -> None:
    t = CostTracker()
    await t.commit("fee-1", 0.10, label="api stamp fee")
    s = t.summary()
    assert s.reserved == 0.0
    assert s.committed == 0.10


@pytest.mark.asyncio
async def test_commit_duplicate_key() -> None:
    t = CostTracker()
    await t.commit("k", 0.5)
    with pytest.raises(DuplicateReservation):
        await t.commit("k", 0.6)


@pytest.mark.asyncio
async def test_commit_negative_rejected() -> None:
    t = CostTracker()
    with pytest.raises(ValueError):
        await t.commit("k", -1)


# ── concurrent reserve race ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_reserve_distinct_keys() -> None:
    """N concurrent reserves under distinct keys should all succeed."""
    t = CostTracker()
    keys = [f"k{i}" for i in range(20)]
    await asyncio.gather(*(t.reserve(k, 1.0) for k in keys))
    assert t.summary().reserved == 20.0


@pytest.mark.asyncio
async def test_concurrent_reserve_same_key_only_one_wins() -> None:
    """The asyncio.Lock guarantees exactly one reserve wins per key."""
    t = CostTracker()
    coros = [t.reserve("k", 1.0) for _ in range(10)]
    results = await asyncio.gather(*coros, return_exceptions=True)
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, DuplicateReservation)]
    assert len(successes) == 1
    assert len(failures) == 9


@pytest.mark.asyncio
async def test_concurrent_reserve_budget_enforced() -> None:
    """Budget cap survives concurrent reserves."""
    t = CostTracker(total_budget=5.0)
    coros = [t.reserve(f"k{i}", 1.0) for i in range(20)]
    results = await asyncio.gather(*coros, return_exceptions=True)
    ok = sum(1 for r in results if not isinstance(r, Exception))
    rejected = sum(1 for r in results if isinstance(r, InsufficientBudget))
    assert ok == 5
    assert rejected == 15
    assert t.summary().reserved == 5.0


# ── reconcile that overruns budget is allowed ───────────────────────


@pytest.mark.asyncio
async def test_reconcile_overrun_allowed_but_blocks_future_reserve() -> None:
    t = CostTracker(total_budget=10.0)
    await t.reserve("k", 5.0)
    # Vendor charged us more than estimated — reconciliation must accept it.
    adj = await t.reconcile("k", 12.0)
    assert adj.delta == pytest.approx(7.0)
    s = t.summary()
    assert s.committed == 12.0
    assert s.remaining == -2.0
    # Future reserves now fail closed.
    with pytest.raises(InsufficientBudget):
        await t.reserve("k2", 0.01)


# ── snapshot + restore ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_round_trip() -> None:
    t = CostTracker(currency="USD", total_budget=100.0, single_call_threshold=20.0)
    await t.reserve("a", 5.0, label="lo")
    await t.reserve("b", 30.0, label="hi")
    await t.reconcile("a", 4.0)
    await t.refund("b")
    snap = t.snapshot()
    raw = snap.to_dict()
    assert raw["currency"] == "USD"
    assert raw["total_budget"] == 100.0
    assert raw["single_call_threshold"] == 20.0
    assert len(raw["entries"]) == 2
    states = sorted(e["state"] for e in raw["entries"])
    assert states == ["committed", "refunded"]


@pytest.mark.asyncio
async def test_restore_from_snapshot_round_trip() -> None:
    t = CostTracker(currency="CNY", total_budget=50.0)
    await t.reserve("a", 10.0, label="x")
    await t.reconcile("a", 9.5)
    await t.reserve("b", 5.0, label="y")
    snap = t.snapshot()

    # Mutate, then restore.
    await t.reserve("c", 20.0)
    await t.refund("b")
    assert t.summary().reserved != snap.to_dict()["entries"]

    await t.restore(snap)
    s = t.summary()
    assert s.committed == 9.5
    assert s.reserved == 5.0
    assert s.refunded == 0.0


@pytest.mark.asyncio
async def test_restore_preserves_currency_and_threshold() -> None:
    t = CostTracker(currency="CNY", single_call_threshold=10.0, total_budget=100.0)
    await t.reserve("k", 1.0)
    snap = t.snapshot()

    # Mutate config drastically.
    t.currency = "USD"
    t.single_call_threshold = 0.0
    t.total_budget = None

    await t.restore(snap)
    assert t.currency == "CNY"
    assert t.single_call_threshold == 10.0
    assert t.total_budget == 100.0


@pytest.mark.asyncio
async def test_restore_from_dict_round_trip() -> None:
    """Snapshot survives JSON round-trip via to_dict / manual reconstruction."""
    t = CostTracker(total_budget=50.0)
    await t.reserve("k", 5.0, label="x")
    raw = t.snapshot().to_dict()

    # Simulate caller persisting + reloading the dict.
    rebuilt = CostSnapshot(
        taken_at=raw["taken_at"],
        currency=raw["currency"],
        total_budget=raw["total_budget"],
        single_call_threshold=raw["single_call_threshold"],
        entries=raw["entries"],
    )
    fresh = CostTracker()
    await fresh.restore(rebuilt)
    s = fresh.summary()
    assert s.reserved == 5.0
    assert fresh.total_budget == 50.0


# ── reset + introspection ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_clears_everything() -> None:
    t = CostTracker(total_budget=10.0)
    await t.reserve("a", 5.0)
    await t.commit("b", 1.0)
    await t.reset()
    s = t.summary()
    assert s.reserved == 0.0
    assert s.committed == 0.0
    assert s.refunded == 0.0
    assert s.entry_count == 0


@pytest.mark.asyncio
async def test_get_entry_and_list_entries() -> None:
    t = CostTracker()
    await t.reserve("a", 1.0, label="x")
    await t.commit("b", 2.0, label="y")
    assert t.get_entry("a").label == "x"
    assert t.get_entry("missing") is None
    assert {e.key for e in t.list_entries()} == {"a", "b"}


@pytest.mark.asyncio
async def test_summary_in_flight_property() -> None:
    t = CostTracker()
    await t.reserve("a", 3.0)
    await t.commit("b", 2.0)
    s = t.summary()
    assert s.in_flight == 5.0


@pytest.mark.asyncio
async def test_summary_after_partial_lifecycle() -> None:
    t = CostTracker(total_budget=20.0)
    await t.reserve("a", 5.0)
    await t.reconcile("a", 4.5)
    await t.reserve("b", 3.0)
    await t.refund("b")
    s = t.summary()
    assert s.reserved == 0.0
    assert s.committed == 4.5
    assert s.refunded == 3.0
    assert s.remaining == pytest.approx(15.5)


# ── adjustment dataclass ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adjustment_to_dict() -> None:
    t = CostTracker()
    await t.reserve("k", 10.0)
    adj = await t.reconcile("k", 12.345678)
    raw = adj.to_dict()
    assert raw["key"] == "k"
    assert raw["reserved"] == 10.0
    # rounded to 6dp
    assert raw["actual"] == 12.345678
    assert raw["delta"] == pytest.approx(2.345678)
