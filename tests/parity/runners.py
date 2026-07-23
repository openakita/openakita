"""Adapters that turn a :class:`ParityCase` into a :class:`ParityResult`.

There is **one** runner per (kind, version) pair. Both versions
are pure functions of the case ``inputs`` — the parity test
suite must stay hermetic, so we never touch the network or
spawn LLM clients here.

The current 5 baseline kinds exercise modules where the v1 and
v2 entry points share the same underlying object (because the
agent rewrite is still inside the shim era). That proves the
framework end-to-end. As Phase 2 REWRITE commits 14–18 land,
the v2 callables below will be re-pointed at the rewritten
modules; commit 19 then expands the case set to 30 to satisfy
G2.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from .harness import ParityCase, ParityResult

# ---------------------------------------------------------------------------
# Kind 1 — plan/ask/coordinator mode permission decision parity
# ---------------------------------------------------------------------------


def _permission_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.permission import check_mode_permission

    decision = check_mode_permission(
        tool_name=case.inputs["tool_name"],
        tool_input=case.inputs.get("tool_input", {}),
        mode=case.inputs["mode"],
    )
    return _permission_to_result(decision)


def _permission_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.permission import check_mode_permission

    decision = check_mode_permission(
        tool_name=case.inputs["tool_name"],
        tool_input=case.inputs.get("tool_input", {}),
        mode=case.inputs["mode"],
    )
    return _permission_to_result(decision)


def _permission_to_result(decision) -> ParityResult:
    if decision is None:
        return ParityResult(
            final_message="",
            success=True,
            extras={"behavior": "passthrough", "policy_name": ""},
        )
    return ParityResult(
        final_message=decision.reason or "",
        success=(decision.behavior == "allow"),
        extras={
            "behavior": decision.behavior,
            "policy_name": decision.policy_name,
        },
    )


# ---------------------------------------------------------------------------
# Kind 2 — token-budget tracker parity (state after recording N deltas)
# ---------------------------------------------------------------------------


def _token_budget_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.token_budget import TokenBudget

    return _token_budget_drive(TokenBudget, case)


def _token_budget_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.token_budget import TokenBudget

    return _token_budget_drive(TokenBudget, case)


def _token_budget_drive(token_budget_cls, case: ParityCase) -> ParityResult:
    budget = token_budget_cls(total_limit=case.inputs["total_limit"])
    for delta in case.inputs["deltas"]:
        budget.record(int(delta))
    return ParityResult(
        final_message=f"used={budget.used}",
        success=not budget.is_exceeded,
        extras={
            "used": budget.used,
            "remaining": budget.remaining,
            "is_exceeded": budget.is_exceeded,
            "should_warn": budget.should_warn,
        },
    )


# ---------------------------------------------------------------------------
# Kind 3 — working-facts extract/merge parity
# ---------------------------------------------------------------------------


def _working_facts_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.working_facts import (
        extract_working_facts,
        format_working_facts,
        merge_working_facts,
    )

    return _working_facts_eval(
        extract_working_facts, merge_working_facts, format_working_facts, case
    )


def _working_facts_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.working_facts import (
        extract_working_facts,
        format_working_facts,
        merge_working_facts,
    )

    return _working_facts_eval(
        extract_working_facts, merge_working_facts, format_working_facts, case
    )


def _working_facts_eval(extract_fn, merge_fn, format_fn, case: ParityCase) -> ParityResult:
    initial = case.inputs.get("initial", {})
    message = case.inputs["message"]
    extracted = extract_fn(message, source_turn=case.inputs.get("source_turn", 0))
    merged = merge_fn(initial, extracted)
    rendered = format_fn(merged)
    return ParityResult(
        final_message=rendered,
        success=True,
        extras={
            "extracted_keys": sorted(extracted.keys()),
            "merged_keys": sorted(merged.keys()),
        },
    )


# ---------------------------------------------------------------------------
# Kind 4 — loop-budget guard decision parity (tool-budget exhaustion path)
# ---------------------------------------------------------------------------


def _loop_budget_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.loop_budget import LoopBudgetGuard

    return _loop_budget_drive(LoopBudgetGuard, case)


def _loop_budget_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.loop_budget import LoopBudgetGuard

    return _loop_budget_drive(LoopBudgetGuard, case)


def _loop_budget_drive(guard_cls, case: ParityCase) -> ParityResult:
    guard = guard_cls(max_total_tool_calls=case.inputs["max_total_tool_calls"])
    rounds = case.inputs["rounds"]
    decision = None
    for round_calls in rounds:
        tool_calls = [{"name": name} for name in round_calls]
        decision = guard.record_tool_calls(tool_calls)
        if decision.should_stop:
            break
    final = decision or guard.record_tool_calls([])
    return ParityResult(
        final_message=final.exit_reason,
        success=not final.should_stop,
        extras={
            "total_tool_calls_seen": guard.total_tool_calls_seen,
            "should_stop": final.should_stop,
            "exit_reason": final.exit_reason,
        },
    )


# ---------------------------------------------------------------------------
# Kind 5 — trusted-workspace-path classifier parity
# ---------------------------------------------------------------------------


def _trusted_paths_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.trusted_paths import is_trusted_workspace_path

    return _trusted_paths_eval(is_trusted_workspace_path, case)


def _trusted_paths_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.trusted_paths import is_trusted_workspace_path

    return _trusted_paths_eval(is_trusted_workspace_path, case)


def _trusted_paths_eval(is_trusted, case: ParityCase) -> ParityResult:
    message = case.inputs["message"]
    trusted = bool(is_trusted(message))
    return ParityResult(
        final_message="trusted" if trusted else "untrusted",
        success=True,
        extras={"trusted": trusted},
    )


# ---------------------------------------------------------------------------
# Runner registry
# ---------------------------------------------------------------------------


RunnerFn = Callable[[ParityCase], ParityResult]


# ---------------------------------------------------------------------------
# Kind 10 — primary-agent registry parity (set/get round-trip)
# ---------------------------------------------------------------------------


def _primary_agent_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.core import get_primary_agent, set_primary_agent

    return _primary_agent_eval(get_primary_agent, set_primary_agent, case)


def _primary_agent_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.core import get_primary_agent, set_primary_agent

    return _primary_agent_eval(get_primary_agent, set_primary_agent, case)


def _primary_agent_eval(get_fn, set_fn, case: ParityCase) -> ParityResult:
    before = get_fn()
    sentinel = object()
    try:
        set_fn(sentinel)  # type: ignore[arg-type]
        mid = get_fn()
        ok = mid is sentinel
    finally:
        set_fn(before)
    after = get_fn()
    return ParityResult(
        final_message="ok" if ok and after is before else "leak",
        success=ok and after is before,
        extras={"restored": after is before},
    )


# ---------------------------------------------------------------------------
# Kind 9 — reasoning engine DecisionType enum parity
# ---------------------------------------------------------------------------


def _reasoning_decision_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.reasoning import Decision, DecisionType

    return _reasoning_decision_eval(Decision, DecisionType, case)


def _reasoning_decision_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.reasoning import Decision, DecisionType

    return _reasoning_decision_eval(Decision, DecisionType, case)


def _reasoning_decision_eval(decision_cls, decision_type_cls, case: ParityCase) -> ParityResult:
    name = case.inputs["decision_type"]
    dtype = getattr(decision_type_cls, name)
    dec = decision_cls(
        type=dtype,
        text_content=case.inputs.get("text_content", ""),
        tool_calls=list(case.inputs.get("tool_calls", [])),
        stop_reason=case.inputs.get("stop_reason", ""),
    )
    return ParityResult(
        final_message=dec.text_content,
        success=True,
        tool_sequence=[(tc.get("name", ""), tc.get("input", {})) for tc in dec.tool_calls],
        extras={
            "type_value": dtype.value,
            "type_name": dtype.name,
            "stop_reason": dec.stop_reason,
            "members": sorted([m.name for m in decision_type_cls]),
        },
    )


# ---------------------------------------------------------------------------
# Kind 8 — brain Response dataclass identity / shape parity
# ---------------------------------------------------------------------------


def _brain_response_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.brain import Response

    return _brain_response_eval(Response, case)


def _brain_response_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.brain import Response

    return _brain_response_eval(Response, case)


def _brain_response_eval(response_cls, case: ParityCase) -> ParityResult:
    resp = response_cls(
        content=case.inputs["content"],
        tool_calls=list(case.inputs.get("tool_calls", [])),
        stop_reason=case.inputs.get("stop_reason", ""),
        usage=dict(case.inputs.get("usage", {})),
    )
    return ParityResult(
        final_message=resp.content,
        success=True,
        tool_sequence=[(tc.get("name", ""), tc.get("input", {})) for tc in resp.tool_calls],
        extras={
            "stop_reason": resp.stop_reason,
            "usage_keys": sorted(resp.usage.keys()),
        },
    )


# ---------------------------------------------------------------------------
# Kind 7 — context estimate_tokens parity
# ---------------------------------------------------------------------------


def _context_estimate_v1(case: ParityCase) -> ParityResult:
    from openakita.core.context_utils import estimate_tokens

    return _context_estimate_eval(estimate_tokens, case)


def _context_estimate_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.context import estimate_tokens

    return _context_estimate_eval(estimate_tokens, case)


def _context_estimate_eval(estimate_fn, case: ParityCase) -> ParityResult:
    text = case.inputs["text"]
    n = estimate_fn(text)
    return ParityResult(
        final_message=str(n),
        success=True,
        extras={"tokens": n},
    )


# ---------------------------------------------------------------------------
# Kind 6 — tool executor smart_truncate parity
# ---------------------------------------------------------------------------


def _smart_truncate_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.tools import smart_truncate

    return _smart_truncate_eval(smart_truncate, case)


def _smart_truncate_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.tools import smart_truncate

    return _smart_truncate_eval(smart_truncate, case)


def _smart_truncate_eval(smart_truncate_fn, case: ParityCase) -> ParityResult:
    text = case.inputs["text"]
    limit = case.inputs["limit"]
    head_ratio = case.inputs.get("head_ratio", 0.65)
    truncated, was_truncated = smart_truncate_fn(
        text,
        limit,
        label=case.inputs.get("label", "content"),
        save_full=False,
        head_ratio=head_ratio,
    )
    return ParityResult(
        final_message="truncated" if was_truncated else "kept",
        success=True,
        extras={
            "length": len(truncated),
            "was_truncated": was_truncated,
        },
    )


# ---------------------------------------------------------------------------
# Kind 11 — normalize_confirmation_answer parity
# ---------------------------------------------------------------------------


def _confirm_normalize_v1(case: ParityCase) -> ParityResult:
    from openakita.core.confirmation_state import normalize_confirmation_answer

    return _confirm_normalize_eval(normalize_confirmation_answer, case)


def _confirm_normalize_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.confirmation import normalize_confirmation_answer

    return _confirm_normalize_eval(normalize_confirmation_answer, case)


def _confirm_normalize_eval(normalize_fn, case: ParityCase) -> ParityResult:
    decision = normalize_fn(case.inputs["answer"])
    return ParityResult(
        final_message=decision.value if hasattr(decision, "value") else str(decision),
        success=True,
        extras={"decision_name": getattr(decision, "name", str(decision))},
    )


# ---------------------------------------------------------------------------
# Kind 12 — DomainAllowlist decide() parity
# ---------------------------------------------------------------------------


def _domain_allowlist_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.domain_allowlist import DomainAllowlist

    return _domain_allowlist_eval(DomainAllowlist, case)


def _domain_allowlist_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.domain_allowlist import DomainAllowlist

    return _domain_allowlist_eval(DomainAllowlist, case)


def _domain_allowlist_eval(domain_cls, case: ParityCase) -> ParityResult:
    allowlist = domain_cls()
    conv_id = case.inputs["conversation_id"]
    for host in case.inputs.get("blocked", []):
        allowlist.block(conv_id, host)
    for host in case.inputs.get("allowed", []):
        allowlist.approve(conv_id, host)
    decision = allowlist.decide(conv_id, case.inputs["host"])
    listing = allowlist.list_for(conv_id)
    return ParityResult(
        final_message=decision,
        success=(decision == "allow"),
        extras={
            "decision": decision,
            "blocked": sorted(listing.get("blocked", [])),
            "allowed": sorted(listing.get("allowed", [])),
        },
    )


# ---------------------------------------------------------------------------
# Kind 13 — user_profile alias→key resolution parity
# ---------------------------------------------------------------------------


def _user_profile_resolve_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.user_profile import resolve_profile_key

    return _user_profile_resolve_eval(resolve_profile_key, case)


def _user_profile_resolve_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.user_profile import resolve_profile_key

    return _user_profile_resolve_eval(resolve_profile_key, case)


def _user_profile_resolve_eval(resolve_fn, case: ParityCase) -> ParityResult:
    key = resolve_fn(case.inputs["alias"])
    return ParityResult(
        final_message=key or "",
        success=bool(key),
        extras={"resolved": key, "found": key is not None},
    )


# ---------------------------------------------------------------------------
# Kind 14 — capabilities namespace+id builder parity
# ---------------------------------------------------------------------------


def _capability_id_v1(case: ParityCase) -> ParityResult:
    from openakita.agent.capabilities import build_capability_id, build_namespace, normalize_slug

    return _capability_id_eval(build_capability_id, build_namespace, normalize_slug, case)


def _capability_id_v2(case: ParityCase) -> ParityResult:
    from openakita.agent.capabilities import build_capability_id, build_namespace, normalize_slug

    return _capability_id_eval(build_capability_id, build_namespace, normalize_slug, case)


def _capability_id_eval(build_id_fn, build_ns_fn, normalize_fn, case: ParityCase) -> ParityResult:
    namespace = build_ns_fn(
        case.inputs["origin"],
        plugin_id=case.inputs.get("plugin_id", ""),
        project_id=case.inputs.get("project_id", ""),
    )
    cap_id = build_id_fn(
        case.inputs["kind"],
        case.inputs["local_id"],
        origin=case.inputs["origin"],
        plugin_id=case.inputs.get("plugin_id", ""),
        project_id=case.inputs.get("project_id", ""),
    )
    slug_norm = normalize_fn(case.inputs["local_id"])
    return ParityResult(
        final_message=cap_id,
        success=True,
        extras={
            "namespace": namespace,
            "slug_normalized": slug_norm,
        },
    )


V1_RUNNERS: dict[str, RunnerFn] = {
    "permission_mode": _permission_v1,
    "token_budget": _token_budget_v1,
    "working_facts": _working_facts_v1,
    "loop_budget": _loop_budget_v1,
    "trusted_paths": _trusted_paths_v1,
    "smart_truncate": _smart_truncate_v1,
    "context_estimate_tokens": _context_estimate_v1,
    "brain_response": _brain_response_v1,
    "reasoning_decision": _reasoning_decision_v1,
    "primary_agent": _primary_agent_v1,
    "confirm_normalize": _confirm_normalize_v1,
    "domain_allowlist": _domain_allowlist_v1,
    "user_profile_resolve": _user_profile_resolve_v1,
    "capability_id": _capability_id_v1,
}

V2_RUNNERS: dict[str, RunnerFn] = {
    "permission_mode": _permission_v2,
    "token_budget": _token_budget_v2,
    "working_facts": _working_facts_v2,
    "loop_budget": _loop_budget_v2,
    "trusted_paths": _trusted_paths_v2,
    "smart_truncate": _smart_truncate_v2,
    "context_estimate_tokens": _context_estimate_v2,
    "brain_response": _brain_response_v2,
    "reasoning_decision": _reasoning_decision_v2,
    "primary_agent": _primary_agent_v2,
    "confirm_normalize": _confirm_normalize_v2,
    "domain_allowlist": _domain_allowlist_v2,
    "user_profile_resolve": _user_profile_resolve_v2,
    "capability_id": _capability_id_v2,
}


# ---------------------------------------------------------------------------
# Kind -> (v1_module, v2_module) — used by `_dispatch` to record the
# resolved on-disk source files in every `ParityResult.extras`. Once the
# Phase-2 rewrites land (P-RC-4..6), v1 and v2 modules will live in
# physically different files; until then a v2 module that is a thin
# re-export of its v1 counterpart still resolves to a different `__file__`
# (because `agent/brain.py` etc. are real source files), but the
# *structural* facade check is enforced separately by `test_no_facade.py`.
# ---------------------------------------------------------------------------

KIND_MODULES: dict[str, tuple[str, str]] = {
    "permission_mode": ("openakita.agent.permission", "openakita.agent.permission"),
    "token_budget": ("openakita.agent.token_budget", "openakita.agent.token_budget"),
    "working_facts": ("openakita.agent.working_facts", "openakita.agent.working_facts"),
    "loop_budget": ("openakita.agent.loop_budget", "openakita.agent.loop_budget"),
    "trusted_paths": ("openakita.agent.trusted_paths", "openakita.agent.trusted_paths"),
    "smart_truncate": ("openakita.core._tool_runtime", "openakita.agent.tools"),
    "context_estimate_tokens": ("openakita.core.context_utils", "openakita.agent.context"),
    "brain_response": ("openakita.core._brain_runtime", "openakita.agent.brain"),
    "reasoning_decision": ("openakita.core._reasoning_runtime", "openakita.agent.reasoning"),
    "primary_agent": ("openakita.core._agent_runtime", "openakita.agent.core"),
    "confirm_normalize": ("openakita.core.confirmation_state", "openakita.agent.confirmation"),
    "domain_allowlist": ("openakita.agent.domain_allowlist", "openakita.agent.domain_allowlist"),
    "user_profile_resolve": ("openakita.agent.user_profile", "openakita.agent.user_profile"),
    "capability_id": ("openakita.agent.capabilities", "openakita.agent.capabilities"),
}


def run_v1(case: ParityCase) -> ParityResult:
    return _dispatch(V1_RUNNERS, case)


def run_v2(case: ParityCase) -> ParityResult:
    return _dispatch(V2_RUNNERS, case)


def _dispatch(table: dict[str, RunnerFn], case: ParityCase) -> ParityResult:
    runner = table.get(case.kind)
    if runner is None:
        raise KeyError(f"No runner registered for kind {case.kind!r}")
    result = runner(case)
    pair = KIND_MODULES.get(case.kind)
    if pair is not None:
        v1_name, v2_name = pair
        v1_mod = sys.modules.get(v1_name)
        v2_mod = sys.modules.get(v2_name)
        # `__file__` is None for namespace packages but populated for
        # every ordinary module we care about here.
        if v1_mod is not None and getattr(v1_mod, "__file__", None):
            result.extras["v1_file"] = v1_mod.__file__
        if v2_mod is not None and getattr(v2_mod, "__file__", None):
            result.extras["v2_file"] = v2_mod.__file__
    return result


__all__ = [
    "KIND_MODULES",
    "ParityCase",
    "ParityResult",
    "RunnerFn",
    "V1_RUNNERS",
    "V2_RUNNERS",
    "run_v1",
    "run_v2",
]
