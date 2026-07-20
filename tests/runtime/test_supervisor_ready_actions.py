from __future__ import annotations

import asyncio
import json

from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import (
    DelegationResult,
    FinalOutcome,
    ReadyDelegationAction,
    Supervisor,
)


class _Brain:
    def __init__(self) -> None:
        self.progress_calls = 0

    async def extract_facts(self, **_kwargs) -> str:
        return "facts"

    async def draft_plan(self, **_kwargs) -> str:
        return "plan"

    async def emit_progress_ledger(self, **_kwargs) -> str:
        self.progress_calls += 1
        return json.dumps(
            {
                "is_request_satisfied": {"answer": True, "reason": "done"},
                "is_progress_being_made": {"answer": True, "reason": "done"},
                "is_in_loop": {"answer": False, "reason": "done"},
                "instruction_or_question": {"answer": "", "reason": "done"},
                "next_speaker": {"answer": "root", "reason": "done"},
            }
        )


class _Provider:
    def __init__(self) -> None:
        self.pending = True
        self.results: list[DelegationResult] = []

    def next_action(self) -> ReadyDelegationAction | None:
        if not self.pending:
            return None
        self.pending = False
        return ReadyDelegationAction("edge:s1", "worker", "consume artifact")

    def record_result(self, _action, result: DelegationResult) -> None:
        self.results.append(result)


class _CaptureStream:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def emit(self, channel, event_type, payload, **_kwargs) -> None:
        self.events.append((channel, event_type, payload))


class _SlowProgressBrain(_Brain):
    async def emit_progress_ledger(self, **_kwargs) -> str:
        await asyncio.sleep(1)
        raise AssertionError("wait_for should cancel slow supervisory reasoning")


async def test_ready_action_runs_before_llm_progress_routing() -> None:
    brain = _Brain()
    provider = _Provider()
    calls: list[str] = []

    async def deliver(speaker, _instruction, _progress) -> DelegationResult:
        calls.append(speaker)
        return DelegationResult(True, speaker, "artifact produced")

    supervisor = Supervisor(
        command_id="cmd",
        org_id="org",
        root_node_id="root",
        task="task",
        brain=brain,
        deliver=deliver,
        stream=StreamBus(strict=True),
        checkpointer=MemoryCheckpointer(),
        ready_action_provider=provider,
    )

    outcome = await supervisor.run()

    assert outcome.outcome is FinalOutcome.DONE
    assert calls == ["worker"]
    assert brain.progress_calls == 1
    assert provider.results[0].success is True


async def test_progress_reasoning_timeout_emits_state_and_routes_to_root() -> None:
    stream = _CaptureStream()

    async def deliver(speaker, _instruction, _progress) -> DelegationResult:
        return DelegationResult(True, speaker, "unused")

    supervisor = Supervisor(
        command_id="cmd-timeout",
        org_id="org",
        root_node_id="root",
        task="task",
        brain=_SlowProgressBrain(),
        deliver=deliver,
        stream=stream,
        checkpointer=MemoryCheckpointer(),
        progress_ledger_timeout_s=0.01,
    )

    progress = await supervisor._emit_progress_ledger()

    event_types = [event_type for _channel, event_type, _payload in stream.events]
    assert event_types == ["supervisor_reasoning_started", "supervisor_reasoning_timeout"]
    assert progress.next_speaker_name == "root"
    assert "超时" in str(progress.instruction_or_question.answer)
