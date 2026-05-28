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
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import orgs_v2_stream
from openakita.api.routes.orgs_v2_stream import (
    _event_stream,
    stream_org_progress,
)
from openakita.config import settings
from openakita.orgs import reset_default_store
from openakita.runtime.models import NodeType, NodeV2, OrgV2
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
    """Vestigial fixture from before v22.

    Sprint 13 H2 (RC-1): the legacy ``store.create`` write path was
    retired -- the SSE route already validates org existence via
    ``request.app.state.org_manager.get`` (a stub :class:`_FakeManager`
    in this test, not the JSON shim), so this helper now only mints
    an in-memory ``OrgV2`` payload for callers that still want to
    reason about the spec object. No persistence is required.
    """
    return OrgV2(
        id=org_id,
        name="SSE smoke org",
        description="for the test",
        nodes=[
            NodeV2(
                id="root",
                org_id=org_id,
                type=NodeType.LLM,
                role="root",
                label="root",
            ),
        ],
        edges=[],
    )


class _FakeManager:
    """Minimal :class:`OrgManager` stand-in used by SSE route validation.

    v22 ``_build_streaming_response`` resolves the org via
    ``request.app.state.org_manager.get(org_id)`` (the same surface
    the mint POST writes to). The legacy ``JsonOrgStore`` fixture used
    to gate org existence is no longer consulted, so we wire a
    duck-typed manager that mirrors the relevant
    :class:`OrgManager.get` semantics: return ``None`` for misses.
    """

    def __init__(self, *, known: set[str] | None = None) -> None:
        self._known: set[str] = set(known or ())

    def add(self, org_id: str) -> None:
        self._known.add(org_id)

    def get(self, org_id: str) -> Any | None:
        if org_id in self._known:
            return SimpleNamespace(id=org_id)
        return None


def _client(monkeypatch, tmp_path, *, enabled: bool, with_org: bool = True) -> TestClient:
    monkeypatch.setattr(settings, "runtime_v2_enabled", enabled, raising=False)
    # Keep the JSON store fixture wired so any indirect callers
    # (e.g. v1 contract tests sharing the same conftest) still see a
    # tmp_path-scoped backend. The SSE route itself no longer reads
    # from it after v22.
    store = reset_default_store(path=tmp_path / "orgs_v2.json")
    if with_org:
        _make_org(store)
    app = FastAPI()
    app.state.org_manager = _FakeManager(known={"org_sse_test"} if with_org else set())
    app.include_router(orgs_v2_stream.router)
    return TestClient(app)


# --------------------------------------------------------------------- 404


def test_returns_404_when_v2_disabled(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path, enabled=False, with_org=False) as c:
        resp = c.get("/api/v2/orgs-spec/anything/stream")
    assert resp.status_code == 404
    assert "v2 is disabled" in resp.json()["detail"]


def test_returns_404_when_org_unknown(monkeypatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path, enabled=True, with_org=False) as c:
        resp = c.get("/api/v2/orgs-spec/org_does_not_exist/stream")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


# ---------------------------------------------------- Generator-level path


class _FakeRequest:
    def __init__(self, *, known_orgs: set[str] | None = None) -> None:
        self.disconnect = asyncio.Event()
        # v22: ``_build_streaming_response`` reads
        # ``request.app.state.org_manager`` to validate the org id, so a
        # stand-in Request needs at least a ``.app.state.org_manager``
        # chain. ``_event_stream`` itself only touches
        # ``request.is_disconnected`` and is unaffected.
        state = SimpleNamespace(org_manager=_FakeManager(known=known_orgs or set()))
        self.app = SimpleNamespace(state=state)

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
                    event_name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data.append(line[len("data:") :].strip())
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
    reset_default_store(path=tmp_path / "orgs_v2.json")
    req = _FakeRequest(known_orgs={"org_sse_test"})
    response = await stream_org_progress(req, "org_sse_test")  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"


async def _drive_to_subscription(gen) -> str:
    """Drive ``gen`` one step past ``register_subscription`` and return the first chunk.

    The generator body starts executing on the first ``__anext__``
    call. After v25 RC-3 deletes the synthetic ``sse_connected``
    first event, the first chunk is ``"retry: 3000\\n\\n"`` (the
    SSE retry directive); pulling it guarantees the
    ``register_subscription`` call inside the generator has
    completed before the test emits, eliminating the
    "emit-before-subscribe" race.
    """
    return await gen.__anext__()  # type: ignore[no-any-return]


async def test_event_stream_yields_retry_directive_first(monkeypatch, tmp_path) -> None:
    """The first SSE chunk is the ``retry:`` directive, not a synthetic event.

    v25 RC-3 fix: the handler used to fabricate a
    ``lifecycle/sse_connected`` first event that masked the
    channel-coverage gap. The retry directive is enough to flush
    response headers through buffering proxies, and browsers
    fire ``EventSource.onopen`` on the HTTP 200 itself.
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    reset_default_store(path=tmp_path / "orgs_v2.json")
    bus = get_or_create_org_stream_bus("org_sse_test")
    request = _FakeRequest()
    gen = _event_stream(request, "org_sse_test")
    try:
        first_chunk = await _drive_to_subscription(gen)
        assert first_chunk == "retry: 3000\n\n"
        # The retry directive is not a parsed SSE event, so
        # ``_collect`` would skip it; assert via the raw chunk
        # instead. The subscription is registered eagerly so the
        # bus already sees this consumer.
        assert len(bus._subscriptions) == 1
    finally:
        request.disconnect.set()
        await gen.aclose()


async def test_event_stream_delivers_published_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    reset_default_store(path=tmp_path / "orgs_v2.json")
    bus = get_or_create_org_stream_bus("org_sse_test")
    request = _FakeRequest()
    gen = _event_stream(request, "org_sse_test")

    # Drive the generator past register_subscription (consumes the
    # one-shot ``retry: 3000`` directive) so the emit below cannot
    # land before the subscriber is attached.
    first_chunk = await _drive_to_subscription(gen)
    assert first_chunk == "retry: 3000\n\n"
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
    first_chunk = await _drive_to_subscription(gen)
    assert first_chunk == "retry: 3000\n\n"
    assert len(bus._subscriptions) == 1
    request.disconnect.set()
    await gen.aclose()
    assert len(bus._subscriptions) == 0
    assert "org_sse_test" in list_org_stream_buses()
