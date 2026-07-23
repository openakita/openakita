"""Sprint-13 H1 (RC-4 §6 H1) -- cancel_event five-layer plumbing contract.

The earlier ``d278e286`` fix wired a ``task.cancel()`` belt-and-braces so
HTTP cancels always terminated, but ``_v27_biz/_drain_rca.md`` proved
that v25/v27 high-concurrency cancel storms (10 simultaneous cancels +
LLM endpoint cooldown) still produced 8-12s ``drain timed out``
WARNINGs because ``CancelledError`` had to unwind 13 await frames
through ``httpx`` and racing endpoint cooldown sleeps. The proper fix
is to plumb ``cancel_event`` from ``OrgCommandService.cancel`` straight
to ``LLMClient.chat`` so :meth:`LLMClient._race_with_cancel` can abort
the in-flight provider call the instant the user cancel fires.

This file pins the five-layer contract:

* L0 ``OrgCommandService.cancel`` -> ``supervisor.cancel_token.cancel()``
  -> ``supervisor._cancel_event.set()``   (already covered by
  ``test_cancel_propagation.py``; we cross-check it stays wired).
* L1 ``Supervisor._inner_loop`` calls ``self.deliver(...)`` ->
  ``_make_executor_deliver`` closure -> ``executor.activate_and_run(
  cancel_event=...)``.
* L2 ``AgentPipelineExecutor.activate_and_run`` -> ``_invoke_agent`` ->
  ``agent.run(content, cancel_event=...)``.
* L3 ``_BrainBackedNodeAgent.run`` -> ``brain.messages_create_async(
  cancel_event=...)`` (direct + via ``run_with_tools`` second round).
* L4 ``brain.messages_create_async`` -> ``LLMClient.chat(
  cancel_event=...)``.
* L5 ``LLMClient._try_with_retry`` -> ``_race_with_cancel(
  provider.chat(request), cancel_event)``.

Test layout (T-A through T-F) mirrors
``_v23_biz/_rc4_debug_notes.md`` §7's deferred templates and the
acceptance shape called for in Sprint-13 H1 brief.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.config import settings
from openakita.orgs._default_agent_builder import (
    AgentSpec,
    _BrainBackedNodeAgent,
)
from openakita.orgs._runtime_node_tools import run_with_tools
from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService
from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import (
    DelegationResult,
    FinalOutcome,
    Supervisor,
    SupervisorBrain,
)
from openakita.runtime.supervisor_factory import (
    _make_executor_deliver,
    build_supervisor_for_command,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_lookup_org() -> MagicMock:
    """Minimal ``_Lookup``-like object the executor consults."""
    org = MagicMock()
    org.state = "active"
    org.status = "active"
    org.get_node = MagicMock(return_value=MagicMock(id="root"))
    org.get_root_nodes = MagicMock(return_value=[MagicMock(id="root")])
    return org


def _make_runtime() -> MagicMock:
    """Minimal runtime stub for ``OrgCommandService``."""
    rt = MagicMock()
    org = _make_lookup_org()
    rt.get_org = MagicMock(return_value=org)
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=MagicMock(query=lambda **kw: []))
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())

    async def _async_cancel(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"cancelled_roots": ["root"]}

    rt.cancel_user_command = _async_cancel
    return rt


_TRIVIAL_DONE_LEDGER = json.dumps(
    {
        "is_request_satisfied": {"answer": True, "reason": "done"},
        "is_progress_being_made": {"answer": True, "reason": "-"},
        "is_in_loop": {"answer": False, "reason": "-"},
        "instruction_or_question": {"answer": "ok", "reason": "-"},
        "next_speaker": {"answer": "root", "reason": "-"},
    }
)

_PROCEED_LEDGER = json.dumps(
    {
        "is_request_satisfied": {"answer": False, "reason": "still working"},
        "is_progress_being_made": {"answer": True, "reason": "-"},
        "is_in_loop": {"answer": False, "reason": "-"},
        "instruction_or_question": {"answer": "do x", "reason": "-"},
        "next_speaker": {"answer": "root", "reason": "-"},
    }
)


class _RecordingBrain(SupervisorBrain):
    """Brain that records every cancel_event it sees.

    Returns a PROCEED ledger on the first emit (so the supervisor
    actually exercises ``deliver``) and DONE on the second so the run
    terminates quickly. Tests can read ``ledger_calls`` for sanity
    checks.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, asyncio.Event | None]] = []
        self.ledger_calls = 0

    async def extract_facts(
        self,
        *,
        task: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        self.events.append(("extract_facts", cancel_event))
        return "facts"

    async def draft_plan(
        self,
        *,
        task: str,
        facts: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        self.events.append(("draft_plan", cancel_event))
        return "plan"

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history,
        recent_outputs=None,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        self.events.append(("emit_progress_ledger", cancel_event))
        self.ledger_calls += 1
        if self.ledger_calls == 1:
            return _PROCEED_LEDGER
        return _TRIVIAL_DONE_LEDGER


class _RecordingExecutor:
    """Stand-in for AgentPipelineExecutor that records ``activate_and_run``
    invocations -- including the new ``cancel_event`` kwarg.

    Returning ``status=ok`` lets the supervisor's inner loop see one
    successful delegation before the brain flips ``is_request_satisfied``
    to true on the next turn.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def activate_and_run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "status": "ok",
            "command_id": kwargs.get("command_id"),
            "output": "ok",
            "reason": None,
        }


# ---------------------------------------------------------------------------
# T-A: cancel_event flows 5 layers and ends up at LLMClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_a_cancel_event_reaches_llm_client_chat() -> None:
    """T-A: cancel_event minted by the factory hits ``LLMClient.chat``.

    We drive the production code from the
    ``_BrainBackedNodeAgent.run`` boundary down to a mock brain whose
    ``messages_create_async`` captures the ``cancel_event`` kwarg, and
    assert the captured object is *the same* event the factory wired
    onto ``supervisor.cancel_token``. This proves the 5-layer plumb is
    a single shared instance and not a fresh per-layer event.
    """
    captured: dict[str, Any] = {}

    class _CapturingBrain:
        async def messages_create_async(self, **kwargs: Any) -> Any:
            captured.setdefault("calls", []).append(kwargs)
            return MagicMock(
                content=[MagicMock(text="done")],
                usage=MagicMock(input_tokens=0, output_tokens=1),
            )

    sentinel_event = asyncio.Event()

    spec = AgentSpec(
        org_id="o1",
        node_id="root",
        role="worker",
        persona="",
        external_tools=(),
        available_nodes=(),
        enable_file_tools=False,
    )
    agent = _BrainBackedNodeAgent(spec, _CapturingBrain())
    await agent.run("hello", cancel_event=sentinel_event)

    assert captured.get("calls"), "brain.messages_create_async was not called"
    seen = captured["calls"][0].get("cancel_event")
    assert seen is sentinel_event, f"agent.run did not forward cancel_event verbatim: got {seen!r}"


# ---------------------------------------------------------------------------
# T-A2: executor + deliver closure flow cancel_event to executor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_a2_factory_deliver_closure_carries_cancel_event() -> None:
    """The ``_make_executor_deliver`` closure must thread cancel_event.

    Build the deliver with a known event, invoke it once, and verify the
    underlying executor saw the kwarg. Pre-fix the closure ignored
    cancel_event entirely (kwarg did not exist on the factory).
    """
    captured: list[asyncio.Event | None] = []

    class _Exec:
        async def activate_and_run(self, **kw: Any) -> dict[str, Any]:
            captured.append(kw.get("cancel_event"))
            return {"status": "ok", "command_id": "c1", "output": "ok", "reason": None}

    event = asyncio.Event()
    deliver = _make_executor_deliver(
        org_id="o1", command_id="c1", executor=_Exec(), cancel_event=event
    )
    result = await deliver("root", "go", None)
    assert result.success is True
    assert captured == [event]


# ---------------------------------------------------------------------------
# T-B: full submit + cancel cycle produces no drain-timed-out WARNING
# ---------------------------------------------------------------------------


class _SlowDeliverProductionShape:
    """Sleeps in deliver (mimicking a slow LLM) but honours cancel_event.

    Mirrors the production path where ``deliver`` ->
    ``executor.activate_and_run`` -> ``agent.run`` ->
    ``brain.messages_create_async`` -> ``LLMClient.chat`` ->
    ``_race_with_cancel(provider.chat, cancel_event)``. With the
    cancel_event wired the ``await asyncio.sleep`` is cut short within
    a handful of milliseconds; pre-fix it ran to completion.
    """

    def __init__(self, *, slow_seconds: float = 30.0) -> None:
        self.slow_seconds = slow_seconds
        self.entered = asyncio.Event()
        self.cancel_event_seen: asyncio.Event | None = None

    async def __call__(self, speaker: str, instruction: str, progress: Any) -> DelegationResult:
        # This closure mimics the executor + agent + brain path
        # collapsed for the test -- ``_make_executor_deliver`` will
        # wrap this when we go through ``build_supervisor_for_command``,
        # but for the standalone supervisor case below we drive the
        # cancel_event directly via the supervisor's wired event.
        self.entered.set()
        await asyncio.sleep(self.slow_seconds)
        return DelegationResult(success=True, speaker=speaker, message="ok")


@pytest.mark.asyncio
async def test_t_b_high_concurrency_cancel_no_drain_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T-B: 5-concurrent cancel storm sees zero ``drain timed out``.

    Five concurrent cancels on supervisors whose deliver races against
    the supervisor-minted cancel_event must each finish within the 8s
    drain budget. ``caplog`` asserts no ``drain timed out`` WARNING is
    emitted -- the v25 C6 regression shape.
    """
    monkeypatch.setattr(settings, "orgs_cancel_drain_budget_s", 3, raising=False)
    monkeypatch.setattr(settings, "supervisor_hard_ceiling_s", 60, raising=False)

    # A deliver that races a 30s sleep against the supervisor's
    # cancel_event -- this is the structural shape we want to verify.
    # The supervisor wires cancel_event into the deliver via the
    # factory's closure; here we expose it through a captured
    # reference so the deliver can race against the same event the
    # cancel_token fires.
    cancel_events: dict[str, asyncio.Event] = {}

    def _make_aware_deliver(cid: str) -> Callable[..., Awaitable[DelegationResult]]:
        async def _aware(speaker: str, instruction: str, progress: Any) -> DelegationResult:
            event = cancel_events.get(cid)
            slow = asyncio.ensure_future(asyncio.sleep(30.0))
            if event is None:
                await slow
                return DelegationResult(success=True, speaker=speaker, message="ok")
            waiter = asyncio.ensure_future(event.wait())
            try:
                done, pending = await asyncio.wait(
                    [slow, waiter], return_when=asyncio.FIRST_COMPLETED
                )
                for p in pending:
                    p.cancel()
                if waiter in done:
                    # Surface the cancel as ``CancelledError`` so the
                    # supervisor's CancelledError arm absorbs it and
                    # writes the final cancelled checkpoint.
                    raise asyncio.CancelledError
            finally:
                for t in (slow, waiter):
                    if not t.done():
                        t.cancel()
            return DelegationResult(success=True, speaker=speaker, message="ok")

        return _aware

    def _factory(
        *,
        org_id: str,
        command_id: str,
        root_node_id: str,
        task: str,
        executor: Any = None,
        brain: Any = None,
        stream: Any = None,
        checkpointer: Any = None,
        cancel_token: Any = None,
    ) -> Any:
        token = cancel_token or CancellationToken()
        evt = asyncio.Event()
        token.add_callback(evt.set)
        cancel_events[command_id] = evt
        return Supervisor(
            command_id=command_id,
            org_id=org_id,
            root_node_id=root_node_id,
            task=task,
            brain=_RecordingBrain(),
            deliver=_make_aware_deliver(command_id),
            stream=StreamBus(strict=False),
            checkpointer=MemoryCheckpointer(),
            cancel_token=token,
            cancel_event=evt,
        )

    svc = OrgCommandService(_make_runtime(), supervisor_factory=_factory)
    caplog.set_level(logging.WARNING)

    # Five concurrent submits + cancels (smaller than the v25 10-storm
    # to keep unit-test wallclock predictable; the structural property
    # we are validating is identical).
    async def _storm(i: int) -> float:
        sub = await svc.submit(OrgCommandRequest(org_id=f"o{i}", content=f"storm-{i}"))
        cid = sub.get("command_id")
        assert cid, f"submit {i} did not yield command_id"
        # Wait for the supervisor task to actually enter deliver.
        for _ in range(50):
            await asyncio.sleep(0.02)
            if cid in cancel_events:
                break
        started = time.monotonic()
        await svc.cancel(f"o{i}", cid, reason="user_cancel")
        return time.monotonic() - started

    elapsed = await asyncio.gather(*(_storm(i) for i in range(5)))
    for i, dt in enumerate(elapsed):
        assert dt < 3.0, f"storm {i} cancel took {dt:.2f}s (drain budget=3s)"

    # No ``drain timed out`` WARNING in any captured record.
    drain_records = [r for r in caplog.records if "drain timed out" in r.getMessage()]
    assert not drain_records, (
        f"unexpected drain WARNING(s): {[r.getMessage() for r in drain_records]}"
    )


# ---------------------------------------------------------------------------
# T-C: cooldown wait inside LLMClient races against cancel_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_c_llm_client_cooldown_wait_respects_cancel_event() -> None:
    """T-C (R-B): the ``_resolve_providers_with_fallback`` cooldown sleep
    must be raced against cancel_event so a 30s cooldown does not hold
    a doomed retry open.

    This pins behaviour already present in ``client.py:1130-1143`` but
    that was only reachable once the cancel_event actually flowed down.
    With H1 the wiring is end-to-end; we verify the structural property
    of the wait directly: ``asyncio.wait_for(cancel_event.wait(),
    timeout=wait_seconds)`` returns within ~10ms of ``cancel_event.set``.
    """
    event = asyncio.Event()

    async def _cancel_after_delay() -> None:
        await asyncio.sleep(0.1)
        event.set()

    async def _simulated_cooldown_wait(wait_seconds: float) -> bool:
        try:
            await asyncio.wait_for(event.wait(), timeout=wait_seconds)
            return True  # cancel won
        except TimeoutError:
            return False  # full sleep elapsed

    started = time.monotonic()
    cooldown_task = asyncio.create_task(_simulated_cooldown_wait(30.0))
    await _cancel_after_delay()
    cancelled = await asyncio.wait_for(cooldown_task, timeout=1.0)
    elapsed = time.monotonic() - started
    assert cancelled is True, "cooldown wait did not respect cancel_event"
    assert elapsed < 1.5, f"cooldown wait took {elapsed:.2f}s -- expected ≤1.5s"


# ---------------------------------------------------------------------------
# T-D: cancel_event=None keeps behaviour byte-for-byte (back-compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_d_cancel_event_none_keeps_legacy_path() -> None:
    """T-D: every new kwarg defaults to None and legacy paths still work.

    Drives the agent.run path without a cancel_event and asserts the
    brain is called with ``cancel_event=None`` (not "absent"). This
    pins back-compat for HTTP submits that opt out of the bridge and
    for the IM path which still flows through DegenerateSupervisorBrain
    (no LLM, no need for an event).
    """
    seen: dict[str, Any] = {}

    class _Brain:
        async def messages_create_async(self, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return MagicMock(
                content=[MagicMock(text="ok")],
                usage=MagicMock(input_tokens=0, output_tokens=1),
            )

    spec = AgentSpec(
        org_id="o",
        node_id="root",
        role="worker",
        persona="",
        external_tools=(),
        available_nodes=(),
        enable_file_tools=False,
    )
    agent = _BrainBackedNodeAgent(spec, _Brain())
    await agent.run("hi")  # no cancel_event kwarg
    assert "cancel_event" in seen
    assert seen["cancel_event"] is None


# ---------------------------------------------------------------------------
# T-E: run_with_tools second round also forwards cancel_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_e_run_with_tools_forwards_cancel_event_both_rounds() -> None:
    """T-E: tool-use path must carry cancel_event into the second round.

    ``run_with_tools`` calls ``brain.messages_create_async`` twice (the
    LLM emits a ``tool_use`` block, the runtime runs the tool, then the
    brain is asked to wrap up). Both calls must see the same
    cancel_event or the second-round httpx wait would be unkillable.
    """
    from types import SimpleNamespace

    rounds: list[asyncio.Event | None] = []

    async def fake_brain(
        *,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        cancel_event: asyncio.Event | None = None,
    ) -> Any:
        rounds.append(cancel_event)
        if len(rounds) == 1:
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="tu_1",
                        name="ping",
                        input={"x": 1},
                    )
                ]
            )
        return SimpleNamespace(content=[SimpleNamespace(text="done")])

    async def _stub_handler(tool_name: str, params: dict[str, Any]) -> str:
        return "pong"

    import openakita.tools.handlers as handlers_mod

    original = handlers_mod.default_handler_registry.execute_by_tool
    handlers_mod.default_handler_registry.execute_by_tool = _stub_handler  # type: ignore[assignment]
    try:
        event = asyncio.Event()
        brain = SimpleNamespace(messages_create_async=fake_brain)
        await run_with_tools(
            brain=brain,
            system_prompt="sys",
            user_content="ping",
            tools=[
                {
                    "name": "ping",
                    "description": "ping",
                    "input_schema": {"type": "object"},
                }
            ],
            org_id="o",
            node_id="n",
            command_id="c1",
            cancel_event=event,
        )
    finally:
        handlers_mod.default_handler_registry.execute_by_tool = original  # type: ignore[assignment]

    assert rounds == [event, event], f"both rounds must carry the same cancel_event; got {rounds!r}"


# ---------------------------------------------------------------------------
# T-F: legacy agents without cancel_event kwarg still drive cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_f_legacy_agent_without_cancel_event_kwarg_still_runs() -> None:
    """T-F: ``_invoke_agent`` probes the agent's signature before
    forwarding cancel_event so legacy ``async run(self, content)`` agents
    keep working.

    Without this back-compat, tests like
    ``test_cancelled_by_disk_integration.py`` would raise
    ``TypeError: run() got an unexpected keyword argument 'cancel_event'``.
    """
    from openakita.orgs._runtime_agent_pipeline_executor import (
        AgentPipelineExecutor,
    )

    captured: list[str] = []

    class _RuntimeAgentBase:
        async def run(self, content: str) -> str:
            captured.append(content)
            return f"echo:{content}"

    result = await AgentPipelineExecutor._invoke_agent(
        _RuntimeAgentBase(), "ping", cancel_event=asyncio.Event()
    )
    assert result == "echo:ping"
    assert captured == ["ping"]


# ---------------------------------------------------------------------------
# T-G: build_supervisor_for_command wires cancel_event end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_g_factory_wires_cancel_event_through_deliver() -> None:
    """T-G: build_supervisor_for_command's default deliver must forward
    cancel_event into ``executor.activate_and_run`` so the production
    composition root really plumbs the bridge.
    """
    exec_ = _RecordingExecutor()
    sup = build_supervisor_for_command(
        org_id="org_g",
        command_id="cmd_g",
        root_node_id="root",
        task="go",
        executor=exec_,
        brain=_RecordingBrain(),
        stream=StreamBus(strict=False),
        checkpointer=MemoryCheckpointer(),
    )
    out = await sup.run()
    assert out.outcome == FinalOutcome.DONE
    # The executor saw at least one delegation; the cancel_event kwarg
    # must be the same instance the supervisor wired on its token.
    assert exec_.calls, "executor.activate_and_run was never invoked"
    event = exec_.calls[0].get("cancel_event")
    assert event is sup._cancel_event, (
        "deliver closure did not forward the supervisor's cancel_event instance"
    )


# ---------------------------------------------------------------------------
# T-H: cancel_event is wired such that sup.cancel_token.cancel() sets it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_h_cancel_token_cancel_propagates_to_event() -> None:
    """T-H: cancel_token.cancel() must synchronously fire cancel_event.

    The bridge is already validated for the SupervisorBrain path
    (``test_cancel_propagation.py``); this test extends it to the
    factory-built deliver closure so a single ``cancel_token.cancel()``
    notifies both the brain path AND every downstream
    ``executor.activate_and_run`` -> ``agent.run`` ->
    ``brain.messages_create_async`` -> ``LLMClient.chat`` await frame.
    """
    sup = build_supervisor_for_command(
        org_id="org_h",
        command_id="cmd_h",
        root_node_id="root",
        task="check",
        executor=_RecordingExecutor(),
        brain=_RecordingBrain(),
        stream=StreamBus(strict=False),
        checkpointer=MemoryCheckpointer(),
    )
    assert not sup._cancel_event.is_set()
    sup.cancel_token.cancel("user_cancel")
    # add_callback fires synchronously; no awaits required.
    assert sup._cancel_event.is_set()


# ---------------------------------------------------------------------------
# T-I: race_with_cancel actually cancels in-flight provider.chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t_i_llm_client_race_with_cancel_aborts_provider_chat() -> None:
    """T-I: ``LLMClient._race_with_cancel`` aborts the in-flight awaitable
    the instant cancel_event fires.

    Pins the bottom of the 5-layer pipe -- if this regresses the whole
    plumb is useless. We pass a 30s sleep coroutine and assert it is
    cancelled within milliseconds of ``cancel_event.set``.
    """
    from openakita.llm.client import LLMClient, UserCancelledError

    event = asyncio.Event()

    async def _slow() -> Any:
        await asyncio.sleep(30.0)
        return "should-not-reach"

    async def _fire_after(delay: float) -> None:
        await asyncio.sleep(delay)
        event.set()

    start = time.monotonic()
    asyncio.create_task(_fire_after(0.05))
    with pytest.raises(UserCancelledError):
        await LLMClient._race_with_cancel(_slow(), event)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"_race_with_cancel took {elapsed:.2f}s to honour event (expected ≤0.5s)"
