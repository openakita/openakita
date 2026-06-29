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
from openakita.orgs import reset_default_store


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    """Return a TestClient bound to a one-off app with v2 enabled.

    Each test gets a freshly-constructed app + a fresh registry-
    bootstrap latch + a tmp-rooted org store so that registration
    side effects and persisted orgs from one test cannot leak into
    another.
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    monkeypatch.setattr(orgs_v2, "_BOOTSTRAPPED", False, raising=False)
    reset_default_store(path=tmp_path / "orgs_v2.json")
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
    resp = disabled_client.get("/api/v2/orgs-spec/templates")
    assert resp.status_code == 404
    assert "runtime v2 is disabled" in resp.json()["detail"]


def test_get_returns_404_when_v2_disabled(disabled_client: TestClient) -> None:
    resp = disabled_client.get("/api/v2/orgs-spec/templates/aigc_video_studio")
    assert resp.status_code == 404


def test_instantiate_returns_404_when_v2_disabled(disabled_client: TestClient) -> None:
    resp = disabled_client.post(
        "/api/v2/orgs-spec/templates/aigc_video_studio/instantiate",
        json={"name": "Acme"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_list_returns_envelope_with_count_and_known_templates(
    client: TestClient,
) -> None:
    resp = client.get("/api/v2/orgs-spec/templates")
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
    body = client.get("/api/v2/orgs-spec/templates").json()
    aigc = next(t for t in body["templates"] if t["id"] == "aigc_video_studio")
    assert "nodes" in aigc and isinstance(aigc["nodes"], list)
    assert "edges" in aigc and isinstance(aigc["edges"], list)
    # node entries carry the v2 schema shape, not the legacy shape
    sample = aigc["nodes"][0]
    assert {"id", "type", "role", "label"}.issubset(sample.keys())
    assert "position" not in sample, "v2 wire format must not leak legacy x/y"
    # ``department`` is now a first-class (but optional) v2 schema field —
    # the migration originally dropped it, which left v2-instantiated orgs
    # with an empty department and broke the blackboard's department tier.
    # Built-in templates populate it (producer -> 制作部).
    assert sample.get("department") == "制作部"


# ---------------------------------------------------------------------------
# Get endpoint
# ---------------------------------------------------------------------------


def test_get_returns_single_template(client: TestClient) -> None:
    resp = client.get("/api/v2/orgs-spec/templates/software_team")
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
    resp = client.get("/api/v2/orgs-spec/templates/no_such_template")
    assert resp.status_code == 404
    assert "no_such_template" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Instantiate endpoint
# ---------------------------------------------------------------------------


def test_instantiate_returns_jsonable_orgv2_with_fresh_ids(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v2/orgs-spec/templates/content_ops/instantiate",
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
        "/api/v2/orgs-spec/templates/software_team/instantiate",
        json={"name": "Alpha"},
    ).json()
    b = client.post(
        "/api/v2/orgs-spec/templates/software_team/instantiate",
        json={"name": "Beta"},
    ).json()
    assert a["id"] != b["id"]
    a_ids = {n["id"] for n in a["nodes"]}
    b_ids = {n["id"] for n in b["nodes"]}
    assert a_ids.isdisjoint(b_ids)


def test_instantiate_applies_persona_override(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs-spec/templates/aigc_video_studio/instantiate",
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
        "/api/v2/orgs-spec/templates/software_team/instantiate",
        json={"name": "x", "defaults": {"max_turns": 99}},
    )
    assert resp.status_code == 200
    assert resp.json()["defaults"]["max_turns"] == 99


def test_instantiate_unknown_template_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs-spec/templates/no_such_template/instantiate",
        json={"name": "Demo"},
    )
    assert resp.status_code == 404


def test_instantiate_unknown_override_key_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs-spec/templates/software_team/instantiate",
        json={"name": "x", "defaults": {"max_task_seconds": 60}},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "max_task_seconds" in detail


def test_instantiate_unknown_node_id_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs-spec/templates/software_team/instantiate",
        json={
            "name": "x",
            "node_persona_prompts": {"no_such_node": "..."},
        },
    )
    assert resp.status_code == 400
    assert "no_such_node" in resp.json()["detail"]


def test_instantiate_missing_name_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/api/v2/orgs-spec/templates/software_team/instantiate",
        json={},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# OrgV2 resource CRUD (Phase 6)
# ---------------------------------------------------------------------------


def _instantiate(client: TestClient, template_id: str = "content_ops", **kw) -> dict:
    payload = {"name": kw.pop("name", "Test Org")}
    payload.update(kw)
    resp = client.post(f"/api/v2/orgs-spec/templates/{template_id}/instantiate", json=payload)
    assert resp.status_code == 200
    return resp.json()


def test_create_then_list_returns_persisted_org(client: TestClient) -> None:
    org = _instantiate(client, name="Acme Editorial")
    resp = client.post("/api/v2/orgs-spec", json={"org": org})
    assert resp.status_code == 201
    saved = resp.json()
    assert saved["id"] == org["id"]
    listing = client.get("/api/v2/orgs-spec").json()
    assert listing["count"] == 1
    assert listing["orgs"][0]["id"] == org["id"]


def test_create_duplicate_returns_409(client: TestClient) -> None:
    org = _instantiate(client, name="Once")
    client.post("/api/v2/orgs-spec", json={"org": org})
    resp = client.post("/api/v2/orgs-spec", json={"org": org})
    assert resp.status_code == 409


def test_get_unknown_org_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v2/orgs-spec/org_does_not_exist")
    assert resp.status_code == 404


def test_get_persisted_org_round_trips(client: TestClient) -> None:
    org = _instantiate(client, name="Round Trip")
    client.post("/api/v2/orgs-spec", json={"org": org})
    got = client.get(f"/api/v2/orgs-spec/{org['id']}").json()
    assert got["id"] == org["id"]
    assert got["name"] == "Round Trip"


def test_patch_updates_name_and_description(client: TestClient) -> None:
    org = _instantiate(client, name="Old")
    client.post("/api/v2/orgs-spec", json={"org": org})
    resp = client.patch(
        f"/api/v2/orgs-spec/{org['id']}",
        json={"name": "New", "description": "now editorial"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New"
    assert body["description"] == "now editorial"


def test_patch_unknown_org_returns_404(client: TestClient) -> None:
    resp = client.patch("/api/v2/orgs-spec/org_does_not_exist", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_removes_org(client: TestClient) -> None:
    org = _instantiate(client, name="Will Delete")
    client.post("/api/v2/orgs-spec", json={"org": org})
    del_resp = client.delete(f"/api/v2/orgs-spec/{org['id']}")
    assert del_resp.status_code == 204
    assert client.get(f"/api/v2/orgs-spec/{org['id']}").status_code == 404


def test_delete_unknown_org_returns_404(client: TestClient) -> None:
    resp = client.delete("/api/v2/orgs-spec/org_does_not_exist")
    assert resp.status_code == 404


def test_create_returns_400_on_malformed_payload(client: TestClient) -> None:
    resp = client.post("/api/v2/orgs-spec", json={"org": {"id": "x"}})
    assert resp.status_code == 400


def test_crud_returns_404_when_v2_disabled(disabled_client: TestClient) -> None:
    assert disabled_client.get("/api/v2/orgs-spec").status_code == 404
    assert disabled_client.post("/api/v2/orgs-spec", json={"org": {}}).status_code == 404
    assert disabled_client.get("/api/v2/orgs-spec/x").status_code == 404
    assert disabled_client.patch("/api/v2/orgs-spec/x", json={}).status_code == 404
    assert disabled_client.delete("/api/v2/orgs-spec/x").status_code == 404


# ---------------------------------------------------------------------------
# Node ``level`` derivation (template level dual-path fix, 2026-06)
# ---------------------------------------------------------------------------
#
# Root cause: the OrgV2 ``NodeV2`` wire shape has no ``level`` field
# (hierarchy lives in HIERARCHY edges). The OrgV2 -> v1 org.json
# projection (``_orgv2_node_dict_to_orgnode_data``) never set ``level``,
# so v2-instantiated orgs persisted ``level=0`` for EVERY node while the
# v1 dict templates (``_runtime_templates.CONTENT_OPS``) hard-code the
# right 0/1/2 levels. We now derive depth along HIERARCHY edges so BOTH
# paths land identical, correct levels.


def test_derive_node_levels_ignores_collaborate_and_handles_depth() -> None:
    from openakita.api.routes.orgs_v2 import _derive_node_levels

    nodes = [{"id": n} for n in ("root", "mid_a", "mid_b", "leaf", "peer")]
    edges = [
        {"src": "root", "dst": "mid_a", "kind": "hierarchy"},
        {"src": "root", "dst": "mid_b", "kind": "hierarchy"},
        {"src": "mid_a", "dst": "leaf", "kind": "hierarchy"},
        # COLLABORATE must NOT deepen a node's level.
        {"src": "leaf", "dst": "peer", "kind": "collaborate"},
        {"src": "mid_b", "dst": "peer", "kind": "hierarchy"},
    ]
    levels = _derive_node_levels(nodes, edges)
    assert levels == {"root": 0, "mid_a": 1, "mid_b": 1, "leaf": 2, "peer": 2}


def test_derive_node_levels_terminates_on_cycle() -> None:
    from openakita.api.routes.orgs_v2 import _derive_node_levels

    nodes = [{"id": "a"}, {"id": "b"}]
    edges = [
        {"src": "a", "dst": "b", "kind": "hierarchy"},
        {"src": "b", "dst": "a", "kind": "hierarchy"},  # malformed cycle
    ]
    # Must not hang; ``a`` is a root only if it has no incoming hierarchy
    # edge. Here both have one, so neither is a root -> all default 0.
    assert _derive_node_levels(nodes, edges) == {"a": 0, "b": 0}


def test_v1_content_ops_dict_levels_match_derived_depth() -> None:
    """v1 dict path parity: the hard-coded CONTENT_OPS levels must equal
    the depth our algorithm derives from the SAME hierarchy edges."""
    from openakita.api.routes.orgs_v2 import _derive_node_levels
    from openakita.orgs._runtime_templates import CONTENT_OPS

    nodes = [{"id": n["id"]} for n in CONTENT_OPS["nodes"]]
    edges = [
        {"src": e["source"], "dst": e["target"], "kind": e["edge_type"]}
        for e in CONTENT_OPS["edges"]
    ]
    derived = _derive_node_levels(nodes, edges)
    hardcoded = {n["id"]: n["level"] for n in CONTENT_OPS["nodes"]}
    assert derived == hardcoded
    # Sanity: it really is a 3-level pyramid, not a degenerate all-zero map.
    assert set(hardcoded.values()) == {0, 1, 2}


def test_instantiate_content_ops_projects_correct_levels(client: TestClient) -> None:
    """v2 instantiate path: the projected org.json must carry derived
    levels (editor=0, planner/seo/data=1, writers/visual=2) instead of the
    pre-fix all-zero degenerate map."""
    from openakita.api.routes.orgs_v2 import _orgv2_dict_to_organization_data

    org = _instantiate(client, name="Levels")
    data = _orgv2_dict_to_organization_data(org)
    # role-handle ('role') survives into ``agent_profile_id`` on the
    # projected v1 node, so we can map level -> the roles at that depth.
    by_role = {n["agent_profile_id"]: n["level"] for n in data["nodes"]}
    assert by_role["editor_in_chief"] == 0
    assert by_role["content_planner"] == 1
    assert by_role["seo_optimizer"] == 1
    assert by_role["data_analyst"] == 1
    assert by_role["visual_designer"] == 2
    # two writers share role "writer"
    writer_levels = [n["level"] for n in data["nodes"] if n["agent_profile_id"] == "writer"]
    assert writer_levels == [2, 2]
    assert {n["level"] for n in data["nodes"]} == {0, 1, 2}


def test_v1_v2_content_ops_departments_match(client: TestClient) -> None:
    """Dual-path parity: the v2-instantiated content_ops org must project
    the SAME department per role as the legacy v1 dict template.

    Before the fix the v2 schema dropped ``department`` entirely, so every
    projected node landed with ``department=""`` while v1 carried
    编辑部/创作组/运营组 — which broke the blackboard's department tier."""
    from openakita.api.routes.orgs_v2 import _orgv2_dict_to_organization_data
    from openakita.orgs._runtime_templates import CONTENT_OPS

    # v1 dict path: role_title -> department (role_title is the stable label)
    v1_by_role = {n["id"].replace("-", "_"): n["department"] for n in CONTENT_OPS["nodes"]}

    org = _instantiate(client, name="Depts")
    data = _orgv2_dict_to_organization_data(org)
    # every projected node carries a non-empty department
    assert all(n["department"] for n in data["nodes"])
    # role-handle survives into agent_profile_id; group departments by role
    v2_by_role: dict[str, set[str]] = {}
    for n in data["nodes"]:
        v2_by_role.setdefault(n["agent_profile_id"], set()).add(n["department"])
    # spot-check parity against the v1 dict's intent
    assert v2_by_role["editor_in_chief"] == {"编辑部"}
    assert v2_by_role["content_planner"] == {"编辑部"}
    assert v2_by_role["writer"] == {"创作组"}
    assert v2_by_role["visual_designer"] == {"创作组"}
    assert v2_by_role["seo_optimizer"] == {"运营组"}
    assert v2_by_role["data_analyst"] == {"运营组"}
    # the full department set matches the v1 dict's department set
    assert {n["department"] for n in data["nodes"]} == set(v1_by_role.values())


def test_instantiate_omits_department_for_user_template_without_one(
    client: TestClient,
) -> None:
    """Graceful empty: a NodeV2 with no department must project to "" rather
    than an invented value (user-authored templates may not model depts)."""
    from openakita.api.routes.orgs_v2 import _orgv2_node_dict_to_orgnode_data

    projected = _orgv2_node_dict_to_orgnode_data({"id": "n1", "role": "x"}, level=0)
    assert projected["department"] == ""
