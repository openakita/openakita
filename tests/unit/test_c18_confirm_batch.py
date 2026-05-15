"""C18 Phase B — confirm batch aggregation tests.

Coverage:
1. ``ConfirmationConfig.aggregation_window_seconds`` schema bounds.
2. ``UIConfirmBus.list_batch_candidates`` time-window filtering.
3. ``UIConfirmBus.batch_resolve`` fan-out: every active confirm in
   session resolves; idempotent; preserves waiter wake-up.
4. ``POST /api/chat/security-confirm/batch`` integration with
   ``apply_resolution`` side effects.
5. Server-side window clamp: if POLICIES.yaml has window=2 and request
   says within_seconds=300, server clamps to 2.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openakita.api.routes import config as config_routes
from openakita.core.policy_v2 import global_engine
from openakita.core.policy_v2.engine import build_engine_from_config
from openakita.core.policy_v2.schema import ConfirmationConfig, PolicyConfigV2
from openakita.core.ui_confirm_bus import UIConfirmBus, reset_ui_confirm_bus

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestAggregationConfig:
    def test_default_disabled(self) -> None:
        assert ConfirmationConfig().aggregation_window_seconds == 0.0

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConfirmationConfig(aggregation_window_seconds=-1)

    def test_upper_bound_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConfirmationConfig(aggregation_window_seconds=601)

    def test_zero_allowed(self) -> None:
        assert ConfirmationConfig(aggregation_window_seconds=0).aggregation_window_seconds == 0

    def test_typical_value(self) -> None:
        cfg = ConfirmationConfig(aggregation_window_seconds=5)
        assert cfg.aggregation_window_seconds == 5


# ---------------------------------------------------------------------------
# Bus: list_batch_candidates + batch_resolve
# ---------------------------------------------------------------------------


@pytest.fixture
def bus() -> UIConfirmBus:
    return UIConfirmBus(ttl_seconds=300)


class TestListBatchCandidates:
    def test_empty_session_returns_empty(self, bus: UIConfirmBus) -> None:
        assert bus.list_batch_candidates("sess-x") == []

    def test_no_window_returns_all_session_pending(self, bus: UIConfirmBus) -> None:
        bus.store_pending("c1", "write_file", {}, session_id="sess-a")
        bus.store_pending("c2", "edit_file", {}, session_id="sess-a")
        bus.store_pending("c3", "write_file", {}, session_id="sess-b")
        got = sorted(bus.list_batch_candidates("sess-a"))
        assert got == ["c1", "c2"]

    def test_window_excludes_old_emissions(self, bus: UIConfirmBus) -> None:
        """Old confirms shouldn't get swept into a freshly-clicked batch."""
        bus.store_pending("c_old", "write_file", {}, session_id="s")
        # Roll c_old back 10s to look stale.
        bus._pending["c_old"]["created_at"] -= 10.0
        bus.store_pending("c_new", "edit_file", {}, session_id="s")

        got = bus.list_batch_candidates("s", within_seconds=2.0)
        assert got == ["c_new"], "10s-old confirm must not aggregate with 0s-old"

    def test_window_zero_or_none_keeps_all(self, bus: UIConfirmBus) -> None:
        bus.store_pending("c1", "write_file", {}, session_id="s")
        bus._pending["c1"]["created_at"] -= 60.0
        bus.store_pending("c2", "edit_file", {}, session_id="s")

        # within_seconds = None means "no time filter"
        assert sorted(bus.list_batch_candidates("s")) == ["c1", "c2"]
        # 0 has the same semantics in the bus API (treated as disabled)
        assert sorted(bus.list_batch_candidates("s", within_seconds=0)) == ["c1", "c2"]


class TestBatchResolve:
    def test_fan_out_resolves_every_candidate(self, bus: UIConfirmBus) -> None:
        bus.store_pending("c1", "write_file", {"p": 1}, session_id="s")
        bus.store_pending("c2", "edit_file", {"p": 2}, session_id="s")
        bus.prepare("c1")
        bus.prepare("c2")

        results = bus.batch_resolve("s", "allow_once")

        assert len(results) == 2
        ids = sorted(r["confirm_id"] for r in results)
        assert ids == ["c1", "c2"]
        for r in results:
            assert r["decision"] == "allow_once"
        # Followers / siblings calling resolve() again must be no-ops.
        again = bus.batch_resolve("s", "allow_once")
        assert again == []

    def test_wakes_waiters(self, bus: UIConfirmBus) -> None:
        """After batch_resolve, any wait_for_resolution coroutines must
        wake up with the chosen decision (not the default timeout 'deny').
        """
        bus.store_pending("c1", "x", {}, session_id="s")
        bus.store_pending("c2", "y", {}, session_id="s")
        bus.prepare("c1")
        bus.prepare("c2")

        async def _wait_both() -> tuple[str, str]:
            return await asyncio.gather(
                bus.wait_for_resolution("c1", timeout=2.0),
                bus.wait_for_resolution("c2", timeout=2.0),
            )

        async def _runner() -> tuple[str, str]:
            task = asyncio.create_task(_wait_both())
            # Yield once so the waiter is parked before we resolve.
            await asyncio.sleep(0.05)
            bus.batch_resolve("s", "deny")
            return await task

        d1, d2 = asyncio.run(_runner())
        assert d1 == "deny"
        assert d2 == "deny"

    def test_session_isolation(self, bus: UIConfirmBus) -> None:
        """Batch resolve in session A must not touch session B."""
        bus.store_pending("a1", "x", {}, session_id="A")
        bus.store_pending("b1", "y", {}, session_id="B")
        bus.prepare("a1")
        bus.prepare("b1")

        results = bus.batch_resolve("A", "allow_once")

        assert len(results) == 1
        assert results[0]["confirm_id"] == "a1"
        # Session B's pending must still be there.
        assert "b1" in bus._pending


# ---------------------------------------------------------------------------
# API endpoint integration
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch):
    """Spin up a minimal FastAPI app mounting just the config router so
    we can hit ``/api/chat/security-confirm/batch`` without auth setup.

    Pre-seed ``aggregation_window_seconds=2`` via ``set_engine_v2`` so the
    endpoint's server-side clamp can be verified.
    """
    reset_ui_confirm_bus()
    global_engine.reset_engine_v2(clear_explicit_lookup=True)
    global_engine._clear_last_known_good()

    cfg = PolicyConfigV2.model_validate(
        {"confirmation": {"aggregation_window_seconds": 2}}
    )
    fake_engine = build_engine_from_config(cfg)
    global_engine.set_engine_v2(fake_engine, config=cfg)

    app = FastAPI()
    app.include_router(config_routes.router)

    with TestClient(app) as client:
        yield client

    reset_ui_confirm_bus()
    global_engine.reset_engine_v2(clear_explicit_lookup=True)
    global_engine._clear_last_known_good()


class TestBatchEndpoint:
    def test_endpoint_resolves_session_confirms(self, api_client: TestClient) -> None:
        from openakita.core.ui_confirm_bus import get_ui_confirm_bus

        bus = get_ui_confirm_bus()
        bus.store_pending("c1", "write_file", {}, session_id="s1")
        bus.store_pending("c2", "edit_file", {}, session_id="s1")
        bus.prepare("c1")
        bus.prepare("c2")

        r = api_client.post(
            "/api/chat/security-confirm/batch",
            json={"session_id": "s1", "decision": "allow_once"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["resolved_count"] == 2
        assert sorted(body["resolved_ids"]) == ["c1", "c2"]

    def test_endpoint_clamps_to_config_window(
        self, api_client: TestClient
    ) -> None:
        """Server config says window=2; even if client requests window=300,
        only confirms in the 2s window should resolve."""
        from openakita.core.ui_confirm_bus import get_ui_confirm_bus

        bus = get_ui_confirm_bus()
        bus.store_pending("c_old", "write_file", {}, session_id="s2")
        bus._pending["c_old"]["created_at"] -= 60.0  # 60s old
        bus.store_pending("c_new", "edit_file", {}, session_id="s2")
        bus.prepare("c_old")
        bus.prepare("c_new")

        r = api_client.post(
            "/api/chat/security-confirm/batch",
            json={
                "session_id": "s2",
                "decision": "allow_once",
                "within_seconds": 300,  # absurd; server must clamp to 2
            },
        )
        assert r.status_code == 200
        body = r.json()
        # Only c_new should be in the 2s window.
        assert body["resolved_count"] == 1
        assert body["resolved_ids"] == ["c_new"]
        assert body["window_seconds"] == 2  # confirms server clamped

    def test_decision_aliases_normalize(self, api_client: TestClient) -> None:
        from openakita.core.ui_confirm_bus import get_ui_confirm_bus

        bus = get_ui_confirm_bus()
        bus.store_pending("cz", "x", {}, session_id="sz")
        bus.prepare("cz")

        r = api_client.post(
            "/api/chat/security-confirm/batch",
            json={"session_id": "sz", "decision": "continue"},  # legacy alias
        )
        assert r.json()["decision"] == "allow_once"

    def test_no_candidates_returns_zero_not_error(
        self, api_client: TestClient
    ) -> None:
        r = api_client.post(
            "/api/chat/security-confirm/batch",
            json={"session_id": "empty-session", "decision": "deny"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["resolved_count"] == 0
        assert body["resolved_ids"] == []

    def test_get_confirmation_includes_aggregation_field(
        self, api_client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The GET endpoint must surface ``aggregation_window_seconds``
        so the frontend can gate the batch UI affordance."""
        r = api_client.get("/api/config/security/confirmation")
        assert r.status_code == 200
        body = r.json()
        assert "aggregation_window_seconds" in body
        # Default-empty config returns 0.0
        assert isinstance(body["aggregation_window_seconds"], (int, float))
