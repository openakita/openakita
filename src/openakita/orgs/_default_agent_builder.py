"""Default ``AgentBuilderProtocol`` -- minimum viable LLM binding for orgs_v2.

Sprint-2 P0-1 (audit ``_orgs_business_capability_audit_v2.md`` §5 / §8)
shipped the bare LLM binding: every node could finally produce one line of
LLM text instead of bouncing off ``_NullAgentBuilder``. Sprint-3 P0-1
(audit v3 §5.2) plumbed the real entry-node id end-to-end so the executor
stopped seeing ``node_id=None``.

Sprint-4 P0-1 (audit ``_orgs_business_capability_audit_v4.md`` §6.2 /
§8) tackles the remaining "single-LLM cosplay" symptom: even after
Sprint-3 the v15 run showed 35/35 commands had only one unique node id
in their LLM debug files (always ``producer``). The producer LLM was
inventing screenwriter / art-director "voices" inside one call rather
than really handing work off to those nodes.

This module supports structured delegation through ``org_delegate_task``:

* A coordinator may declare up to :data:`MAX_DISPATCH_BLOCKS` assignments
  during one activation. Each assignment carries a stable ``step_id`` and
  ``output_slot`` plus optional ``depends_on`` and media-contract fields.
* The runtime validates the declared DAG, rejects unknown dependencies and
  cycles, and executes every dependency-ready wave concurrently with
  :func:`asyncio.gather`. A dependent wave starts only after its prerequisites
  have completed, with their structured outputs injected into its instruction.
* The callback recurses through the same executor pipeline so each
  child gets its own :class:`_BrainBackedNodeAgent`, its own
  ``agent_run_started`` / ``agent_run_finished`` events, its own
  artefact write (Sprint-4 P0-2), and its own ``context.node_id`` in
  LLM debug.
* Recursion is bounded by :data:`MAX_DISPATCH_DEPTH` so a runaway LLM
  cannot trigger an unbounded fan-out. Depth tracking flows through a
  module-level :class:`contextvars.ContextVar` set by the executor in
  ``activate_and_run``.
* Coordinators at any permitted depth may delegate to their own direct reports;
  the topology guard prevents skipping levels or inventing node ids.
* Legacy ``<dispatch>`` text parsing is disabled by default and is available
  only through ``OPENAKITA_ORG_LEGACY_TEXT_DISPATCH`` for compatibility.

Sprint-4 P0-2 (audit v4 §5.4 / §6.2 #2) -- node artefact persistence -
is implemented at the executor layer (see
:mod:`._runtime_node_artifacts`), not here, because the executor is the
only layer that already owns the post-success bookkeeping (events,
emit, error mapping) and has clean access to the
``get_org_dir`` lookup. The builder stays small and stateless.

The builder is intentionally fail-fast: if the brain provider returns
``None`` (lifespan ordering -- HTTP up before the desktop ``Agent`` is
ready) we raise :class:`BuilderUnavailable`, which the executor catches
and turns into ``agent_run_failed reason=agent_build_failed``. That is
the same observable as the legacy ``_NullAgentBuilder`` path, so
downstream contracts (events.jsonl + SSE shape, ``get_status`` reading)
keep working unchanged.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from openakita.runtime.execution_context import UpstreamContext, current_upstream_context_var

from ._runtime_agent_pipeline import (
    MAX_DISPATCH_BLOCKS,
    MAX_DISPATCH_DEPTH,
    AgentSpec,
    dispatch_depth_var,
)
from ._runtime_delegation import (
    DelegationExecutionResult,
    DelegationExecutionStatus,
    DelegationRequest,
    build_delegate_tool,
    current_delegation_assignment_var,
    current_delegation_media_spec_var,
    current_delegation_output_slot_var,
    current_delegation_requests_var,
    current_delegation_targets_var,
    delegation_key,
    delegation_ledger,
)
from ._runtime_node_tools import (
    NodeToolEmit,
    NodeToolHostProvider,
    resolve_node_tools,
    run_with_tools,
)

__all__ = [
    "MAX_DISPATCH_BLOCKS",
    "MAX_DISPATCH_DEPTH",
    "BuilderUnavailable",
    "DefaultAgentBuilder",
    "DispatchCallback",
    "NodeToolEmit",
    "NodeToolHostProvider",
    "dispatch_depth_var",
    "parse_dispatch_blocks",
]

_LOGGER = logging.getLogger(__name__)

# A short marker prepended to every node system prompt so logs / debug
# dumps clearly attribute the LLM call to the orgs_v2 path (the v13
# audit's L4.1 finding: 0 LLM debug files were tagged with orgs_v2).
_NODE_SYSTEM_PREFIX = "[openakita orgs_v2 node agent]"


# Sprint-4 P0-1 recursion-safety constants and the depth ContextVar
# live in :mod:`._runtime_agent_pipeline` and are re-exported above
# (rationale: import-cycle avoidance with the executor shard; see the
# note next to the constant definitions in the pipeline module). The
# ``MAX_DISPATCH_*`` numbers chosen there cover the typical
# "producer (root) -> mid-tier (screenwriter/art-director) ->
# workbench leaf (wb-hh-*)" three-layer pattern of the
# ``aigc-video-studio`` template. Anything deeper is almost always
# a hallucinated runaway; we'd rather drop the tail than burn token
# budget on a 7-level pyramid that nobody asked for.


# ``<dispatch target="...">...</dispatch>`` -- ``DOTALL`` so multi-line
# child content is captured verbatim. We tolerate either quote style
# and arbitrary whitespace between the tag name and the ``target=``
# attribute so small LLM formatting drift (single vs double quotes,
# leading newline inside the tag) does not silently drop the block.
_DISPATCH_RE = re.compile(
    r"<dispatch\s+target\s*=\s*[\"']([^\"']+)[\"']\s*>(.*?)</dispatch>",
    re.IGNORECASE | re.DOTALL,
)


# Type alias for the dispatch callback the executor wires in. Keeping
# it on the module so tests / docstrings can refer to a single name.
DispatchCallback = Callable[..., Awaitable[DelegationExecutionResult]]


def _callable_accepts_kwarg(fn: Callable[..., Any], name: str) -> bool:
    """Detect whether ``fn`` accepts a keyword argument named ``name``.

    Sprint-13 H1: we plumb ``cancel_event`` into the dispatch callback so
    a user cancel reaches grandchildren. Old test fakes (e.g.
    ``_dispatch_subtask_cb`` in ``test_child_dispatch.py``) were written
    before the kwarg existed and have a closed signature; calling them
    with ``cancel_event=...`` would raise ``TypeError``. We probe the
    signature once per call site and only pass the kwarg when the
    callee actually declares it (or accepts ``**kwargs``). Failures of
    :func:`inspect.signature` (C-implemented callables, slot wrappers)
    default to ``True`` because such callables typically forward via
    ``**kwargs`` themselves and we cannot prove otherwise.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    params = sig.parameters
    if name in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


# 图4 思考过程展示: noise prefixes a model sometimes prepends to its
# reasoning stream. Stripped so the 编排过程 timeline / 思维链 show clean
# Chinese reasoning rather than a bare "thinking:" label or stray tags.
_THINKING_NOISE_PREFIXES = (
    "thinking:",
    "thinking…",
    "thinking...",
    "thinking",
    "<thinking>",
    "</thinking>",
    "reasoning:",
    "let me think",
    "思考：",
    "思考:",
    "思考中",
    "我的思考",
)


def _clean_thinking(thinking: str | None) -> str:
    """Normalise a model reasoning/thinking stream for UI display.

    Strips ``<thinking>`` tags + common noise prefixes and collapses runs of
    blank lines, so the surfaced snippet reads as clean reasoning. Returns ``""``
    for a blank/None channel (the caller then emits nothing). This NEVER touches
    the visible deliverable text — only the dedicated reasoning channel — so the
    artifact / filename / billing paths are unaffected.
    """
    if not thinking:
        return ""
    text = str(thinking).replace("<thinking>", "").replace("</thinking>", "")
    text = text.replace("<think>", "").replace("</think>", "")
    stripped = text.strip()
    low = stripped.lower()
    for pref in _THINKING_NOISE_PREFIXES:
        if low.startswith(pref):
            stripped = stripped[len(pref) :].lstrip(" :：\n\t")
            low = stripped.lower()
    # Collapse 3+ newlines so the snippet stays compact.
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped.strip()


# Type alias for the artefact persistor (Sprint-4 P0-2). The executor
# wires this in too so the builder stays free of filesystem concerns.
ArtifactPersistor = Callable[..., None]


class BuilderUnavailable(RuntimeError):
    """Raised by :class:`DefaultAgentBuilder.build` when the brain provider
    returns ``None`` (lifespan startup race / desktop Agent not yet ready).

    The executor catches this and emits the v1-parity
    ``agent_run_failed reason=agent_build_failed`` event, identical to
    what the legacy ``_NullAgentBuilder`` produced. Naming the exception
    distinctly makes log triage easier.
    """


def _dispatch_instructions(*, is_root: bool) -> str:
    """Return the coordinator system-prompt tutorial for child dispatch.

    ★ Multi-level routing: this tutorial is now given to EVERY node that
    has direct reports (its ``delegates_to`` children), not just the root.
    A middle node is a sub-coordinator: it splits the sub-task its parent
    handed it and dispatches DOWN to its OWN direct reports, then
    integrates their results back up. ``is_root`` only tweaks the framing
    (the root owns the whole user request and is the entry; a middle node
    owns the sub-task delegated to it).

    Kept as a short paragraph so the per-node token budget stays bounded.
    Delegation is a structured tool call so examples and prose cannot be
    mistaken for executable routing instructions.
    """

    who = (
        "You are the ROOT coordinator (入口): you own the whole user "
        "request. Split it and delegate to your DIRECT reports listed "
        "below."
        if is_root
        else "You are a MIDDLE-LEVEL coordinator: your parent handed you a "
        "sub-task. Decide yourself how to split it and delegate to YOUR "
        "OWN direct reports listed below (逐级下派). You have real "
        "autonomy here — do not bounce every decision back up."
    )
    return (
        f"{who} Work like a real team lead, in this order: "
        "(1) PARSE the task into concrete parts; "
        "(2) MATCH each part to the report whose capability best fits it — "
        "read the capability notes (部门/职责) next to each report below and "
        "pick by capability, not by guessing; "
        "(3) DO YOURSELF the part that falls under your OWN role/expertise "
        "(don't reflexively hand everything down — a part you are the right "
        "specialist for, you complete directly, including calling tools / "
        "writing files when that produces the deliverable); "
        "(4) DELEGATE only the parts that genuinely need a different "
        "specialist among your reports; "
        "(5) after their results come back, REVIEW them and INTEGRATE into a "
        "coherent result you hand back UP to your parent (逐级汇报). "
        "To delegate, call org_delegate_task once for each concrete subtask. "
        "Set target to one of YOUR direct reports and put the complete task in instruction. "
        "For every delegation, set output_slot to the intended result slot. Keep the same slot "
        "when retrying one result. If the user explicitly requests multiple results, use one "
        "distinct output_slot per result and set expected_outputs to that total. For media work, "
        "also set segment_id and tool_name. Runtime assignment + output_slot identity suppresses "
        "duplicate generation without suppressing requested variants. The target MUST be one of "
        "YOUR direct reports "
        "listed below — never a node that is not in that list (no skipping "
        "levels, no inventing links). Emit at most "
        f"{MAX_DISPATCH_BLOCKS} delegation calls. "
        "Work in parallel: when several reports' subtasks are independent, "
        "make ALL their org_delegate_task calls in THIS SAME reply — they run "
        "concurrently. For a multi-stage workflow, still declare ALL known steps in this same "
        "reply: assign each a unique step_id and set depends_on to prerequisite step ids. The "
        "runtime runs the DAG and injects prerequisite outputs into dependent instructions. "
        "create_todo is only a user-facing checklist; org_delegate_task step dependencies are the "
        "executable plan. "
        "Right-size the delegation: a small/simple task you can fully handle "
        "yourself should NOT be fanned out just to use the org chart — "
        "over-delegating trivial work only adds hops and latency. "
        "After the structured delegations the orchestrator appends each report's "
        "output to your reply, so your own text should (1) state who you "
        "delegated what and why, and (2) integrate the returned results. "
        "If — and only if — you genuinely cannot decide how to proceed, or "
        "the request falls outside your team's scope, do NOT guess: say so "
        "plainly in your reply so your parent can re-route (逐级上报)."
    )


def _leaf_worker_instructions() -> str:
    """Instruction for a leaf node (no direct reports): do the work itself.

    A node with no ``delegates_to`` children cannot delegate further, so we
    tell it to produce the deliverable directly and never pretend to
    dispatch (the closed-list dispatch tutorial is not even shown to it).
    """

    return (
        "You are a leaf specialist: you have no reports to delegate to. "
        "Do the work yourself and produce the concrete deliverable for the "
        "instruction below, focused on your role. Do NOT call org_delegate_task "
        "or pretend to hand work to other nodes — deeper "
        "coordination is not yours to do. When you finish, your output "
        "flows back UP to the node that delegated to you."
    )


def _available_nodes_block(spec: AgentSpec) -> str:
    """Render the closed list of dispatch targets for the producer prompt.

    Sprint-5 unexpected-finding #1 (audit v5 §4.2 + §5.3): the v16 run
    showed the producer LLM inventing a ``director`` node that did not
    exist in the spec. The ``<dispatch target="...">`` parser tolerated
    it (unknown_target -> skip + warning, see Sprint-4 ``Decision F``)
    but the invention still cost one LLM round and polluted the
    aggregated reply. Listing the *actual* node ids inline -- with their
    role label so the LLM picks the right one -- measurably reduces
    invention without going as far as a structured-output / JSON-mode
    constraint that would also raise the bar for legitimate creative
    coordination text.

    ★ Multi-level routing: ``spec.available_nodes`` is now the node's
    DIRECT reports only (see ``_available_nodes_for``), so this closed list
    is shown to every coordinator (root or middle) and naturally enforces
    "only delegate to your own reports". Leaf nodes have an empty list and
    never see this block.
    """

    if not spec.available_nodes:
        return ""
    lines = [
        "Your DIRECT reports you may delegate to (use the exact id; the "
        "capability notes after each id — 部门/职责 — tell you what that "
        "report is good at, so match sub-tasks to reports by capability):"
    ]
    for node_id, label in spec.available_nodes:
        if label:
            lines.append(f"- {node_id}: {label}")
        else:
            lines.append(f"- {node_id}")
    lines.append(
        "Do NOT invent new node ids. If none of the listed reports fits a "
        "part of the task — or the part is squarely your own specialty — do "
        "that part yourself instead of dispatching."
    )
    return "\n".join(lines)


def _language_consistency_rule() -> str:
    """Force every node reply to match the ORIGINAL user request language.

    Exploratory testing v11 (UI issue #10): Chinese tasks were coming
    back with English deliverables because the English system prompt
    biased the model toward English. This single rule -- appended to
    EVERY node prompt regardless of depth/tools -- pins the output
    language to the user's ORIGINAL request rather than the immediate
    routing instruction. That distinction matters: the supervisor's
    progress-ledger prompt is a strict JSON contract we cannot safely
    add a language bullet to, so the orchestrator sometimes relays an
    English ``instruction_or_question`` even for a Chinese task. The
    original request (and upstream node outputs) are inlined into every
    node's context in the user's language, so anchoring on THAT keeps
    the whole deliverable Chinese even when one routing hop is English.
    """

    return (
        "Language policy (MANDATORY): Detect the natural language of the "
        "ORIGINAL user request / the task content provided in this "
        "conversation, and write your ENTIRE reply in THAT language. If the "
        "original task is in Chinese, respond fully in Chinese -- including "
        "all prose, headings, file contents and summaries -- EVEN IF a "
        "routing or coordination instruction you receive happens to be "
        "phrased in English. Only reply in English when the user's original "
        "request itself is in English. Never switch to English merely because "
        "this system prompt or a relayed instruction is in English."
    )


def _structured_delivery_rule(*, is_root: bool) -> str:
    final_rule = (
        "Set final=true only when this is the integrated delivery to the user."
        if is_root
        else "As a child node, normally set final=false; your parent owns the final user delivery."
    )
    return (
        "Before your final text response, call org_submit_deliverable exactly once. "
        "Report state=complete only when your assigned work is actually ready; otherwise use "
        "in_progress, blocked, or failed. Declare every real output in artifacts with its kind, "
        "status, asset_ids/task_ids/paths when available. Suggested paths, quoted examples, and "
        "future outputs must not be declared as ready artifacts. "
        "For media delivery, trust the runtime artifact ledger and its validation result. "
        "Do not run shell commands to copy, rename, hash, or repeatedly probe a file in an "
        "attempt to make an unregistered asset pass; report it as blocked instead. "
        f"{final_rule} The runtime decides completion from this structured manifest, not from prose."
    )


def _tool_use_encouragement() -> str:
    """Sprint-7 P0-B node-level tool-use encouragement.

    Audit ``_orgs_business_capability_audit_v7.md`` §1.1 + §5 finding 2:
    8 v18 R.D4 cases explicitly asked the dispatched node to call a
    specific tool (``write_file`` / ``read_file`` / ``web_search`` /
    ``list_dir`` / ``run_shell`` / ``web_fetch``), but only 3/8 LLM
    turns actually emitted a ``tool_use`` block -- the other 5 replied
    with plain text refusing or describing what they would do. The
    Sprint-6 system prompt only said "Reply directly to the user
    instruction below" which gives the LLM no reason to prefer the
    available tools over chat-style prose.

    This block is appended **only when the resolved node has at least
    one available tool**: we don't want the encouragement to hallucinate
    tools that the node's external_tools whitelist + plugin manifest
    did not actually expose. The phrasing is deliberately conservative
    (``SHOULD use`` not ``MUST use``) so the LLM still has the latitude
    to reply with text when no listed tool applies; the goal is to
    flip the default from "narrate then maybe call" to "call when a
    listed tool clearly fits".

    The "match by intent, not by exact wording" line addresses the v18
    observation that some Chinese prompts named the tool with a verb
    ("调用 list_dir") that the LLM treated as a label rather than an
    instruction.
    """

    return (
        "Tool-use policy: You have access to the tools listed below. "
        "When the user's request can be satisfied by invoking one of "
        "these tools (e.g. write/read a file, list a directory, run a "
        "shell command, search/fetch the web, generate or edit an image "
        "or video, query plugin functions, etc.), you SHOULD emit a "
        "`tool_use` block and call the tool instead of replying with "
        "plain text describing what you would do. Match the user's "
        "intent against the available tools by purpose, not by exact "
        "wording. Do not invent tool names that are not in the list; "
        "if none of the listed tools fits the request, reply with text "
        "explaining why."
    )


def _tool_quality_guidance() -> str:
    """Focused tool-use guidance to raise relevance + reliability (v22 P1.2).

    Exploratory testing surfaced three recurring quality issues that waste
    rounds and cause node failures, all of which the model can largely avoid
    with explicit guidance (no engine change needed):

    * Web search / browse wandered onto irrelevant pages (music MVs, unrelated
      entertainment) instead of staying on the task topic.
    * ``write_file`` of a very large document was truncated when the whole body
      was stuffed into one tool-call argument (JSON arg size), failing the call.
    * A node read stale/off-topic material and drifted away from the CURRENT
      task theme (defense-in-depth on top of the per-command workspace sandbox).

    Appended only alongside :func:`_tool_use_encouragement` (i.e. when the node
    actually has tools), so zero-tool personas keep their lean prompt.
    """

    return (
        "工具使用质量要求："
        "1) 检索/浏览：查询词要紧扣【本次任务主题与关键词】，先看标题与摘要、"
        "只打开明显高相关的少数结果，跳过明显无关内容（如音乐 MV、无关娱乐、"
        "广告页），不要逐条点击；一次检索不理想就换更精确的关键词，而不是反复"
        "翻无关页面。"
        "2) 写大文档：当正文很长时分成多段、分多次写入落盘（先写主体再分段补充），"
        "不要把超长正文一次性塞进单个工具参数，以免内容被截断或调用失败；若写入"
        "失败，缩小单次内容后立即重试，不要因此放弃任务。"
        "3) 主题锚定：始终以【本次指令的主题】为准；若读到的资料与当前主题不一致"
        "（例如另一个题材的旧报告），以本次指令主题为准，绝不要被无关或过期资料带偏。"
        "4) 够用即止、必须成文：检索/分析是手段不是目的。一旦已能回答任务的核心"
        "问题（关键事实/数据已掌握个大概），就【立即停止继续检索】，转入成文阶段，"
        "把完整结论写出来（需要落盘交付的用 write_file/append_file 写成文件）。"
        "不要无限检索、反复换词查同一件事而迟迟不产出；个别次要数据查不到时，"
        "在产出中标注‘该项暂未联网核实’即可，但主体成果必须完整成文，"
        "绝不能跑了很多轮检索却交付空内容或一句‘正在整理’。"
    )


# Markers that identify a cloud-model CONTENT-MODERATION rejection (vs a
# transient network/quota error). Exploratory v23: data-analyst does the most
# web_search calls and accumulates 同人/H漫-adjacent titles that, even after the
# retrieval sanitizer strips the overtly explicit lines, can still trip
# dashscope deepseek-r1's safety审核 (HTTP 400 data_inspection_failed) -> "All
# endpoints failed" -> the node hard-failed (task phase=partial). A moderation
# rejection will recur on retry with the same context, so we DEGRADE the node
# to a best-effort note instead of failing the whole node -- the org keeps
# converging and the node contributes a structured "retrieval限制" deliverable.
_CONTENT_MODERATION_MARKERS: tuple[str, ...] = (
    "data_inspection_failed",
    "内容安全审核",
    "content safety",
    "content_filter",
    "content_policy",
    "risk_control",
    "data_inspection",
)


def _is_content_moderation_error(exc: BaseException) -> bool:
    """True when an LLM failure is a content-moderation rejection."""

    text = str(exc).lower()
    return any(marker.lower() in text for marker in _CONTENT_MODERATION_MARKERS)


def _moderation_degraded_note(spec: AgentSpec) -> str:
    """A structured, valid deliverable used when a node degrades gracefully."""

    return (
        f"## 自动检索受限说明（节点 {spec.node_id}）\n\n"
        "本节点在执行联网检索后，部分网络检索结果触发了云端模型的内容安全审核"
        "（data_inspection_failed），无法基于这些原始结果继续自动生成完整内容。\n\n"
        "**已做处理**：\n"
        "- 检索类工具结果已自动过滤明显的成人/无关内容；\n"
        "- 本节点改为提交降级结论而非整体失败，避免阻塞编排，下游与主编可照常汇总。\n\n"
        "**建议**：如需本部分数据，请用更精确、去口语化的检索词重试，或由人工补充"
        "权威平台（B 站 / 抖音 / 微博官方）数据后回灌本节点。"
    )


def _persona_system_prompt(spec: AgentSpec, *, depth: int = 0, has_tools: bool = False) -> str:
    """Compose the per-node system prompt from the resolved spec.

    Kept deliberately small (< 1 KB on a typical node) so single-shot
    calls don't blow the per-node token budget. The Sprint-4 dispatch
    tutorial is only spliced in at the entry level (``depth == 0``) --
    children get the classic Sprint-2 "stay in your lane" instruction,
    so even if a child's LLM decided to emit a ``<dispatch>`` tag the
    gate would skip it AND the prompt did not teach it the syntax in
    the first place.

    Sprint-5 P0-1 / unexpected-finding #1 splices the closed list of
    available child node ids in at depth 0 (after the dispatch
    tutorial) so the producer LLM dispatches to real targets only.

    Sprint-7 P0-B (audit v7 §1.1 + §5 finding 2): when the node has
    at least one resolved tool, append :func:`_tool_use_encouragement`
    so the LLM treats the tool surface as the default action path
    instead of narrating around it. The flag is opt-in (defaults to
    ``False``) so unit tests / parity gates that build the prompt
    without a tool-host context keep the byte-for-byte Sprint-5 shape.
    """

    persona = (spec.persona or "").strip()
    role = (spec.role or "worker").strip()
    parts: list[str] = [
        _NODE_SYSTEM_PREFIX,
        f"You are running as node `{spec.node_id}` (role: {role}) "
        f"inside organisation `{spec.org_id}`.",
    ]
    if persona:
        parts.append(f"Persona: {persona}.")
    # ★ Multi-level routing: a node is a coordinator iff it actually has
    # direct reports (``available_nodes`` is now its delegates_to children,
    # see ``_available_nodes_for``). Coordinators — at ANY depth, not just
    # the root — get the dispatch tutorial + their closed child list so a
    # middle node (策划编辑) delegates DOWN to its own reports (文案写手)
    # instead of being told "stay in your lane". Leaf nodes do the work.
    has_children = bool(spec.available_nodes)
    if has_children:
        parts.append(_dispatch_instructions(is_root=(depth == 0)))
        available_block = _available_nodes_block(spec)
        if available_block:
            parts.append(available_block)
    else:
        parts.append(_leaf_worker_instructions())
    if has_tools:
        parts.append(_tool_use_encouragement())
        parts.append(_tool_quality_guidance())
    parts.append(_structured_delivery_rule(is_root=(depth == 0)))
    parts.append(_language_consistency_rule())
    return "\n".join(parts)


def _decode_review_payload(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    payload: Any = None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        decoder = json.JSONDecoder()
        offset = 0
        while True:
            start = raw.find("{", offset)
            if start < 0:
                break
            try:
                payload, _ = decoder.raw_decode(raw[start:])
                break
            except ValueError:
                offset = start + 1
    return payload if isinstance(payload, dict) else None


def _parse_review_verdict(text: str) -> tuple[bool, str]:
    """Parse the review JSON contract without interpreting prose tokens."""

    payload = _decode_review_payload(text)
    if payload is None:
        return True, "审阅未返回结构化 JSON，默认采纳。"
    decision = payload.get("decision")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = "未给出具体理由。"
    else:
        reason = reason.strip()[:300]
    if decision == "rework":
        return False, reason
    if decision == "accept":
        return True, reason
    return True, "审阅 JSON 的 decision 无效，默认采纳。"


def _extract_text_from_response(resp: Any) -> str:
    """Pull a plain-text reply out of the Anthropic-shaped ``Message``.

    Mirrors the loose duck-type the desktop ``Agent`` uses elsewhere:
    we walk ``content`` blocks looking for ``.text``; if nothing
    surfaces we fall back to ``str(resp)`` so the executor still sees a
    non-empty output rather than ``None`` (the ``_invoke_agent``
    contract).
    """

    content = getattr(resp, "content", None)
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                chunks.append(text)
                continue
            block_type = getattr(block, "type", "")
            if block_type == "text":
                value = getattr(block, "value", None)
                if isinstance(value, str):
                    chunks.append(value)
        if chunks:
            return "\n".join(chunks).strip()
    if isinstance(content, str):
        return content.strip()
    return str(resp).strip()


def parse_dispatch_blocks(text: str) -> list[tuple[str, str]]:
    """Extract ``(target_node_id, child_content)`` pairs from an LLM reply.

    Returns at most :data:`MAX_DISPATCH_BLOCKS` pairs in the order they
    appear (LLM-ordered, not alphabetised: ordering carries intent
    when the model says "first ask screenwriter, then art-director").
    Target / content are stripped of leading and trailing whitespace
    because regex captures preserve newlines around the tags. Empty
    targets are filtered out so a malformed ``<dispatch target="">`` is
    skipped instead of being forwarded to a non-existent node.
    """

    if not isinstance(text, str) or "<dispatch" not in text.lower():
        return []
    pairs: list[tuple[str, str]] = []
    for match in _DISPATCH_RE.finditer(text):
        target = (match.group(1) or "").strip()
        content = (match.group(2) or "").strip()
        if not target:
            continue
        pairs.append((target, content))
        if len(pairs) >= MAX_DISPATCH_BLOCKS:
            break
    return pairs


def _strip_dispatch_blocks(text: str) -> str:
    """Replace each ``<dispatch>...</dispatch>`` block with a short marker.

    The original LLM text is preserved verbatim around the dispatch
    blocks so the parent's "coordination commentary" still surfaces to
    the user. Pre-fix attempts to keep the raw blocks made the
    aggregated output illegible (the user saw the XML on screen and
    then the same child reply repeated underneath); the marker makes
    the flow obvious ("here producer asked X, here is X's reply").
    """

    if not isinstance(text, str) or "<dispatch" not in text.lower():
        return text

    def _sub(match: re.Match[str]) -> str:
        target = (match.group(1) or "").strip() or "?"
        return f"[dispatched to {target}]"

    return _DISPATCH_RE.sub(_sub, text)


def _aggregate_with_children(
    parent_text: str,
    children: list[tuple[str, str]],
) -> str:
    """Combine the parent's stripped text with serial child outputs.

    The aggregation format is intentionally markdown-light so it shows
    up readably in chat UIs that escape HTML but render newlines. Each
    child is fenced by its node id so the user can tell which voice is
    speaking even when several children replied. Empty child outputs
    are kept (with an explicit ``(no output)`` placeholder) because the
    absence of output is itself signal -- pre-fix we silently dropped
    empties and the user could not tell whether the child failed or
    just had nothing to say.
    """

    parent_stripped = _strip_dispatch_blocks(parent_text).strip()
    sections: list[str] = []
    if parent_stripped:
        sections.append(parent_stripped)
    for target, output in children:
        body = (output or "").strip() or "(no output)"
        sections.append(f"[from node `{target}`]\n{body}")
    return "\n\n".join(sections)


class _BrainBackedNodeAgent:
    """Single-shot LLM agent for one orgs_v2 node.

    Implements the
    ``_runtime_agent_pipeline_executor._AgentRunCallable`` Protocol
    (``async run(content) -> Any``). The executor handles the rest of
    the v1-parity event lifecycle.

    Sprint-4 P0-1: when an injected ``dispatch_callback`` is wired and
    the LLM emits ``<dispatch target="...">...</dispatch>`` blocks,
    :meth:`run` calls the callback once per block (serially), then
    aggregates the children outputs into the returned text. Recursion
    depth is controlled by :data:`dispatch_depth_var` (set by the
    executor) and capped at :data:`MAX_DISPATCH_DEPTH`.

    Sprint-5 P0-1: when the resolved :class:`AgentSpec` carries an
    ``external_tools`` whitelist that maps to at least one known
    handler in :data:`openakita.tools.handlers.default_handler_registry`,
    :meth:`run` swaps the empty Sprint-4 ``tools=[]`` brain call for a
    real one-round tool-use loop (see
    :func:`._runtime_node_tools.run_with_tools`). The dispatch parser
    still runs after the loop so a node can both call a tool *and*
    dispatch to siblings in the same turn (the LLM's text after the
    tool round may contain ``<dispatch>`` blocks too).
    """

    __slots__ = (
        "_spec",
        "_brain",
        "_dispatch_callback",
        "_event_emitter",
        "_tool_host_provider",
    )

    def __init__(
        self,
        spec: AgentSpec,
        brain: Any,
        *,
        dispatch_callback: DispatchCallback | None = None,
        event_emitter: NodeToolEmit | None = None,
        tool_host_provider: NodeToolHostProvider | None = None,
    ) -> None:
        self._spec = spec
        self._brain = brain
        self._dispatch_callback = dispatch_callback
        self._event_emitter = event_emitter
        self._tool_host_provider = tool_host_provider

    async def run(
        self,
        content: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        # Sprint-13 H1 (RC-4 §6): ``cancel_event`` is the asyncio event
        # the supervisor wires from its ``cancel_token``. We forward it
        # straight to ``brain.messages_create_async`` (via ``run_with_tools``
        # for the tool-call path) so :meth:`LLMClient._race_with_cancel`
        # can abort the in-flight ``httpx`` request the instant a user
        # cancel fires. Child dispatches also receive the same event so
        # the cancel propagates through ``<dispatch>`` recursion without
        # leaving long-running grandchildren stranded.
        text = content if isinstance(content, str) else str(content or "")
        if not text.strip():
            # Empty content shouldn't land here (command_service rejects
            # blank submits) but be defensive: a noop reply keeps the
            # executor's "ok" path reachable.
            return ""
        depth = max(0, int(dispatch_depth_var.get(0)))
        # Sprint-6 P0-1 / P0-3: resolve the node-bound :class:`NodeToolHost`
        # via the lazy provider. When the desktop Agent has finished wiring
        # the host gives us access to the populated handler registry +
        # plugin tool catalog. When it has not we keep the Sprint-5
        # fallback path (empty registry -> failed events), which the
        # integration tests document explicitly.
        tool_host = None
        if self._tool_host_provider is not None:
            try:
                tool_host = self._tool_host_provider()
            except Exception:  # noqa: BLE001 -- provider must not crash run
                tool_host = None
        # Sprint-5 P0-1 (extended Sprint-6 P0-3): resolve the per-node
        # tools whitelist *before* we tag the trace context so the LLM
        # debug dump can carry an accurate ``tools_count``. The host
        # lookup includes plugin tools (``hh_*``) so workbench nodes
        # (``wb-hh-*``) finally see their declared whitelist instead
        # of having it silently dropped (Sprint-5 §3 limitation).
        tool_defs = resolve_node_tools(
            external_tools=self._spec.external_tools,
            enable_file_tools=self._spec.enable_file_tools,
            tool_host=tool_host,
        )
        if self._spec.available_nodes:
            tool_defs.append(build_delegate_tool(self._spec.available_nodes))
        from openakita.runtime.execution_context import (
            ExecutionPhase,
            current_execution_phase_var,
        )

        execution_phase = current_execution_phase_var.get()
        upstream_context = current_upstream_context_var.get()
        declarative_planning_mode = bool(self._spec.available_nodes) and (
            execution_phase is ExecutionPhase.PLANNING
            or bool(upstream_context and upstream_context.is_present)
        )
        if declarative_planning_mode:
            tool_defs = [
                tool
                for tool in tool_defs
                if tool.get("name") in {"org_delegate_task", "org_submit_deliverable"}
            ]
        finalization_mode = execution_phase is ExecutionPhase.FINALIZATION
        if finalization_mode:
            tool_defs = [tool for tool in tool_defs if tool.get("name") == "org_submit_deliverable"]
        # Tag the brain's debug dump with the node identity + tool
        # count so the v13 audit's "0 orgs_v2 LLM files" finding stays
        # verifiable AND the v17 audit can confirm tools_count > 0 on
        # workbench dispatch.
        set_trace = getattr(self._brain, "set_trace_context", None)
        if callable(set_trace):
            try:
                set_trace(
                    {
                        "org_id": self._spec.org_id,
                        "node_id": self._spec.node_id,
                        "caller": "orgs_v2_node_agent",
                        "tools_count": str(len(tool_defs)),
                    }
                )
            except Exception:  # noqa: BLE001 -- trace tagging is best-effort
                pass
        system_prompt = _persona_system_prompt(self._spec, depth=depth, has_tools=bool(tool_defs))

        # Sprint-5 P0-1: branch on whether the node has any resolved
        # tools. Zero-tool nodes still use the Sprint-4 single-shot
        # call (no risk of an unintended provider feature flip when
        # ``tools`` is an empty list vs absent); >0-tool nodes go
        # through the one-round tool-use loop helper.
        from ._runtime_agent_pipeline import current_command_id_var

        command_id_for_events = current_command_id_var.get("") or None
        delegation_requests: list[DelegationRequest] = []
        targets_token = current_delegation_targets_var.set(
            frozenset(node_id for node_id, _label in self._spec.available_nodes)
        )
        requests_token = current_delegation_requests_var.set(delegation_requests)
        try:
            parent_text = await self._produce_text(
                tool_defs=tool_defs,
                system_prompt=system_prompt,
                text=text,
                command_id_for_events=command_id_for_events,
                tool_host=tool_host,
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            for request in delegation_requests:
                if command_id_for_events:
                    delegation_ledger.finish(
                        self._spec.org_id,
                        command_id_for_events,
                        delegation_key(request),
                        success=False,
                    )
            raise
        except Exception as exc:  # noqa: BLE001 -- only DEGRADE on moderation
            for request in delegation_requests:
                if command_id_for_events:
                    delegation_ledger.finish(
                        self._spec.org_id,
                        command_id_for_events,
                        delegation_key(request),
                        success=False,
                    )
            if _is_content_moderation_error(exc):
                _LOGGER.warning(
                    "orgs_v2 node %s degraded after content-moderation rejection "
                    "(not a hard failure): %s",
                    self._spec.node_id,
                    exc,
                )
                return _moderation_degraded_note(self._spec)
            raise
        finally:
            current_delegation_requests_var.reset(requests_token)
            current_delegation_targets_var.reset(targets_token)
        return await self._maybe_dispatch(
            parent_text=parent_text,
            depth=depth,
            cancel_event=cancel_event,
            structured_requests=delegation_requests,
            command_id=command_id_for_events,
        )

    async def _produce_text(
        self,
        *,
        tool_defs: list[dict[str, Any]],
        system_prompt: str,
        text: str,
        command_id_for_events: str | None,
        tool_host: Any,
        cancel_event: asyncio.Event | None,
    ) -> str:
        """Run the brain (tool-loop or no-tools) and return the node's text."""
        if tool_defs:
            response, _rounds = await run_with_tools(
                brain=self._brain,
                system_prompt=system_prompt,
                user_content=text,
                tools=tool_defs,
                org_id=self._spec.org_id,
                node_id=self._spec.node_id,
                command_id=command_id_for_events,
                workspace_dir=self._spec.workspace_dir,
                emit=self._event_emitter,
                tool_host=tool_host,
                cancel_event=cancel_event,
            )
            return _extract_text_from_response(response)
        else:
            # No-tools (writer/leaf) path. Stream the long-form reply token by
            # token into the 编排过程 timeline (``node_run_delta`` events) so the
            # executing entry rolls characters live instead of appearing only
            # when the node finishes. Streaming is best-effort with a hard
            # fallback: any failure (provider can't stream, empty text, error)
            # transparently drops back to the original non-streaming call so
            # billing / failover / debug-dump behaviour is never weaker than
            # before.
            streamed_text, streamed_ok = await self._run_no_tools_streaming(
                system_prompt=system_prompt,
                text=text,
                command_id=command_id_for_events,
                cancel_event=cancel_event,
            )
            if streamed_ok:
                return streamed_text
            response = await self._brain.messages_create_async(
                messages=[{"role": "user", "content": text}],
                system=system_prompt,
                tools=[],
                cancel_event=cancel_event,
            )
            return _extract_text_from_response(response)

    async def _maybe_dispatch(
        self,
        *,
        parent_text: str,
        depth: int,
        cancel_event: asyncio.Event | None,
        structured_requests: list[DelegationRequest],
        command_id: str | None,
    ) -> str:
        # Sprint-4 P0-1: parse + recurse on child dispatch blocks.
        # Skip if the dispatch callback is not wired (unit tests /
        # bare-builder users get exactly the Sprint-3 behaviour) or
        # the current depth is at the cap (recursion would exceed
        # MAX_DISPATCH_DEPTH).
        if self._dispatch_callback is None or depth >= MAX_DISPATCH_DEPTH - 1:
            return parent_text
        structured = bool(structured_requests)
        blocks = [(request.target, request.instruction) for request in structured_requests]
        legacy_enabled = os.environ.get("OPENAKITA_ORG_LEGACY_TEXT_DISPATCH", "0") in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not blocks and legacy_enabled:
            blocks = parse_dispatch_blocks(parent_text)
        if not blocks:
            return parent_text

        dispatch_accepts_cancel = _callable_accepts_kwarg(self._dispatch_callback, "cancel_event")
        dispatch_accepts_assignment = _callable_accepts_kwarg(
            self._dispatch_callback, "assignment_id"
        )
        dispatch_accepts_output_slot = _callable_accepts_kwarg(
            self._dispatch_callback, "output_slot"
        )
        dispatch_accepts_upstream_context = _callable_accepts_kwarg(
            self._dispatch_callback, "upstream_context"
        )

        async def _emit_plan_event(name: str, payload: dict[str, Any]) -> None:
            if self._event_emitter is None:
                return
            emitted = self._event_emitter(
                name,
                {
                    "org_id": self._spec.org_id,
                    "node_id": self._spec.node_id,
                    "command_id": command_id,
                    **payload,
                },
            )
            if inspect.isawaitable(emitted):
                await emitted

        def _plan_error() -> str | None:
            if not structured:
                return None
            step_ids = [request.step_id for request in structured_requests]
            if len(set(step_ids)) != len(step_ids):
                return "step_id values must be unique within one delegation plan"
            known = set(step_ids)
            for request in structured_requests:
                unknown = set(request.depends_on) - known
                if unknown:
                    return f"step {request.step_id!r} depends on unknown steps: " + ", ".join(
                        sorted(unknown)
                    )
            resolved: set[str] = set()
            remaining = set(step_ids)
            while remaining:
                ready = {
                    request.step_id
                    for request in structured_requests
                    if request.step_id in remaining and set(request.depends_on) <= resolved
                }
                if not ready:
                    return "delegation plan contains a dependency cycle"
                resolved.update(ready)
                remaining.difference_update(ready)
            media_contracts: dict[tuple[str, str], tuple[str, str, int, int]] = {}
            for request in structured_requests:
                spec = request.media_spec
                if spec is None:
                    continue
                contract_key = (spec.kind, spec.output_group)
                dimensions = (
                    spec.aspect_ratio,
                    spec.resolution,
                    spec.width,
                    spec.height,
                )
                previous = media_contracts.setdefault(contract_key, dimensions)
                if previous != dimensions:
                    return (
                        "media_spec mismatch for output group "
                        f"{spec.output_group!r}: {previous!r} != {dimensions!r}"
                    )
            return None

        plan_error = _plan_error()
        if plan_error is not None:
            for request in structured_requests:
                if command_id and not request.reuse_completed:
                    delegation_ledger.finish(
                        self._spec.org_id,
                        command_id,
                        delegation_key(request),
                        success=False,
                    )
            await _emit_plan_event("delegation_plan_invalid", {"reason": plan_error})
            return f"{parent_text}\n\n[delegation plan invalid: {plan_error}]"
        if structured:
            await _emit_plan_event(
                "delegation_plan_declared",
                {
                    "steps": [
                        {
                            "step_id": request.step_id,
                            "target": request.target,
                            "depends_on": list(request.depends_on),
                            "output_slot": request.output_slot,
                            "assignment_id": delegation_key(request),
                            "media_spec": (
                                request.media_spec.to_dict() if request.media_spec else None
                            ),
                        }
                        for request in structured_requests
                    ]
                },
            )

        async def _run_one(
            index: int,
            child_target: str,
            child_content: str,
            *,
            upstream_context: UpstreamContext | None = None,
        ) -> DelegationExecutionResult:
            request = structured_requests[index] if structured else None
            try:
                if request is not None and request.reuse_completed and command_id:
                    from ._runtime_delivery_manifest import delivery_manifest_ledger

                    manifest = delivery_manifest_ledger.latest(
                        self._spec.org_id,
                        command_id,
                        child_target,
                        assignment_id=delegation_key(request),
                    )
                    if manifest is not None and manifest.state == "complete":
                        return DelegationExecutionResult.completed(
                            f"[reused completed assignment `{delegation_key(request)}`]\n"
                            f"{manifest.summary}\n"
                            f"[structured manifest]\n{json.dumps(manifest.to_dict(), ensure_ascii=False)}",
                            delivery_manifest=manifest.to_dict(),
                        )
                # Sprint-13 H1: forward ``cancel_event`` into child
                # dispatch so a user cancel terminates grandchildren
                # without waiting for the parent to finish its outer
                # await frame. Old test callbacks (audit
                # ``tests/runtime/orgs/test_child_dispatch.py``) have a
                # closed signature without ``cancel_event``; the probe
                # above keeps them working.
                dispatch_kwargs: dict[str, Any] = {
                    "org_id": self._spec.org_id,
                    "parent_node_id": self._spec.node_id,
                    "child_node_id": child_target,
                    "child_content": child_content,
                }
                if dispatch_accepts_cancel:
                    dispatch_kwargs["cancel_event"] = cancel_event
                if request is not None and dispatch_accepts_assignment:
                    dispatch_kwargs["assignment_id"] = delegation_key(request)
                if request is not None and dispatch_accepts_output_slot:
                    dispatch_kwargs["output_slot"] = request.output_slot
                if upstream_context is not None and dispatch_accepts_upstream_context:
                    dispatch_kwargs["upstream_context"] = upstream_context
                media_token = current_delegation_media_spec_var.set(
                    request.media_spec if request is not None else None
                )
                try:
                    raw_result = await self._dispatch_callback(**dispatch_kwargs)
                finally:
                    current_delegation_media_spec_var.reset(media_token)
                result = (
                    raw_result
                    if isinstance(raw_result, DelegationExecutionResult)
                    else DelegationExecutionResult.completed(str(raw_result or ""))
                )
                if request is not None and command_id:
                    delegation_ledger.finish(
                        self._spec.org_id,
                        command_id,
                        delegation_key(request),
                        success=result.succeeded,
                    )
                return result
            except asyncio.CancelledError:
                # A user cancel must unwind the whole tree -- re-raise so
                # ``gather`` cancels the surviving siblings too (Sprint-3
                # P0-2 cancel pipeline stays intact under fan-out).
                if request is not None and command_id:
                    delegation_ledger.finish(
                        self._spec.org_id,
                        command_id,
                        delegation_key(request),
                        success=False,
                    )
                raise
            except Exception as exc:  # noqa: BLE001 -- one child failure
                # must not poison siblings or the parent's reply. The
                # executor inside the callback already emitted
                # ``agent_run_failed`` / persisted artefacts for the
                # surviving children; here we surface the failure
                # textually so the aggregated reply still shows the
                # parent "tried" all branches.
                _LOGGER.warning(
                    "child dispatch failed (parent=%s target=%s): %s",
                    self._spec.node_id,
                    child_target,
                    exc,
                )
                if request is not None and command_id:
                    delegation_ledger.finish(
                        self._spec.org_id,
                        command_id,
                        delegation_key(request),
                        success=False,
                    )
                return DelegationExecutionResult.failed(
                    reason_code="child_dispatch_raised",
                    reason=str(exc),
                )

        def _output_succeeded(result: DelegationExecutionResult) -> bool:
            return result.succeeded

        # UI issue #9: fan the sibling dispatches out **concurrently** via
        # ``asyncio.gather`` instead of awaiting them one-by-one. This is the
        # root of the "一次只一个节点、一跳一跳" complaint -- e.g. the editor-in-chief
        # delegating to writer-a / writer-b / visual / seo now runs them in
        # parallel rather than serially. Convergence is preserved (we still
        # await every child before aggregating) and ``gather`` keeps results in
        # the original dispatch order, so ``_aggregate_with_children`` produces
        # byte-identical aggregation to the old serial path. The block count is
        # already bounded by ``MAX_DISPATCH_BLOCKS`` so the fan-out can't run
        # away. Per-org isolation is unchanged (each child still routes through
        # its own executor + node agent under the same org).
        if structured:
            results = [
                DelegationExecutionResult(
                    status=DelegationExecutionStatus.SKIPPED,
                    reason_code="not_started",
                )
                for _request in structured_requests
            ]
            index_by_step = {
                request.step_id: index for index, request in enumerate(structured_requests)
            }
            completed_steps: set[str] = set()
            remaining_steps = set(index_by_step)
            while remaining_steps:
                ready_steps = [
                    request.step_id
                    for request in structured_requests
                    if request.step_id in remaining_steps
                    and set(request.depends_on) <= completed_steps
                ]

                async def _run_ready(
                    step_id: str,
                ) -> tuple[int, DelegationExecutionResult]:
                    index = index_by_step[step_id]
                    request = structured_requests[index]
                    failed_dependencies = [
                        dependency
                        for dependency in request.depends_on
                        if not _output_succeeded(results[index_by_step[dependency]])
                    ]
                    if failed_dependencies:
                        if command_id and not request.reuse_completed:
                            delegation_ledger.finish(
                                self._spec.org_id,
                                command_id,
                                delegation_key(request),
                                success=False,
                            )
                        await _emit_plan_event(
                            "delegation_dependency_blocked",
                            {
                                "step_id": step_id,
                                "depends_on": list(request.depends_on),
                                "failed_dependencies": failed_dependencies,
                            },
                        )
                        return index, DelegationExecutionResult(
                            status=DelegationExecutionStatus.BLOCKED,
                            reason_code="dependency_failed",
                            reason=(
                                f"step {step_id} was not started because "
                                f"{', '.join(failed_dependencies)} failed"
                            ),
                        )
                    upstream_context = UpstreamContext()
                    if request.depends_on:
                        dependencies: list[dict[str, Any]] = []
                        for dependency in request.depends_on:
                            dependency_result = results[index_by_step[dependency]]
                            dependency_request = structured_requests[index_by_step[dependency]]
                            ledger_context = None
                            if command_id:
                                from ._runtime_artifact_flow import structured_upstream_records
                                from ._runtime_delivery_manifest import delivery_manifest_ledger

                                ledger_context = structured_upstream_records(
                                    org_id=self._spec.org_id,
                                    command_id=command_id,
                                    source_node_ids=(dependency_request.target,),
                                )
                                manifest = delivery_manifest_ledger.latest(
                                    self._spec.org_id,
                                    command_id,
                                    dependency_request.target,
                                    assignment_id=delegation_key(dependency_request),
                                )
                                if manifest is not None:
                                    ledger_context["delivery_manifest"] = manifest.to_dict()
                            output = dependency_result.output
                            if len(output) > 2000:
                                output = output[:2000] + "\n…（依赖摘要已截断）"
                            dependencies.append(
                                {
                                    "step_id": dependency,
                                    "node_id": dependency_request.target,
                                    "output": output,
                                    "evidence": ledger_context or {},
                                }
                            )
                        upstream_context = UpstreamContext(
                            dependencies=tuple(dependencies)
                        )
                        await _emit_plan_event(
                            "delegation_dependency_ready",
                            {
                                "step_id": step_id,
                                "depends_on": list(request.depends_on),
                            },
                        )
                    return index, await _run_one(
                        index,
                        request.target,
                        request.instruction,
                        upstream_context=upstream_context,
                    )

                wave_results = await asyncio.gather(*(_run_ready(step) for step in ready_steps))
                for index, output in wave_results:
                    results[index] = output
                    completed_steps.add(structured_requests[index].step_id)
                    remaining_steps.discard(structured_requests[index].step_id)
        else:
            results = await asyncio.gather(
                *(_run_one(i, target, content) for i, (target, content) in enumerate(blocks))
            )
        children: list[tuple[str, str]] = [
            (blocks[i][0], results[i].render_for_parent()) for i in range(len(blocks))
        ]
        if structured and command_id:
            from ._runtime_delivery_manifest import aggregate_completed_child_manifests

            assignment_id = current_delegation_assignment_var.get("")
            child_assignments = tuple(
                (request.target, delegation_key(request)) for request in structured_requests
            )
            promoted = aggregate_completed_child_manifests(
                org_id=self._spec.org_id,
                command_id=command_id,
                node_id=self._spec.node_id,
                assignment_id=assignment_id,
                output_slot=current_delegation_output_slot_var.get("default"),
                children=child_assignments,
            )
            if promoted is not None and self._event_emitter is not None:
                emitted = self._event_emitter(
                    "delivery_manifest_recorded",
                    {
                        "org_id": self._spec.org_id,
                        "node_id": self._spec.node_id,
                        "command_id": command_id,
                        "tool_name": "runtime_child_aggregation",
                        "manifest": promoted.to_dict(),
                        "assignment_ids": [value for _node, value in child_assignments],
                    },
                )
                if inspect.isawaitable(emitted):
                    await emitted
        return _aggregate_with_children(parent_text, children)

    # ------------------------------------------------------------------
    # 逐级校验 (核心1): a parent node reviews a direct report's output
    # ------------------------------------------------------------------

    async def review_child_output(
        self,
        *,
        child_node_id: str,
        task: str,
        output: str,
        structured_evidence: Mapping[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[bool, str]:
        """The parent node ACTUALLY reviews a direct report's deliverable.

        核心1: completeness/quality is judged by the connected upstream node
        (this agent), not by a central heuristic. The parent's own brain reads
        the sub-task brief + the child's output and returns a verdict:

            (ok=True,  reason="...")  -> accept, splice it, continue upward
            (ok=False, reason="...")  -> send it back for rework with reason

        Generic over any topology (root→mid→leaf). The call is deliberately
        small (truncated output, low token budget). Provider errors and invalid
        verdicts fall back to the structured manifest: a complete manifest is
        accepted, while missing/incomplete evidence is rejected. The bounded
        rework loop in the executor still caps retries on the reject path.
        """
        body = (output or "").strip()
        if not body:
            # Empty output is an unambiguous reject — no model call needed.
            return False, "下级未产出任何内容（空产出），需重做并给出完整成果。"
        role = (self._spec.role or "worker").strip()
        persona = (self._spec.persona or "").strip()
        # Truncate the reviewed output so review stays cheap on huge deliverables.
        sample = (
            body if len(body) <= 4000 else (body[:4000] + "\n…（内容过长已截断，仅审阅前 4000 字）")
        )
        system = (
            f"{_NODE_SYSTEM_PREFIX} 你是节点 `{self._spec.node_id}`（角色：{role}）。"
            + (f" 设定：{persona}。" if persona else "")
            + " 现在你要审阅你的直属下级提交的成果，判断它是否达标、可被采纳并上汇。"
            " 评判依据：是否真正完成了交办任务、是否是成文成果而非纯思考过程/中途自述、"
            "是否有明显缺漏。请务实：只要是成文且基本覆盖任务要点，就应判定通过；"
            "仅当明显未完成（只有思考、空泛、跑题、严重缺漏）才判定不通过。"
            " 结构化交付证据（delivery manifest 与资产账本）是权威事实；如果其中已包含"
            "任务要求的 JSON、segments、asset_ids、task_ids 或文件，不得仅因为自由文本"
            "没有重复粘贴这些字段而要求重做。"
            " 严格只输出一个 JSON 对象，不要代码围栏或额外文字："
            ' {"decision":"accept|rework","reason":"一句话说明"}。'
            " decision 只能是 accept 或 rework；rework 时 reason 必须给出具体改进点。"
        )
        evidence = json.dumps(structured_evidence or {}, ensure_ascii=False, default=str)
        if len(evidence) > 12_000:
            evidence = evidence[:12_000] + "..."
        user = (
            f"【交给下级 `{child_node_id}` 的任务】\n{(task or '').strip()[:1500]}\n\n"
            f"【运行时结构化交付证据（权威）】\n{evidence}\n\n"
            f"【下级 `{child_node_id}` 的产出】\n{sample}\n\n"
            "请给出你的审阅裁决。"
        )
        try:
            kwargs: dict[str, Any] = {
                "messages": [{"role": "user", "content": user}],
                "system": system,
                "tools": [],
            }
            if cancel_event is not None:
                kwargs["cancel_event"] = cancel_event
            resp = await self._brain.messages_create_async(**kwargs)
            verdict_text = _extract_text_from_response(resp)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- fall back to structured evidence
            _LOGGER.debug(
                "parent review raised (parent=%s child=%s); defaulting to accept",
                self._spec.node_id,
                child_node_id,
                exc_info=True,
            )
            manifest = (structured_evidence or {}).get("delivery_manifest")
            if isinstance(manifest, Mapping) and manifest.get("state") == "complete":
                return True, "审阅调用异常；结构化交付清单已完成，按权威证据采纳。"
            return False, "审阅调用异常，且没有完整的结构化交付清单。"
        payload = _decode_review_payload(verdict_text)
        if payload is None or payload.get("decision") not in {"accept", "rework"}:
            manifest = (structured_evidence or {}).get("delivery_manifest")
            if isinstance(manifest, Mapping) and manifest.get("state") == "complete":
                return True, "审阅未返回有效 JSON；结构化交付清单已完成，按权威证据采纳。"
            return False, "审阅未返回有效 JSON，且没有完整的结构化交付清单。"
        return _parse_review_verdict(verdict_text)

    # ------------------------------------------------------------------
    # Token-level streaming for the no-tools (writer/leaf) path
    # ------------------------------------------------------------------

    async def _run_no_tools_streaming(
        self,
        *,
        system_prompt: str,
        text: str,
        command_id: str | None,
        cancel_event: asyncio.Event | None,
    ) -> tuple[str, bool]:
        """Stream a no-tools reply, emitting ``node_run_delta`` increments.

        Returns ``(final_text, ok)``. ``ok=False`` means the caller MUST fall
        back to the non-streaming :meth:`messages_create_async` path -- this
        is the isolation contract that keeps billing / failover / debug-dump
        intact when streaming is unavailable or fails mid-flight.

        We use ``brain.messages_create_stream`` (not the bare ``stream_chat``
        primitive) on purpose: the legacy stream method writes the LLM debug
        dump and pushes a ``TokenTrackingContext`` exactly like the
        non-streaming call, so the only billing concern left to us is
        recording the final ``usage`` (done in :meth:`_record_stream_usage`).
        ``CancelledError`` is re-raised unchanged so the Sprint-3 cancel
        pipeline still aborts a streaming node.
        """
        emit = self._event_emitter
        stream_factory = getattr(self._brain, "messages_create_stream", None)
        if emit is None or not callable(stream_factory):
            return "", False

        import time as _time

        try:
            from openakita.core.stream_accumulator import StreamAccumulator
        except Exception:  # noqa: BLE001 -- accumulator import must not break run
            return "", False

        acc = StreamAccumulator()
        seq = 0
        last_emit = 0.0
        had_delta = False

        async def _emit(*, done: bool) -> None:
            nonlocal seq, last_emit
            seq += 1
            last_emit = _time.monotonic()
            payload = {
                "org_id": self._spec.org_id,
                "node_id": self._spec.node_id,
                "command_id": command_id,
                # Frontend accumulates ``text``; ``delta`` is informational.
                # Cap the rolling preview so a multi-KB reply can't bloat a
                # single SSE frame (the final artifact carries the full text).
                "text": (acc.text_content or "")[:8000],
                # 图4: stream the node's REASONING alongside the deliverable so
                # the 编排过程 timeline shows "what it is thinking" live (not just
                # the final output). ``thinking_content`` is the accumulator's
                # dedicated reasoning channel — distinct from the visible text,
                # so the deliverable / filename / billing paths are untouched.
                "thinking": _clean_thinking(acc.thinking_content)[:4000],
                "seq": seq,
                "done": done,
            }
            try:
                res = emit("node_run_delta", payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001 -- a dropped delta must not abort the run
                pass

        stream = None
        try:
            stream = stream_factory(
                messages=[{"role": "user", "content": text}],
                system=system_prompt,
                tools=[],
                conversation_id=command_id or "",
            )
            async for raw in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError()
                if not isinstance(raw, dict):
                    continue
                if raw.get("type") == "endpoint_meta":
                    continue
                produced = False
                for hi in acc.feed(raw):
                    htype = hi.get("type")
                    if hi.get("content") and htype in ("text_delta", "thinking_delta"):
                        # Either channel producing content is worth a frame so
                        # reasoning shows up live even before the first visible
                        # token; only TEXT counts toward "did we stream a real
                        # deliverable" (thinking-only -> fall back, see below).
                        produced = True
                        if htype == "text_delta":
                            had_delta = True
                if produced and (_time.monotonic() - last_emit) >= 0.1:
                    await _emit(done=False)
        except asyncio.CancelledError:
            # Close the underlying stream so the httpx response is released,
            # then propagate so the cancel pipeline unwinds the node.
            if stream is not None:
                aclose = getattr(stream, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:  # noqa: BLE001
                        pass
            raise
        except Exception as exc:  # noqa: BLE001 -- any stream error -> fallback
            _LOGGER.warning(
                "orgs_v2 no-tools streaming failed (node=%s); falling back to "
                "non-streaming path: %s",
                self._spec.node_id,
                exc,
            )
            return "", False

        final_text = (acc.text_content or "").strip()
        if not final_text or not had_delta:
            # Nothing usable streamed -> let the caller do the resilient
            # non-streaming call (which has full multi-endpoint failover).
            return "", False

        # Billing: messages_create_stream set the tracking context but leaves
        # usage recording to the caller (see its docstring). Record it now so
        # streamed nodes are accounted exactly like non-streamed ones.
        self._record_stream_usage(acc)
        # 图4: persist the node's reasoning ONCE per run (not per delta) so the
        # 运行监控 "思维链" panel can show what the node reasoned about. Distinct
        # from node_run_delta (transient/live); this single event is what the
        # event-store-backed monitor timeline reads.
        await self._emit_node_thinking(emit, command_id, acc.thinking_content)
        # Final marker so the frontend can settle the rolling entry.
        await _emit(done=True)
        return final_text, True

    async def _emit_node_thinking(self, emit: Any, command_id: str, thinking: str | None) -> None:
        """Emit a single ``node_thinking`` event carrying the run's reasoning.

        Best-effort + fail-silent: the reasoning is cleaned (noise prefixes
        stripped) and truncated so it stays a compact monitor row, never the
        deliverable. A missing/blank reasoning channel emits nothing.
        """
        if emit is None:
            return
        snippet = _clean_thinking(thinking)[:1200]
        if not snippet:
            return
        payload = {
            "org_id": self._spec.org_id,
            "node_id": self._spec.node_id,
            "command_id": command_id,
            "thinking": snippet,
        }
        try:
            res = emit("node_thinking", payload)
            if asyncio.iscoroutine(res):
                await res
        except Exception:  # noqa: BLE001 -- a dropped reasoning event is harmless
            pass

    def _record_stream_usage(self, acc: Any) -> None:
        """Record token usage from a finished stream into the brain accumulator.

        Best-effort and fail-silent: builds a tiny usage-only response object
        (``brain._record_usage`` only reads ``usage`` / ``model`` /
        ``endpoint_name``) so we don't depend on the full ``LLMResponse``
        constructor. A missing usage block simply records nothing rather than
        raising.
        """
        try:
            usage = getattr(acc, "usage", None) or {}
            in_tok = int(usage.get("input_tokens", 0) or 0)
            out_tok = int(usage.get("output_tokens", 0) or 0)
            if in_tok == 0 and out_tok == 0:
                return
            record = getattr(self._brain, "_record_usage", None)
            if not callable(record):
                return
            from openakita.llm.types import Usage

            u = Usage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
                cache_read_input_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            )
            info: dict[str, Any] = {}
            try:
                info = self._brain.get_current_endpoint_info() or {}
            except Exception:  # noqa: BLE001
                info = {}

            class _UsageOnly:
                __slots__ = ("usage", "model", "endpoint_name")

                def __init__(self) -> None:
                    self.usage = u
                    self.model = info.get("model", "") or ""
                    self.endpoint_name = info.get("name", "") or ""

            record(_UsageOnly())
        except Exception:  # noqa: BLE001 -- billing record is best-effort
            _LOGGER.debug("orgs_v2 stream usage record failed", exc_info=True)


class DefaultAgentBuilder:
    """Production :class:`AgentBuilderProtocol` (Sprint-2 P0-1).

    The builder is constructed by the API server lifespan
    (``api/server.py`` ``create_app``) before the desktop ``Agent`` is
    available. To handle that ordering without ``app.state`` reach-ins
    inside the orgs subsystem, callers pass a ``brain_provider``
    callable that the builder dereferences each :meth:`build` -- the
    desktop ``Agent`` is wired into ``app.state.agent`` later by
    ``main.py`` and the closure picks it up on first use.

    Sprint-4 P0-1: optionally accepts a ``dispatch_callback`` that the
    built :class:`_BrainBackedNodeAgent` invokes when the LLM emits
    ``<dispatch target="...">...</dispatch>`` blocks. The callback is
    wired by the runtime composition root to point back at
    :meth:`AgentPipelineExecutor.dispatch_subtask`. Leaving it as
    ``None`` (the default) restores Sprint-3 behaviour byte-for-byte,
    so unit tests / parity gates that instantiate the builder
    standalone keep working without changes.
    """

    def __init__(
        self,
        *,
        brain_provider: Callable[[], Any],
        dispatch_callback: DispatchCallback | None = None,
        event_emitter: NodeToolEmit | None = None,
        tool_host_provider: NodeToolHostProvider | None = None,
    ) -> None:
        if not callable(brain_provider):
            raise TypeError("brain_provider must be callable")
        if dispatch_callback is not None and not callable(dispatch_callback):
            raise TypeError("dispatch_callback must be callable when provided")
        if event_emitter is not None and not callable(event_emitter):
            raise TypeError("event_emitter must be callable when provided")
        if tool_host_provider is not None and not callable(tool_host_provider):
            raise TypeError("tool_host_provider must be callable when provided")
        self._brain_provider = brain_provider
        self._dispatch_callback = dispatch_callback
        self._event_emitter = event_emitter
        self._tool_host_provider = tool_host_provider

    def build(self, spec: AgentSpec) -> Any:
        try:
            brain = self._brain_provider()
        except Exception as exc:  # noqa: BLE001 -- propagate as builder-unavailable
            raise BuilderUnavailable(f"brain_provider raised: {type(exc).__name__}: {exc}") from exc
        if brain is None:
            raise BuilderUnavailable(
                "main agent brain not yet initialised "
                f"(org={spec.org_id} node={spec.node_id}); "
                "the API loop came up before the desktop Agent finished "
                "wiring -- retry the command in a moment"
            )
        # Sanity: the brain must expose ``messages_create_async``;
        # alternative LLM frontends will need their own builder until
        # the multi-node sprint introduces a richer adapter layer.
        if not hasattr(brain, "messages_create_async"):
            raise BuilderUnavailable(
                f"brain of type {type(brain).__name__} has no "
                "messages_create_async; cannot bind orgs_v2 node "
                f"(org={spec.org_id} node={spec.node_id})"
            )
        _LOGGER.debug(
            "DefaultAgentBuilder built node agent (org=%s node=%s role=%s persona=%s)",
            spec.org_id,
            spec.node_id,
            spec.role,
            (spec.persona or "")[:40],
        )
        return _BrainBackedNodeAgent(
            spec,
            brain,
            dispatch_callback=self._dispatch_callback,
            event_emitter=self._event_emitter,
            tool_host_provider=self._tool_host_provider,
        )

    def teardown(self, agent: Any) -> None:  # noqa: ARG002
        # Brain references are shared with the main desktop Agent; we do
        # not own its lifecycle. The cache evicts node agents but nothing
        # downstream needs explicit cleanup here.
        return None
