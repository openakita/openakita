"""Stage 3 — /api/health + /api/memory/repair degraded-subsystem surface tests.

Covers the new v1.29 boot-fault-tolerance HTTP surface:

* ``GET  /api/health``                       -> exposes ``degraded_subsystems``
* ``GET  /api/memory/repair/degraded``       -> snapshot + confirmation token
* ``POST /api/memory/repair/quarantine``     -> validates payload, consumes
                                                 single-use token, renames
                                                 the .db triplet, clears the
                                                 degraded registry, returns
                                                 ``restart_required=True``.

These tests are deliberately scoped at the HTTP boundary (FastAPI
``TestClient``) so the assertions match what the desktop banner /
``DegradedRepairDialog`` will see. Underlying SQLite/atomic-IO behaviour
is covered separately by ``tests/integration/test_corruption_degraded.py``
and ``tests/unit/test_safe_sqlite.py``.

Two cross-test hygiene rules:

1. Always ``clear()`` the module-level ``DegradedRegistry`` before/after.
   Other tests in the suite (and previous failures) may have left entries.
2. Always reset ``health._memory_repair_restart_required`` because
   ``mark_memory_repair_completed_restart_required`` is sticky for the
   lifetime of the process by design.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import health as health_module
from openakita.api.routes import memory_repair as repair_module
from openakita.storage.degraded import registry as degraded_registry


@pytest.fixture(autouse=True)
def _reset_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Each test runs against a fresh registry, token table, and data dir."""
    degraded_registry.clear()
    repair_module._tokens.clear()
    health_module.clear_memory_repair_restart_required()
    health_module._readyz_cache.update({"ts": 0.0, "payload": None, "ready": False})

    # Redirect data_dir into the per-test tmp_path so a real ``shutil.move``
    # of the .db file can happen without touching the developer's workspace.
    monkeypatch.setattr(
        repair_module.settings, "project_root", tmp_path, raising=False
    )
    (tmp_path / "data" / "memory").mkdir(parents=True, exist_ok=True)

    # No desktop token in env -> _verify_desktop_token becomes a no-op.
    monkeypatch.delenv("OPENAKITA_DESKTOP_SESSION_TOKEN", raising=False)

    yield

    degraded_registry.clear()
    repair_module._tokens.clear()
    health_module.clear_memory_repair_restart_required()


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(health_module.router)
    app.include_router(repair_module.router)
    app.state.scheduler = None
    app.state.gateway = None
    app.state.asset_bus = None
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestHealthDegradedField:
    """``GET /api/health`` must always include ``degraded_subsystems``."""

    def test_empty_when_no_subsystem_degraded(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert "degraded_subsystems" in body
        assert body["degraded_subsystems"] == []

    def test_reports_registered_subsystem(self, client: TestClient) -> None:
        degraded_registry.register(
            "feedback",
            reason="corrupted",
            details="header magic mismatch",
            repair="POST /api/memory/repair/quarantine",
        )

        r = client.get("/api/health")
        assert r.status_code == 200
        snap = r.json()["degraded_subsystems"]
        assert len(snap) == 1
        entry = snap[0]
        assert entry["subsystem"] == "feedback"
        assert entry["reason"] == "corrupted"
        assert entry["details"] == "header magic mismatch"
        assert entry["repair_action"] == "POST /api/memory/repair/quarantine"
        assert "since" in entry  # ISO-ish timestamp

    def test_unregister_clears_entry(self, client: TestClient) -> None:
        degraded_registry.register("token_tracking", reason="corrupted")
        assert len(client.get("/api/health").json()["degraded_subsystems"]) == 1

        degraded_registry.unregister("token_tracking")
        assert client.get("/api/health").json()["degraded_subsystems"] == []


class TestDegradedSnapshotEndpoint:
    """``GET /api/memory/repair/degraded`` is read-only and unauthenticated.

    It must issue a single-use confirmation token. The token is the only
    thing protecting the destructive ``POST /quarantine`` from CSRF when
    no desktop session token is configured (dev/web modes).
    """

    def test_returns_snapshot_and_token(self, client: TestClient) -> None:
        degraded_registry.register("asset_bus", reason="locked")

        r = client.get("/api/memory/repair/degraded")
        assert r.status_code == 200
        body = r.json()
        assert {"subsystems", "desktop_token_required", "confirmation_token"} <= body.keys()
        assert len(body["subsystems"]) == 1
        assert body["subsystems"][0]["subsystem"] == "asset_bus"
        assert body["desktop_token_required"] is False  # env not set
        assert isinstance(body["confirmation_token"], str)
        assert len(body["confirmation_token"]) >= 16

    def test_each_call_issues_new_token(self, client: TestClient) -> None:
        t1 = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        t2 = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        assert t1 != t2


class TestQuarantineValidation:
    """Body / token validation for ``POST /api/memory/repair/quarantine``."""

    def test_rejects_unknown_subsystem(self, client: TestClient) -> None:
        token = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        r = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "totally_made_up", "confirmation_token": token},
        )
        assert r.status_code == 400
        assert "unknown subsystem" in r.json()["detail"]

    def test_rejects_bad_token(self, client: TestClient) -> None:
        r = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "feedback", "confirmation_token": "not-a-real-token"},
        )
        assert r.status_code == 403

    def test_rejects_reused_token(self, client: TestClient, tmp_path: Path) -> None:
        # First call consumes the token via a no-op (no .db file -> nothing to move).
        token = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        r1 = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "feedback", "confirmation_token": token},
        )
        assert r1.status_code == 200

        # Second call with same token -> rejected.
        r2 = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "feedback", "confirmation_token": token},
        )
        assert r2.status_code == 403


class TestQuarantineHappyPath:
    """End-to-end quarantine flow with a real .db triplet on disk.

    ``feedback`` is the simplest target because ``_quiesce_subsystem``
    is a no-op for it (the store opens connections per call), so there
    is no global background thread to coordinate with.
    """

    def _make_triplet(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        db = data / "feedback.db"
        wal = data / "feedback.db-wal"
        shm = data / "feedback.db-shm"
        db.write_bytes(b"fake-sqlite-bytes")
        wal.write_bytes(b"fake-wal")
        shm.write_bytes(b"fake-shm")
        return db, wal, shm

    def test_moves_db_triplet_and_clears_registry(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        db, wal, shm = self._make_triplet(tmp_path)

        degraded_registry.register(
            "feedback", reason="corrupted", details="quick_check failed"
        )
        assert "feedback" in {e["subsystem"] for e in degraded_registry.snapshot()}

        token = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        r = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "feedback", "confirmation_token": token},
        )
        assert r.status_code == 200, r.text

        body = r.json()
        assert body["ok"] is True
        assert body["subsystem"] == "feedback"
        assert body["restart_required"] is True
        assert isinstance(body["quarantined"], list)
        # The .db (and any sibling) should have been moved into
        # data/.quarantine.{stamp}/. Source files are gone.
        assert not db.exists()
        assert not wal.exists()
        assert not shm.exists()
        # Quarantine dir exists with the moved files.
        quarantine_dirs = list((tmp_path / "data").glob(".quarantine.*"))
        assert len(quarantine_dirs) == 1
        moved_names = {p.name for p in quarantine_dirs[0].iterdir()}
        assert "feedback.db" in moved_names

        # Registry cleared -> banner goes away on next /api/health poll.
        assert degraded_registry.snapshot() == []

        # And health endpoint should report the restart_required marker
        # via the memory subsystem block (sticky for this process).
        h = client.get("/api/health").json()
        assert h["degraded_subsystems"] == []
        assert (
            h["memory_subsystem"]["status"] == "repair_completed_restart_required"
        )

    def test_quarantine_when_no_file_exists_still_succeeds(
        self, client: TestClient
    ) -> None:
        """If the user is in degraded state but the .db file is already
        gone (e.g. they manually deleted it), quarantine should still
        succeed and just clear the registry."""
        degraded_registry.register("feedback", reason="missing")

        token = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        r = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "feedback", "confirmation_token": token},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["quarantined"] == []
        assert degraded_registry.snapshot() == []


class TestDesktopTokenGate:
    """When ``OPENAKITA_DESKTOP_SESSION_TOKEN`` is set, the quarantine
    endpoint must reject calls missing / mismatching the header. The
    snapshot endpoint stays unauthenticated (read-only)."""

    def test_quarantine_rejects_missing_desktop_token(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAKITA_DESKTOP_SESSION_TOKEN", "desktop-secret")
        token = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        r = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "feedback", "confirmation_token": token},
        )
        assert r.status_code == 403

    def test_quarantine_accepts_matching_desktop_token(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("OPENAKITA_DESKTOP_SESSION_TOKEN", "desktop-secret")
        token = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        r = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "feedback", "confirmation_token": token},
            headers={"X-OpenAkita-Desktop-Token": "desktop-secret"},
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Post-review hardening — covers the four follow-up fixes for the v1.29
# boot fault tolerance plan:
#
#   1. memory degraded → appears in DegradedRegistry (banner) AND clears
#      itself when the legacy memory_repair flow finishes.
#   2. memory subsystem is *not* a valid quarantine target (the rich
#      restore-from-backup flow under StatusView must own that path).
#   3. token_stats failures register into the same key as token_tracking
#      so users see one banner entry rather than two confused ones.
#   4. per-profile memory failures get a unique registry key so multiple
#      broken profiles don't collapse into a single entry.
# ---------------------------------------------------------------------------


class TestMemoryRepairClearsRegistry:
    """``mark_memory_repair_completed_restart_required`` clears the banner.

    Without this, the user finishes a memory restore in StatusView, the
    backend stays in ``repair_completed_restart_required`` until they
    restart — but the unified yellow banner would also stay up forever,
    making them think the restore did nothing.
    """

    def test_clears_memory_entry(self, client: TestClient) -> None:
        degraded_registry.register("memory", reason="corrupted")
        assert degraded_registry.is_degraded("memory")

        health_module.mark_memory_repair_completed_restart_required()

        assert not degraded_registry.is_degraded("memory")
        # The legacy memory_subsystem flag stays set (sticky until
        # process restart) so StatusView still shows the restart prompt.
        body = client.get("/api/health").json()
        assert body["degraded_subsystems"] == []

    def test_leaves_other_entries_alone(self, client: TestClient) -> None:
        degraded_registry.register("memory", reason="corrupted")
        degraded_registry.register("feedback", reason="corrupted")

        health_module.mark_memory_repair_completed_restart_required()

        keys = {e["subsystem"] for e in degraded_registry.snapshot()}
        assert keys == {"feedback"}


class TestMemoryNotInQuarantineWhitelist:
    """``memory`` must NOT be a generic quarantine target — its real
    repair lives under ``StatusView`` (restore-from-backup, snapshot
    diff, etc.). The frontend offers a different CTA for memory rows;
    the backend defends in depth by rejecting the request even if a
    custom client tries to call ``POST /quarantine`` directly."""

    def test_quarantine_memory_rejected(self, client: TestClient) -> None:
        degraded_registry.register("memory", reason="corrupted")
        token = client.get("/api/memory/repair/degraded").json()["confirmation_token"]
        r = client.post(
            "/api/memory/repair/quarantine",
            json={"subsystem": "memory", "confirmation_token": token},
        )
        # Backend should refuse — memory is not in _SUBSYSTEM_PATHS.
        assert r.status_code == 400
        assert "unknown subsystem" in r.json()["detail"]
        # Entry still degraded (not auto-cleared by the rejection).
        assert degraded_registry.is_degraded("memory")


class TestProfileMemoryUniqueKeys:
    """Two corrupted profiles register two separate entries."""

    def test_distinct_profile_keys(self, client: TestClient) -> None:
        degraded_registry.register("profile_memory:abc", reason="corrupted")
        degraded_registry.register("profile_memory:def", reason="corrupted")

        snap = client.get("/api/health").json()["degraded_subsystems"]
        keys = {e["subsystem"] for e in snap}
        assert keys == {"profile_memory:abc", "profile_memory:def"}


class TestTokenStatsRegistersUnderTokenTrackingKey:
    """``token_stats._get_db`` failures map to the same key as the
    writer thread (``token_tracking``) — they share ``agent.db``, so
    two banner entries would just confuse the user."""

    def test_register_helper_uses_shared_key(self) -> None:
        from openakita.api.routes import token_stats as ts

        ts._register_degraded_token_stats("corrupted", "header magic mismatch")
        keys = {e["subsystem"] for e in degraded_registry.snapshot()}
        assert keys == {"token_tracking"}
        # Reason is preserved; details truncated below 200 chars.
        entry = next(
            e for e in degraded_registry.snapshot() if e["subsystem"] == "token_tracking"
        )
        assert entry["reason"] == "corrupted"
        assert "header magic mismatch" in entry.get("details", "")
