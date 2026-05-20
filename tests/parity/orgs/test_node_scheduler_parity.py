"""Parity fixtures for OrgNodeScheduler v2-baseline (P-RC-9 P9.9δ-2a; was P9.3c v1 oracle).

Each :class:`ParityCase` exercises a scripted scenario against
the v2 ``openakita.runtime.orgs.node_scheduler.OrgNodeScheduler``
and asserts the normalised :class:`ParityResult` equals the
captured golden dict in ``_golden_node_scheduler.json``.

Per P-RC-9-P9.9 δ-2a (audit §6 Option B): this file shipped 4
v1-vs-v2 oracle cases in P9.3c. The v1 import / runners were
removed in δ-2a; the golden dicts were captured from the v2
output at HEAD ``a3a5fde6`` (close of δ-1). The 1-ms next-
fire-time tolerance from the v1-vs-v2 contract is dropped --
v2 is the single source of truth at this commit, so the
asserts collapse to verbatim equality.

The dispatch_prompt case strips the ``时间: <iso>`` line so
the prompt compares structurally; that timestamp line is also
absent from the golden dict.

Sentinel discipline (P-RC-9 §7.1): sentinel #3 stays ACTIVE
through G-RC-9.9; semantics shift from oracle-equality to
v2-baseline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.parity.harness import ParityCase, ParityResult

_GOLDEN: dict[str, dict] = json.loads(
    (Path(__file__).parent / "_golden_node_scheduler.json").read_text(encoding="utf-8")
)


def _strip_timestamp_line(prompt: str) -> str:
    """Remove the ``时间: <iso>`` line so prompts compare structurally."""
    return "\n".join(line for line in prompt.split("\n") if not line.startswith("时间: "))


def _next_fire_v2(case: ParityCase, now: datetime) -> ParityResult:
    """v2 next-fire via :func:`compute_next_fire_time` pure helper."""
    from openakita.runtime.orgs.node_scheduler import compute_next_fire_time
    from openakita.runtime.orgs.scheduler_models import NodeSchedule as V2NS
    from openakita.runtime.orgs.scheduler_models import ScheduleType as V2ST

    sched = V2NS(
        name=case.inputs["name"],
        schedule_type=V2ST(case.inputs["schedule_type"]),
        cron=case.inputs.get("cron"),
        interval_s=case.inputs.get("interval_s"),
        run_at=case.inputs.get("run_at"),
        prompt=case.inputs["prompt"],
    )
    target = compute_next_fire_time(sched, now)
    return ParityResult(
        final_message="next_fire",
        success=True,
        extras={"fire_iso": target.isoformat()},
    )


def _v2_capture_prompt(case: ParityCase) -> str:
    """Run v2 ``OrgNodeScheduler.trigger_once`` end-to-end and capture the prompt."""
    import asyncio

    from openakita.runtime.orgs.node_scheduler import OrgNodeScheduler as V2Sched
    from openakita.runtime.orgs.scheduler_models import NodeSchedule as V2NS
    from openakita.runtime.orgs.scheduler_models import ScheduleType as V2ST

    captured: dict[str, str] = {}

    class CapDispatcher:
        async def dispatch(self, org_id: str, node_id: str, prompt: str) -> dict:
            captured["prompt"] = prompt
            return {"result": "ok"}

    sched = V2NS(
        name=case.inputs["name"],
        schedule_type=V2ST(case.inputs["schedule_type"]),
        interval_s=case.inputs.get("interval_s"),
        prompt=case.inputs["prompt"],
        report_condition=case.inputs.get("report_condition", "on_issue"),
        report_to=case.inputs.get("report_to"),
    )

    class CapStore:
        def __init__(self) -> None:
            self._scheds = [sched]

        def get_node_schedules(self, org_id: str, node_id: str):
            return list(self._scheds)

        def save_node_schedules(self, org_id: str, node_id: str, scheds) -> None:
            self._scheds = list(scheds)

    class CapProbe:
        def is_node_runnable(self, org_id: str, node_id: str) -> bool:
            return True

        def emit_event(self, org_id: str, event_type: str, node_id: str, payload: dict) -> None:
            pass

    scheduler = V2Sched(CapDispatcher(), CapStore(), CapProbe())
    asyncio.run(scheduler.trigger_once("o", "n", sched.id))
    return captured["prompt"]


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


CASES: list[ParityCase] = [
    ParityCase(
        id="scheduler_next_fire_interval",
        kind="node_scheduler",
        inputs={
            "op": "next_fire",
            "name": "int600",
            "schedule_type": "interval",
            "interval_s": 600,
            "prompt": "check",
        },
    ),
    ParityCase(
        id="scheduler_next_fire_once",
        kind="node_scheduler",
        inputs={
            "op": "next_fire",
            "name": "once120",
            "schedule_type": "once",
            "run_at": (_NOW + timedelta(seconds=120)).isoformat(),
            "prompt": "do",
        },
    ),
    ParityCase(
        id="scheduler_next_fire_cron",
        kind="node_scheduler",
        inputs={
            "op": "next_fire",
            "name": "cron300",
            "schedule_type": "cron",
            "cron": "*/5 * * * *",
            "interval_s": 300,
            "prompt": "poll",
        },
    ),
    ParityCase(
        id="scheduler_dispatch_prompt",
        kind="node_scheduler",
        inputs={
            "op": "dispatch_prompt",
            "name": "巡检",
            "schedule_type": "interval",
            "interval_s": 3600,
            "prompt": "检查服务状态",
            "report_condition": "on_issue",
            "report_to": "领导",
        },
    ),
]


def _run_case(case: ParityCase) -> ParityResult:
    op = case.inputs["op"]
    if op == "next_fire":
        return _next_fire_v2(case, _NOW)
    if op == "dispatch_prompt":
        prompt = _strip_timestamp_line(_v2_capture_prompt(case))
        return ParityResult(final_message="prompt", success=True, extras={"prompt": prompt})
    raise KeyError(f"unknown op: {op}")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_node_scheduler_parity(case: ParityCase) -> None:
    """v2-baseline OrgNodeScheduler contract (P-RC-9 P9.9δ-2a, 4 cases)."""
    v2 = _run_case(case)
    expected = _GOLDEN[case.id]
    actual = dict(v2.to_compare())
    actual["tool_sequence"] = [list(t) for t in actual.get("tool_sequence", [])]
    assert actual == expected, f"v2-baseline drift on {case.id}: {actual} != {expected}"
