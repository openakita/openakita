"""RC-5 sprint S1 live convergence reproduction (PRODUCTION code path).

Unlike the gap⑤ spike (which closed the feedback loop inside a brain
*subclass* ``FeedbackLLMSupervisorBrain`` reading a shared ``delivery_log``),
this harness drives the **productionised** path:

* the stock :class:`LLMSupervisorBrain` (no subclass, no shared log);
* the stock :class:`Supervisor` whose ``_inner_loop`` now records every
  ``DelegationResult`` into ``self.delegation_history`` (S1) and feeds the
  most-recent N back to ``brain.emit_progress_ledger(recent_outputs=...)``;
* the stock S2 convergence prompt (``ORCHESTRATOR_PROGRESS_LEDGER_PROMPT``)
  rendering the fed-back outputs into ``{outputs}``.

The orchestration LLM is pinned to the no-thinking DashScope endpoint added
in ``_endpoint_config_change.md`` via ``LLMClient.switch_model(..,
policy="require")`` -- so this exercises the exact production wiring the
sprint built, NOT a harness shortcut.

Cost control: 2 commands only (normal + contradictory). Each command is a
single ``Supervisor.run`` with a low turn budget.

Run from repo root with the venv python:
    .venv\\Scripts\\python.exe _rc5_biz/sprint_s1/s1_live_convergence.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

# Reuse Q2 prototype instruments + the gateway adapter (NOT the feedback
# subclass -- we use the production brain).
_PROTO_DIR = Path(__file__).resolve().parent.parent / "prototype"
sys.path.insert(0, str(_PROTO_DIR))
_Q2_DIR = Path(__file__).resolve().parent.parent / "q2_live"
sys.path.insert(0, str(_Q2_DIR))
from cost_probe import CostTally, InstrumentedClient, classify_sse_event  # noqa: E402
from q2_harness import GatewayLLMClient, RecordingStreamBus  # noqa: E402

from openakita.llm.client import LLMClient  # noqa: E402
from openakita.runtime.checkpoint import MemoryCheckpointer  # noqa: E402
from openakita.runtime.llm_supervisor_brain import (  # noqa: E402
    LLMSupervisorBrain,
    NodeDescriptor,
)
from openakita.runtime.supervisor import DelegationResult, Supervisor  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
ORCH_ENDPOINT = "dashscope-qwen3.5-plus-nothinking"

_DIRECTORY = [
    NodeDescriptor(node_id="node_root", role="root", capabilities="入口/总协调"),
    NodeDescriptor(node_id="writer", role="copywriter", capabilities="文案撰写"),
    NodeDescriptor(node_id="designer", role="art_director", capabilities="配图/视觉方案"),
    NodeDescriptor(node_id="reviewer", role="qa", capabilities="终稿审校"),
]

# Same representative node outputs as the spike (so a behaviour comparison is
# apples-to-apples), but delivered through the PRODUCTION feedback path.
_GOOD_INTRO = (
    "【静夜 Pro 主动降噪蓝牙耳机·产品介绍】\n"
    "静夜 Pro 是一款专为通勤与办公场景打造的主动降噪蓝牙耳机。它搭载第三代混合"
    "降噪芯片，可消除高达 42dB 的环境噪声，无论地铁轰鸣还是开放工位的嘈杂，都能"
    "为你隔出一片安静。单耳仅 4.8 克的轻量化设计配合人体工学耳柄，长时间佩戴也不"
    "压耳。蓝牙 5.4 双设备无缝切换，手机看剧、电脑开会一键互联，延迟低至 60ms，"
    "音画严丝合缝。续航方面，单次充电可用 8 小时，配合充电盒可达 32 小时，充电 10"
    "分钟即可续航 2 小时。IPX5 级防水让运动与雨天不再有后顾之忧，六麦克风通话降噪"
    "确保每一通电话都清晰如面对面。静夜 Pro，把世界调到静音，把声音留给你真正在乎"
    "的内容。（约 300 字）"
)
_GOOD_REVIEW = (
    "审校结论：终稿合格，可直接交付。文案约 300 字，符合字数要求；卖点（降噪/轻量/"
    "续航/防水/通话）齐全且无夸大违规用语，无错别字与语病，结构清晰。无需进一步修改。"
)


def make_deliver(mode: str) -> Any:
    """Production-style deliver: returns scenario node outputs. NO shared log
    (the supervisor itself now records delegation_history -- that is the point
    being verified)."""
    turn_counter = {"n": 0}

    async def deliver(speaker: str, instruction: str, progress: Any) -> DelegationResult:
        turn_counter["n"] += 1
        t = turn_counter["n"]
        if mode == "normal":
            if speaker == "reviewer" or "审校" in instruction or "review" in instruction.lower():
                msg = _GOOD_REVIEW
            else:
                msg = _GOOD_INTRO
            success = True
        else:  # contradictory
            msg = (
                f"[{speaker}] 无法完成该指令。需求要求文章同时满足『恰好 100 字』与"
                f"『恰好 5000 字』，这两个约束在数学上互斥，无法同时成立。我已尝试折中"
                f"（如 100 字摘要 + 5000 字正文），但任何单篇文章都无法同时『是 100 字』"
                f"又『是 5000 字』。这是需求定义自相矛盾，非执行问题，需澄清取舍后才能推进。"
            )
            success = False
        return DelegationResult(success=success, speaker=speaker, message=f"(第{t}次) {msg}")

    return deliver


async def run_scenario(
    *,
    name: str,
    task: str,
    mode: str,
    shared_llm: LLMClient,
    max_turns: int,
    max_stalls: int = 3,
    max_replans: int = 2,
) -> dict[str, Any]:
    tally = CostTally()
    bus = RecordingStreamBus()
    store = MemoryCheckpointer()
    deliver = make_deliver(mode)

    gateway = GatewayLLMClient(shared_llm)
    client = InstrumentedClient(gateway, tally)
    # PRODUCTION brain -- no FeedbackLLMSupervisorBrain subclass.
    brain = LLMSupervisorBrain(
        root_node_id="node_root",
        client=client,
        node_directory=_DIRECTORY,
    )

    sup = Supervisor(
        command_id=f"s1_{name}",
        org_id="s1_org_rc5_sprint",
        root_node_id="node_root",
        task=task,
        brain=brain,
        deliver=deliver,
        stream=bus,
        checkpointer=store,
        max_stalls=max_stalls,
        max_turns=max_turns,
        max_replans=max_replans,
    )

    t0 = time.monotonic()
    try:
        out = await sup.run()
        outcome = out.outcome.value
        n_turns = out.n_turns
        n_replans = out.n_replans
        final_message = out.final_message
    except Exception as exc:  # noqa: BLE001
        outcome = f"EXCEPTION:{type(exc).__name__}"
        n_turns = sup.stall_detector.n_turns
        n_replans = sup.n_replans
        final_message = str(exc)
    latency_s = round(time.monotonic() - t0, 2)

    for channel, type_, _payload in bus.events:
        classify_sse_event(channel, type_, tally)

    def _ans(p: dict, key: str) -> Any:
        v = p.get(key)
        return v.get("answer") if isinstance(v, dict) else None

    turn_snapshots = []
    for (c, t, p) in bus.events:
        if c == "progress_ledger" and t == "ledger":
            turn_snapshots.append({
                "turn_id": p.get("turn_id"),
                "next_speaker": _ans(p, "next_speaker"),
                "satisfied": _ans(p, "is_request_satisfied"),
                "progress": _ans(p, "is_progress_being_made"),
                "in_loop": _ans(p, "is_in_loop"),
                "reason_satisfied": (
                    p.get("is_request_satisfied", {}).get("reason", "")[:160]
                    if isinstance(p.get("is_request_satisfied"), dict) else ""
                ),
            })

    parse_errors = [
        p for (c, t, p) in bus.events
        if c == "debug" and t == "progress_ledger_parse_error"
    ]

    return {
        "scenario": name,
        "mode": mode,
        "task": task,
        "endpoint": ORCH_ENDPOINT,
        "production_feedback_path": True,
        "outcome": outcome,
        "final_message": final_message[:240],
        "n_turns": n_turns,
        "max_turns_effective": sup.cfg.max_turns,
        "max_turns_requested": max_turns,
        "hit_max_turns": outcome == "out_of_turns",
        "satisfied_terminal": turn_snapshots[-1]["satisfied"] if turn_snapshots else None,
        "n_replans": n_replans,
        "n_stalls_final": sup.stall_detector.n_stalls,
        "delegation_history_len": len(sup.delegation_history),
        "latency_s": latency_s,
        "llm_calls": tally.llm_calls,
        "llm_calls_by_role": dict(tally.llm_calls_by_role),
        "prompt_tokens": tally.prompt_tokens,
        "completion_tokens": tally.completion_tokens,
        "total_tokens": tally.total_tokens,
        "parse_error_retries": len(parse_errors),
        "turn_snapshots": turn_snapshots,
    }


async def main() -> None:
    shared = LLMClient()
    # Pin the orchestration path to the no-thinking endpoint (require policy).
    ok, msg = shared.switch_model(ORCH_ENDPOINT, policy="require", reason="rc5-s1-live")
    print(f">>> pin orchestration endpoint -> {ORCH_ENDPOINT}: ok={ok} ({msg})", flush=True)
    if not ok:
        print(">>> ABORT: endpoint not available", flush=True)
        return

    results: list[dict[str, Any]] = []

    print(">>> [1/2] normal task (good node outputs) ...", flush=True)
    r1 = await run_scenario(
        name="normal_300word_intro",
        task=(
            "为『静夜 Pro 主动降噪蓝牙耳机』写一篇约 300 字的中文产品介绍，"
            "随后做一次终稿审校确认质量合格即可。"
        ),
        mode="normal",
        shared_llm=shared,
        max_turns=6,
    )
    results.append(r1)
    print(
        f"    outcome={r1['outcome']} n_turns={r1['n_turns']}/{r1['max_turns_effective']} "
        f"satisfied={r1['satisfied_terminal']} hit_max={r1['hit_max_turns']} "
        f"calls={r1['llm_calls']} tokens={r1['total_tokens']} retries={r1['parse_error_retries']} "
        f"{r1['latency_s']}s",
        flush=True,
    )
    _dump(results)

    print(">>> [2/2] contradictory task (impossible) ...", flush=True)
    r2 = await run_scenario(
        name="contradictory_100v5000",
        task=(
            "写一篇文章，要求这篇文章同时恰好是 100 字、又恰好是 5000 字。"
            "请推进直到团队对交付完全满意为止。"
        ),
        mode="contradictory",
        shared_llm=shared,
        max_turns=6,
        max_stalls=3,
        max_replans=2,
    )
    results.append(r2)
    print(
        f"    outcome={r2['outcome']} n_turns={r2['n_turns']}/{r2['max_turns_effective']} "
        f"replan={r2['n_replans']} stalls={r2['n_stalls_final']} hit_max={r2['hit_max_turns']} "
        f"calls={r2['llm_calls']} tokens={r2['total_tokens']} retries={r2['parse_error_retries']} "
        f"{r2['latency_s']}s",
        flush=True,
    )
    _dump(results)
    print(">>> DONE. wrote _s1_live_convergence.jsonl", flush=True)


def _dump(results: list[dict[str, Any]]) -> None:
    with (OUT_DIR / "_s1_live_convergence.jsonl").open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
