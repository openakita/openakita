"""Tests for the selector self-healing probe cycle.

We wire a fake task-manager (matching the Protocol in
``omni_post_selfheal``) plus a deterministic probe function so we can
assert on exactly which platforms alerted and how many times.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from omni_post_selfheal import (
    ALERT_COOLDOWN,
    ALERT_THRESHOLD,
    probe_platform,
    run_probe_cycle,
)


class FakeTM:
    """Minimal in-memory stand-in for OmniPostTaskManager."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.rows: dict[str, dict[str, Any]] = {}

    async def record_selector_health(
        self,
        *,
        platform: str,
        hit_rate: float,
        total_probes: int,
        failed_probes: int,
        last_error: str | None = None,
    ) -> None:
        self.rows[platform] = {
            "platform": platform,
            "hit_rate": hit_rate,
            "total_probes": total_probes,
            "failed_probes": failed_probes,
            "last_error": last_error,
            "last_alerted_at": self.rows.get(platform, {}).get("last_alerted_at"),
        }
        self.records.append(
            {"platform": platform, "hit": hit_rate, "failed": failed_probes}
        )

    async def mark_selector_alerted(self, platform: str) -> None:
        row = self.rows.setdefault(platform, {"platform": platform})
        row["last_alerted_at"] = datetime.now(timezone.utc).isoformat()

    async def list_selector_health(self) -> list[dict[str, Any]]:
        return list(self.rows.values())


# ---------------------------------------------------------------------------
# probe_platform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_platform_aggregates_hit_rate() -> None:
    async def probe(_p: str, key: str, _spec: Any) -> bool:
        return key != "broken"

    r = await probe_platform(
        "douyin",
        {"title": "css-1", "submit": "css-2", "broken": "gone"},
        probe_fn=probe,
    )
    assert r.total == 3
    assert r.failed == 1
    assert 0.66 < r.hit_rate < 0.67
    assert r.last_error and "broken" in r.last_error


@pytest.mark.asyncio
async def test_probe_platform_counts_exceptions_as_failure() -> None:
    async def probe(*_a: Any, **_kw: Any) -> bool:
        raise RuntimeError("boom")

    r = await probe_platform("x", {"a": "1", "b": "2"}, probe_fn=probe)
    assert r.failed == 2
    assert r.hit_rate == 0.0
    assert r.last_error and "RuntimeError" in r.last_error


@pytest.mark.asyncio
async def test_probe_platform_empty_bundle_is_healthy() -> None:
    async def probe(*_a: Any, **_kw: Any) -> bool:
        return True

    r = await probe_platform("y", {}, probe_fn=probe)
    assert r.total == 0
    assert r.hit_rate == 1.0


# ---------------------------------------------------------------------------
# run_probe_cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_records_and_alerts_below_threshold() -> None:
    tm = FakeTM()
    alerts: list[tuple[str, dict[str, Any]]] = []

    async def probe(platform: str, key: str, _spec: Any) -> bool:
        return not (platform == "rednote" and key in {"a", "b", "c", "d"})

    async def notifier(platform: str, payload: dict[str, Any]) -> None:
        alerts.append((platform, payload))

    await run_probe_cycle(
        selectors_by_platform={
            "douyin": {"x": "1", "y": "2"},
            "rednote": {"a": "1", "b": "2", "c": "3", "d": "4", "ok": "5"},
        },
        task_manager=tm,
        probe_fn=probe,
        notifier=notifier,
    )

    assert set(tm.rows) == {"douyin", "rednote"}
    assert tm.rows["douyin"]["hit_rate"] == 1.0
    # 1/5 hits ⇒ below threshold ⇒ alert fires exactly once
    assert tm.rows["rednote"]["hit_rate"] == pytest.approx(0.2, abs=1e-6)
    assert [a[0] for a in alerts] == ["rednote"]
    assert alerts[0][1]["threshold"] == ALERT_THRESHOLD
    assert "hit_rate" in alerts[0][1]


@pytest.mark.asyncio
async def test_cycle_respects_alert_cooldown() -> None:
    tm = FakeTM()
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    tm.rows["rednote"] = {
        "platform": "rednote",
        "last_alerted_at": recent.isoformat(),
    }
    fired: list[str] = []

    async def probe(*_a: Any, **_kw: Any) -> bool:
        return False

    async def notifier(p: str, _payload: dict[str, Any]) -> None:
        fired.append(p)

    await run_probe_cycle(
        selectors_by_platform={"rednote": {"a": "1", "b": "2"}},
        task_manager=tm,
        probe_fn=probe,
        notifier=notifier,
        alert_cooldown=ALERT_COOLDOWN,  # 24 h
    )
    assert fired == []  # still in cooldown

    old = datetime.now(timezone.utc) - timedelta(hours=48)
    tm.rows["rednote"]["last_alerted_at"] = old.isoformat()
    await run_probe_cycle(
        selectors_by_platform={"rednote": {"a": "1", "b": "2"}},
        task_manager=tm,
        probe_fn=probe,
        notifier=notifier,
    )
    assert fired == ["rednote"]


@pytest.mark.asyncio
async def test_cycle_survives_notifier_exception() -> None:
    tm = FakeTM()

    async def probe(*_a: Any, **_kw: Any) -> bool:
        return False

    async def notifier(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("webhook down")

    results = await run_probe_cycle(
        selectors_by_platform={"douyin": {"x": "1"}},
        task_manager=tm,
        probe_fn=probe,
        notifier=notifier,
    )
    # The cycle must not raise; the row is still written.
    assert len(results) == 1
    assert tm.rows["douyin"]["hit_rate"] == 0.0
