import asyncio
from types import SimpleNamespace

from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from openakita.api.server import _wait_for_uvicorn_started, create_app, update_runtime_refs
from openakita.core.agent import Agent


async def test_update_runtime_refs_attaches_late_agent_plugin_routes() -> None:
    app = create_app(agent=None)

    router = APIRouter()

    @router.get("/ping")
    async def ping():
        return {"ok": True}

    plugin_manager = SimpleNamespace(
        _external_host_refs={"_pending_plugin_routers": [("late_plugin", router)]}
    )
    agent = SimpleNamespace(_plugin_manager=plugin_manager)
    api_task = SimpleNamespace(_openakita_api_app=app)

    assert update_runtime_refs(api_task, agent=agent) is True

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/api/plugins/late_plugin/ping")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert app.state.agent is agent
    assert plugin_manager._external_host_refs["api_app"] is app
    assert "_pending_plugin_routers" not in plugin_manager._external_host_refs


def test_update_runtime_refs_rebinds_org_command_session_manager() -> None:
    app = create_app(agent=None, session_manager=None)
    api_task = SimpleNamespace(_openakita_api_app=app)
    session_manager = object()

    assert app.state.org_command_service._session_manager is None
    assert update_runtime_refs(api_task, session_manager=session_manager) is True

    assert app.state.session_manager is session_manager
    assert app.state.org_command_service._session_manager is session_manager


async def test_agent_initialize_is_single_flight(monkeypatch) -> None:
    agent = Agent.__new__(Agent)
    agent._initialized = False
    agent._initialize_lock = asyncio.Lock()

    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_initialize_unlocked(self, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        self._initialized = True

    monkeypatch.setattr(Agent, "_initialize_unlocked", fake_initialize_unlocked)

    first = asyncio.create_task(agent.initialize())
    await asyncio.wait_for(started.wait(), timeout=1)
    second = asyncio.create_task(agent.initialize())

    await asyncio.sleep(0)
    assert calls == 1

    release.set()
    await asyncio.gather(first, second)

    assert calls == 1
    assert agent._initialized is True


class _FakeApiThread:
    def __init__(self, *, alive: bool = True) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


async def test_wait_for_uvicorn_started_uses_server_signal() -> None:
    server = SimpleNamespace(started=False)
    api_thread = _FakeApiThread()

    async def mark_started() -> None:
        await asyncio.sleep(0.01)
        server.started = True

    marker = asyncio.create_task(mark_started())
    await _wait_for_uvicorn_started(server, api_thread, [], timeout=1.0)
    await marker

    assert server.started is True


async def test_wait_for_uvicorn_started_fails_when_thread_exits() -> None:
    server = SimpleNamespace(started=False)
    api_thread = _FakeApiThread(alive=False)

    try:
        await _wait_for_uvicorn_started(server, api_thread, [], timeout=1.0)
    except RuntimeError as exc:
        assert "exited before uvicorn" in str(exc)
    else:
        raise AssertionError("dead API thread must fail startup readiness")


async def test_wait_for_uvicorn_started_has_bounded_timeout() -> None:
    server = SimpleNamespace(started=False)
    api_thread = _FakeApiThread()

    try:
        await _wait_for_uvicorn_started(server, api_thread, [], timeout=0.01)
    except TimeoutError as exc:
        assert "startup completion" in str(exc)
    else:
        raise AssertionError("missing uvicorn started signal must time out")
