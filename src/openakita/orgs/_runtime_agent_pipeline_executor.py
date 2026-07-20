"""``_runtime_agent_pipeline_executor.py`` -- v2 OrgRuntime activate-and-run executor.

Companion to :mod:`_runtime_agent_pipeline` (split out in
P-RC-10 P10.5a per ADR-0014). Owns :class:`AgentPipelineExecutor`
(plus the ``_QUOTA_AUTH_HINTS`` table, the
``_AgentRunCallable`` Protocol and ``_looks_like_quota_or_auth_error``
string-sniff). The companion shard owns :class:`AgentCache` /
:class:`ProfileResolver` / ``ORG_STATE_PAUSED``; this file imports
them as a one-way dependency, and the companion re-exports
:class:`AgentPipelineExecutor` so the
``from openakita.orgs._runtime_agent_pipeline import
AgentPipelineExecutor`` import path keeps resolving byte-for-byte.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from openakita.runtime.execution_context import (
    ArtifactRole,
    UpstreamContext,
    artifact_role_for_phase,
    current_execution_phase_var,
    current_upstream_context_var,
)

from ._runtime_agent_pipeline import (
    MAX_DISPATCH_DEPTH,
    ORG_STATE_PAUSED,
    current_chain_id_var,
    current_command_id_var,
    dispatch_depth_var,
)
from ._runtime_artifact_flow import (
    artifact_ledger,
    current_artifact_delivery_dir_var,
    current_artifact_edges_var,
    structured_upstream_records,
)
from ._runtime_delegation import (
    DelegationExecutionResult,
    DelegationExecutionStatus,
    current_delegation_assignment_var,
    current_delegation_output_slot_var,
)
from ._runtime_delivery_manifest import (
    delivery_manifest_ledger,
    validate_manifest_media_delivery,
    validate_manifest_runtime_evidence,
)
from ._runtime_dispatch import _append_delegation_log
from ._runtime_external_tasks import (
    ExternalTaskTimeout,
    ExternalTaskTracker,
    NodeActivationTimeout,
    current_external_task_tracker_var,
    wait_with_external_task_budget,
)
from ._runtime_media_quality import (
    current_media_quality_failures,
    current_media_quality_failures_var,
    format_media_quality_reason,
    record_media_quality_failure,
)
from ._runtime_node_artifacts import (
    classify_node_output,
    persist_node_artifact,
    persist_node_memory,
    strip_deliverable_thinking,
)
from .command_service import OrgLookupProtocol

if TYPE_CHECKING:
    from ._runtime_agent_pipeline import AgentCache, ProfileResolver

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ActivationResumeContext:
    parent_node_id: str
    depth: int
    assignment_id: str
    output_slot: str
    upstream_context: UpstreamContext = UpstreamContext()


def _render_upstream_context(context: UpstreamContext) -> str:
    """Render structured dependency evidence for the LLM prompt boundary."""
    if not context.is_present:
        return ""
    import json

    payload = json.dumps(context.to_dict(), ensure_ascii=False, default=str)
    if len(payload) > 16_000:
        payload = payload[:16_000] + "..."
    return (
        "=== 已完成的前置步骤结构化上下文 ===\n"
        f"{payload}\n"
        "只使用以上已登记证据推进当前步骤；不要重新生成或扫描文件系统。"
    )


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    """Read a bounded int env override (clamped to [lo, hi])."""
    try:
        val = int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, val))


# Max chars we read back from a recovered on-disk deliverable so a huge file
# can't blow the parent-review / upstream prompt budget. The head of a long
# doc is enough for the parent to judge completeness; the FULL file stays on
# disk and is registered as the downloadable artifact.
_RECOVER_READ_CAP = 40000


def _pick_recoverable_deliverable(written: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the best on-disk file to adopt as an empty-text node's output.

    Prefers text-like deliverables (``.md`` / ``.txt`` / ``.json`` / ``.csv``),
    then the LARGEST by byte size (the substantive document over a stub). Binary
    media (images / video) is skipped here -- those are surfaced as delivery
    cards via ``file_output_registered`` but are not a substitute for a missing
    textual deliverable the parent must review.
    """

    if not written:
        return None
    text_like = (".md", ".txt", ".json", ".csv", ".markdown", ".html")

    def _score(d: dict[str, Any]) -> tuple[int, int]:
        path = str(d.get("path") or "").lower()
        is_text = 1 if path.endswith(text_like) else 0
        size = int(d.get("size_bytes") or 0)
        return (is_text, size)

    best = max(written, key=_score)
    # Only adopt a file with real content (a 0-byte stub is not a deliverable).
    if int(best.get("size_bytes") or 0) <= 0:
        return None
    return best


def _read_text_bounded(path: str, *, cap: int = _RECOVER_READ_CAP) -> str:
    """Read a recovered deliverable's text, bounded + fail-silent."""

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = fh.read(cap + 1)
    except OSError:
        return ""
    if len(data) > cap:
        data = data[:cap] + "\n\n[...内容较长，完整文件见下载附件...]"
    return data


# Defaults for the orchestration knobs. They are read DYNAMICALLY (see the
# ``_rework_max`` / ``_review_enabled`` / ``_node_timeout_s`` helpers) so a
# deployment can override them via env at runtime and the test-suite can pin
# them per-test (monkeypatch / env) without import-time freezing.

# 核心2 (重做闭环): how many times a direct-upstream node may send a failed
# deliverable back to its report before escalating. Bounded so a hard-to-satisfy
# reviewer can't spin forever; on exhaustion we escalate (emit an event + return
# the last output so the parent/supervisor can decide). 0 disables the loop.
_REWORK_MAX_DEFAULT = 1

# 核心3 (超时隔离): hard wall-clock cap on a SINGLE node activation. A node that
# blocks past this (e.g. an LLM stuck generating an oversized write_file arg)
# is failed-and-reported instead of stalling the whole org indefinitely. The
# supervisor then continues with siblings / replans. Generous default; tune via
# env. 0 disables the per-node cap (falls back to the supervisor hard-ceiling).
_NODE_TIMEOUT_DEFAULT = 420


def _review_enabled() -> bool:
    """核心1: is the parent-executed review on? On by default; ``0`` disables."""
    return os.environ.get("OPENAKITA_ORG_REVIEW_ENABLED", "1").strip() != "0"


def _rework_max() -> int:
    return _env_int("OPENAKITA_ORG_REWORK_MAX", _REWORK_MAX_DEFAULT, lo=0, hi=5)


def _node_timeout_s() -> int:
    return _env_int("OPENAKITA_ORG_NODE_TIMEOUT_S", _NODE_TIMEOUT_DEFAULT, lo=0, hi=3600)


def _new_chain_id() -> str:
    """Mint a unique-per-run chain id (``chain_<13ms>_<8hex>``).

    Loosely chronological + collision-free within a millisecond, so the
    delegation tree edges (``parent_chain_id`` -> ``chain_id``) stay
    stable across the events.jsonl stream and the kanban task store.
    """
    return f"chain_{int(time.time() * 1000):013d}_{secrets.token_hex(4)}"


def _direct_dispatch_children(org: Any, node_id: str) -> set[str] | None:
    """Return the set of node ids ``node_id`` may dispatch DOWN to, or ``None``.

    Topology guard helper (audit 2026-06). Mirrors
    :meth:`ProfileResolver._available_nodes_for`: a node's legitimate dispatch
    targets are the downstream ``source -> target`` endpoints of its
    ``hierarchy`` / ``escalate`` edges (collaborate / consult are peer links,
    not delegable). Returns:

    * ``None`` when the org exposes NO readable edge metadata (test stubs /
      flat orgs with no wiring) -> caller FAILS OPEN (keeps the legacy
      existence-only check) so we never break a topology we cannot read.
    * a ``set[str]`` (possibly empty) when edges ARE present -> caller HARD
      ENFORCES membership; an empty set means ``node_id`` is a leaf within a
      wired org and must not dispatch at all.
    """
    edges = getattr(org, "edges", None)
    if not edges:
        return None
    children: set[str] = set()
    for e in list(edges):
        src = getattr(e, "source", "") or ""
        tgt = getattr(e, "target", "") or ""
        if src != node_id or not tgt or tgt == node_id:
            continue
        et = getattr(e, "edge_type", None)
        et_val = getattr(et, "value", None) or str(et)
        if et_val not in ("hierarchy", "escalate"):
            continue
        children.add(tgt)
    return children


_QUOTA_AUTH_HINTS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "quota",
    "billing",
    "insufficient",
    "exhausted",
    "payment required",
    "unauthorized",
    "forbidden",
    "authorization",
    "authentication",
    "invalid api key",
    "invalid_api_key",
    "permission_denied",
)

# Structured error categories (carried by ``AllEndpointsFailedError``)
# that authoritatively mean "quota / auth" regardless of the message.
_QUOTA_AUTH_CATEGORIES: frozenset[str] = frozenset({"quota", "auth"})

# HTTP status codes that mean quota/auth (429 rate-limit, 401/403 authz,
# 402 payment-required). Matched on the exception's ``status_code`` /
# ``status`` attribute so a bare "401" appearing in unrelated prose
# (e.g. "processed 401 records") does NOT trip the classifier.
_QUOTA_AUTH_STATUS: frozenset[int] = frozenset({401, 402, 403, 429})

# Parenthesised HTTP codes ("(401)" / "(402)" / "(403)" / "(429)") as
# emitted by the relay's "Error (401)" phrasing. This keeps the
# message-level detection for those codes while avoiding the bare-number
# false positive that plain substring matching of "401"/"403" caused.
_PAREN_CODE_RE = re.compile(r"\(\s*(?:401|402|403|429)\s*\)")


class _AgentRunCallable(Protocol):
    """Minimal callable contract the cached agent must satisfy.

    v2 stays decoupled from concrete Agent / Brain types: the
    executor only calls ``await agent.run(content)`` and
    expects a string-coercible response. Concrete agents
    (e.g. ``openakita.core.agent.Agent``) already match.

    Sprint-13 H1: production agents may optionally accept a
    ``cancel_event`` keyword. The executor probes the runtime
    signature so legacy implementations without the kwarg keep
    working byte-for-byte; only callables that opt in receive the
    event and are expected to thread it down to
    ``Brain.messages_create_async`` and ``LLMClient.chat``.
    """

    async def run(self, content: str) -> Any: ...


def _run_accepts_cancel_event(run: Any) -> bool:
    """True when ``run`` declares ``cancel_event`` or accepts ``**kwargs``.

    Sprint-13 H1: cancel_event is a structural extension; the executor
    only forwards it to callables that opted in. Callables for which
    :func:`inspect.signature` raises (C-implemented, slot wrapper) are
    treated as accepting it because we cannot prove otherwise and
    those wrappers typically forward via ``**kwargs``. Cached per
    callable identity keeps the introspection cost negligible across
    storm-shaped workloads (10+ concurrent runs).
    """
    try:
        sig = inspect.signature(run)
    except (TypeError, ValueError):
        return True
    params = sig.parameters
    if "cancel_event" in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _looks_like_quota_or_auth_error(exc: BaseException) -> bool:
    """Classify an LLM exception as quota/auth (-> pause the org) — v1 parity.

    Restores the v1 ``_is_quota_auth_error`` contract that the P9.6f2
    refactor accidentally narrowed to a bare substring sniff (which both
    ignored the structured ``error_categories`` signal and false-matched a
    plain "401" in unrelated prose). The classification, in order:

    1. ``error_categories`` (``AllEndpointsFailedError``): a ``quota`` /
       ``auth`` category is authoritative; ``structural`` / ``transient``
       alone are not (they fall through to the message checks below).
    2. A quota/auth HTTP ``status_code`` / ``status`` (401/402/403/429)
       anywhere on the ``__cause__`` / ``__context__`` chain.
    3. Keyword hints in the joined message text (quota / billing /
       unauthorized / forbidden / authentication / …).
    4. A parenthesised HTTP code such as "(401)" — relay phrasing —
       without re-introducing the bare-"401"-in-prose false positive.
    """

    cats = getattr(exc, "error_categories", None)
    if isinstance(cats, (set, frozenset)) and cats & _QUOTA_AUTH_CATEGORIES:
        return True

    parts: list[str] = []
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(str(cur))
        parts.append(type(cur).__name__)
        sc = getattr(cur, "status_code", None)
        if sc is None:
            sc = getattr(cur, "status", None)
        if sc is not None:
            try:
                if int(sc) in _QUOTA_AUTH_STATUS:
                    return True
            except (TypeError, ValueError):
                pass
            parts.append(str(sc))
        cur = cur.__cause__ or cur.__context__

    blob = " ".join(parts).lower()
    if any(h in blob for h in _QUOTA_AUTH_HINTS):
        return True
    return bool(_PAREN_CODE_RE.search(blob))


class AgentPipelineExecutor:
    """v2 message-to-agent-run executor (P9.6f2).

    Replaces v1 ``_activate_and_run`` (24 LOC) +
    ``_activate_and_run_inner`` (556 LOC) +
    ``_run_agent_task`` (110 LOC) + ``_emit_llm_usage``
    (23 LOC) + ``_pause_org_for_quota`` (78 LOC) +
    ``_is_quota_auth_error`` (11 LOC). v1 ~800 LOC ->
    v2 ~180 LOC.

    DI:

    * ``cache`` -- :class:`AgentCache` (P9.6f1).
    * ``resolver`` -- :class:`ProfileResolver` (P9.6f1).
    * ``lookup`` -- :class:`OrgLookupProtocol` for org-state
      probing (the v1 ``ORG_STATE_PAUSED`` gate).
    * ``event_bus`` -- :class:`EventBusProtocol` for
      ``agent_run_started`` / ``agent_run_finished`` /
      ``agent_run_failed`` / ``org_paused_quota`` /
      ``llm_usage`` events.
    * ``on_org_paused`` -- optional sync callback the
      runtime composition root wires to flip the org-state
      machine (P9.6d :class:`OrgLifecycleManager.pause_org`)
      when quota / auth errors are detected. Signature:
      ``(org_id, reason) -> None``.
    """

    def __init__(
        self,
        *,
        cache: AgentCache,
        resolver: ProfileResolver,
        lookup: OrgLookupProtocol,
        event_bus: Any,
        on_org_paused: Any = None,
        cancel_source_provider: Any = None,
    ) -> None:
        self._cache = cache
        self._resolver = resolver
        self._lookup = lookup
        self._bus = event_bus
        self._on_org_paused = on_org_paused
        # Sprint-6 P0-2 (RCA ``_v17_p1_rca.md`` §2.5): optional
        # ``(command_id: str) -> str | None`` callback that resolves
        # the cancel source (``stop_org``/``watchdog``/``user_cancel``)
        # so the ``except CancelledError`` branch below can stamp
        # ``cancelled_by`` on the ``agent_run_cancelled`` event
        # payload. Defaults to ``None`` so existing tests (and
        # composition roots that don't wire the bridge) keep the
        # Sprint-5 observable: ``reason="user_cancel"`` + no
        # ``cancelled_by`` field.
        self._cancel_source_provider = cancel_source_provider
        self._activation_contexts: OrderedDict[tuple[str, str, str], _ActivationResumeContext] = (
            OrderedDict()
        )

    async def activate_and_run(
        self,
        *,
        org_id: str,
        node_id: str,
        content: str,
        command_id: str | None = None,
        role: str | None = None,
        persona: str | None = None,
        unattended: bool = False,
        depth: int = 0,
        parent_node_id: str | None = None,
        chain_id: str | None = None,
        parent_chain_id: str | None = None,
        assignment_id: str | None = None,
        output_slot: str = "default",
        upstream_context: UpstreamContext | Mapping[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        """v1 ``_activate_and_run`` + ``_activate_and_run_inner`` parity.

        Returns a v1-shaped dict:
            {"status": "ok" | "skipped" | "paused" | "error",
             "command_id": str | None,
             "output": str | None,
             "reason": str | None}

        Sprint-4 P0-1 (audit v4 §6.2): ``depth`` controls the
        :data:`dispatch_depth_var` context variable so the agent's
        ``run`` method can decide whether to parse and recurse into
        ``<dispatch>`` blocks. Entry runs pass ``depth=0`` (the
        default); :meth:`dispatch_subtask` re-enters this method with
        ``depth=parent_depth + 1``. ``parent_node_id`` carries the
        dispatcher's node id into the started / finished event
        payloads so the SSE stream and events.jsonl reflect the
        delegation chain (Sprint-3 only had the root entry node).

        Sprint-4 P0-2 (audit v4 §5.4): after a successful agent run we
        persist the full output to ``data/orgs/<id>/artifacts/`` and a
        bounded summary to ``memory/`` via
        :mod:`._runtime_node_artifacts`. Failures are swallowed by the
        helpers themselves; the returned ``agent_run_finished`` payload
        carries an ``artifact_path`` field (``None`` when persistence
        is disabled / failed) so the UI can surface the path without a
        second round-trip.
        """

        context_restored = False
        resolved_upstream_context = UpstreamContext.from_value(upstream_context)
        context_key = (org_id, str(command_id or ""), node_id)
        if command_id and not parent_node_id and depth == 0 and not assignment_id:
            resume = self._activation_contexts.get(context_key)
            if resume is not None:
                parent_node_id = resume.parent_node_id
                depth = resume.depth
                assignment_id = resume.assignment_id
                output_slot = resume.output_slot
                if not resolved_upstream_context.is_present:
                    resolved_upstream_context = resume.upstream_context
                self._activation_contexts.move_to_end(context_key)
                context_restored = True

        org = self._lookup.get_org(org_id)
        if org is None:
            return self._result("error", command_id, reason="org_not_found")
        # v1 parity: skip if the org is paused (quota / manual).
        state = getattr(org, "state", None) or getattr(org, "status", None)
        if state == ORG_STATE_PAUSED:
            return self._result("skipped", command_id, reason="org_paused")
        spec = self._resolver.resolve(
            org_id=org_id,
            node_id=node_id,
            role=role,
            persona=persona,
            unattended=unattended,
        )
        if spec is None:
            return self._result("error", command_id, reason="profile_unresolved")
        # Resolve this run's chain id. The root entry run (no chain
        # passed in) mints a fresh one; a child run carries the chain
        # ``dispatch_subtask`` minted for it. Stamped on every lifecycle
        # event below so the kanban can rebuild the parent/child tree.
        run_chain_id = chain_id or _new_chain_id()
        run_assignment_id = (
            str(assignment_id or "").strip()
            or f"command:{command_id or run_chain_id}|node:{node_id}"
        )
        run_output_slot = str(output_slot or "default").strip() or "default"
        if command_id and parent_node_id:
            previous = self._activation_contexts.get(context_key)
            if not resolved_upstream_context.is_present and previous is not None:
                resolved_upstream_context = previous.upstream_context
            self._activation_contexts[context_key] = _ActivationResumeContext(
                parent_node_id=parent_node_id,
                depth=max(1, int(depth)),
                assignment_id=run_assignment_id,
                output_slot=run_output_slot,
                upstream_context=resolved_upstream_context,
            )
            self._activation_contexts.move_to_end(context_key)
            while len(self._activation_contexts) > 256:
                self._activation_contexts.popitem(last=False)
        started_payload: dict[str, Any] = {
            "org_id": org_id,
            "node_id": node_id,
            "command_id": command_id,
            "chain_id": run_chain_id,
            "assignment_id": run_assignment_id,
            "output_slot": run_output_slot,
        }
        if context_restored:
            started_payload["context_restored"] = True
        if parent_chain_id:
            started_payload["parent_chain_id"] = parent_chain_id
        if depth:
            started_payload["depth"] = depth
        if parent_node_id:
            started_payload["parent_node_id"] = parent_node_id
        if resolved_upstream_context.is_present:
            started_payload["upstream_context"] = resolved_upstream_context.to_dict()
        await self._emit("agent_run_started", started_payload)
        try:
            agent = self._cache.get_or_create(spec)
        except Exception as exc:  # noqa: BLE001 (v1 parity: never crash dispatch)
            _LOGGER.exception("AgentCache.get_or_create raised (org=%s node=%s)", org_id, node_id)
            await self._emit(
                "agent_run_failed",
                {
                    "org_id": org_id,
                    "node_id": node_id,
                    "command_id": command_id,
                    "chain_id": run_chain_id,
                    "reason": "agent_build_failed",
                    "error": str(exc),
                },
            )
            return self._result("error", command_id, reason="agent_build_failed")
        # Sprint-4 P0-1: set the depth context so the agent's
        # ``run`` knows whether it is allowed to dispatch children
        # (and so the system prompt picks the right tutorial slot).
        # ContextVar.set returns a Token we reset in ``finally`` so a
        # crash inside ``_invoke_agent`` cannot leak the depth across
        # subsequent activations on the same event loop.
        #
        # The command-id context is the lane the dispatch callback
        # uses to re-attribute child runs to the same parent command;
        # see :data:`current_command_id_var` and the docstring of
        # :meth:`dispatch_subtask`. We always set it (even when
        # ``command_id`` is ``None``) so the dispatch callback gets a
        # clean empty string rather than whatever the previous run
        # left behind.
        depth_token = dispatch_depth_var.set(max(0, int(depth)))
        cid_token = current_command_id_var.set(str(command_id or ""))
        # Expose THIS run's chain so a ``<dispatch>`` the agent emits is
        # attributed to it as the child's ``parent_chain_id``.
        chain_token = current_chain_id_var.set(run_chain_id)
        assignment_token = current_delegation_assignment_var.set(run_assignment_id)
        output_slot_token = current_delegation_output_slot_var.set(run_output_slot)
        upstream_context_token = current_upstream_context_var.set(resolved_upstream_context)
        artifact_edges_token = current_artifact_edges_var.set(
            tuple(getattr(org, "edges", None) or ())
        )
        get_org_dir = getattr(self._lookup, "get_org_dir", None)
        artifact_delivery_dir: Path | None = None
        if command_id and callable(get_org_dir):
            try:
                artifact_delivery_dir = (
                    Path(get_org_dir(org_id))
                    / "commands"
                    / str(command_id)
                    / "artifacts"
                    / "deliverables"
                    / "plugin_assets"
                )
            except (OSError, TypeError, ValueError):
                artifact_delivery_dir = None
        artifact_delivery_token = current_artifact_delivery_dir_var.set(artifact_delivery_dir)
        media_quality_token = current_media_quality_failures_var.set({})
        external_task_tracker = ExternalTaskTracker()
        external_task_token = current_external_task_tracker_var.set(external_task_tracker)
        media_quality_failures: list[dict[str, Any]] = []
        # test11 P1: timestamp the run start so we only adopt files THIS run
        # wrote (not a stale file from an earlier attempt) when recovering an
        # empty-text node from its on-disk deliverable.
        run_start_ts = time.time()
        rendered_upstream = _render_upstream_context(resolved_upstream_context)
        run_content = (
            f"{content}\n\n{rendered_upstream}" if rendered_upstream else content
        )
        try:
            # Sprint-13 H1 (RC-4 §6 H1): forward ``cancel_event`` so
            # the agent's ``run`` (and through it
            # ``brain.messages_create_async`` and ``LLMClient.chat``)
            # can race ``_race_with_cancel`` against the in-flight
            # ``httpx`` request and abort the moment a user cancel
            # fires. Old test agents whose ``run`` signature has no
            # ``cancel_event`` kwarg keep working via the signature
            # probe inside ``_invoke_agent``.
            node_timeout = _node_timeout_s()
            # 核心3: the wall-clock cap applies ONLY to LEAF nodes (no direct
            # reports). A coordinator / root activation legitimately spans its
            # ENTIRE subtree (children + reviews + reworks run inside its gather),
            # so capping it would kill the whole org the moment the tree gets
            # deep — exactly the test8 RCA where the 主编 timed out at 300s and
            # cancelled everyone. Coordinators are bounded by the supervisor
            # ceiling + their leaves' own caps instead.
            is_leaf_node = not bool(getattr(spec, "available_nodes", None))
            if node_timeout > 0 and is_leaf_node:
                # bound one stuck leaf (e.g. an LLM grinding on an oversized
                # write_file arg) so it cannot freeze the org. On timeout we
                # surface agent_run_failed and let the supervisor move on.
                output = await wait_with_external_task_budget(
                    self._invoke_agent(agent, run_content, cancel_event=cancel_event),
                    node_timeout_s=node_timeout,
                    tracker=external_task_tracker,
                )
            else:
                output = await self._invoke_agent(agent, run_content, cancel_event=cancel_event)
        except ExternalTaskTimeout:
            _LOGGER.warning(
                "external task wait timed out after %.1fs (org=%s node=%s)",
                external_task_tracker.max_wait_s,
                org_id,
                node_id,
            )
            await self._emit(
                "agent_run_failed",
                {
                    "org_id": org_id,
                    "node_id": node_id,
                    "command_id": command_id,
                    "chain_id": run_chain_id,
                    "reason": "external_task_timeout",
                    "error": (
                        "external task wait exceeded declared budget "
                        f"{external_task_tracker.max_wait_s:.0f}s"
                    ),
                },
            )
            return self._result("error", command_id, reason="external_task_timeout")
        except (NodeActivationTimeout, TimeoutError):
            node_timeout = _node_timeout_s()
            _LOGGER.warning(
                "node activation timed out after %ss (org=%s node=%s)",
                node_timeout,
                org_id,
                node_id,
            )
            await self._emit(
                "agent_run_failed",
                {
                    "org_id": org_id,
                    "node_id": node_id,
                    "command_id": command_id,
                    "chain_id": run_chain_id,
                    "reason": "node_timeout",
                    "error": f"node activation exceeded {node_timeout}s",
                },
            )
            return self._result("error", command_id, reason="node_timeout")
        except asyncio.CancelledError:
            # Sprint-3 P0-2 (audit ``_orgs_business_capability_audit_v3.md``
            # §5.3): the user pressed cancel and ``CancelledError`` arrived
            # via ``task.cancel()`` somewhere down the await chain
            # (``Brain.messages_create_async`` -> ``LLMClient.chat`` ->
            # ``httpx``). Emit a distinct ``agent_run_cancelled`` event so
            # the command_service outcome cache can flip ``event_ref`` to
            # ``agent_run_cancelled`` (instead of mis-classifying as
            # ``agent_run_failed``), then re-raise so the asyncio task
            # finalises with ``task.cancelled() == True`` and the
            # ``_run_minimal`` cancel branch runs.
            #
            # We swallow ``CancelledError`` from the emit itself: the bus
            # may surface the cancellation when ``await`` resumes inside
            # ``_InMemoryEventBus.emit``, but the outcome we *care about*
            # is the original cancel, not the nested one.
            # Sprint-6 P0-2 (RCA ``_v17_p1_rca.md`` §2.5): consult the
            # cancel-source bridge before emitting so the events.jsonl
            # payload distinguishes user-cancel / stop-org / watchdog
            # kills. The Sprint-5 commit only wrote ``cancelled_by``
            # to the in-memory outcome cache; v17 audit caught that
            # the on-disk payload still hard-coded ``user_cancel``.
            # Sources arrive verbatim from
            # :meth:`OrgCommandService.cancel_all_for_org` (``stop_org``,
            # forwarded verbatim by ``cancel_user_command``) and the
            # watchdog (``watchdog``). ``None`` -> keep the Sprint-3
            # default so user-initiated cancels stay byte-for-byte
            # compatible with the existing reader.
            cancel_source: str | None = None
            if self._cancel_source_provider is not None and command_id:
                try:
                    cancel_source = self._cancel_source_provider(command_id)
                except Exception:  # noqa: BLE001 -- best-effort
                    cancel_source = None
            cancel_payload: dict[str, Any] = {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "chain_id": run_chain_id,
                "reason": cancel_source or "user_cancel",
                "cancelled_by": cancel_source or "user_cancel",
            }
            try:
                await self._emit("agent_run_cancelled", cancel_payload)
            except asyncio.CancelledError:
                pass
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("agent.run raised (org=%s node=%s)", org_id, node_id)
            if _looks_like_quota_or_auth_error(exc):
                await self.pause_org_for_quota(org_id, reason=str(exc))
                return self._result("paused", command_id, reason="quota_auth")
            await self._emit(
                "agent_run_failed",
                {
                    "org_id": org_id,
                    "node_id": node_id,
                    "command_id": command_id,
                    "chain_id": run_chain_id,
                    "reason": "agent_run_raised",
                    "error": str(exc),
                },
            )
            return self._result("error", command_id, reason="agent_run_raised")
        finally:
            media_quality_failures = current_media_quality_failures()
            dispatch_depth_var.reset(depth_token)
            current_command_id_var.reset(cid_token)
            current_chain_id_var.reset(chain_token)
            current_delegation_assignment_var.reset(assignment_token)
            current_delegation_output_slot_var.reset(output_slot_token)
            current_upstream_context_var.reset(upstream_context_token)
            current_artifact_edges_var.reset(artifact_edges_token)
            current_artifact_delivery_dir_var.reset(artifact_delivery_token)
            current_media_quality_failures_var.reset(media_quality_token)
            current_external_task_tracker_var.reset(external_task_token)

        # Sprint-4 P0-2: persist the artefact + memory summary BEFORE
        # emitting agent_run_finished so the event payload can carry
        # the resolved path. The helpers are fail-silent: ``None`` means
        # persistence is either disabled or hit an I/O snag, never that
        # the agent run itself failed.
        output_text = str(output) if output else ""
        # Exploratory v21 (2026-06): strip any leaked ``<thinking>…</thinking>``
        # reasoning block from the deliverable BEFORE it is classified,
        # persisted (.md + PDF source), summarised to memory, or returned up the
        # chain to become the parent review sample / root ``final_message``. A
        # multi-layer content-team run leaked the root主编's full chain-of-thought
        # into both the persisted artifact and the 713 KB final PDF because the
        # block is preceded by a markdown heading, which the completeness gate
        # (correctly) treats as a valid "reasoning + document" deliverable. The
        # live ``node_thinking`` timeline channel is unaffected.
        output_text = strip_deliverable_thinking(output_text)
        delivery_manifest = (
            delivery_manifest_ledger.latest(
                org_id,
                str(command_id),
                node_id,
                since=run_start_ts,
                assignment_id=run_assignment_id,
            )
            if command_id
            else None
        )
        if delivery_manifest is None and command_id:
            # Backward compatibility for integrations/tests that record a
            # manifest without the new runtime assignment metadata.
            delivery_manifest = delivery_manifest_ledger.latest(
                org_id,
                str(command_id),
                node_id,
                since=run_start_ts,
            )
        delivery_manifest_payload = (
            delivery_manifest.to_dict() if delivery_manifest is not None else None
        )
        execution_phase = current_execution_phase_var.get()
        artifact_role = artifact_role_for_phase(execution_phase)
        if delivery_manifest is not None:
            artifact_role = ArtifactRole(delivery_manifest.artifact_role)
        delegated_deliveries = (
            [
                {
                    "node_id": manifest.node_id,
                    **manifest.to_dict(),
                }
                for manifest in delivery_manifest_ledger.list_since(
                    org_id,
                    str(command_id),
                    since=run_start_ts,
                    exclude_node_id=node_id,
                )
            ]
            if command_id
            else []
        )
        if not parent_node_id and command_id:
            media_quality_failures.extend(
                validate_manifest_media_delivery(
                    delivery_manifest,
                    artifact_records=artifact_ledger.get(org_id, str(command_id)),
                )
            )
        # Quality gate. 核心1: completeness for a CHILD node (one with a
        # connected upstream ``parent_node_id``) is decided by that parent's
        # review in :meth:`dispatch_subtask`, NOT by this central heuristic —
        # so we DON'T let the heuristic suppress a child's artifact or stamp it
        # ``incomplete`` here (that was the "中央门禁凭空判定" the user flagged).
        # The ROOT node (no parent / no upstream reviewer) keeps the heuristic
        # as its only guard against delivering a raw ``thinking…`` leak.
        has_upstream_reviewer = bool(parent_node_id)
        quality_status, quality_reason = classify_node_output(
            output_text,
            delivery_state=(delivery_manifest.state if delivery_manifest else None),
        )
        artifact_path: str | None = None
        # test11 P1 (有输出却空产出): a node that did its real work by WRITING A
        # FILE (e.g. writer-a wrote a 12 KB plan via write_file) but ended its
        # turn with empty / mid-reasoning TEXT must NOT be judged 空产出 -- the
        # on-disk deliverable IS the work. Recover it: adopt the largest file
        # the node wrote during THIS run as the deliverable so the parent review
        # sees real content, the artifact is registered, and the node is not
        # needlessly bounced / escalated (which is what fizzled writer-b).
        recovered_from_file = False
        if quality_reason == "empty_output" and command_id:
            try:
                from ._runtime_node_tools import pop_node_file_outputs

                written = pop_node_file_outputs(org_id, command_id, node_id, since_ts=run_start_ts)
            except Exception:  # noqa: BLE001 -- recovery must never crash the run
                written = []
            doc = _pick_recoverable_deliverable(written)
            if doc is not None:
                recovered_text = _read_text_bounded(doc["path"])
                if recovered_text.strip():
                    output_text = recovered_text
                    artifact_path = doc["path"]
                    quality_status, quality_reason = classify_node_output(
                        output_text,
                        delivery_state=(delivery_manifest.state if delivery_manifest else None),
                    )
                    recovered_from_file = True
                    _LOGGER.info(
                        "[quality-gate] recovered empty-text node from on-disk file "
                        "(org=%s node=%s path=%s len=%d): treated as deliverable",
                        org_id,
                        node_id,
                        doc["path"],
                        len(output_text),
                    )
        deterministic_quality_failed = bool(media_quality_failures)
        if deterministic_quality_failed:
            quality_reason = format_media_quality_reason(media_quality_failures)
        is_incomplete = deterministic_quality_failed or quality_status != "ok"
        if output_text and command_id and not is_incomplete and not recovered_from_file:
            try:
                artifact_path = persist_node_artifact(
                    org_id=org_id,
                    command_id=command_id,
                    node_id=node_id,
                    output=output_text,
                    parent_node_id=parent_node_id,
                    get_org_dir=get_org_dir,
                )
                persist_node_memory(
                    org_id=org_id,
                    command_id=command_id,
                    node_id=node_id,
                    output=output_text,
                    role=spec.role,
                    parent_node_id=parent_node_id,
                    get_org_dir=get_org_dir,
                )
            except Exception:  # noqa: BLE001 -- belt-and-braces
                _LOGGER.debug(
                    "node artefact persistence raised (org=%s node=%s)",
                    org_id,
                    node_id,
                    exc_info=True,
                )
        elif recovered_from_file and command_id:
            # The deliverable file already lives on disk (the node wrote it),
            # so we DON'T re-persist an artifact; we still write the bounded
            # memory summary so downstream nodes can consult what this node
            # produced.
            try:
                persist_node_memory(
                    org_id=org_id,
                    command_id=command_id,
                    node_id=node_id,
                    output=output_text,
                    role=spec.role,
                    parent_node_id=parent_node_id,
                    get_org_dir=get_org_dir,
                )
            except Exception:  # noqa: BLE001 -- belt-and-braces
                _LOGGER.debug(
                    "recovered-node memory persist raised (org=%s node=%s)",
                    org_id,
                    node_id,
                    exc_info=True,
                )
        elif is_incomplete:
            _LOGGER.info(
                "[quality-gate] node output rejected as deliverable "
                "(org=%s node=%s reason=%s len=%d): not persisted / not delivered",
                org_id,
                node_id,
                quality_reason,
                len(output_text),
            )

        finished_payload: dict[str, Any] = {
            "org_id": org_id,
            "node_id": node_id,
            "command_id": command_id,
            "output_len": len(output_text),
            "chain_id": run_chain_id,
            "artifact_role": artifact_role.value,
        }
        if parent_chain_id:
            finished_payload["parent_chain_id"] = parent_chain_id
        if depth:
            finished_payload["depth"] = depth
        if parent_node_id:
            finished_payload["parent_node_id"] = parent_node_id
        if artifact_path:
            finished_payload["artifact_path"] = artifact_path
        if recovered_from_file:
            finished_payload["recovered_from_file"] = True
        if is_incomplete:
            finished_payload["incomplete"] = True
            finished_payload["quality_reason"] = quality_reason
        if media_quality_failures:
            finished_payload["media_quality_failures"] = media_quality_failures
        if delivery_manifest_payload is not None:
            finished_payload["delivery_manifest"] = delivery_manifest_payload
        if delegated_deliveries:
            finished_payload["delegated_deliveries"] = delegated_deliveries
        await self._emit("agent_run_finished", finished_payload)
        if deterministic_quality_failed and not has_upstream_reviewer:
            await self._emit(
                "agent_run_failed",
                {
                    "org_id": org_id,
                    "node_id": node_id,
                    "command_id": command_id,
                    "chain_id": run_chain_id,
                    "reason": "media_validation_failed",
                    "error": quality_reason,
                    "media_quality_failures": media_quality_failures,
                },
            )
            return self._result(
                "error",
                command_id,
                output=output_text,
                reason="media_validation_failed",
                media_quality_failures=media_quality_failures,
                delivery_manifest=delivery_manifest_payload,
                delegated_deliveries=delegated_deliveries,
                artifact_role=artifact_role.value,
            )
        if delivery_manifest is None:
            return self._result(
                "incomplete",
                command_id,
                output=output_text,
                reason="delivery_manifest_missing",
                delegated_deliveries=delegated_deliveries,
                artifact_role=artifact_role.value,
            )
        if delivery_manifest.state != "complete" and not has_upstream_reviewer:
            return self._result(
                "incomplete",
                command_id,
                output=output_text,
                reason=f"delivery_state_{delivery_manifest.state}",
                delivery_manifest=delivery_manifest_payload,
                delegated_deliveries=delegated_deliveries,
                artifact_role=artifact_role.value,
            )
        return self._result(
            "ok",
            command_id,
            output=output_text,
            media_quality_failures=media_quality_failures,
            delivery_manifest=delivery_manifest_payload,
            delegated_deliveries=delegated_deliveries,
            artifact_role=artifact_role.value,
        )

    async def dispatch_subtask(
        self,
        *,
        org_id: str,
        parent_node_id: str,
        parent_command_id: str | None,
        child_node_id: str,
        child_content: str,
        assignment_id: str | None = None,
        output_slot: str = "default",
        upstream_context: UpstreamContext | Mapping[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> DelegationExecutionResult:
        """Sprint-4 P0-1 -- recurse from a parent node into a child node.

        Wired into :class:`DefaultAgentBuilder` so the per-node agent
        can hand work off when its LLM emits a ``<dispatch>`` block.
        Returns the child's textual output (already extracted by
        :meth:`activate_and_run`) so the parent can splice it into the
        aggregated reply.

        Hard rules:

        * Depth is read from :data:`dispatch_depth_var` (set by the
          parent ``activate_and_run`` call). When
          ``depth + 1 >= MAX_DISPATCH_DEPTH`` we refuse and return a
          short marker string instead of recursing; the parent's
          aggregation surfaces this so the user can see the gate
          fired.
        * Child must exist on the org. Unknown ``child_node_id`` logs
          a warning, emits no events, returns a placeholder. We avoid
          raising because one bad dispatch must not poison sibling
          dispatches in the same parent reply.
        * Empty / blank ``child_content`` is also skipped (a
          ``<dispatch target="x">  </dispatch>`` is almost certainly
          a hallucination the parent didn't mean).
        * ``subtask_assigned`` event + ``delegation_logs/`` JSONL line
          are emitted from here (NOT from
          :meth:`activate_and_run`) so the parent->child edge is
          recorded with the real parent node id, not with
          ``parent_node_id=None`` the entry-dispatch path uses.
        """

        current_depth = max(0, int(dispatch_depth_var.get(0)))
        next_depth = current_depth + 1
        if next_depth >= MAX_DISPATCH_DEPTH:
            _LOGGER.info(
                "dispatch refused: max depth %d reached (parent=%s child=%s)",
                MAX_DISPATCH_DEPTH,
                parent_node_id,
                child_node_id,
            )
            return DelegationExecutionResult.failed(
                reason_code="max_dispatch_depth",
                reason=f"maximum dispatch depth {MAX_DISPATCH_DEPTH} reached",
            )
        target = (child_node_id or "").strip()
        body = (child_content or "").strip()
        if not target or not body:
            return DelegationExecutionResult(
                status=DelegationExecutionStatus.SKIPPED,
                reason_code="empty_dispatch",
            )

        org = self._lookup.get_org(org_id)
        if org is None:
            _LOGGER.warning(
                "dispatch_subtask org missing (org=%s parent=%s child=%s)",
                org_id,
                parent_node_id,
                target,
            )
            return DelegationExecutionResult.failed(reason_code="org_not_found")
        resolve_reference = getattr(org, "resolve_reference", None)
        if callable(resolve_reference):
            resolved, candidates, resolution = resolve_reference(target)
            if resolved is None or resolution not in {"exact_id", "exact_title"}:
                _LOGGER.warning(
                    "dispatch_subtask rejected non-exact node reference "
                    "(org=%s parent=%s child=%s resolution=%s candidates=%s)",
                    org_id,
                    parent_node_id,
                    target,
                    resolution,
                    [getattr(item, "id", "") for item in candidates],
                )
                return DelegationExecutionResult.failed(
                    reason_code=f"node_reference_{resolution}",
                    reason=f"child node reference {target!r} is not exact",
                )
            target = str(getattr(resolved, "id", target))
        else:
            get_node = getattr(org, "get_node", None)
            resolved = get_node(target) if callable(get_node) else None
            if resolved is not None and str(getattr(resolved, "id", target)) != target:
                resolved = None
        if resolved is None:
            _LOGGER.warning(
                "dispatch_subtask child node missing (org=%s parent=%s child=%s)",
                org_id,
                parent_node_id,
                target,
            )
            return DelegationExecutionResult.failed(
                reason_code="node_not_found",
                reason=f"child node {target!r} does not exist",
            )

        # Hard topology guard (audit 2026-06): the agent prompt only offers a
        # node's DIRECT reports (``_available_nodes_for``), but nothing
        # STRUCTURALLY stopped a hallucinated ``<dispatch target=...>`` from
        # jumping to a non-child (越级) or an unrelated node (凭空连线). Enforce
        # adjacency here so dispatch ALWAYS follows a real org edge regardless of
        # what the LLM emits. Fails open only when the org has no readable edge
        # metadata (test stubs / unwired orgs), so existing flows are unaffected.
        allowed_children = _direct_dispatch_children(org, parent_node_id)
        if allowed_children is not None and target not in allowed_children:
            _LOGGER.warning(
                "dispatch refused: `%s` is not a direct report of `%s` "
                "(越级/凭空连线 blocked; allowed=%s)",
                target,
                parent_node_id,
                sorted(allowed_children),
            )
            return DelegationExecutionResult.failed(
                reason_code="not_direct_report",
                reason=f"{target!r} is not a direct report of {parent_node_id!r}",
            )

        preview = body[:200]
        # Chain wiring: the parent's chain (set by its own
        # ``activate_and_run``) becomes this edge's ``parent_chain_id``;
        # the child run gets a freshly-minted chain that we both emit on
        # ``subtask_assigned`` and thread into ``activate_and_run`` so the
        # child's ``agent_run_*`` events carry the SAME chain. This lets
        # the kanban link child task -> parent task by chain id exactly.
        parent_chain_id = current_chain_id_var.get("") or None
        stable_assignment_id = (
            str(assignment_id or "").strip()
            or f"legacy:{parent_command_id or parent_chain_id}|{parent_node_id}|{target}"
        )
        stable_output_slot = str(output_slot or "default").strip() or "default"
        child_chain_id = _new_chain_id()
        subtask_payload: dict[str, Any] = {
            "org_id": org_id,
            "command_id": parent_command_id,
            "node_id": target,
            "parent_node_id": parent_node_id,
            "child_node_id": target,
            "content_preview": preview,
            "depth": next_depth,
            "kind": "child_dispatch",
            "chain_id": child_chain_id,
            "assignment_id": stable_assignment_id,
            "output_slot": stable_output_slot,
        }
        if parent_chain_id:
            subtask_payload["parent_chain_id"] = parent_chain_id
        await self._emit("subtask_assigned", subtask_payload)
        # Mirror Sprint-3's entry-dispatch delegation log shape so
        # downstream log readers don't have to special-case child
        # entries. ``kind`` distinguishes the two so analyses that
        # count "real" recursive hops from "always-1" entry hops have
        # an unambiguous discriminator.
        _append_delegation_log(
            {
                "command_id": parent_command_id,
                "org_id": org_id,
                "parent_node": parent_node_id,
                "child_node": target,
                "node_id": target,
                "kind": "child_dispatch",
                "depth": next_depth,
                "content_preview": preview,
                "chain_id": child_chain_id,
                "parent_chain_id": parent_chain_id,
            }
        )

        # 核心1 + 核心2: run the child, then the DIRECT UPSTREAM (this parent)
        # reviews the deliverable. On reject, re-dispatch the child with the
        # parent's concrete feedback (bounded rework); on exhaustion, escalate.
        attempt = 0
        feedback = ""
        last_output = ""
        rework_max = _rework_max()
        review_enabled = _review_enabled()
        while True:
            child_body = body if not feedback else f"{body}\n\n{feedback}"
            # Each rework run gets a fresh chain + a re-dispatch event so the
            # timeline shows the node genuinely going back to 进行中.
            run_chain = child_chain_id if attempt == 0 else _new_chain_id()
            if attempt > 0:
                await self._emit(
                    "subtask_assigned",
                    {
                        "org_id": org_id,
                        "command_id": parent_command_id,
                        "node_id": target,
                        "parent_node_id": parent_node_id,
                        "child_node_id": target,
                        "content_preview": f"[第{attempt}次重做] " + preview,
                        "depth": next_depth,
                        "kind": "rework_dispatch",
                        "chain_id": run_chain,
                        "rework_attempt": attempt,
                        **({"parent_chain_id": parent_chain_id} if parent_chain_id else {}),
                    },
                )
            result = await self.activate_and_run(
                org_id=org_id,
                node_id=target,
                content=child_body,
                command_id=parent_command_id,
                depth=next_depth,
                parent_node_id=parent_node_id,
                chain_id=run_chain,
                parent_chain_id=parent_chain_id,
                assignment_id=stable_assignment_id,
                output_slot=stable_output_slot,
                upstream_context=upstream_context,
                cancel_event=cancel_event,
            )
            last_output = str(result.get("output") or "")
            # A child that errored / timed out / was skipped is NOT re-reviewed:
            # the supervisor handles those terminal states. Return what we have.
            if result.get("status") != "ok":
                reason = str(result.get("reason") or result.get("status") or "unknown")
                return DelegationExecutionResult.failed(
                    reason_code=reason,
                    reason=reason,
                    output=last_output,
                    delivery_manifest=(
                        result.get("delivery_manifest")
                        if isinstance(result.get("delivery_manifest"), dict)
                        else None
                    ),
                )
            media_failures = result.get("media_quality_failures") or []
            delivery_manifest = result.get("delivery_manifest")
            delivery_state = (
                str(delivery_manifest.get("state") or "")
                if isinstance(delivery_manifest, Mapping)
                else ""
            )
            if delivery_state == "in_progress":
                # A coordinator may have legitimately queued work that has not
                # reached a terminal manifest yet. Re-running its original brief
                # can duplicate expensive side effects, especially when an LLM
                # changes a segment label. Leave it pending for supervisor
                # reconciliation; only an explicit failed quality verdict may
                # consume the rework budget.
                await self._emit(
                    "node_review_deferred",
                    {
                        "org_id": org_id,
                        "command_id": parent_command_id,
                        "node_id": target,
                        "parent_node_id": parent_node_id,
                        "child_node_id": target,
                        "chain_id": run_chain,
                        "reason": "delivery_state_in_progress",
                    },
                )
                return DelegationExecutionResult(
                    status=DelegationExecutionStatus.BLOCKED,
                    output=last_output,
                    reason_code="delivery_state_in_progress",
                    delivery_manifest=(
                        dict(delivery_manifest)
                        if isinstance(delivery_manifest, Mapping)
                        else None
                    ),
                )
            evidence_failures = validate_manifest_runtime_evidence(
                (
                    delivery_manifest_ledger.latest(
                        org_id,
                        str(parent_command_id or ""),
                        target,
                        assignment_id=stable_assignment_id,
                    )
                    if isinstance(delivery_manifest, Mapping)
                    else None
                ),
                artifact_records=artifact_ledger.get(org_id, str(parent_command_id or "")),
                workspace_dir=getattr(
                    self._resolver.resolve(org_id=org_id, node_id=target),
                    "workspace_dir",
                    None,
                ),
            )
            if media_failures or evidence_failures:
                ok = False
                failures = list(media_failures) + evidence_failures
                messages = [str(item.get("message") or "确定性交付证据无效") for item in failures]
                prefix = (
                    "确定性媒体校验未通过："
                    if failures
                    and all(str(item.get("code") or "").startswith("media_") for item in failures)
                    else "确定性交付校验未通过："
                )
                reason = prefix + "；".join(dict.fromkeys(messages))
            elif not isinstance(delivery_manifest, Mapping):
                ok = False
                reason = "下级未提交结构化交付清单，不能通过完成度校验。"
            elif delivery_state and delivery_state != "complete":
                ok = False
                reason = f"下级结构化交付状态为 {delivery_state}，尚未完成。"
            else:
                structured_evidence = {
                    "delivery_manifest": (
                        dict(delivery_manifest) if isinstance(delivery_manifest, Mapping) else None
                    ),
                    "artifact_ledger": structured_upstream_records(
                        org_id=org_id,
                        command_id=str(parent_command_id or ""),
                        source_node_ids=(target,),
                    ),
                }
                ok, reason = await self._parent_review(
                    org_id=org_id,
                    parent_node_id=parent_node_id,
                    child_node_id=target,
                    task=body,
                    output=last_output,
                    structured_evidence=structured_evidence,
                    cancel_event=cancel_event,
                )
            if ok:
                if attempt > 0 or review_enabled:
                    await self._emit(
                        "node_review_passed",
                        {
                            "org_id": org_id,
                            "command_id": parent_command_id,
                            "node_id": target,
                            "parent_node_id": parent_node_id,
                            "child_node_id": target,
                            "chain_id": run_chain,
                            "rework_attempt": attempt,
                            "reason": reason,
                        },
                    )
                return DelegationExecutionResult.completed(
                    last_output,
                    delivery_manifest=(
                        dict(delivery_manifest)
                        if isinstance(delivery_manifest, Mapping)
                        else None
                    ),
                )
            if attempt >= rework_max:
                # 核心2: exhausted the rework budget — escalate to the parent /
                # supervisor (who may swap, downgrade, accept or terminate). We
                # still return the last output so the run converges rather than
                # hanging on a node that can't satisfy its reviewer.
                await self._emit(
                    "node_review_escalated",
                    {
                        "org_id": org_id,
                        "command_id": parent_command_id,
                        "node_id": target,
                        "parent_node_id": parent_node_id,
                        "child_node_id": target,
                        "chain_id": run_chain,
                        "rework_attempts": attempt,
                        "reason": reason,
                    },
                )
                for failure in media_failures:
                    if isinstance(failure, Mapping):
                        record_media_quality_failure(failure)
                return DelegationExecutionResult.failed(
                    reason_code="review_rework_exhausted",
                    reason=reason,
                    output=last_output,
                    delivery_manifest=(
                        dict(delivery_manifest)
                        if isinstance(delivery_manifest, Mapping)
                        else None
                    ),
                )
            attempt += 1
            feedback = (
                f"【直属上级 `{parent_node_id}` 第 {attempt} 次退回意见】{reason}\n"
                "请据此修订并重新产出【完整成果】（不要只写思考过程或中途自述）。"
            )
            await self._emit(
                "node_rework_requested",
                {
                    "org_id": org_id,
                    "command_id": parent_command_id,
                    "node_id": target,
                    "parent_node_id": parent_node_id,
                    "child_node_id": target,
                    "rework_attempt": attempt,
                    "reason": reason,
                },
            )

    async def _parent_review(
        self,
        *,
        org_id: str,
        parent_node_id: str,
        child_node_id: str,
        task: str,
        output: str,
        structured_evidence: Mapping[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[bool, str]:
        """Resolve the parent node's agent and have it review the child output.

        核心1: review is executed BY the connected upstream node (generic over
        any topology), not by a central heuristic. Fail-open: if review is
        disabled, the parent can't be resolved, or the agent doesn't support
        review, we accept (return ``(True, "")``) so convergence is never
        blocked by infrastructure gaps."""
        if not _review_enabled() or not parent_node_id:
            return True, ""
        try:
            spec = self._resolver.resolve(org_id=org_id, node_id=parent_node_id)
            if spec is None:
                return True, ""
            agent = self._cache.get_or_create(spec)
        except Exception:  # noqa: BLE001 -- never block convergence on resolve
            _LOGGER.debug(
                "parent review: resolve/cache failed (org=%s parent=%s)",
                org_id,
                parent_node_id,
                exc_info=True,
            )
            return True, ""
        review = getattr(agent, "review_child_output", None)
        if not callable(review):
            return True, ""
        try:
            return await review(
                child_node_id=child_node_id,
                task=task,
                output=output,
                structured_evidence=structured_evidence,
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "parent review raised (org=%s parent=%s child=%s)",
                org_id,
                parent_node_id,
                child_node_id,
                exc_info=True,
            )
            return True, ""

    async def pause_org_for_quota(self, org_id: str, *, reason: str) -> None:
        """v1 ``_pause_org_for_quota`` parity (78 LOC -> ~15 LOC).

        Emits an event + fires the optional org-paused
        callback (which the runtime wires to
        :meth:`OrgLifecycleManager.pause_org`).
        """

        await self._emit("org_paused_quota", {"org_id": org_id, "reason": reason})
        cb = self._on_org_paused
        if cb is None:
            return
        try:
            cb(org_id, reason)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("on_org_paused callback raised (org=%s)", org_id)

    async def emit_llm_usage(self, usage: Mapping[str, Any]) -> None:
        """v1 ``_emit_llm_usage`` parity -- just publish the event."""

        await self._emit("llm_usage", dict(usage))

    @staticmethod
    def is_quota_auth_error(exc: BaseException) -> bool:
        """Public hook over the private string-sniff (v1 parity name)."""

        return _looks_like_quota_or_auth_error(exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    async def _invoke_agent(
        agent: Any,
        content: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> Any:
        # Accept any agent that exposes ``async run(content) -> Any``.
        # Sprint-13 H1: probe the agent's signature before forwarding
        # ``cancel_event`` so legacy unit-test agents that declare
        # ``async def run(self, content)`` keep working unchanged. The
        # production :class:`._default_agent_builder._BrainBackedNodeAgent`
        # accepts the kwarg and chains it into the brain call.
        run = getattr(agent, "run", None)
        if run is None:
            raise RuntimeError(f"agent {type(agent).__name__} has no .run()")
        if cancel_event is not None and _run_accepts_cancel_event(run):
            return await run(content, cancel_event=cancel_event)
        return await run(content)

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            await self._bus.emit(event, payload)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("event_bus.emit raised (event=%s)", event)

    @staticmethod
    def _result(
        status: str,
        command_id: str | None,
        *,
        output: str | None = None,
        reason: str | None = None,
        media_quality_failures: list[dict[str, Any]] | None = None,
        delivery_manifest: dict[str, Any] | None = None,
        delegated_deliveries: list[dict[str, Any]] | None = None,
        artifact_role: str | None = None,
    ) -> dict[str, Any]:
        result = {
            "status": status,
            "command_id": command_id,
            "output": output,
            "reason": reason,
        }
        if media_quality_failures:
            result["media_quality_failures"] = media_quality_failures
        if delivery_manifest is not None:
            result["delivery_manifest"] = delivery_manifest
        if delegated_deliveries:
            result["delegated_deliveries"] = delegated_deliveries
        if artifact_role:
            result["artifact_role"] = artifact_role
        return result


__all__ = [
    "AgentPipelineExecutor",
]
