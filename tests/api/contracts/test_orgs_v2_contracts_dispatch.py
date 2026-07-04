"""Contract tests for cluster 3.3 Runtime control + Commands + Broadcast (B34-B41).

Pairs with ``src/openakita/api/routes/orgs_v2_runtime_dispatch.py``
(P9.7beta-3). Cluster covers org lifecycle verbs (start/stop/pause/
resume), user-command submit / poll / cancel, and the org-level
broadcast adapter. Exercises 200/201 happy paths, 422 (Pydantic
``extra="forbid"`` + ``content`` min_length), 400 (lifecycle
ValueError + broadcast empty), 404 (command not found), and 409
(``OrgCommandConflict`` envelope).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


from tests.api.contracts.conftest import _async_return, _async_raise


# ---------------------------------------------------------------------------
# B34-B37: lifecycle verbs
# ---------------------------------------------------------------------------


def test_b34_start_org_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.start_org = _async_return({"status": "active"})
    resp = mint_client.post("/api/v2/orgs/o1/start")
    assert resp.status_code == 200
    assert resp.json() == {"status": "active"}


def test_b34_start_org_400_on_value_error(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.start_org = _async_raise(ValueError("bad state"))
    resp = mint_client.post("/api/v2/orgs/o1/start")
    assert resp.status_code == 400
    assert "bad state" in resp.json()["detail"]


def test_b34_start_org_503_when_method_missing(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.start_org = None
    resp = mint_client.post("/api/v2/orgs/o1/start")
    assert resp.status_code == 503


def test_b35_stop_org_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.stop_org = _async_return({"status": "stopped"})
    resp = mint_client.post("/api/v2/orgs/o1/stop")
    assert resp.status_code == 200


def test_b36_pause_org_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.pause_org = _async_return({"status": "paused"})
    resp = mint_client.post("/api/v2/orgs/o1/pause")
    assert resp.status_code == 200


def test_b37_resume_org_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.resume_org = _async_return({"status": "active"})
    resp = mint_client.post("/api/v2/orgs/o1/resume")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# State-machine edges (P-RC-10 P10.5c epsilon-O1 strategic ports):
# illegal lifecycle transitions on B35 / B36 / B37 share the same
# ValueError -> HTTP 400 pathway through ``_call_lifecycle``, but each
# verb's mock is wired separately and merits its own contract pin.
# Mirrors a slice of v1 ``test_plan_features.py`` (73 cases) which
# exercised these transitions end-to-end against ``orgs/runtime.py``.
# ---------------------------------------------------------------------------


def test_b35_stop_org_400_on_illegal_transition(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_runtime.stop_org = _async_raise(ValueError("Cannot stop dormant org"))
    resp = mint_client.post("/api/v2/orgs/o1/stop")
    assert resp.status_code == 400
    assert "dormant" in resp.json()["detail"]


def test_b36_pause_org_400_on_illegal_transition(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_runtime.pause_org = _async_raise(ValueError("Org already paused"))
    resp = mint_client.post("/api/v2/orgs/o1/pause")
    assert resp.status_code == 400
    assert "already paused" in resp.json()["detail"]


def test_b37_resume_org_400_on_illegal_transition(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_runtime.resume_org = _async_raise(
        ValueError("Org not in paused state")
    )
    resp = mint_client.post("/api/v2/orgs/o1/resume")
    assert resp.status_code == 400
    assert "paused state" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# B38: submit command
# ---------------------------------------------------------------------------


def test_b38_submit_command_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.submit = _async_return(
        {"command_id": "cmd_1", "org_id": "o1", "status": "running", "content": "hi"}
    )
    resp = mint_client.post(
        "/api/v2/orgs/o1/command",
        json={"content": "hello there"},
    )
    assert resp.status_code == 200
    assert resp.json()["command_id"] == "cmd_1"


def test_b38_submit_command_422_when_content_empty(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/command", json={"content": ""})
    assert resp.status_code == 422


def test_b38_submit_command_422_when_extra_field(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/command", json={"content": "hi", "evil": True})
    assert resp.status_code == 422


def test_b38_submit_command_409_on_conflict(mint_app: FastAPI, mint_client: TestClient) -> None:
    from openakita.orgs import OrgCommandConflict

    err = OrgCommandConflict("already running", command_id="cmd_old")
    mint_app.state.org_command_service.submit = _async_raise(err)
    resp = mint_client.post(
        "/api/v2/orgs/o1/command", json={"content": "hi", "replace_existing": False}
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "org_command_conflict"


def test_b38_submit_command_400_on_command_error(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    from openakita.orgs import OrgCommandError

    err = OrgCommandError("not allowed")
    err.status_code = 400  # type: ignore[attr-defined]
    mint_app.state.org_command_service.submit = _async_raise(err)
    resp = mint_client.post("/api/v2/orgs/o1/command", json={"content": "hi"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# B38 input attachments (upstream e2874585 port): the command console may
# attach files. Text-file contents are inlined into the *execution* content
# while the console-facing text stays clean, and structured attachment
# metadata rides along on ``input_attachments`` for history rebuild.
# ---------------------------------------------------------------------------


def test_b38_submit_command_attachments_field_accepted(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_command_service.submit = _async_return(
        {"command_id": "cmd_b", "org_id": "o1", "status": "running"}
    )
    resp = mint_client.post(
        "/api/v2/orgs/o1/command",
        json={"content": "hi", "attachments": []},
    )
    assert resp.status_code == 200


def test_b38_submit_command_enriches_and_splits_attachment_text(
    mint_app: FastAPI, mint_client: TestClient, tmp_path
) -> None:
    captured: dict[str, Any] = {}

    async def _capture(request: Any) -> dict[str, Any]:
        captured["req"] = request
        return {"command_id": "cmd_a", "org_id": "o1", "status": "running"}

    mint_app.state.org_command_service.submit = MagicMock(side_effect=_capture)

    doc = tmp_path / "notes.txt"
    doc.write_text("SECRET-FILE-BODY", encoding="utf-8")

    resp = mint_client.post(
        "/api/v2/orgs/o1/command",
        json={
            "content": "please review",
            "attachments": [
                {"type": "file", "name": "notes.txt", "local_path": str(doc)},
            ],
        },
    )
    assert resp.status_code == 200

    req = captured["req"]
    # Execution content is enriched with the inlined file body...
    assert "SECRET-FILE-BODY" in req.content
    assert "notes.txt" in req.content
    # ...while the console-facing text is the original, unpolluted input.
    assert req.user_facing_content == "please review"
    assert "SECRET-FILE-BODY" not in (req.user_facing_content or "")
    # Structured attachment metadata is preserved for history rendering.
    assert len(req.input_attachments) == 1
    assert req.input_attachments[0]["name"] == "notes.txt"
    assert req.input_attachments[0]["local_path"] == str(doc)


# ---------------------------------------------------------------------------
# B39: poll command status
# ---------------------------------------------------------------------------


def test_b39_get_status_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.get_status.return_value = {
        "command_id": "cmd_1",
        "status": "completed",
    }
    resp = mint_client.get("/api/v2/orgs/o1/commands/cmd_1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_b39_get_status_404(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.get_status.return_value = None
    resp = mint_client.get("/api/v2/orgs/o1/commands/missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B40: cancel command
# ---------------------------------------------------------------------------


def test_b40_cancel_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.cancel = _async_return(
        {"command_id": "cmd_1", "status": "cancelled"}
    )
    resp = mint_client.post("/api/v2/orgs/o1/commands/cmd_1/cancel", json={"reason": "user"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_b40_cancel_400_on_value_error(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.cancel = _async_raise(ValueError("already done"))
    resp = mint_client.post("/api/v2/orgs/o1/commands/cmd_1/cancel")
    assert resp.status_code == 400


def test_b40_cancel_404_when_none(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.cancel = _async_return(None)
    resp = mint_client.post("/api/v2/orgs/o1/commands/missing/cancel")
    assert resp.status_code == 404


def test_b40_cancel_500_on_unhandled(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_command_service.cancel = _async_raise(RuntimeError("boom"))
    resp = mint_client.post("/api/v2/orgs/o1/commands/cmd_1/cancel")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Cancel-during-plan body invariants (P-RC-10 P10.5c epsilon-O1 strategic
# ports): CancelRequest is ``extra="forbid"`` with an optional ``reason``
# field. Two cases pin the body-validation envelope that v1 ``test_plan_
# features.py`` covered end-to-end while a plan was in-flight.
# ---------------------------------------------------------------------------


def test_b40_cancel_with_reason_body_accepted(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_command_service.cancel = _async_return(
        {"command_id": "cmd_1", "status": "cancelled", "reason": "timeout"}
    )
    resp = mint_client.post(
        "/api/v2/orgs/o1/commands/cmd_1/cancel",
        json={"reason": "timeout"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["reason"] == "timeout"


def test_b40_cancel_422_on_extra_body_field(mint_client: TestClient) -> None:
    resp = mint_client.post(
        "/api/v2/orgs/o1/commands/cmd_1/cancel",
        json={"reason": "timeout", "unexpected": True},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# B41: broadcast
# ---------------------------------------------------------------------------


def test_b41_broadcast_happy(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.broadcast_to_org = _async_return({"sent": 3})
    resp = mint_client.post("/api/v2/orgs/o1/broadcast", json={"content": "team meeting"})
    assert resp.status_code == 200
    assert resp.json()["result"] == {"sent": 3}


def test_b41_broadcast_400_when_empty_content(mint_client: TestClient) -> None:
    resp = mint_client.post("/api/v2/orgs/o1/broadcast", json={"content": ""})
    assert resp.status_code == 400


def test_b41_broadcast_503_when_not_wired(mint_app: FastAPI, mint_client: TestClient) -> None:
    mint_app.state.org_runtime.broadcast_to_org = None
    mint_app.state.org_runtime.broadcast = None
    resp = mint_client.post("/api/v2/orgs/o1/broadcast", json={"content": "hi"})
    assert resp.status_code == 503
