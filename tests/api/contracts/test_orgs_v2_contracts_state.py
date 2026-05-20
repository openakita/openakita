"""Contract tests for cluster 3.4 Memory + Events + Policies (B42-B53).

Pairs with ``src/openakita/api/routes/orgs_v2_runtime_state.py``
(P9.7beta-4). Cluster covers blackboard memory CRUD (B42-B44),
event-store queries (B45-B48 events / activity / messages /
audit), and policy markdown CRUD (B49-B53).

Mocks the OrgBlackboard with duck-typed entries that expose
``to_dict()`` and the OrgRuntime with ``get_event_store(org_id)``.
File-IO endpoints (messages / policies) lift ``mgr.get_org_dir``
to a ``tmp_path`` so the route can read / write under
``<org_dir>/logs/`` and ``<org_dir>/policies/`` without touching
the real filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _bb_entry(entry_id: str, content: str = "x", scope: str = "org") -> MagicMock:
    e = MagicMock(spec=["to_dict"])
    e.to_dict.return_value = {
        "id": entry_id,
        "content": content,
        "scope": scope,
        "memory_type": "fact",
    }
    return e


def _set_org_dir(app: FastAPI, org_dir: Path) -> None:
    app.state.org_manager.get_org_dir.return_value = str(org_dir)


# ---------------------------------------------------------------------------
# B42: query memory
# ---------------------------------------------------------------------------


def test_b42_query_memory_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_blackboard.query.return_value = [_bb_entry("m1")]
    resp = mint_client.get("/api/v2/orgs/o1/memory?scope=org&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body[0]["id"] == "m1"


def test_b42_query_memory_400_invalid_scope(mint_app: FastAPI, mint_client: TestClient) -> None:
    resp = mint_client.get("/api/v2/orgs/o1/memory?scope=invalid_scope")
    assert resp.status_code == 400


def test_b42_query_memory_400_invalid_type(mint_app: FastAPI, mint_client: TestClient) -> None:
    resp = mint_client.get("/api/v2/orgs/o1/memory?type=not_real_type")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# B43: add memory
# ---------------------------------------------------------------------------


def test_b43_add_memory_org_201(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_blackboard.write_org.return_value = _bb_entry("m1")
    resp = mint_client.post(
        "/api/v2/orgs/o1/memory",
        json={"scope": "org", "content": "hello", "memory_type": "fact"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "m1"


def test_b43_add_memory_400_empty_content(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/memory", json={"scope": "org", "content": ""})
    assert resp.status_code == 400


def test_b43_add_memory_400_bad_scope(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/memory", json={"scope": "weird", "content": "hi"})
    assert resp.status_code == 400


def test_b43_add_memory_400_dept_without_owner(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/memory", json={"scope": "department", "content": "hi"})
    assert resp.status_code == 400


def test_b43_add_memory_dept_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_blackboard.write_department.return_value = _bb_entry(
        "m2", scope="department"
    )
    resp = mint_client.post(
        "/api/v2/orgs/o1/memory",
        json={
            "scope": "department",
            "content": "hi",
            "scope_owner": "engineering",
        },
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# B44: delete memory
# ---------------------------------------------------------------------------


def test_b44_delete_memory_ok(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_blackboard.delete_entry.return_value = True
    resp = mint_client.delete("/api/v2/orgs/o1/memory/m1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_b44_delete_memory_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_blackboard.delete_entry.return_value = False
    resp = mint_client.delete("/api/v2/orgs/o1/memory/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B45-B46: events + activity
# ---------------------------------------------------------------------------


def test_b45_events_404_when_no_store(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_event_store.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/events")
    assert resp.status_code == 404


def test_b45_events_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    es = MagicMock()
    es.query.return_value = [{"id": "e1", "event_type": "node_created"}]
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.get("/api/v2/orgs/o1/events?limit=5")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "e1"
    es.query.assert_called_once()


def test_b46_activity_returns_envelope_when_no_store(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_runtime.get_event_store.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/activity")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "count": 0}


def test_b46_activity_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    es = MagicMock()
    es.query.return_value = [{"id": "e1"}, {"id": "e2"}]
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.get("/api/v2/orgs/o1/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2


# ---------------------------------------------------------------------------
# B47: messages (file IO)
# ---------------------------------------------------------------------------


def test_b47_messages_empty_when_log_missing(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _set_org_dir(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/o1/messages")
    assert resp.status_code == 200
    assert resp.json() == {"messages": [], "count": 0}


def test_b47_messages_filters_by_node(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _set_org_dir(mint_app, tmp_path)
    log = tmp_path / "logs" / "communications.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join(
            [
                json.dumps({"from_node": "a", "to_node": "b", "msg": "hi"}),
                json.dumps({"from_node": "c", "to_node": "b", "msg": "yo"}),
            ]
        ),
        encoding="utf-8",
    )
    resp = mint_client.get("/api/v2/orgs/o1/messages?from_node=a")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["messages"][0]["from_node"] == "a"


# ---------------------------------------------------------------------------
# B48: audit log
# ---------------------------------------------------------------------------


def test_b48_audit_log_empty_when_no_store(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_event_store.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/audit-log")
    assert resp.status_code == 200
    assert resp.json() == []


def test_b48_audit_log_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    es = MagicMock()
    es.get_audit_log = MagicMock(return_value=[{"event": "audit_x"}])
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.get("/api/v2/orgs/o1/audit-log?days=14")
    assert resp.status_code == 200
    assert resp.json() == [{"event": "audit_x"}]
    es.get_audit_log.assert_called_once_with(days=14)


# ---------------------------------------------------------------------------
# B49-B53: policies CRUD
# ---------------------------------------------------------------------------


def test_b49_list_policies_empty_when_dir_missing(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _set_org_dir(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/o1/policies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_b49_list_policies_returns_files(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _set_org_dir(mint_app, tmp_path)
    pdir = tmp_path / "policies"
    pdir.mkdir()
    (pdir / "a.md").write_text("# a")
    (pdir / "b.md").write_text("# b")
    resp = mint_client.get("/api/v2/orgs/o1/policies")
    assert resp.status_code == 200
    body = resp.json()
    assert {x["filename"] for x in body} == {"a.md", "b.md"}


def test_b50_search_policies_400_when_no_q(mint_client: TestClient) -> None:
    resp = mint_client.get("/api/v2/orgs/o1/policies/search")
    assert resp.status_code == 400


def test_b50_search_policies_empty_when_no_search_method(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_runtime.get_policies.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/policies/search?q=hello")
    assert resp.status_code == 200
    assert resp.json() == []


def test_b51_read_policy_404(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _set_org_dir(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/o1/policies/missing.md")
    assert resp.status_code == 404


def test_b51_read_policy_400_traversal(mint_client: TestClient) -> None:
    resp = mint_client.get("/api/v2/orgs/o1/policies/..bad.md")
    assert resp.status_code == 400


def test_b51_read_policy_happy(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _set_org_dir(mint_app, tmp_path)
    pdir = tmp_path / "policies"
    pdir.mkdir()
    (pdir / "x.md").write_text("# hello", encoding="utf-8")
    resp = mint_client.get("/api/v2/orgs/o1/policies/x.md")
    assert resp.status_code == 200
    assert resp.json()["content"] == "# hello"


def test_b52_write_policy_creates_file(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _set_org_dir(mint_app, tmp_path)
    resp = mint_client.put("/api/v2/orgs/o1/policies/x.md", json={"content": "# new"})
    assert resp.status_code == 200
    assert (tmp_path / "policies" / "x.md").read_text(encoding="utf-8") == "# new"


def test_b52_write_policy_400_traversal(mint_client: TestClient) -> None:
    resp = mint_client.put("/api/v2/orgs/o1/policies/..bad.md", json={"content": "x"})
    assert resp.status_code == 400


def test_b53_delete_policy_404(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _set_org_dir(mint_app, tmp_path)
    resp = mint_client.delete("/api/v2/orgs/o1/policies/missing.md")
    assert resp.status_code == 404


def test_b53_delete_policy_happy(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _set_org_dir(mint_app, tmp_path)
    pdir = tmp_path / "policies"
    pdir.mkdir()
    (pdir / "x.md").write_text("# hello", encoding="utf-8")
    resp = mint_client.delete("/api/v2/orgs/o1/policies/x.md")
    assert resp.status_code == 200
    assert not (pdir / "x.md").exists()
