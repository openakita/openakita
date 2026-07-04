"""H3 regression: the agent dispatch callback fires per user command.

The audit at ``_orgs_business_capability_audit_v1.md`` §3.2 P0
showed ``CommandDispatchManager._agent_dispatch`` was always
``None`` in production (api/server.py did not inject one), so the
``AgentPipelineExecutor`` -- the only code path that ever invokes
``agent.run`` -- was never reached for any user command.

The fix wires an ``agent_dispatch`` callback through
``OrgRuntime.__init__`` to the dispatch sibling. These tests pin:

* ``OrgRuntime`` accepts the ``agent_dispatch`` kwarg.
* The callback is invoked once per ``send_command`` call with the
  exact ``(org_id, node_id, command_id, content)`` tuple.
* When the callback is omitted, ``send_command`` still returns a
  ``submitted`` envelope (back-compat with NodeScheduler / contract
  tests).
* Exceptions inside the callback do not poison the dispatch path.
"""

from __future__ import annotations

import asyncio
from typing import Any

from openakita.orgs.runtime import OrgRuntime


class _Lookup:
    def get_org(self, org_id: str) -> Any:
        return type("Org", (), {"id": org_id, "state": "active"})()


def _make_runtime(**kwargs: Any) -> OrgRuntime:
    return OrgRuntime(
        lookup=_Lookup(),
        persistence=object(),
        lifecycle_emitter=object(),
        **kwargs,
    )


def test_send_command_invokes_agent_dispatch_when_wired() -> None:
    """case id: h3.dispatch.callback_invoked_once

    Pin the call shape ``CommandDispatchManager.send_command`` uses:
    positional ``(org_id, node_id, command_id, content)``. The audit
    flagged that the executor was unreachable; this test fails if
    the wiring ever regresses to ``None``.
    """

    calls: list[tuple[str, str, str, str]] = []

    async def fake_agent_dispatch(
        org_id: str, node_id: str, command_id: str, content: str
    ) -> dict[str, Any]:
        calls.append((org_id, node_id, command_id, content))
        return {"status": "ok", "output": "fake-result", "command_id": command_id}

    rt = _make_runtime(agent_dispatch=fake_agent_dispatch)
    asyncio.run(rt.send_command("o1", "n1", "do work", command_id="cmd_xyz"))
    assert calls == [("o1", "n1", "cmd_xyz", "do work")]


def test_send_command_returns_submitted_envelope_when_dispatch_wired() -> None:
    """case id: h3.dispatch.envelope_preserved"""

    async def fake_agent_dispatch(
        org_id: str, node_id: str, command_id: str, content: str
    ) -> dict[str, Any]:
        return {"status": "ok", "output": "ignored"}

    rt = _make_runtime(agent_dispatch=fake_agent_dispatch)
    out = asyncio.run(rt.send_command("o1", "n1", "do work", command_id="cmd_xyz"))
    assert out["status"] == "submitted"
    assert out["command_id"] == "cmd_xyz"
    assert out["node_id"] == "n1"


def test_send_command_without_agent_dispatch_is_unchanged() -> None:
    """case id: h3.dispatch.default_none_back_compat

    Existing tests (parity, p9_6gamma contract) construct OrgRuntime
    without an agent_dispatch; they must keep working.
    """

    rt = _make_runtime()
    out = asyncio.run(rt.send_command("o1", "n1", "do work"))
    assert out["status"] == "submitted"


def test_agent_dispatch_exception_isolated_from_dispatch_path() -> None:
    """case id: h3.dispatch.callback_exception_swallowed

    The audit's risk callout: a flaky agent path must not turn into
    a 500 / unhandled exception that crashes the background
    ``_run_minimal`` task -- the per-command tracker still needs to
    flip to error cleanly via OrgCommandService.
    """

    async def boom_dispatch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("agent crashed")

    rt = _make_runtime(agent_dispatch=boom_dispatch)
    out = asyncio.run(rt.send_command("o1", "n1", "do work"))
    assert out["status"] == "submitted"
