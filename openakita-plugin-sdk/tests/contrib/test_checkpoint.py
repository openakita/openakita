"""Tests for openakita_plugin_sdk.contrib.checkpoint."""

from __future__ import annotations

import json

import pytest
from openakita_plugin_sdk.contrib import (
    Checkpoint,
    CostTracker,
    restore_from_snapshot,
    take_checkpoint,
)

# ── basic capture ──────────────────────────────────────────────────


def test_take_checkpoint_without_tracker() -> None:
    cp = take_checkpoint("stage-a")
    assert cp.name == "stage-a"
    assert cp.cost_snapshot is None
    assert cp.extra == {}
    assert cp.taken_at > 0


def test_take_checkpoint_requires_name() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        take_checkpoint("")


def test_take_checkpoint_with_extra() -> None:
    cp = take_checkpoint("s", extra={"shots_done": [1, 2, 3]})
    assert cp.extra == {"shots_done": [1, 2, 3]}


@pytest.mark.asyncio
async def test_take_checkpoint_with_tracker() -> None:
    t = CostTracker(currency="USD", total_budget=100.0)
    await t.reserve("k", 10.0, label="x")
    cp = take_checkpoint("generate", cost_tracker=t)
    assert cp.cost_snapshot is not None
    assert cp.cost_snapshot.currency == "USD"
    assert cp.cost_snapshot.total_budget == 100.0
    assert len(cp.cost_snapshot.entries) == 1


# ── restore round-trip ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_undoes_leaked_reservations() -> None:
    t = CostTracker(total_budget=50.0)
    await t.reserve("safe", 5.0)
    cp = take_checkpoint("hot-stage", cost_tracker=t)

    # Simulate a partial failure: reserved + reconciled + reserved again,
    # then never refunded.  Without restore, future ``reserve`` would see
    # phantom reservations.
    await t.reserve("oops-1", 10.0)
    await t.reserve("oops-2", 8.0)
    assert t.summary().reserved == 23.0

    await restore_from_snapshot(cp, cost_tracker=t)
    assert t.summary().reserved == 5.0


@pytest.mark.asyncio
async def test_restore_no_op_without_snapshot() -> None:
    t = CostTracker()
    await t.reserve("k", 1.0)
    cp = take_checkpoint("x")  # no tracker → no snapshot
    await restore_from_snapshot(cp, cost_tracker=t)
    # tracker untouched
    assert t.summary().reserved == 1.0


@pytest.mark.asyncio
async def test_restore_no_op_without_tracker() -> None:
    t = CostTracker()
    await t.reserve("k", 1.0)
    cp = take_checkpoint("x", cost_tracker=t)
    await t.reserve("k2", 5.0)
    # restore against a None tracker is a silent no-op
    await restore_from_snapshot(cp, cost_tracker=None)
    # original tracker still has both reservations (we passed None)
    assert t.summary().reserved == 6.0


# ── JSON persistence round-trip ────────────────────────────────────


def test_checkpoint_to_dict_is_json_safe() -> None:
    cp = take_checkpoint("s", extra={"a": 1, "b": [1, 2], "c": "hi"})
    raw = cp.to_dict()
    # Must round-trip through json.dumps without TypeError.
    blob = json.dumps(raw)
    parsed = json.loads(blob)
    assert parsed["name"] == "s"
    assert parsed["extra"] == {"a": 1, "b": [1, 2], "c": "hi"}


@pytest.mark.asyncio
async def test_checkpoint_from_dict_round_trip() -> None:
    t = CostTracker(currency="CNY", total_budget=20.0, single_call_threshold=5.0)
    await t.reserve("k1", 3.0, label="alpha")
    await t.commit("k2", 1.0, label="beta")
    cp1 = take_checkpoint("warmup", cost_tracker=t, extra={"step": 1})

    blob = json.dumps(cp1.to_dict())
    cp2 = Checkpoint.from_dict(json.loads(blob))

    assert cp2.name == "warmup"
    assert cp2.extra == {"step": 1}
    assert cp2.cost_snapshot is not None
    assert cp2.cost_snapshot.currency == "CNY"
    assert cp2.cost_snapshot.total_budget == 20.0
    assert cp2.cost_snapshot.single_call_threshold == 5.0
    assert len(cp2.cost_snapshot.entries) == 2

    # And restoring it into a fresh tracker reproduces the state.
    fresh = CostTracker()
    await restore_from_snapshot(cp2, cost_tracker=fresh)
    s = fresh.summary()
    assert s.reserved == 3.0
    assert s.committed == 1.0
    assert fresh.currency == "CNY"
    assert fresh.total_budget == 20.0


def test_checkpoint_from_dict_tolerates_missing_keys() -> None:
    cp = Checkpoint.from_dict({})
    assert cp.name == ""
    assert cp.cost_snapshot is None
    assert cp.extra == {}
    assert cp.taken_at > 0


def test_checkpoint_from_dict_tolerates_partial_snapshot() -> None:
    raw = {
        "name": "s",
        "cost_snapshot": {
            "entries": [
                {"key": "k", "amount": 1.0, "state": "committed"},
            ],
        },
    }
    cp = Checkpoint.from_dict(raw)
    assert cp.cost_snapshot is not None
    assert cp.cost_snapshot.currency == "CNY"  # default
    assert cp.cost_snapshot.total_budget is None
    assert len(cp.cost_snapshot.entries) == 1
