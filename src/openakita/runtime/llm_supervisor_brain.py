"""RC-5 route-B prototype: an LLM-driven :class:`SupervisorBrain` (dry-run).

Why this file exists
--------------------
The dual-ledger :class:`~openakita.runtime.supervisor.Supervisor` skeleton
(outer/inner loop, :class:`~openakita.runtime.stall_detector.StallDetector`,
replan, per-turn checkpoint) is complete and correct, but the only brains
that satisfy its protocol are the *scaffold* brains in
``openakita.agent.supervisor_brain`` (``Degenerate`` / ``PassThrough``),
both of which terminate on turn 2 and therefore never drive stall / replan.
The real Magentic-One style orchestration brain was the never-delivered
``P-RC-4`` follow-up (see ``_rc5_biz/rc5_rca_report.md``).

This module is the **route-B prototype** answering the pathfinding question
*"is the skeleton actually ready, and only the brain missing?"*. It is a
deliberately **dry-run** implementation: enough to drive the loop across
multiple turns and trip stall / replan / checkpoint, not a production-grade
orchestrator.

⚠️ Two-protocol trap (RC-5 §2)
------------------------------
There are **two** unrelated protocols named ``SupervisorBrain``:

* ``openakita.runtime.supervisor.SupervisorBrain`` -- the *orchestration*
  protocol the :class:`Supervisor` actually depends on:
  ``extract_facts`` / ``draft_plan`` / ``emit_progress_ledger``.
* ``openakita.agent.brain`` -- a *gateway* protocol (``think_lightweight`` /
  ``get_current_endpoint_info``) used for plain LLM calls. The real
  LLM-backed ``Brain`` implements *that* one and is **not** plug-compatible
  with the Supervisor.

To avoid the trap, this brain implements the *orchestration* protocol and
talks to the LLM through a narrow, injectable :class:`SupervisorLLMClient`
seam (see below). Mock tests inject a fake client; the live phase injects an
adapter over the real gateway ``Brain``. The Supervisor never knows which.

Dry-run simplifications (intentional, RC-5 Phase B)
---------------------------------------------------
* ``next_speaker`` address resolution is *best-effort*: when a node
  directory is supplied we normalise a role-style answer to a concrete
  ``node_id`` via :meth:`LLMSupervisorBrain.resolve_next_speaker`; on any
  ambiguity we leave the model's answer untouched and let the Supervisor's
  existing 10x JSON-retry path own correctness. A complete role/address
  resolver is the next-phase gap (#2).
* The three orchestrator prompts are adapted from AutoGen Magentic-One
  ``_prompts.py`` (see ``_rc5_biz/prototype/prompts/``) but otherwise kept
  short; cheap-model tiering is deferred to the live phase.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.supervisor import DelegationResult, SupervisorBrain

__all__ = [
    "ORCHESTRATOR_TASK_LEDGER_FACTS_PROMPT",
    "ORCHESTRATOR_TASK_LEDGER_PLAN_PROMPT",
    "ORCHESTRATOR_PROGRESS_LEDGER_PROMPT",
    "SupervisorLLMClient",
    "CallableSupervisorLLMClient",
    "NodeDescriptor",
    "LLMSupervisorBrain",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Three-segment orchestrator prompts (adapted from AutoGen Magentic-One)
#
# Source: D:\claw-research\repos\autogen\python\packages\autogen-agentchat\
#   src\autogen_agentchat\teams\_group_chat\_magentic_one\_prompts.py
# Referenced by docs/adr/0004-dual-ledger-supervisor.md (Outer/inner loop).
# The PROGRESS_LEDGER schema is reordered to match OpenAkita's strict
# REQUIRED_PROGRESS_KEYS contract in runtime/ledger.py.
# ---------------------------------------------------------------------------

ORCHESTRATOR_TASK_LEDGER_FACTS_PROMPT = """Below is a user request handled by an organization of AI agent nodes. Before we begin, build a fact sheet.

Request:

{task}

List, under exactly these four headings and nothing else:

    1. GIVEN OR VERIFIED FACTS   -- facts/figures stated in the request itself
    2. FACTS TO LOOK UP          -- and where they might be found
    3. FACTS TO DERIVE           -- via deduction, computation, or tool use
    4. EDUCATED GUESSES          -- hunches and well-reasoned assumptions

DO NOT propose a plan or next steps yet. Output only the fact sheet.
"""


ORCHESTRATOR_TASK_LEDGER_PLAN_PROMPT = """To address the request we have assembled the following organization of nodes:

{team}

Known facts:

{facts}

Devise a short bullet-point plan that delegates work across these nodes.
There is no requirement to involve every node. Keep it concise.
"""


# RC-5 S2 (gap⑤): production convergence-judgement progress-ledger prompt.
#
# Augments the original Magentic-One-style prompt with (1) an
# ``=== ACTUAL OUTPUTS ===`` block carrying the REAL node deliverables (filled
# by S1's ``_render_outputs`` from the supervisor-fed ``recent_outputs``), and
# (2) three explicit convergence Decision rules so a *sighted* brain finishes
# gracefully when the outputs truly satisfy the request, and flags
# no-progress / loop when the nodes spin -- instead of optimistically reporting
# progress=true/satisfied=false forever until the hard turn cap.
#
# The "reasoning, then PURE JSON" wording is kept tight to reduce thinking-mode
# chain-of-thought prefixes that pollute the JSON head and trigger parse
# retries (see ``_prereq_apikey_403.md`` §澄清②). Validated by the gap⑤ spike
# (``_rc5_biz/gap5_spike/gap5_spike_harness.py``).
ORCHESTRATOR_PROGRESS_LEDGER_PROMPT = """Recall the request we are working on:

{task}

The organization of nodes available to address it:

{team}

Plan we are following:

{plan}

Your own past progress assessments (most recent last):

{history}

=== ACTUAL OUTPUTS produced by the nodes so far (most recent last) ===
These are the REAL deliverables each node has returned. Judge progress and
satisfaction from THESE concrete outputs, not from optimism about future work.

{outputs}
=== end of actual outputs ===

Decision rules (follow strictly):
- is_request_satisfied = true ONLY IF the actual outputs above ALREADY fully
  and concretely satisfy every part of the request. If so, we will finish
  immediately and gracefully -- do NOT keep delegating busywork.
- If the request is impossible, self-contradictory, or under-specified such
  that no output can ever satisfy it, set is_progress_being_made = false and
  explain why in the reason; do NOT pretend optimistic progress.
- If the latest outputs add no real improvement over earlier ones (the nodes
  are spinning / repeating without resolving the request), set
  is_progress_being_made = false and/or is_in_loop = true so we can replan or
  stop, instead of looping until a hard turn cap.
- Otherwise, route the single most useful next step. (next_speaker must be one
  of: {names})

Answer with brief reasoning, then output PURE JSON matching this exact schema,
parsable as-is, with NOTHING else after it:

    {{
        "is_request_satisfied":    {{"answer": boolean, "reason": string}},
        "is_progress_being_made":  {{"answer": boolean, "reason": string}},
        "is_in_loop":              {{"answer": boolean, "reason": string}},
        "instruction_or_question": {{"answer": string,  "reason": string}},
        "next_speaker":            {{"answer": string (one of: {names}), "reason": string}}
    }}
"""


# ---------------------------------------------------------------------------
# Injectable LLM seam
# ---------------------------------------------------------------------------


class SupervisorLLMClient(Protocol):
    """The narrow LLM surface :class:`LLMSupervisorBrain` needs.

    A single ``complete`` coroutine. ``role`` lets an implementation route
    cheap calls (``"facts"`` / ``"plan"`` / ``"progress_ledger"``) to a
    cheaper model tier per ADR-0004's "Negative / Accepted Cost" note.

    The mock tests inject a scripted fake; the live phase injects an
    adapter over the real gateway ``Brain`` (see
    :class:`CallableSupervisorLLMClient`). ``cancel_event`` is forwarded so
    an in-flight request can be aborted the instant the supervisor's cancel
    token fires (RC-4 cancel bridge).
    """

    async def complete(
        self,
        *,
        role: str,
        system: str,
        user: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str: ...


@dataclass
class CallableSupervisorLLMClient:
    """Adapt an arbitrary async callable into a :class:`SupervisorLLMClient`.

    The live phase wraps the real gateway ``Brain`` here, e.g.::

        client = CallableSupervisorLLMClient(
            lambda *, role, system, user, cancel_event: brain.think_lightweight(
                system_prompt=system, user_prompt=user, cancel_event=cancel_event,
            )
        )

    Keeping the seam this thin means the prototype carries zero coupling to
    the gateway ``Brain`` protocol (the two-protocol trap stays sealed).
    """

    fn: Callable[..., Awaitable[str]]

    async def complete(
        self,
        *,
        role: str,
        system: str,
        user: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        return await self.fn(
            role=role, system=system, user=user, cancel_event=cancel_event
        )


# ---------------------------------------------------------------------------
# Node directory (gap #4: capability/topology injection)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeDescriptor:
    """One addressable node the brain may route ``next_speaker`` to.

    ``node_id`` is the concrete address the Supervisor's deliver callable
    expects; ``role`` / ``capabilities`` are the human-facing labels the
    LLM reasons over when picking the next speaker.
    """

    node_id: str
    role: str = ""
    capabilities: str = ""

    def render(self) -> str:
        bits = [f"- {self.node_id}"]
        if self.role:
            bits.append(f"（角色：{self.role}）")
        if self.capabilities:
            bits.append(f" 能力：{self.capabilities}")
        return "".join(bits)


# ---------------------------------------------------------------------------
# The brain
# ---------------------------------------------------------------------------


class LLMSupervisorBrain(SupervisorBrain):
    """Dry-run LLM orchestration brain implementing the *orchestration* protocol.

    Args:
        root_node_id: fallback speaker / address when the model omits or
            emits an unresolvable ``next_speaker``.
        client: the injectable LLM seam (fake in tests, real adapter live).
        node_directory: optional capability/topology list injected into the
            facts/plan/progress prompts (gap #4). When supplied it also
            powers best-effort ``next_speaker`` role resolution (gap #2).
        team_label: optional human label for the team block when no
            ``node_directory`` is given.
    """

    def __init__(
        self,
        *,
        root_node_id: str,
        client: SupervisorLLMClient,
        node_directory: Sequence[NodeDescriptor] | None = None,
        team_label: str | None = None,
        feedback_window: int = 6,
    ) -> None:
        if not root_node_id:
            raise ValueError("LLMSupervisorBrain requires a root_node_id")
        self.root_node_id = root_node_id
        self.client = client
        self.node_directory: list[NodeDescriptor] = list(node_directory or [])
        self.team_label = team_label
        # RC-5 S1 (gap⑤): how many most-recent node outputs to render back into
        # the convergence prompt. Bounded so the context stays cheap.
        self._feedback_window = max(1, int(feedback_window))

    # -- prompt context helpers ------------------------------------------

    def _names(self) -> str:
        if self.node_directory:
            return ", ".join(n.node_id for n in self.node_directory)
        return self.root_node_id

    def _team_block(self) -> str:
        if self.node_directory:
            return "\n".join(n.render() for n in self.node_directory)
        if self.team_label:
            return self.team_label
        return f"- {self.root_node_id}（角色：root / 入口节点）"

    @staticmethod
    def _render_history(history: Sequence[ProgressLedger]) -> str:
        if not history:
            return "(尚无进展记录 —— 这是第一个 turn)"
        lines: list[str] = []
        for p in history:
            lines.append(
                f"turn {p.turn_id}: next_speaker={p.next_speaker_name!r} "
                f"satisfied={p.request_satisfied} progress={p.progress_being_made} "
                f"in_loop={p.in_loop} :: {p.instruction[:160]}"
            )
        return "\n".join(lines)

    def _render_outputs(
        self, recent_outputs: Sequence[DelegationResult] | None
    ) -> str:
        """Render the REAL node deliverables for the convergence prompt.

        RC-5 S1 (gap⑤): the supervisor feeds the most-recent
        :class:`DelegationResult` records here so the brain judges
        satisfaction/progress from concrete outputs rather than being blind.
        The ``history`` (this brain's own past ledgers) is aligned to outputs
        by index so we can recover the originating turn / instruction without
        any new field on ``DelegationResult``.
        """
        if not recent_outputs:
            return "(尚无任何节点产出 —— 这是第一个 turn，还没有 delegate 过)"
        recent = list(recent_outputs)[-self._feedback_window :]
        lines: list[str] = []
        for i, r in enumerate(recent, start=1):
            status = "成功" if r.success else "失败"
            lines.append(
                f"[产出 {i}] 节点 {r.speaker!r}（{status}）产出：\n"
                f"    {str(r.message).strip()}"
            )
        return "\n".join(lines)

    # -- protocol methods ------------------------------------------------

    async def extract_facts(
        self,
        *,
        task: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        user = ORCHESTRATOR_TASK_LEDGER_FACTS_PROMPT.format(task=task)
        return await self.client.complete(
            role="facts",
            system="You are the orchestrator of an AI agent organization.",
            user=user,
            cancel_event=cancel_event,
        )

    async def draft_plan(
        self,
        *,
        task: str,
        facts: str,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        user = ORCHESTRATOR_TASK_LEDGER_PLAN_PROMPT.format(
            team=self._team_block(), facts=facts
        )
        return await self.client.complete(
            role="plan",
            system="You are the orchestrator of an AI agent organization.",
            user=user,
            cancel_event=cancel_event,
        )

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        recent_outputs: list[DelegationResult] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        user = ORCHESTRATOR_PROGRESS_LEDGER_PROMPT.format(
            task=task,
            team=self._team_block(),
            plan=plan,
            history=self._render_history(history),
            outputs=self._render_outputs(recent_outputs),
            names=self._names(),
        )
        raw = await self.client.complete(
            role="progress_ledger",
            system="You are the orchestrator of an AI agent organization.",
            user=user,
            cancel_event=cancel_event,
        )
        return self._normalise_next_speaker(raw)

    # -- next_speaker address resolution (gap #2, best-effort) -----------

    def _normalise_next_speaker(self, raw: str) -> str:
        """Best-effort rewrite of ``next_speaker`` from role -> node_id.

        Never raises: any parse / shape issue returns ``raw`` unchanged so
        the Supervisor's own strict parser + 10x retry path stays the single
        source of correctness. Only a clean JSON object whose
        ``next_speaker.answer`` resolves to a different concrete node id is
        rewritten in place.
        """
        if not self.node_directory:
            return raw
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
        if not isinstance(payload, dict):
            return raw
        entry = payload.get("next_speaker")
        if not isinstance(entry, dict) or "answer" not in entry:
            return raw
        original = str(entry.get("answer", ""))
        resolved = self.resolve_next_speaker(
            original, self.node_directory, self.root_node_id
        )
        if resolved == original:
            return raw
        entry["answer"] = resolved
        prior_reason = str(entry.get("reason", ""))
        entry["reason"] = (
            f"{prior_reason} [resolved {original!r} -> {resolved!r}]"
        ).strip()
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def resolve_next_speaker(
        name: str,
        directory: Sequence[NodeDescriptor],
        root_node_id: str,
    ) -> str:
        """Map a model-emitted ``next_speaker`` to a concrete ``node_id``.

        Resolution order: exact node_id match -> exact role match ->
        case-insensitive substring match on role/node_id -> ``root_node_id``
        fallback. The terminal sentinel ``"supervisor"`` is passed through
        untouched (it means "no further delegation"; the Supervisor only
        delegates after a non-DONE verdict anyway).
        """
        if not name or name.strip().lower() == "supervisor":
            return name
        target = name.strip()
        for n in directory:
            if n.node_id == target:
                return n.node_id
        for n in directory:
            if n.role and n.role == target:
                return n.node_id
        low = target.lower()
        for n in directory:
            if (n.role and low in n.role.lower()) or low in n.node_id.lower():
                return n.node_id
        return root_node_id
