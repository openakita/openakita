"""HTTP- and generator-level tests for the v2 orgs SSE stream endpoint.

P-RC-2 commit P2.3. The 404 cases use the synchronous
``TestClient``; the happy-path cases drive the ``_event_stream``
async generator with a fake :class:`Request` so emit + subscribe
share the same event loop (httpx + ASGI streaming would force
the test into a thread/loop juggle that is fragile and slow).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import orgs_v2_stream
from openakita.api.routes.orgs_v2_stream import (
    _event_stream,
    stream_org_progress,
)
from openakita.config import settings
from openakita.runtime.models import NodeType, NodeV2, OrgV2
from openakita.runtime.orgs import reset_default_store
from openakita.runtime.stream_registry import (
    get_or_create_org_stream_bus,
    list_org_stream_buses,
    reset_org_stream_buses,
)


@pytest.fixture(autouse=True)
def _isolate_stream_registry() -> Iterator[None]:
    reset_org_stream_buses()
    yield
    reset_org_stream_buses()


def _make_org(store, org_id: str = "org_sse_test") -> OrgV2:
    org = OrgV2(
        id=org_id,
        name="SSE smoke org",
        description="for the test",
        nodes=[
            NodeV2(
                id="root", org_id=org_id, type=NodeType.LLM,
                role="root", label="root",
            ),
        ],
        edges=[],
    )
    store.create(org)
    return org


def _client(monkeypatch, tmp_path, *, enabled: bool, with_org: bool = True) -> TestClient:
    monkeypatch.setattr(settings, "runtime_v2_enabled", enabled, raising=False)
    store = reset_default_store(path=tmp_path / "orgs_v2.json")
    if with_org:
        _make_org(store)
    app = FastAPI()
    app.include_router(orgs_v2_stream.router)
    return TestClient(app)


# --------------------------------------------------------------------- 404


def test_returns_404_when_v2_disabled(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path, enabled=False, with_org=False) as c:
        resp = c.get("/api/v2/orgs/anything/stream")
    assert resp.status_code == 404
    assert "v2 is disabled" in resp.json()["detail"]


def test_returns_404_when_org_unknown(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path, enabled=True, with_org=False) as c:
        resp = c.get("/api/v2/orgs/org_does_not_exist/stream")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


# ---------------------------------------------------- Generator-level path


class _FakeRequest:
    def __init__(self) -> None:
        self.disconnect = asyncio.Event()

    async def is_disconnected(self) -> bool:
        return self.disconnect.is_set()


async def _collect(gen, n: int, *, timeout: float = 3.0) -> list[dict]:
    out: list[dict] = []
    buf = ""
    deadline = asyncio.get_running_loop().time() + timeout
    async for chunk in gen:
        if asyncio.get_running_loop().time() > deadline:
            break
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            event_name = "message"
            data: list[str] = []
            saw_real_line = False
            for line in block.split("\n"):
                if line.startswith(":"):
                    continue
                saw_real_line = True
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data.append(line[len("data:"):].strip())
            if not saw_real_line or not data:
                continue
            try:
                payload = json.loads("\n".join(data))
            except Exception:
                payload = "\n".join(data)
            out.append({"event": event_name, "data": payload})
            if len(out) >= n:
                return out
    return out


async def test_response_headers_are_correct(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    store = reset_default_store(path=tmp_path / "orgs_v2.json")
    _make_org(store)
    response = await stream_org_progress(_FakeRequest(), "org_sse_test")  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"


async def test_event_stream_delivers_published_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    reset_default_store(path=tmp_path / "orgs_v2.json")
    bus = get_or_create_org_stream_bus("org_sse_test")
    request = _FakeRequest()
    gen = _event_stream(request, "org_sse_test")

    # Pull the connected event so the Subscription is registered.
    first = await _collect(gen, n=1, timeout=2.0)
    assert first[0]["event"] == "lifecycle"
    assert first[0]["data"]["type"] == "sse_connected"
    assert len(bus._subscriptions) == 1

    await bus.emit(
        "progress_ledger",
        "ledger_emitted",
        {
            "is_request_satisfied": False,
            "is_in_loop": False,
            "is_progress_being_made": True,
            "next_speaker": "writer",
        },
        command_id="cmd_x",
        org_id="org_sse_test",
        superstep=1,
    )
    events = await _collect(gen, n=1, timeout=2.0)
    request.disconnect.set()
    await gen.aclose()
    assert events
    pub = events[0]
    assert pub["event"] == "progress_ledger"
    assert pub["data"]["payload"]["next_speaker"] == "writer"
    assert "ts" in pub["data"]


async def test_event_stream_disconnect_detaches_subscription(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    reset_default_store(path=tmp_path / "orgs_v2.json")
    bus = get_or_create_org_stream_bus("org_sse_test")
    request = _FakeRequest()
    gen = _event_stream(request, "org_sse_test")
    first = await _collect(gen, n=1, timeout=2.0)
    assert first[0]["event"] == "lifecycle"
    assert len(bus._subscriptions) == 1
    request.disconnect.set()
    await gen.aclose()
    assert len(bus._subscriptions) == 0
    assert "org_sse_test" in list_org_stream_buses()
