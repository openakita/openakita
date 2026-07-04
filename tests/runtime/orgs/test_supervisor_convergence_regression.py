"""RC-5 S6: supervisor convergence + gray-switch regression suite (CI-safe).

Locks the gap⑤ behaviour against regression WITHOUT burning real tokens: a
scripted :class:`FakeLLMClient` returns canned progress-ledger JSON per turn,
driving the *real* Supervisor skeleton (StallDetector / replan / checkpoint /
the production ``LLMSupervisorBrain``). Live model verification is the opt-in
e2e harness under ``_rc5_biz/sprint_s2/`` -- this file is the cheap CI gate.

Covered:

1. ``test_convergence_normal_finishes`` -- good outputs -> brain reports
   satisfied -> DONE in fewer than max_turns (n_turns > 2, NOT the
   PassThrough turn-2 cliff, NOT OUT_OF_TURNS).
2. ``test_convergence_contradictory_replan_budget_exhausted`` -- sustained
   no-progress -> stall -> REPLAN -> budget exhausted ->
   REPLAN_BUDGET_EXHAUSTED (a graceful terminate, NOT OUT_OF_TURNS).
3. ``test_param_clamp_keeps_replan_budget_reachable`` -- S0 clamp boundary.
4. gray-switch wiring (``test_gray_*``) -- default org stays PassThrough;
   allowlist org engages the LLM brain; flag-driven full rollout; safe
   fallback when no client is wired.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.llm_supervisor_brain import LLMSupervisorBrain, NodeDescriptor
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import DelegationResult, FinalOutcome, Supervisor

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Scripted test doubles (same shape as the S1/S2 dry-run suite)
# ---------------------------------------------------------------------------


def _ledger_json(
    *,
    satisfied: bool = False,
    progress: bool = True,
    loop: bool = False,
    speaker: str = "node_writer",
    instruction: str = "next",
) -> str:
    return json.dumps(
        {
            "is_request_satisfied": {"answer": satisfied, "reason": "scripted"},
            "is_progress_being_made": {"answer": progress, "reason": "scripted"},
            "is_in_loop": {"answer": loop, "reason": "scripted"},
            "instruction_or_question": {"answer": instruction, "reason": "scripted"},
            "next_speaker": {"answer": speaker, "reason": "scripted"},
        }
    )


class FakeLLMClient:
    def __init__(self, *, progress_script: list[str]) -> None:
        self._progress = list(progress_script)
        self.calls_by_role: dict[str, int] = {}

    async def complete(self, *, role: str, system: str, user: str, cancel_event=None) -> str:
        self.calls_by_role[role] = self.calls_by_role.get(role, 0) + 1
        if role == "facts":
            return "GIVEN OR VERIFIED FACTS\n- f"
        if role == "plan":
            return "- step"
        if role == "progress_ledger":
            if not self._progress:
                return _ledger_json(satisfied=True)
            return self._progress.pop(0)
        return ""


def _make_deliver() -> tuple[Callable[..., Awaitable[DelegationResult]], list[dict]]:
    log: list[dict] = []

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        log.append({"speaker": speaker})
        return DelegationResult(success=True, speaker=speaker, message=f"{speaker} delivered output")

    return deliver, log


_DIRECTORY = [
    NodeDescriptor(node_id="node_root", role="root"),
    NodeDescriptor(node_id="node_writer", role="copywriter"),
    NodeDescriptor(node_id="node_qa", role="qa"),
]


def _build(
    *,
    progress_script: list[str],
    command_id: str,
    max_stalls: int = 3,
    max_turns: int = 30,
    max_replans: int = 5,
) -> tuple[Supervisor, FakeLLMClient, list[dict]]:
    client = FakeLLMClient(progress_script=progress_script)
    brain = LLMSupervisorBrain(root_node_id="node_root", client=client, node_directory=_DIRECTORY)
    deliver, log = _make_deliver()
    sup = Supervisor(
        command_id=command_id,
        org_id="org_rc5_s6",
        root_node_id="node_root",
        task="multi-node deliverable",
        brain=brain,
        deliver=deliver,
        stream=StreamBus(strict=True),
        checkpointer=MemoryCheckpointer(),
        max_stalls=max_stalls,
        max_turns=max_turns,
        max_replans=max_replans,
    )
    return sup, client, log


# ---------------------------------------------------------------------------
# 1. normal convergence -> graceful DONE
# ---------------------------------------------------------------------------


async def test_convergence_normal_finishes() -> None:
    sup, client, log = _build(
        command_id="s6_normal",
        progress_script=[
            _ledger_json(speaker="copywriter", instruction="draft"),
            _ledger_json(speaker="qa", instruction="review"),
            _ledger_json(satisfied=True, speaker="supervisor", instruction="done"),
        ],
    )
    out = await sup.run()

    assert out.outcome is FinalOutcome.DONE
    # n_turns decision-driven and > the PassThrough turn-2 cliff.
    assert out.n_turns == 3
    assert out.n_replans == 0
    # progress_ledger genuinely updated each turn.
    assert len(sup.history) == 3
    # did NOT hit the hard cap.
    assert sup.stall_detector.n_turns < sup.cfg.max_turns
    # role-style speakers resolved to node_ids on the working turns.
    assert [e["speaker"] for e in log] == ["node_writer", "node_qa"]


# ---------------------------------------------------------------------------
# 2. contradictory / no-progress -> REPLAN budget exhausted (not OUT_OF_TURNS)
# ---------------------------------------------------------------------------


async def test_convergence_contradictory_replan_budget_exhausted() -> None:
    # max_stalls=2, max_replans=1: stall twice -> REPLAN (budget 1), stall
    # twice more -> REPLAN requested but budget exhausted -> graceful stop.
    sup, _client, _log = _build(
        command_id="s6_contradictory",
        max_stalls=2,
        max_replans=1,
        progress_script=[
            _ledger_json(progress=False, instruction="stuck 1"),  # n_stalls=1 SUSPECT
            _ledger_json(progress=False, instruction="stuck 2"),  # n_stalls=2 REPLAN -> replan #1
            _ledger_json(progress=False, instruction="stuck 3"),  # n_stalls=1 SUSPECT
            _ledger_json(progress=False, instruction="stuck 4"),  # n_stalls=2 REPLAN -> exhausted
        ],
    )
    out = await sup.run()

    assert out.outcome is FinalOutcome.REPLAN_BUDGET_EXHAUSTED
    assert out.n_replans == 1
    # Critically: it did NOT run to the hard turn cap.
    assert sup.stall_detector.n_turns < sup.cfg.max_turns


async def test_convergence_in_loop_signal_also_stalls() -> None:
    # is_in_loop=true counts as a stall even when progress is claimed true.
    sup, _client, _log = _build(
        command_id="s6_loop",
        max_stalls=2,
        max_replans=0,
        progress_script=[
            _ledger_json(progress=True, loop=True, instruction="spin 1"),  # n_stalls=1
            _ledger_json(progress=True, loop=True, instruction="spin 2"),  # n_stalls=2 REPLAN -> no budget
        ],
    )
    out = await sup.run()
    assert out.outcome is FinalOutcome.REPLAN_BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# 3. S0 clamp boundary -- replan budget stays reachable under bad params
# ---------------------------------------------------------------------------


async def test_param_clamp_keeps_replan_budget_reachable() -> None:
    # max_turns=4 is below max_stalls(2) * (max_replans(2)+2) = 8; the S0 clamp
    # must raise it so the replan budget can actually be reached.
    sup, _client, _log = _build(
        command_id="s6_clamp",
        progress_script=[_ledger_json(satisfied=True)],
        max_stalls=2,
        max_turns=4,
        max_replans=2,
    )
    assert sup.cfg.max_turns >= 2 * (2 + 2)
    out = await sup.run()
    assert out.outcome is FinalOutcome.DONE


# ---------------------------------------------------------------------------
# 4. gray-switch wiring -- the org-gated LLM brain injection
# ---------------------------------------------------------------------------


class _Node:
    def __init__(self, id_: str, role_title: str = "") -> None:
        self.id = id_
        self.role_title = role_title
        self.role_goal = ""
        self.department = ""


class _Org:
    def __init__(self) -> None:
        self.nodes = [_Node("node_root", "root"), _Node("node_writer", "copywriter")]


class _Lookup:
    def get_org(self, org_id: str) -> _Org:
        return _Org()


def _capturing_service(*, llm_client: Any) -> tuple[Any, dict]:
    from openakita.orgs.command_service import OrgCommandService

    captured: dict[str, Any] = {}

    def _factory(**kwargs: Any) -> Any:
        captured.clear()
        captured.update(kwargs)
        return MagicMock()

    rt = MagicMock()
    svc = OrgCommandService(
        rt,
        lookup=_Lookup(),
        executor_provider=lambda: MagicMock(),
        supervisor_factory=_factory,
        llm_client_provider=(lambda org_id: llm_client),
    )
    return svc, captured


def _set_gray(monkeypatch, *, mode: str, allowlist: list[str]) -> None:
    from openakita.config import settings

    monkeypatch.setattr(settings, "orgs_supervisor_brain_mode", mode, raising=False)
    monkeypatch.setattr(settings, "orgs_supervisor_llm_org_allowlist", allowlist, raising=False)


async def test_gray_default_org_stays_passthrough(monkeypatch) -> None:
    _set_gray(monkeypatch, mode="passthrough", allowlist=[])
    svc, captured = _capturing_service(llm_client=MagicMock())
    svc._build_supervisor(org_id="org_default", command_id="c", root_node_id="node_root", task="t")
    # No LLM wiring injected -> the factory defaults to PassThrough.
    assert "brain_mode" not in captured
    assert "llm_client" not in captured
    assert "node_directory" not in captured


async def test_gray_allowlisted_org_engages_llm(monkeypatch) -> None:
    _set_gray(monkeypatch, mode="passthrough", allowlist=["org_gray"])
    sentinel = MagicMock()
    svc, captured = _capturing_service(llm_client=sentinel)
    svc._build_supervisor(org_id="org_gray", command_id="c", root_node_id="node_root", task="t")
    assert captured.get("brain_mode") == "llm"
    assert captured.get("llm_client") is sentinel
    # gap④: a real node directory was injected from the store.
    directory = captured.get("node_directory")
    assert directory and [d.node_id for d in directory] == ["node_root", "node_writer"]


async def test_gray_non_allowlisted_org_isolated(monkeypatch) -> None:
    # An org NOT in the allowlist stays passthrough even while another org is grayed.
    _set_gray(monkeypatch, mode="passthrough", allowlist=["org_gray"])
    svc, captured = _capturing_service(llm_client=MagicMock())
    svc._build_supervisor(org_id="org_other", command_id="c", root_node_id="node_root", task="t")
    assert "brain_mode" not in captured
    assert "llm_client" not in captured


async def test_gray_global_flag_full_rollout(monkeypatch) -> None:
    _set_gray(monkeypatch, mode="llm", allowlist=[])
    svc, captured = _capturing_service(llm_client=MagicMock())
    svc._build_supervisor(org_id="any_org", command_id="c", root_node_id="node_root", task="t")
    assert captured.get("brain_mode") == "llm"


async def test_gray_no_client_falls_back_to_passthrough(monkeypatch) -> None:
    # Allowlisted org but the client provider returns None -> no llm wiring
    # (the factory's _resolve_brain also guards, but we never even pass llm).
    _set_gray(monkeypatch, mode="passthrough", allowlist=["org_gray"])
    svc, captured = _capturing_service(llm_client=None)
    svc._build_supervisor(org_id="org_gray", command_id="c", root_node_id="node_root", task="t")
    assert "brain_mode" not in captured
    assert "llm_client" not in captured
