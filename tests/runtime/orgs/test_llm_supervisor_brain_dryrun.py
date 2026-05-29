"""RC-5 route-B pathfinding: drive the real Supervisor skeleton with the
dry-run :class:`LLMSupervisorBrain` + a scripted FakeLLMClient.

This is the *core evidence* for pathfinding question Q1:

    "If we feed the existing Supervisor skeleton a brain that returns real
    orchestration decisions (proceed / stall / replan / done), do
    progress_ledger / stall_detector / replan / checkpoint actually get
    driven? (i.e. is the skeleton ready, only the brain missing?)"

We do NOT mock the Supervisor, StallDetector, ledger parsing, or
checkpointer -- only the LLM itself (the injectable ``SupervisorLLMClient``
seam). So a green run here proves the whole orchestration machine turns
over end-to-end under a real brain.

Three scenarios:

* ``test_scenario1_multi_turn_progress`` -- brain advances different nodes
  across >2 turns; progress_ledger is genuinely updated each turn (not all
  False / not the PassThrough turn-2 cliff).
* ``test_scenario2_stall_then_replan`` -- brain reports sustained
  no-progress; StallDetector trips, replan fires, ``n_replans`` increments.
* ``test_scenario3_clean_done_checkpoint`` -- brain reports satisfied;
  clean terminate with a DONE checkpoint persisted.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import pytest

from openakita.runtime.checkpoint import CheckpointStatus, MemoryCheckpointer
from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.llm_supervisor_brain import (
    LLMSupervisorBrain,
    NodeDescriptor,
)
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import (
    DelegationResult,
    FinalOutcome,
    Supervisor,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _ledger_json(
    *,
    satisfied: bool = False,
    progress: bool = True,
    loop: bool = False,
    speaker: str = "writer",
    instruction: str = "do the next thing",
) -> str:
    """Build a strict-schema progress-ledger JSON string (what the LLM emits)."""
    return json.dumps(
        {
            "is_request_satisfied":    {"answer": satisfied, "reason": "scripted"},
            "is_progress_being_made":  {"answer": progress, "reason": "scripted"},
            "is_in_loop":              {"answer": loop, "reason": "scripted"},
            "instruction_or_question": {"answer": instruction, "reason": "scripted"},
            "next_speaker":            {"answer": speaker, "reason": "scripted"},
        }
    )


class FakeLLMClient:
    """Scripted :class:`SupervisorLLMClient` -- the only thing we mock.

    Returns canned text for ``facts`` / ``plan`` roles and pops the next
    scripted JSON ledger for the ``progress_ledger`` role. Records every
    call so tests can assert how many LLM round-trips a command costs
    (the live-phase cost-probe measures the same counters).
    """

    def __init__(self, *, progress_script: list[str]) -> None:
        self._progress = list(progress_script)
        self.calls: list[str] = []
        self.calls_by_role: dict[str, int] = {}
        # RC-5 S1/S2: keep every rendered user prompt per role so tests can
        # assert the convergence prompt + the fed-back node outputs.
        self.user_by_role: dict[str, list[str]] = {}

    async def complete(
        self,
        *,
        role: str,
        system: str,
        user: str,
        cancel_event=None,
    ) -> str:
        self.calls.append(role)
        self.calls_by_role[role] = self.calls_by_role.get(role, 0) + 1
        self.user_by_role.setdefault(role, []).append(user)
        if role == "facts":
            return "GIVEN OR VERIFIED FACTS\n- scripted fact"
        if role == "plan":
            return "- step 1\n- step 2"
        if role == "progress_ledger":
            if not self._progress:
                return _ledger_json(satisfied=True)
            return self._progress.pop(0)
        return ""


class RecordingStreamBus(StreamBus):
    """StreamBus that records every emit while keeping strict validation."""

    def __init__(self) -> None:
        super().__init__(strict=True)
        self.events: list[tuple[str, str, dict]] = []

    async def emit(self, channel, type, payload, **kwargs):  # type: ignore[override]
        self.events.append((channel, type, dict(payload)))
        return await super().emit(channel, type, payload, **kwargs)

    def types_on(self, channel: str) -> list[str]:
        return [t for (c, t, _p) in self.events if c == channel]


def _make_deliver() -> tuple[Callable[..., Awaitable[DelegationResult]], list[dict]]:
    log: list[dict] = []

    async def deliver(
        speaker: str, instruction: str, progress: ProgressLedger
    ) -> DelegationResult:
        log.append({"speaker": speaker, "instruction": instruction})
        return DelegationResult(
            success=True, speaker=speaker, message=f"{speaker} delivered"
        )

    return deliver, log


_DIRECTORY = [
    NodeDescriptor(node_id="node_root", role="root", capabilities="entry"),
    NodeDescriptor(node_id="writer", role="copywriter", capabilities="copy"),
    NodeDescriptor(node_id="designer", role="art_director", capabilities="visuals"),
    NodeDescriptor(node_id="reviewer", role="qa", capabilities="review"),
]


def _build(
    *,
    progress_script: list[str],
    command_id: str,
    max_stalls: int = 3,
    max_turns: int = 30,
    max_replans: int = 5,
) -> tuple[Supervisor, FakeLLMClient, RecordingStreamBus, MemoryCheckpointer, list[dict]]:
    client = FakeLLMClient(progress_script=progress_script)
    brain = LLMSupervisorBrain(
        root_node_id="node_root",
        client=client,
        node_directory=_DIRECTORY,
    )
    bus = RecordingStreamBus()
    store = MemoryCheckpointer()
    deliver, log = _make_deliver()
    sup = Supervisor(
        command_id=command_id,
        org_id="org_rc5",
        root_node_id="node_root",
        task="produce a multi-node deliverable",
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
        max_stalls=max_stalls,
        max_turns=max_turns,
        max_replans=max_replans,
    )
    return sup, client, bus, store, log


# ---------------------------------------------------------------------------
# Scenario 1 -- multi-turn progress across distinct nodes
# ---------------------------------------------------------------------------


async def test_scenario1_multi_turn_progress() -> None:
    sup, client, bus, _store, log = _build(
        command_id="cmd_s1",
        progress_script=[
            _ledger_json(speaker="copywriter", instruction="draft copy"),
            _ledger_json(speaker="art_director", instruction="design layout"),
            _ledger_json(speaker="qa", instruction="review draft"),
            _ledger_json(satisfied=True, speaker="supervisor", instruction="all done"),
        ],
    )
    out = await sup.run()

    # The loop ran past the PassThrough turn-2 cliff.
    assert out.outcome is FinalOutcome.DONE
    assert out.n_turns == 4
    assert out.n_replans == 0

    # progress_ledger genuinely updated every turn (NOT all-false / static).
    assert len(sup.history) == 4
    speakers = [p.next_speaker_name for p in sup.history]
    # role-style answers got resolved to concrete node_ids (gap #2 layer);
    # the terminal "supervisor" sentinel passes through untouched.
    assert speakers == ["writer", "designer", "reviewer", "supervisor"]
    # the three working turns all reported forward progress.
    assert all(p.progress_being_made for p in sup.history[:3])

    # The skeleton actually delegated to three distinct nodes.
    delegated = [e["speaker"] for e in log]
    assert delegated == ["writer", "designer", "reviewer"]

    # A progress_ledger stream event fired on each working turn.
    assert bus.types_on("progress_ledger").count("ledger") == 4
    # Outer-loop ran exactly once (no replan): facts/plan once each.
    assert client.calls_by_role.get("facts") == 1
    assert client.calls_by_role.get("plan") == 1
    assert client.calls_by_role.get("progress_ledger") == 4


# ---------------------------------------------------------------------------
# Scenario 2 -- sustained stall trips replan
# ---------------------------------------------------------------------------


async def test_scenario2_stall_then_replan() -> None:
    sup, client, bus, _store, _log = _build(
        command_id="cmd_s2",
        max_stalls=3,
        progress_script=[
            _ledger_json(progress=False, instruction="stuck 1"),  # n_stalls=1 SUSPECT
            _ledger_json(progress=False, instruction="stuck 2"),  # n_stalls=2 SUSPECT
            _ledger_json(progress=False, instruction="stuck 3"),  # n_stalls=3 REPLAN
            _ledger_json(satisfied=True, instruction="recovered"),  # DONE
        ],
    )
    out = await sup.run()

    assert out.outcome is FinalOutcome.DONE
    # The replan fired exactly once (StallDetector REPLAN -> outer loop).
    assert out.n_replans == 1
    assert sup.task_ledger.revision == 1

    # Outer loop ran twice (initial + replan) -> facts/plan twice.
    assert client.calls_by_role.get("facts") == 2
    assert client.calls_by_role.get("plan") == 2

    # Stall warnings were emitted before the threshold (SUSPECT turns).
    stall_warnings = [t for t in bus.types_on("lifecycle") if t == "stall_warning"]
    assert len(stall_warnings) >= 2
    # The replan lifecycle event landed.
    assert "replanning" in bus.types_on("lifecycle")


# ---------------------------------------------------------------------------
# Scenario 3 -- clean done + DONE checkpoint persisted
# ---------------------------------------------------------------------------


async def test_scenario3_clean_done_checkpoint() -> None:
    sup, _client, bus, store, log = _build(
        command_id="cmd_s3",
        progress_script=[
            _ledger_json(speaker="copywriter", instruction="do the work"),
            _ledger_json(satisfied=True, instruction="done"),
        ],
    )
    out = await sup.run()

    assert out.outcome is FinalOutcome.DONE
    assert out.n_turns == 2
    assert out.n_replans == 0
    # Exactly one delegation happened (turn 1), then terminate (turn 2).
    assert [e["speaker"] for e in log] == ["writer"]

    # A terminal DONE checkpoint was persisted and is fetchable.
    assert out.final_checkpoint_id is not None
    fetched = await store.aget(out.final_checkpoint_id)
    assert fetched is not None
    assert fetched.metadata.status == CheckpointStatus.DONE
    # Per-turn checkpoints were streamed.
    assert "checkpoint_written" in bus.types_on("checkpoints")


# ---------------------------------------------------------------------------
# RC-5 S1 (gap⑤) -- the brain receives real node outputs and renders them
# into the progress-ledger prompt (no longer "blind").
# ---------------------------------------------------------------------------


async def test_s1_brain_renders_fed_back_node_outputs() -> None:
    """Turn 1 delegates; turn 2's progress prompt must carry the node output."""
    sup, client, _bus, _store, log = _build(
        command_id="cmd_s1_feedback",
        progress_script=[
            _ledger_json(speaker="copywriter", instruction="draft copy"),  # turn 1
            _ledger_json(satisfied=True, instruction="done"),  # turn 2
        ],
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.DONE

    pl_prompts = client.user_by_role["progress_ledger"]
    assert len(pl_prompts) == 2

    # Turn 1: no node output yet -> the "no outputs" placeholder is rendered.
    assert "尚无任何节点产出" in pl_prompts[0]

    # Turn 2: the REAL deliverable from turn 1 must be visible in the prompt
    # (the deliver stub returns "<speaker> delivered"); the delegation went to
    # the resolved node_id "writer".
    assert "writer delivered" in pl_prompts[1]
    assert "ACTUAL OUTPUTS" in pl_prompts[1]
    # And the supervisor recorded the delegation result it fed back.
    assert len(sup.delegation_history) == 1
    assert log[0]["speaker"] == "writer"


# ---------------------------------------------------------------------------
# RC-5 S2 -- the production progress-ledger prompt fixes the convergence rules.
# ---------------------------------------------------------------------------


async def test_s2_progress_prompt_contains_convergence_rules() -> None:
    sup, client, _bus, _store, _log = _build(
        command_id="cmd_s2_prompt",
        progress_script=[_ledger_json(satisfied=True, instruction="done")],
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.DONE

    prompt = client.user_by_role["progress_ledger"][0]
    # The {outputs} block placeholder header is present.
    assert "=== ACTUAL OUTPUTS" in prompt
    # The three explicit convergence Decision rules are fixed into the prompt.
    assert "Decision rules" in prompt
    assert "is_request_satisfied = true ONLY IF" in prompt
    assert "is_progress_being_made = false" in prompt
    assert "is_in_loop = true" in prompt
