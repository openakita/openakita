"""C17 Phase C — /api/healthz + /api/readyz 集成测试。

覆盖：

- ``/api/healthz`` 不论 app.state 怎么坏都返回 200
- ``/api/readyz`` 在所有 readiness check 通过时返回 200
- 任何子系统 fail → 503 + ``failing[]`` 含错误
- 远程客户端只看到 ``failing[].name``，不暴露 ``details`` 路径
- 5s 缓存避免热轮询打到底层 chain-verify
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import health as health_module


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    # Reset the readyz cache between tests so monkeypatching propagates.
    health_module._readyz_cache.update({"ts": 0.0, "payload": None, "ready": False})
    app = FastAPI()
    app.include_router(health_module.router)
    # Sensible defaults; individual tests overwrite via app.state.
    app.state.scheduler = None
    app.state.gateway = None
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/healthz — liveness
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert isinstance(body["ts"], (int, float))
        assert isinstance(body["pid"], int) and body["pid"] > 0

    def test_returns_200_even_when_policy_broken(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """healthz never depends on policy / audit / etc. by design."""

        def _boom():
            raise RuntimeError("policy import boom")

        monkeypatch.setattr(
            "openakita.core.policy_v2.global_engine.get_engine_v2",
            _boom,
        )
        r = client.get("/api/healthz")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/readyz — readiness
# ---------------------------------------------------------------------------


def _force_all_checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every check to return None (no failures)."""

    async def _ok_async() -> None:
        return None

    monkeypatch.setattr(health_module, "_check_policy_engine", _ok_async)
    monkeypatch.setattr(health_module, "_check_audit_chain", _ok_async)
    monkeypatch.setattr(health_module, "_check_event_loop_lag", _ok_async)
    monkeypatch.setattr(health_module, "_check_scheduler", lambda req: None)
    monkeypatch.setattr(health_module, "_check_gateway", lambda req: None)


class TestReadyzHappyPath:
    def test_all_checks_pass_returns_200(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_all_checks_pass(monkeypatch)
        r = client.get("/api/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True
        assert body["failing"] == []


class TestReadyzFailurePaths:
    def test_policy_check_failure_returns_503(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_all_checks_pass(monkeypatch)

        async def _fail() -> dict:
            return {"name": "policy_v2", "details": "/private/path/POLICIES.yaml"}

        monkeypatch.setattr(health_module, "_check_policy_engine", _fail)
        r = client.get("/api/readyz")
        assert r.status_code == 503
        body = r.json()
        assert body["ready"] is False
        names = [f["name"] for f in body["failing"]]
        assert "policy_v2" in names

    def test_event_loop_lag_failure(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_all_checks_pass(monkeypatch)

        async def _lag() -> dict:
            return {"name": "event_loop", "details": "lag 800ms"}

        monkeypatch.setattr(health_module, "_check_event_loop_lag", _lag)
        r = client.get("/api/readyz")
        assert r.status_code == 503
        assert any(f["name"] == "event_loop" for f in r.json()["failing"])

    def test_scheduler_failure(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_all_checks_pass(monkeypatch)
        monkeypatch.setattr(
            health_module,
            "_check_scheduler",
            lambda req: {"name": "scheduler", "details": "not running"},
        )
        r = client.get("/api/readyz")
        assert r.status_code == 503

    def test_check_exception_bubbles_as_internal(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_all_checks_pass(monkeypatch)

        async def _explode() -> dict:
            raise RuntimeError("boom inside check")

        monkeypatch.setattr(health_module, "_check_audit_chain", _explode)
        r = client.get("/api/readyz")
        assert r.status_code == 503
        body = r.json()
        names = [f["name"] for f in body["failing"]]
        assert "internal" in names


class TestReadyzSanitization:
    def test_remote_caller_does_not_see_details(
        self,
        app: FastAPI,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _force_all_checks_pass(monkeypatch)

        async def _fail() -> dict:
            return {"name": "audit_chain", "details": "C:/secrets/audit.jsonl"}

        monkeypatch.setattr(health_module, "_check_audit_chain", _fail)
        monkeypatch.setattr(health_module, "_is_localhost", lambda req: False)

        client = TestClient(app)
        r = client.get("/api/readyz")
        assert r.status_code == 503
        body = r.json()
        for f in body["failing"]:
            assert "details" not in f
            assert "name" in f
        # Crucially: no secret path leaked anywhere in the JSON.
        assert "secrets" not in json.dumps(body)

    def test_localhost_sees_full_details(
        self,
        app: FastAPI,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _force_all_checks_pass(monkeypatch)

        async def _fail() -> dict:
            return {"name": "audit_chain", "details": "TAIL_PARSE_ERROR_AT_LINE_7"}

        monkeypatch.setattr(health_module, "_check_audit_chain", _fail)
        monkeypatch.setattr(health_module, "_is_localhost", lambda req: True)

        client = TestClient(app)
        r = client.get("/api/readyz")
        body = r.json()
        msgs = [f.get("details", "") for f in body["failing"]]
        assert any("TAIL_PARSE_ERROR" in m for m in msgs)


class TestReadyzCache:
    def test_cached_within_ttl(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_all_checks_pass(monkeypatch)
        call_count = {"n": 0}

        async def _counting_check() -> None:
            call_count["n"] += 1
            return None

        # Force one async check to count invocations.
        monkeypatch.setattr(health_module, "_check_policy_engine", _counting_check)

        # First request should compute; second within TTL should reuse cache.
        r1 = client.get("/api/readyz")
        assert r1.status_code == 200
        assert call_count["n"] == 1
        r2 = client.get("/api/readyz")
        assert r2.status_code == 200
        assert call_count["n"] == 1  # cached

    def test_cache_invalidated_after_ttl(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_all_checks_pass(monkeypatch)
        call_count = {"n": 0}

        async def _counting_check() -> None:
            call_count["n"] += 1
            return None

        monkeypatch.setattr(health_module, "_check_policy_engine", _counting_check)

        # First request computes.
        client.get("/api/readyz")
        assert call_count["n"] == 1

        # Roll back the cache timestamp past the TTL to simulate expiry.
        health_module._readyz_cache["ts"] = time.time() - (
            health_module._READYZ_CACHE_TTL_SECONDS + 1
        )

        client.get("/api/readyz")
        assert call_count["n"] == 2
