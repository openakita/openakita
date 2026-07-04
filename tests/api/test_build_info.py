"""P-RC-2 P2.8 -- backend build-info endpoint test.

We mount the router on a stand-alone FastAPI app to avoid pulling
the full ``openakita.api.server.create_app`` graph (which triggers
agent / channels / plugins imports). This mirrors the lightweight
fixture used in ``tests/api/test_orgs_v2_stream.py``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import build_info
from openakita.api.routes.build_info import _resolve_build_id


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_info.router)
    return TestClient(app)


def test_build_info_returns_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAKITA_BUILD_ID", "ci-1234abcd")
    with _client() as c:
        r = c.get("/api/build-info")
    assert r.status_code == 200
    assert r.json() == {"build_id": "ci-1234abcd"}


def test_build_info_falls_back_to_dev_when_pkg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAKITA_BUILD_ID", raising=False)

    def _raise(_name: str) -> str:
        raise build_info.PackageNotFoundError("openakita")

    monkeypatch.setattr(build_info, "version", _raise)
    assert _resolve_build_id() == "dev"


def test_build_info_route_is_mounted_on_minimal_app() -> None:
    app = FastAPI()
    app.include_router(build_info.router)
    paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
    assert "/api/build-info" in paths
