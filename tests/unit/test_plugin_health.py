"""Unit tests for weighted PluginErrorTracker (commit A).

Covers:
- weighted error thresholds (timeout=3, exception=1, permission_denied=0)
- record_success time-stamp & reset() purges _last_success
- ERROR_WINDOW expiry of stale errors
- auto-disable callback fires exactly once
- health_snapshot() shape
"""

from __future__ import annotations

import time

import pytest

from openakita.plugins.sandbox import (
    ERROR_WEIGHTS,
    ERROR_WINDOW,
    MAX_CONSECUTIVE_ERRORS,
    PluginErrorTracker,
)


# ---------- weighted thresholds ----------


def test_weights_match_plan_constants():
    assert ERROR_WEIGHTS["timeout"] == 3
    assert ERROR_WEIGHTS["exception"] == 1
    assert ERROR_WEIGHTS["permission_denied"] == 0
    assert MAX_CONSECUTIVE_ERRORS == 10


def test_three_timeouts_below_threshold():
    t = PluginErrorTracker()
    for _ in range(3):
        triggered = t.record_error("p1", "ctx", "timeout", kind="timeout")
        assert triggered is False
    assert not t.is_disabled("p1")
    snap = t.health_snapshot("p1")
    assert snap["weighted_errors"] == 9
    assert snap["timeout_count"] == 3
    assert snap["exception_count"] == 0
    assert snap["is_disabled"] is False


def test_four_timeouts_trigger_auto_disable():
    fired: list[str] = []
    t = PluginErrorTracker()
    t.set_auto_disable_callback(fired.append)

    for i in range(4):
        triggered = t.record_error("p1", f"ctx{i}", "timeout", kind="timeout")
    assert triggered is True
    assert t.is_disabled("p1")
    assert fired == ["p1"], "callback must fire exactly once"
    assert t.health_snapshot("p1")["weighted_errors"] >= MAX_CONSECUTIVE_ERRORS


def test_mixed_one_timeout_seven_exceptions_triggers():
    t = PluginErrorTracker()
    t.record_error("p1", "c", "boom", kind="timeout")  # +3
    for _ in range(7):
        t.record_error("p1", "c", "boom")  # default kind=exception, +1 each
    assert t.is_disabled("p1")
    snap = t.health_snapshot("p1")
    assert snap["weighted_errors"] == 10
    assert snap["timeout_count"] == 1
    assert snap["exception_count"] == 7


def test_permission_denied_is_no_op():
    t = PluginErrorTracker()
    for _ in range(50):
        triggered = t.record_error(
            "p1", "perm", "denied", kind="permission_denied"
        )
        assert triggered is False
    assert not t.is_disabled("p1")
    snap = t.health_snapshot("p1")
    assert snap["weighted_errors"] == 0
    assert snap["timeout_count"] == 0
    assert snap["exception_count"] == 0


def test_unknown_kind_defaults_to_weight_1():
    t = PluginErrorTracker()
    # unknown kind falls back to weight 1 (defensive)
    for _ in range(9):
        assert t.record_error("p1", "c", "x", kind="weird") is False
    assert t.record_error("p1", "c", "x", kind="weird") is True


# ---------- record_success / reset ----------


def test_record_success_stamps_time():
    t = PluginErrorTracker()
    assert t.health_snapshot("p1")["last_success_at"] is None

    before = time.time()
    t.record_success("p1")
    after = time.time()

    snap = t.health_snapshot("p1")
    assert snap["last_success_at"] is not None
    assert before <= snap["last_success_at"] <= after


def test_record_success_empty_id_noop():
    t = PluginErrorTracker()
    t.record_success("")  # no exception, no entry
    assert t.health_snapshot("")["last_success_at"] is None


def test_reset_purges_last_success_and_errors_and_disabled():
    t = PluginErrorTracker()
    t.record_success("p1")
    for _ in range(4):
        t.record_error("p1", "c", "e", kind="timeout")
    assert t.is_disabled("p1")
    assert t.health_snapshot("p1")["last_success_at"] is not None

    t.reset("p1")

    snap = t.health_snapshot("p1")
    assert snap["last_success_at"] is None
    assert snap["weighted_errors"] == 0
    assert snap["is_disabled"] is False
    assert not t.is_disabled("p1")


# ---------- window expiry ----------


def test_old_errors_outside_window_drop_off():
    t = PluginErrorTracker()
    # Manually inject 3 stale timeouts (> ERROR_WINDOW seconds ago)
    stale = time.time() - ERROR_WINDOW - 10
    t._errors["p1"] = [
        {"time": stale, "context": "old", "error": "x",
         "kind": "timeout", "weight": 3}
        for _ in range(3)
    ]
    # A fresh single timeout: stale entries get pruned during record_error,
    # so total weighted = 3, not 12
    t.record_error("p1", "c", "x", kind="timeout")
    snap = t.health_snapshot("p1")
    assert snap["weighted_errors"] == 3
    assert snap["timeout_count"] == 1
    assert not t.is_disabled("p1")


def test_health_snapshot_only_counts_within_window():
    t = PluginErrorTracker()
    fresh = time.time()
    stale = fresh - ERROR_WINDOW - 10
    t._errors["p1"] = [
        {"time": stale, "context": "old", "error": "x",
         "kind": "exception", "weight": 1},
        {"time": fresh, "context": "new", "error": "y",
         "kind": "exception", "weight": 1},
    ]
    snap = t.health_snapshot("p1")
    assert snap["weighted_errors"] == 1
    assert snap["exception_count"] == 1


# ---------- callback safety ----------


def test_auto_disable_callback_failure_does_not_swallow_disable():
    t = PluginErrorTracker()

    def boom(_pid: str) -> None:
        raise RuntimeError("callback exploded")

    t.set_auto_disable_callback(boom)
    for _ in range(4):
        t.record_error("p1", "c", "x", kind="timeout")
    # Even though callback raised, plugin is still marked disabled
    assert t.is_disabled("p1")


# ---------- snapshot contract ----------


def test_health_snapshot_for_unknown_plugin_is_zero():
    t = PluginErrorTracker()
    snap = t.health_snapshot("never-seen")
    assert snap == {
        "weighted_errors": 0,
        "timeout_count": 0,
        "exception_count": 0,
        "last_success_at": None,
        "is_disabled": False,
    }


@pytest.mark.parametrize(
    "kind,weight",
    [("timeout", 3), ("exception", 1)],
)
def test_kind_weight_round_trip(kind: str, weight: int):
    t = PluginErrorTracker()
    t.record_error("p1", "c", "x", kind=kind)
    assert t.health_snapshot("p1")["weighted_errors"] == weight
