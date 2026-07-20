"""Deterministic root-finalization backstop for the Supervisor.

Task A: when the orchestration loop converges (``is_request_satisfied``) but the
root/主编 has not itself produced the final integrated deliverable, the
supervisor forces ONE closing delegation to the root so the final deliverable /
PDF always come from the root's integration -- never from a report node's
output nor the root's initial kickoff. These tests pin that behaviour
independently of any LLM/prompt guidance.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.execution_context import ExecutionPhase, current_execution_phase_var
from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import (
    DelegationResult,
    FinalOutcome,
    Supervisor,
    SupervisorBrain,
)

ROOT = "node_root"
LONG = "x" * 400  # comfortably above the 200-char integration threshold


def _ledger_json(
    *,
    satisfied: bool = False,
    speaker: str = "planner",
    instruction: str = "do the next thing",
    phase: str = "execution",
) -> str:
    return json.dumps(
        {
            "is_request_satisfied": {"answer": satisfied, "reason": "r"},
            "is_progress_being_made": {"answer": True, "reason": "r"},
            "is_in_loop": {"answer": False, "reason": "r"},
            "instruction_or_question": {"answer": instruction, "reason": "r"},
            "next_speaker": {"answer": speaker, "reason": "r"},
            "execution_phase": phase,
        }
    )


class _Brain(SupervisorBrain):
    def __init__(self, progress_responses: list[str]) -> None:
        self._progress = list(progress_responses)

    async def extract_facts(self, *, task: str, **_kwargs) -> str:
        return "facts"

    async def draft_plan(self, *, task: str, facts: str, **_kwargs) -> str:
        return "plan"

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        recent_outputs: list | None = None,
        **_kwargs,
    ) -> str:
        if not self._progress:
            return _ledger_json(satisfied=True)
        return self._progress.pop(0)


def _make_deliver() -> tuple[Callable[..., Awaitable[DelegationResult]], list[dict]]:
    log: list[dict] = []

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        log.append({"speaker": speaker, "instruction": instruction})
        return DelegationResult(
            success=True,
            speaker=speaker,
            message=f"{speaker} integrated result: {LONG}",
            metadata={
                "delivery_manifest": {
                    "state": "complete",
                    "final": speaker == ROOT,
                    "artifacts": [{"kind": "document", "status": "ready"}],
                }
            },
        )

    return deliver, log


def _build(
    brain: _Brain,
    deliver,
    *,
    force: bool,
    hard_ceiling_s: float = 0.0,
    max_replans: int = 5,
    asset_inventory_provider=None,
) -> Supervisor:
    return Supervisor(
        command_id="cmd_x",
        org_id="org_1",
        root_node_id=ROOT,
        task="ship it",
        brain=brain,
        deliver=deliver,
        stream=StreamBus(strict=True),
        checkpointer=MemoryCheckpointer(),
        force_root_finalization=force,
        wall_clock_hard_ceiling_s=hard_ceiling_s,
        max_replans=max_replans,
        asset_inventory_provider=asset_inventory_provider,
    )


async def test_forces_root_finalization_when_last_speaker_is_report() -> None:
    # Turn 1 routes to a report node (planner); turn 2 is satisfied. Because the
    # root never produced the integrated result, the backstop must fire ONE
    # closing delegation to the root before terminating DONE.
    brain = _Brain([_ledger_json(satisfied=False, speaker="planner"), _ledger_json(satisfied=True)])
    deliver, log = _make_deliver()
    sup = _build(brain, deliver, force=True)

    out = await sup.run()

    assert out.outcome is FinalOutcome.DONE
    assert [row["speaker"] for row in log] == ["planner", ROOT]
    assert "最终整合" in log[-1]["instruction"]
    # The final deliverable comes from the root's integration turn.
    assert ROOT in sup.delegation_history[-1].speaker
    assert LONG in out.deliverable


async def test_supervisor_marks_returning_root_turn_as_structured_finalization() -> None:
    brain = _Brain(
        [
            _ledger_json(satisfied=False, speaker="planner"),
            _ledger_json(
                satisfied=False,
                speaker=ROOT,
                instruction="收拢结果",
                phase="finalization",
            ),
            _ledger_json(satisfied=True),
        ]
    )
    phases: list[ExecutionPhase] = []

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        phases.append(current_execution_phase_var.get())
        return DelegationResult(
            success=True,
            speaker=speaker,
            message=LONG,
            metadata={
                "delivery_manifest": {
                    "state": "complete",
                    "final": speaker == ROOT,
                    "artifacts": [],
                }
            },
        )

    out = await _build(brain, deliver, force=False).run()

    assert out.outcome is FinalOutcome.DONE
    assert phases == [ExecutionPhase.EXECUTION, ExecutionPhase.FINALIZATION]


async def test_supervisor_propagates_structured_planning_phase() -> None:
    brain = _Brain(
        [
            _ledger_json(
                satisfied=False,
                speaker=ROOT,
                instruction="declare the delegation DAG",
                phase="planning",
            ),
            _ledger_json(satisfied=True),
        ]
    )
    phases: list[ExecutionPhase] = []

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        phases.append(current_execution_phase_var.get())
        return DelegationResult(success=True, speaker=speaker, message=LONG)

    out = await _build(brain, deliver, force=False).run()

    assert out.outcome is FinalOutcome.DONE
    assert phases == [ExecutionPhase.PLANNING]


async def test_skips_when_root_already_integrated() -> None:
    # Turn 1 already routes to the root with a substantial body; the backstop
    # must NOT add a redundant extra root turn.
    brain = _Brain([_ledger_json(satisfied=False, speaker=ROOT), _ledger_json(satisfied=True)])
    deliver, log = _make_deliver()
    sup = _build(brain, deliver, force=True)

    out = await sup.run()

    assert out.outcome is FinalOutcome.DONE
    assert [row["speaker"] for row in log] == [ROOT]


async def test_disabled_by_default_no_extra_turn() -> None:
    # Same shape as the "forces" scenario but with the flag off: behaviour is
    # byte-for-byte the pre-existing path (no forced root turn).
    brain = _Brain([_ledger_json(satisfied=False, speaker="planner"), _ledger_json(satisfied=True)])
    deliver, log = _make_deliver()
    sup = _build(brain, deliver, force=False)

    out = await sup.run()

    assert out.outcome is FinalOutcome.DONE
    assert [row["speaker"] for row in log] == ["planner"]


async def test_complete_structured_root_manifest_skips_second_routing_judgment() -> None:
    brain = _Brain(
        [
            _ledger_json(satisfied=False, speaker=ROOT),
            _ledger_json(satisfied=False, speaker="planner", instruction="must not be consumed"),
        ]
    )
    log: list[dict] = []

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        is_finalization = "最终整合" in instruction
        log.append({"speaker": speaker, "instruction": instruction})
        return DelegationResult(
            success=True,
            speaker=speaker,
            message=LONG,
            metadata={
                "delivery_manifest": {
                    "state": "complete",
                    "final": is_finalization,
                    "artifacts": [{"kind": "video", "status": "ready"}],
                }
            },
        )

    out = await _build(brain, deliver, force=True).run()

    assert out.outcome is FinalOutcome.DONE
    assert [row["speaker"] for row in log] == [ROOT, ROOT]
    assert len(brain._progress) == 1


async def test_failed_root_finalization_replans_and_retries() -> None:
    brain = _Brain(
        [
            _ledger_json(satisfied=False, speaker="planner"),
            _ledger_json(satisfied=True),
            _ledger_json(satisfied=True),
        ]
    )
    log: list[dict] = []
    root_attempts = 0

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        nonlocal root_attempts
        log.append({"speaker": speaker, "instruction": instruction})
        if speaker == ROOT:
            root_attempts += 1
            if root_attempts == 1:
                return DelegationResult(
                    success=False,
                    speaker=speaker,
                    message="视频资产未登记到命令附件",
                    metadata={"reason": "media_delivery_file_missing"},
                )
        return DelegationResult(
            success=True,
            speaker=speaker,
            message=LONG,
            metadata={
                "delivery_manifest": {
                    "state": "complete",
                    "final": speaker == ROOT,
                    "artifacts": [{"kind": "document", "status": "ready"}],
                }
            },
        )

    out = await _build(brain, deliver, force=True).run()

    assert out.outcome is FinalOutcome.DONE
    assert out.n_replans == 1
    assert [row["speaker"] for row in log] == ["planner", ROOT, ROOT]


async def test_failed_root_finalization_never_reports_done_when_replans_exhausted() -> None:
    brain = _Brain([_ledger_json(satisfied=False, speaker="planner"), _ledger_json(satisfied=True)])

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        if speaker == ROOT:
            return DelegationResult(
                success=False,
                speaker=speaker,
                message="最终报告声称已交付视频，但没有已登记的视频资产",
                metadata={"reason": "media_delivery_unregistered"},
            )
        return DelegationResult(success=True, speaker=speaker, message=LONG)

    out = await _build(brain, deliver, force=True, max_replans=0).run()

    assert out.outcome is FinalOutcome.REPLAN_BUDGET_EXHAUSTED
    assert out.reason == "media_delivery_unregistered"


async def test_successful_text_without_final_manifest_never_reports_done() -> None:
    brain = _Brain([_ledger_json(satisfied=False, speaker="planner"), _ledger_json(satisfied=True)])

    async def deliver(speaker: str, instruction: str, progress: ProgressLedger) -> DelegationResult:
        return DelegationResult(success=True, speaker=speaker, message=LONG)

    out = await _build(brain, deliver, force=True, max_replans=0).run()

    assert out.outcome is FinalOutcome.REPLAN_BUDGET_EXHAUSTED
    assert out.reason == "root_final_manifest_missing"


async def test_skips_finalization_when_hard_ceiling_budget_insufficient() -> None:
    # test13 RCA: with an outer hard ceiling almost fully consumed, the forced
    # finalization would be a doomed extra root turn that the ceiling kills
    # mid-flight (deliverable then falls back to the kickoff). The budget gate
    # must SKIP the closing root turn and terminate cleanly on the report output.
    brain = _Brain([_ledger_json(satisfied=False, speaker="planner"), _ledger_json(satisfied=True)])
    deliver, log = _make_deliver()
    # ceiling of 1s -> remaining budget (1 - elapsed - 20 margin) floors at 0,
    # which is <= the 150s minimum -> skip.
    sup = _build(brain, deliver, force=True, hard_ceiling_s=1.0)

    out = await sup.run()

    assert out.outcome is FinalOutcome.OUT_OF_TURNS
    # No extra ROOT turn was forced (budget too low).
    assert [row["speaker"] for row in log] == ["planner"]


async def test_forces_finalization_when_hard_ceiling_budget_ample() -> None:
    # With a generous ceiling the budget gate is a no-op: the closing root turn
    # still fires (regression guard so the gate doesn't over-suppress).
    brain = _Brain([_ledger_json(satisfied=False, speaker="planner"), _ledger_json(satisfied=True)])
    deliver, log = _make_deliver()
    sup = _build(brain, deliver, force=True, hard_ceiling_s=3600.0)

    out = await sup.run()

    assert out.outcome is FinalOutcome.DONE
    assert [row["speaker"] for row in log] == ["planner", ROOT]


async def test_finalization_instruction_contains_command_asset_inventory() -> None:
    brain = _Brain([_ledger_json(satisfied=False, speaker="planner"), _ledger_json(satisfied=True)])
    deliver, log = _make_deliver()
    assets = [
        {
            "task_ids": ["video-task"],
            "asset_ids": ["video-asset"],
            "registered_video_paths": ["D:/command/video.mp4"],
            "media_validation_passed": True,
        }
    ]
    sup = _build(
        brain,
        deliver,
        force=True,
        asset_inventory_provider=lambda: assets,
    )

    out = await sup.run()

    assert out.outcome is FinalOutcome.DONE
    instruction = log[-1]["instruction"]
    assert "本命令资产账本" in instruction
    assert "D:/command/video.mp4" in instruction
    assert "不要调用 glob/list/read/shell" in instruction


def test_best_effort_prefers_non_kickoff_output() -> None:
    # test13 RCA: the root's turn-1 kickoff dump (dispatch scaffolding) must NOT
    # be surfaced as the deliverable when a real (non-kickoff) output exists.
    brain = _Brain([_ledger_json(satisfied=True)])
    deliver, _log = _make_deliver()
    sup = _build(brain, deliver, force=False)
    sup.delegation_history.append(
        DelegationResult(
            success=True,
            speaker=ROOT,
            message="# 项目启动指令\n请各节点执行\n[dispatched to planner]\n" + LONG,
            artifact_role="kickoff",
        )
    )
    sup.delegation_history.append(
        DelegationResult(
            success=True,
            speaker="planner",
            message="真实整合报告：完整成果 " + LONG,
        )
    )
    out = sup.best_effort_deliverable()
    assert "真实整合报告" in out
    assert "项目启动指令" not in out


def test_best_effort_falls_back_to_kickoff_when_only_content() -> None:
    # Safety floor: if the kickoff dump is the ONLY successful output, we must
    # still return it (never an empty deliverable).
    brain = _Brain([_ledger_json(satisfied=True)])
    deliver, _log = _make_deliver()
    sup = _build(brain, deliver, force=False)
    sup.delegation_history.append(
        DelegationResult(
            success=True,
            speaker=ROOT,
            message="# 项目启动指令\n[dispatched to planner]\n" + LONG,
            artifact_role="kickoff",
        )
    )
    out = sup.best_effort_deliverable()
    assert "项目启动指令" in out


def test_best_effort_does_not_infer_role_from_report_prose() -> None:
    brain = _Brain([_ledger_json(satisfied=True)])
    deliver, _log = _make_deliver()
    sup = _build(brain, deliver, force=False)
    report = "复盘报告引用了‘项目启动指令’和 [dispatched to planner]，但它仍是正式成果。" + LONG
    sup.delegation_history.append(
        DelegationResult(
            success=True,
            speaker=ROOT,
            message=report,
            artifact_role="final",
        )
    )
    assert sup.best_effort_deliverable() == report
