import asyncio
from types import SimpleNamespace

from openakita.api.server import (
    _cancel_startup_llm_health_check,
    _schedule_startup_llm_health_check,
    create_app,
)


class BlockingHealthClient:
    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def startup_health_check(self) -> dict[str, str]:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return {"main": "ok"}


async def test_startup_hook_schedules_llm_health_check_without_waiting() -> None:
    client = BlockingHealthClient()
    brain = SimpleNamespace(_llm_client=client, _compiler_client=None)
    app = create_app(agent=SimpleNamespace(brain=brain))
    app.state.org_runtime = None

    startup_hook = next(
        hook for hook in app.router.on_startup if hook.__name__ == "_startup_org_runtime"
    )

    await startup_hook()

    task = app.state.llm_startup_health_check_task
    assert task is not None
    assert not task.done()
    assert client.calls == 0

    await asyncio.wait_for(client.started.wait(), timeout=1)
    await _cancel_startup_llm_health_check(app.state)

    assert client.cancelled is True
    assert app.state.llm_startup_health_check_task is None


async def test_shutdown_cancel_stops_pending_startup_llm_health_check() -> None:
    client = BlockingHealthClient()
    brain = SimpleNamespace(_llm_client=client, _compiler_client=None)
    state = SimpleNamespace(agent=SimpleNamespace(brain=brain))

    task = _schedule_startup_llm_health_check(state)
    assert task is not None

    await asyncio.wait_for(client.started.wait(), timeout=1)
    await _cancel_startup_llm_health_check(state)

    assert task.cancelled()
    assert client.cancelled is True
    assert state.llm_startup_health_check_task is None

