"""Contract tests for cluster 3.5 Inbox + Scaling + Reports + Stats (B54-B67).

Pairs with ``src/openakita/api/routes/orgs_v2_runtime_ops.py``
(P9.7beta-5). Cluster covers org inbox CRUD (B54-B57), scaling
governance (B58-B62 requests / approve / reject / clone / recruit),
status snapshot (B63), stats aggregation (B64), and reports list
/ summary / generate (B65-B67).

Inbox + scaling are duck-typed off the OrgRuntime via
``rt.get_inbox(org_id)`` / ``rt.get_scaler()`` factory methods;
status / stats / reports use direct attribute access on rt.
File-IO endpoints lift ``mgr.get_org_dir`` to a ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _set_org_dir(app: FastAPI, org_dir: Path) -> None:
    app.state.org_manager.get_org_dir.return_value = str(org_dir)


from tests.api.contracts.conftest import _async_return, _async_raise


# ---------------------------------------------------------------------------
# B54-B57: inbox
# ---------------------------------------------------------------------------


def _wire_inbox(app: FastAPI, **overrides) -> MagicMock:
    inbox = MagicMock()
    inbox.list_messages.return_value = overrides.get("messages", [])
    inbox.unread_count.return_value = overrides.get("unread", 0)
    inbox.pending_approval_count.return_value = overrides.get("pending", 0)
    inbox.mark_read.return_value = overrides.get("mark_read", True)
    inbox.mark_all_read.return_value = overrides.get("mark_all", 0)
    inbox.resolve_approval.return_value = overrides.get("resolve")
    app.state.org_runtime.get_inbox.return_value = inbox
    return inbox


def test_b54_list_inbox_empty_envelope_when_none(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_runtime.get_inbox.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/inbox")
    assert resp.status_code == 200
    assert resp.json() == {"messages": [], "unread_count": 0, "pending_approvals": 0}


def test_b54_list_inbox_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    msg = MagicMock(spec=["to_dict"])
    msg.to_dict.return_value = {"id": "m1", "category": "ping"}
    _wire_inbox(mint_app, messages=[msg], unread=2, pending=1)
    resp = mint_client.get("/api/v2/orgs/o1/inbox")
    assert resp.status_code == 200
    body = resp.json()
    assert body["unread_count"] == 2
    assert body["pending_approvals"] == 1
    assert body["messages"][0]["id"] == "m1"


def test_b55_mark_inbox_read_ok(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_inbox(mint_app, mark_read=True)
    resp = mint_client.post("/api/v2/orgs/o1/inbox/m1/read")
    assert resp.status_code == 200


def test_b55_mark_inbox_read_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_inbox(mint_app, mark_read=False)
    resp = mint_client.post("/api/v2/orgs/o1/inbox/missing/read")
    assert resp.status_code == 404


def test_b56_mark_all_returns_count(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_inbox(mint_app, mark_all=5)
    resp = mint_client.post("/api/v2/orgs/o1/inbox/read-all")
    assert resp.status_code == 200
    assert resp.json() == {"marked": 5}


def test_b57_resolve_400_bad_decision(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_inbox(mint_app)
    resp = mint_client.post("/api/v2/orgs/o1/inbox/m1/resolve", json={"decision": "weird"})
    assert resp.status_code == 400


def test_b57_resolve_404_when_msg_missing(mint_app: FastAPI, mint_client: TestClient) -> None:
    _wire_inbox(mint_app, resolve=None)
    resp = mint_client.post("/api/v2/orgs/o1/inbox/missing/resolve", json={"decision": "approve"})
    assert resp.status_code == 404


def test_b57_resolve_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    msg = MagicMock(spec=["to_dict"])
    msg.to_dict.return_value = {"id": "m1", "decision": "approve"}
    _wire_inbox(mint_app, resolve=msg)
    resp = mint_client.post("/api/v2/orgs/o1/inbox/m1/resolve", json={"decision": "approve"})
    assert resp.status_code == 200
    assert resp.json()["decision"] == "approve"


# ---------------------------------------------------------------------------
# B58-B62: scaling
# ---------------------------------------------------------------------------


def _wire_scaler(app: FastAPI) -> MagicMock:
    scaler = MagicMock()
    app.state.org_runtime.get_scaler = MagicMock(return_value=scaler)
    return scaler


def test_b58_scaling_503_when_no_scaler(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_scaler = MagicMock(return_value=None)
    resp = mint_client.get("/api/v2/orgs/o1/scaling/requests")
    assert resp.status_code == 503


def test_b58_scaling_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    req = MagicMock(
        id="r1",
        request_type="recruit",
        requester_node_id="ceo",
        role_title="dev",
        status="pending",
        created_at="2026-01-01",
    )
    scaler.get_pending_requests.return_value = [req]
    resp = mint_client.get("/api/v2/orgs/o1/scaling/requests")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "r1"


def test_b59_approve_400_on_value_error(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    scaler.approve_request = _async_raise(ValueError("nope"))
    resp = mint_client.post("/api/v2/orgs/o1/scaling/r1/approve")
    assert resp.status_code == 400


def test_b59_approve_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    req = MagicMock(id="r1", status="approved", result_node_id="n_new")
    scaler.approve_request = _async_return(req)
    resp = mint_client.post("/api/v2/orgs/o1/scaling/r1/approve")
    assert resp.status_code == 200
    assert resp.json()["result_node_id"] == "n_new"


def test_b60_reject_400_on_value_error(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    scaler.reject_request.side_effect = ValueError("bad")
    resp = mint_client.post("/api/v2/orgs/o1/scaling/r1/reject", json={"reason": "no fit"})
    assert resp.status_code == 400


def test_b60_reject_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    scaler.reject_request.return_value = MagicMock(id="r1", status="rejected")
    resp = mint_client.post("/api/v2/orgs/o1/scaling/r1/reject", json={"reason": "no fit"})
    assert resp.status_code == 200


def test_b61_clone_400_when_no_source(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/scale/clone", json={})
    assert resp.status_code == 400


def test_b61_clone_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    req = MagicMock(id="r1", status="approved", result_node_id="n_clone")
    scaler.request_clone = _async_return(req)
    resp = mint_client.post("/api/v2/orgs/o1/scale/clone", json={"source_node_id": "n1"})
    assert resp.status_code == 200
    assert resp.json()["result_node_id"] == "n_clone"


def test_b62_recruit_400_when_missing_fields(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/scale/recruit", json={"role_title": "x"})
    assert resp.status_code == 400


def test_b62_recruit_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    scaler = _wire_scaler(mint_app)
    scaler.request_recruit.return_value = MagicMock(id="r1", status="pending")
    resp = mint_client.post(
        "/api/v2/orgs/o1/scale/recruit",
        json={"role_title": "dev", "parent_node_id": "ceo"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


# ---------------------------------------------------------------------------
# B63-B64: status + stats
# ---------------------------------------------------------------------------


def test_b63_status_503_when_no_method(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_status_snapshot = None
    resp = mint_client.get("/api/v2/orgs/o1/status")
    assert resp.status_code == 503


def test_b63_status_404_when_payload_none(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_status_snapshot = MagicMock(return_value=None)
    resp = mint_client.get("/api/v2/orgs/o1/status")
    assert resp.status_code == 404


def test_b63_status_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_status_snapshot = MagicMock(
        return_value={"id": "o1", "status": "running"}
    )
    resp = mint_client.get("/api/v2/orgs/o1/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def test_b64_stats_503_when_no_method(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_stats = None
    resp = mint_client.get("/api/v2/orgs/o1/stats")
    assert resp.status_code == 503


def test_b64_stats_404_when_payload_none(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_stats = MagicMock(return_value=None)
    resp = mint_client.get("/api/v2/orgs/o1/stats")
    assert resp.status_code == 404


def test_b64_stats_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_stats = MagicMock(return_value={"messages": 5, "tasks": 2})
    resp = mint_client.get("/api/v2/orgs/o1/stats")
    assert resp.status_code == 200
    assert resp.json()["messages"] == 5


# ---------------------------------------------------------------------------
# B65-B67: reports
# ---------------------------------------------------------------------------


def test_b65_reports_empty_when_dir_missing(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    _set_org_dir(mint_app, tmp_path)
    resp = mint_client.get("/api/v2/orgs/o1/reports")
    assert resp.status_code == 200
    assert resp.json() == []


def test_b65_reports_lists_files(mint_app: FastAPI, mint_client: TestClient, tmp_path) -> None:
    _set_org_dir(mint_app, tmp_path)
    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "r1.md").write_text("# r1")
    resp = mint_client.get("/api/v2/orgs/o1/reports")
    assert resp.status_code == 200
    assert resp.json()[0]["filename"] == "r1.md"


def test_b66_summary_empty_when_no_store(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_event_store.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/reports/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == ""


def test_b66_summary_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    es = MagicMock()
    es.generate_summary_report = MagicMock(return_value={"summary": "all good", "days": 7})
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.get("/api/v2/orgs/o1/reports/summary?days=7")
    assert resp.status_code == 200
    assert resp.json()["summary"] == "all good"


def test_b67_generate_503_when_no_store(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.get_event_store.return_value = None
    resp = mint_client.post("/api/v2/orgs/o1/reports/generate")
    assert resp.status_code == 503


def test_b67_generate_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    es = MagicMock()
    es.generate_report_markdown = MagicMock(return_value="/tmp/r.md")
    mint_app.state.org_runtime.get_event_store.return_value = es
    resp = mint_client.post("/api/v2/orgs/o1/reports/generate", json={"days": 14})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["path"] == "/tmp/r.md"
