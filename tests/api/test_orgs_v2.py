"""HTTP-level tests for the v2 organisation API facade.

These tests build a minimal FastAPI app on the fly with only the v2
router mounted. They do not boot the rest of the application, so
they are immune to the legacy import side effects in
``api/routes/orgs.py``.

The feature flag ``settings.runtime_v2_enabled`` is mutated through
``monkeypatch.setattr`` so individual tests can flip it without
leaking state to other tests.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import orgs_v2
from openakita.config import settings


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Return a TestClient bound to a one-off app with v2 enabled.

    Each test gets a freshly-constructed app + a fresh registry-
    bootstrap latch so that registration side effects from one test
    cannot leak into another.
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    monkeypatch.setattr(orgs_v2, "_BOOTSTRAPPED", False, raising=False)
    app = FastAPI()
    app.include_router(orgs_v2.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def disabled_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "runtime_v2_enabled", False, raising=False)
    monkeypatch.setattr(orgs_v2, "_BOOTSTRAPPED", False, raising=False)
    app = FastAPI()
    app.include_router(orgs_v2.router)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Feature-flag gating
# ---------------------------------------------------------------------------


def test_list_returns_404_when_v2_disabled(disabled_client: TestClient) -> None:
    resp = disabled_client.get("/api/v2/orgs/templates")
    assert resp.status_code == 404
    assert "runtime v2 is disabled" in resp.json()["detail"]


def test_get_returns_404_when_v2_disabled(disabled_client: TestClient) -> None:
    resp = disabled_client.get("/api/v2/orgs/templates/aigc_video_studio")
    assert resp.status_code == 404


def test_instantiate_returns_404_when_v2_disabled(disabled_client: TestClient) -> None:
    resp = disabled_client.post(
        "/api/v2/orgs/templates/aigc_video_studio/instantiate",
        json={"name": "Acme"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_list_returns_envelope_with_count_and_known_templates(
    client: TestClient,
) -> None:
    resp = client.get("/api/v2/orgs/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert "templates" in body
    assert body["count"] == len(body["templates"])
    assert body["count"] >= 4
    ids = {t["id"] for t in body["templates"]}
    assert "aigc_video_studio" in ids
    assert "software_team" in ids
    assert "startup_company" in ids
    assert "content_ops" in ids


def test_list_returns_jsonable_node_and_edge_records(client: TestClient) -> None:
    body = client.get("/api/v2/orgs/templates").json()
    aigc = next(t for t in body["templates"] if t["id"] == "aigc_video_studio")
    assert "nodes" in aigc and isinstance(aigc["nodes"], list)
    assert "edges" in aigc and isinstance(aigc["edges"], list)
    # node entries carry the v2 schema shape, not the legacy shape
    sample = aigc["nodes"][0]
    assert {"id", "type", "role", "label"}.issubset(sample.keys())
    assert "position" not in sample, "v2 wire format must not leak legacy x/y"
    assert "department" not in sample, "v2 wire format must not leak legacy department"


# ---------------------------------------------------------------------------
# Get endpoint
# ---------------------------------------------------------------------------


def test_get_returns_single_template(client: TestClient) -> None:
    resp = client.get("/api/v2/orgs/templates/software_team")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "software_team"
    assert {n["id"] for n in body["nodes"]} == {
        "tech_lead",
        "fe_lead",
        "fe_dev_a",
        "fe_dev_b",
        "be_lead",
        "be_dev_a",
        "be_dev_b",
        "qa",
        "devops_eng",
        "tech_writer",
    }


def test_get_unknown_template_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v2/orgs/templates/no_such_template")
    assert resp.status_code == 404
    assert "no_such_template" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Instantiate endpoint
# ---------------------------------------------------------------------------


def test_instantiate_returns_jsonable_orgv2_with_fresh_ids(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v2/orgs/templates/content_ops/instantiate",
        json={"name": "Acme Editorial"},
    )
    assert resp.status_code == 200
    org = resp.json()
    assert org["name"] == "Acme Editorial"
    assert org["template_id"] == "content_ops"
    assert org["id"].startswith("org_")
    assert len(org["nodes"]) == 7
    assert len(org["edges"]) == 11
    for node in org["nodes"]:
        assert node["id"].startswith("node_")
    for edge in org["edges"]:
        assert edge["id"].startswith("edge_")


def test_instantiate_two_calls_yield_disjoint_orgs(client: TestClient) -> None:
    a = client.post(
        "/api/v2/orgs/templates/software_team/instantiate",
        json={"name": "Alpha"},
    ).json()
    b = client.post(
        "/api/v2/orgs/templates/software_team/instantiate",
        json={"name": "Beta"},
    ).json()
    assert a["id"] != b["id"]
    a_ids = {n["id"] for n in a["nodes"]}
    b_ids = {n["id"] for n in b["nodes"]}
    assert a_ids.isdisjoint(b_ids)


def test_instantiate_applies_persona_override(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs/templates/aigc_video_studio/instantiate",
        json={
            "name": "Demo",
            "node_persona_prompts": {"art_director": "你是新美术指导。"},
        },
    )
    assert resp.status_code == 200
    org = resp.json()
    art = next(n for n in org["nodes"] if n["role"] == "art_director")
    assert art["persona_prompt"] == "你是新美术指导。"


def test_instantiate_applies_defaults_override(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs/templates/software_team/instantiate",
        json={"name": "x", "defaults": {"max_turns": 99}},
    )
    assert resp.status_code == 200
    assert resp.json()["defaults"]["max_turns"] == 99


def test_instantiate_unknown_template_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs/templates/no_such_template/instantiate",
        json={"name": "Demo"},
    )
    assert resp.status_code == 404


def test_instantiate_unknown_override_key_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs/templates/software_team/instantiate",
        json={"name": "x", "defaults": {"max_task_seconds": 60}},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "max_task_seconds" in detail


def test_instantiate_unknown_node_id_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs/templates/software_team/instantiate",
        json={
            "name": "x",
            "node_persona_prompts": {"no_such_node": "..."},
        },
    )
    assert resp.status_code == 400
    assert "no_such_node" in resp.json()["detail"]


def test_instantiate_missing_name_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs/templates/software_team/instantiate",
        json={},
    )
    assert resp.status_code == 422
