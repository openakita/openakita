from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.agents.cli_detector import CliProviderId
from openakita.api.routes.sessions import router as sessions_router


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    app = FastAPI()
    app.include_router(sessions_router, prefix="/api/sessions")
    # _claude_cwd_hash("/tmp/project") == "tmp-project" (lstrip("/") + replace("/","-"))
    claude_root = tmp_path / ".claude" / "projects" / "tmp-project"
    claude_root.mkdir(parents=True)
    session_file = claude_root / "sid-42.jsonl"
    session_file.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"},
                    "timestamp": "2026-04-22T10:00:00Z"}) + "\n" +
        json.dumps({"type": "system", "subtype": "init"}) + "\n" +
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "hello"},
                    "timestamp": "2026-04-22T10:00:01Z"}) + "\n"
    )

    monkeypatch.setitem(
        __import__("openakita.api.routes.sessions", fromlist=["_PROVIDER_ROOTS"])
        ._PROVIDER_ROOTS,
        CliProviderId.CLAUDE_CODE,
        tmp_path / ".claude" / "projects",
    )
    return TestClient(app)


def test_detected_endpoint_returns_all_providers(client, monkeypatch):
    async def fake_discover_all():
        return {pid: type("D", (), {"binary_path": None, "version": None, "error": "not found"})()
                for pid in CliProviderId}
    monkeypatch.setattr(
        "openakita.api.routes.sessions.discover_all",
        fake_discover_all,
    )
    r = client.get("/api/sessions/external-cli/detected")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == len(CliProviderId)
    assert all("provider_id" in x for x in data)


def test_listing_returns_session_file(client):
    r = client.get("/api/sessions/external-cli/claude_code?cwd=/tmp/project")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["session_id"] == "sid-42"


def test_messages_pagination_uses_byte_offset(client):
    r = client.get("/api/sessions/external-cli/claude_code/sid-42/messages?limit=1")
    assert r.status_code == 200
    page1 = r.json()
    assert len(page1["entries"]) == 1
    assert page1["entries"][0]["role"] == "user"
    assert page1["eof"] is False
    assert page1["next_cursor"] is not None

    r = client.get(f"/api/sessions/external-cli/claude_code/sid-42/messages?cursor={page1['next_cursor']}&limit=10")
    page2 = r.json()
    # system line skipped, assistant line returned, then EOF
    assert any(e.get("role") == "assistant" for e in page2["entries"])
    assert page2["eof"] is True


def test_unknown_provider_returns_empty_gracefully(client):
    r = client.get("/api/sessions/external-cli/unknown")
    assert r.status_code == 200
    assert r.json()["sessions"] == []
