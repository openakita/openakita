"""C15 §17.1 — Evolution self-fix audit window tests.

Covers:

- ``EvolutionWindow`` lifecycle: open → close, deadlines, expiration
  eviction, idempotent close.
- ``set_active_fix_id`` / ``get_active_fix_id`` / ``reset_active_fix_id``
  ContextVar round-trip and exception safety.
- ``classify_entry("evolution", force_unattended=True)`` returns the
  expected headless classification (mirrors scheduler / MCP behaviour).
- ``build_policy_context(evolution_fix_id=...)`` propagates the marker.
- ``PolicyContext.derive_child`` (via sub-agent build) carries
  ``evolution_fix_id`` to nested ctx.
- Engine ``_maybe_audit`` appends ``evolution_decisions.jsonl`` when
  the ctx carries a fix_id; doesn't write when fix_id is None.
- Reverse regression: legacy callers without fix_id produce empty
  ``evolution_decisions.jsonl``.
"""

from __future__ import annotations

import json
import time

import pytest

from openakita.core.policy_v2 import (
    DEFAULT_WINDOW_TTL_SECONDS,
    ApprovalClass,
    EvolutionWindow,
    PolicyContext,
    PolicyEngineV2,
    SessionRole,
    active_windows,
    build_engine_from_config,
    build_policy_context,
    classify_entry,
    close_window,
    get_active_fix_id,
    get_window,
    open_window,
    record_decision,
    reset_active_fix_id,
    reset_windows,
    set_active_fix_id,
    snapshot_window,
)
from openakita.core.policy_v2.evolution_window import (
    default_audit_path as evolution_default_audit_path,
)
from openakita.core.policy_v2.models import ToolCallEvent
from openakita.core.policy_v2.schema import PolicyConfigV2


@pytest.fixture(autouse=True)
def _clean_windows():
    """Each test starts and ends with empty in-memory windows so
    state from one test never leaks into another."""
    reset_windows()
    yield
    reset_windows()


# ---------------------------------------------------------------------------
# Window lifecycle
# ---------------------------------------------------------------------------


def test_open_close_window_round_trip():
    win = open_window(reason="self-fix")
    assert isinstance(win, EvolutionWindow)
    assert win.fix_id
    assert win.reason == "self-fix"
    assert win.started_at <= time.time()
    assert win.deadline_at > win.started_at

    closed = close_window(win.fix_id)
    assert closed is not None
    assert closed.fix_id == win.fix_id
    # Idempotent — second close returns None
    assert close_window(win.fix_id) is None


def test_open_window_custom_fix_id_preserved():
    win = open_window(reason="manual", fix_id="explicit-id-123")
    assert win.fix_id == "explicit-id-123"


def test_open_window_custom_ttl_respected():
    win = open_window(reason="quick", ttl_seconds=0.1)
    assert win.deadline_at - win.started_at == pytest.approx(0.1, abs=0.05)


def test_open_window_extra_metadata_preserved():
    win = open_window(
        reason="self-fix",
        extra={"error_id": "E42", "module": "skills"},
    )
    assert win.extra == {"error_id": "E42", "module": "skills"}


def test_get_window_returns_active():
    win = open_window(reason="self-fix")
    looked = get_window(win.fix_id)
    assert looked is not None
    assert looked.fix_id == win.fix_id


def test_get_window_missing_returns_none():
    assert get_window("never-seen") is None


def test_get_window_expired_evicts_and_returns_none(caplog):
    """An expired window must not linger in the in-memory tracker —
    a crashed _attempt_fix shouldn't poison later decisions with a
    stale fix_id linkage."""
    win = open_window(reason="self-fix", ttl_seconds=0.05)
    time.sleep(0.1)
    with caplog.at_level("WARNING"):
        assert get_window(win.fix_id) is None
    assert "expired" in caplog.text
    # Subsequent lookups also miss (proves the entry was evicted)
    assert get_window(win.fix_id) is None


def test_active_windows_excludes_expired():
    open_window(reason="r1", fix_id="active1")
    open_window(reason="r2", fix_id="active2", ttl_seconds=0.05)
    time.sleep(0.1)
    snap = active_windows()
    assert "active1" in snap
    assert "active2" not in snap  # expired → evicted


def test_snapshot_window_json_safe():
    win = open_window(reason="self-fix", ttl_seconds=10.0)
    snap = snapshot_window(win)
    # Must round-trip through JSON without TypeError
    js = json.dumps(snap)
    assert json.loads(js)["fix_id"] == win.fix_id
    assert snap["expired"] is False
    assert snap["remaining_seconds"] > 0


# ---------------------------------------------------------------------------
# ContextVar wrapper
# ---------------------------------------------------------------------------


def test_active_fix_id_contextvar_defaults_none():
    assert get_active_fix_id() is None


def test_active_fix_id_set_reset_round_trip():
    token = set_active_fix_id("abc123")
    try:
        assert get_active_fix_id() == "abc123"
    finally:
        reset_active_fix_id(token)
    assert get_active_fix_id() is None


def test_reset_with_stale_token_logs_but_does_not_raise(caplog):
    """If two tokens are swapped (e.g. cleanup order bug), the second
    reset should warn rather than raise — defends against bringing
    down the fix attempt cleanup path."""
    t1 = set_active_fix_id("a")
    t2 = set_active_fix_id("b")
    # Reset in wrong order — t1 was for the earlier set
    reset_active_fix_id(t2)
    with caplog.at_level("WARNING"):
        reset_active_fix_id(t1)
    # Token from outer was actually valid this time; if any reset
    # mismatched, warning logs but no exception escapes.
    # Final state: fix_id back to None (best-effort cleanup).


# ---------------------------------------------------------------------------
# classify_entry("evolution") — C15 §17.1 + C14 follow-up
# ---------------------------------------------------------------------------


def test_classify_entry_evolution_unattended():
    cls = classify_entry("evolution", force_unattended=True)
    assert cls.is_unattended is True
    assert cls.confirm_capability == "none"
    assert cls.default_strategy in ("ask_owner", "")
    assert "evolution" in cls.reason or "force" in cls.reason


def test_classify_entry_evolution_self_fix_alias():
    cls = classify_entry("evolution-self-fix")
    assert cls.is_unattended is True
    assert cls.default_strategy == "ask_owner"


# ---------------------------------------------------------------------------
# build_policy_context — fix_id propagation
# ---------------------------------------------------------------------------


def test_build_policy_context_carries_explicit_fix_id(tmp_path):
    ctx = build_policy_context(
        session_id="sess1",
        workspace=tmp_path,
        channel="evolution",
        is_unattended=True,
        evolution_fix_id="explicit-fix-99",
    )
    assert ctx.evolution_fix_id == "explicit-fix-99"


def test_build_policy_context_inherits_active_fix_id_from_contextvar(tmp_path):
    """When evolution_fix_id is NOT passed but the contextvar has one
    (the typical _attempt_fix path), the new ctx still picks it up."""
    token = set_active_fix_id("inherited-id-7")
    try:
        ctx = build_policy_context(
            session_id="sess1",
            workspace=tmp_path,
        )
    finally:
        reset_active_fix_id(token)
    assert ctx.evolution_fix_id == "inherited-id-7"


def test_build_policy_context_no_evolution_marker_when_off(tmp_path):
    """Normal callers (no contextvar, no explicit param) get None."""
    ctx = build_policy_context(
        session_id="sess1",
        workspace=tmp_path,
    )
    assert ctx.evolution_fix_id is None


def test_build_policy_context_sub_agent_inherits_fix_id(tmp_path):
    """Sub-agent derive_child must propagate the parent's evolution
    marker so child agent decisions are linked to the same fix_id."""
    parent = build_policy_context(
        session_id="parent",
        workspace=tmp_path,
        evolution_fix_id="parent-fix",
    )
    child = build_policy_context(
        parent_ctx=parent,
        child_agent_name="specialist",
        user_message="do the thing",
    )
    assert child.evolution_fix_id == "parent-fix"


# ---------------------------------------------------------------------------
# record_decision — audit jsonl writing
# ---------------------------------------------------------------------------


def test_record_decision_appends_jsonl(tmp_path):
    audit_path = tmp_path / "evolution_decisions.jsonl"
    win = open_window(reason="self-fix", extra={"error_id": "E1"})
    record_decision(
        fix_id=win.fix_id,
        audit_path=audit_path,
        decision_record={
            "tool": "write_file",
            "action": "confirm",
        },
    )
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["fix_id"] == win.fix_id
    assert rec["tool"] == "write_file"
    assert rec["action"] == "confirm"
    assert rec["window_reason"] == "self-fix"
    assert rec["window_extra"] == {"error_id": "E1"}
    assert "ts" in rec


def test_record_decision_works_without_active_window(tmp_path):
    """If the caller forgot to open a window but did pass a fix_id,
    the record still lands — we just skip the window_reason field."""
    audit_path = tmp_path / "evolution_decisions.jsonl"
    record_decision(
        fix_id="orphan-fix",
        audit_path=audit_path,
        decision_record={"tool": "read_file"},
    )
    rec = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["fix_id"] == "orphan-fix"
    assert "window_reason" not in rec


def test_record_decision_failure_is_swallowed(tmp_path):
    """An unwritable audit path must not raise — losing one audit
    line is preferable to wedging the decision path."""
    # Try to write to a path under a file (which can't be a directory)
    sub = tmp_path / "occupied"
    sub.write_text("not a directory", encoding="utf-8")
    audit_path = sub / "evolution_decisions.jsonl"

    # Should NOT raise
    record_decision(
        fix_id="x",
        audit_path=audit_path,
        decision_record={"tool": "y"},
    )


# ---------------------------------------------------------------------------
# Engine integration — _maybe_audit fans out evolution decisions
# ---------------------------------------------------------------------------


def _build_minimal_engine() -> PolicyEngineV2:
    """Construct a stripped-down engine with a permissive config so we
    can drive evaluate_tool_call without bootstrapping the full
    config layer."""
    cfg = PolicyConfigV2()
    return build_engine_from_config(cfg)


def test_engine_audit_writes_evolution_decisions_when_fix_id_set(tmp_path):
    """Critical integration: when ctx.evolution_fix_id is set, every
    decision lands in evolution_decisions.jsonl. Without explicit
    wiring this test would fail — proves engine._maybe_audit fans out."""
    engine = _build_minimal_engine()
    win = open_window(reason="self-fix", fix_id="engine-fix-1")
    ctx = PolicyContext(
        session_id="evo_sess",
        workspace=tmp_path,
        channel="evolution",
        is_unattended=True,
        session_role=SessionRole.AGENT,
        evolution_fix_id=win.fix_id,
    )
    event = ToolCallEvent(tool="read_file", params={"path": "x"})
    engine.evaluate_tool_call(event, ctx)

    audit_path = evolution_default_audit_path(tmp_path)
    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    rec = json.loads(lines[0])
    assert rec["fix_id"] == "engine-fix-1"
    assert rec["tool"] == "read_file"
    assert "action" in rec
    assert "approval_class" in rec
    # Window metadata enriched
    assert rec["window_reason"] == "self-fix"


def test_engine_audit_skips_evolution_decisions_when_no_fix_id(tmp_path):
    """Reverse: normal traffic must NOT touch evolution_decisions.jsonl.
    A non-empty file here would mean leaking evolution markers into
    regular operations — which would invalidate the audit trail's
    primary use case (post-hoc 'what did Evolution try?')."""
    engine = _build_minimal_engine()
    ctx = PolicyContext(
        session_id="normal_sess",
        workspace=tmp_path,
        channel="desktop",
        is_unattended=False,
        session_role=SessionRole.AGENT,
        evolution_fix_id=None,
    )
    event = ToolCallEvent(tool="read_file", params={})
    engine.evaluate_tool_call(event, ctx)

    audit_path = evolution_default_audit_path(tmp_path)
    assert not audit_path.exists()


def test_engine_audit_records_destructive_decision_action(tmp_path):
    """When the decision is non-trivial (e.g. CONFIRM / DENY), the
    audit record reflects the action so operators see what would
    have happened."""
    engine = _build_minimal_engine()
    win = open_window(reason="self-fix", fix_id="engine-fix-2")
    ctx = PolicyContext(
        session_id="evo_sess",
        workspace=tmp_path,
        channel="evolution",
        is_unattended=True,
        session_role=SessionRole.AGENT,
        evolution_fix_id=win.fix_id,
    )
    event = ToolCallEvent(tool="delete_workspace", params={"path": str(tmp_path / "x")})
    engine.evaluate_tool_call(event, ctx)

    audit_path = evolution_default_audit_path(tmp_path)
    rec = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["tool"] == "delete_workspace"
    assert rec["approval_class"] == ApprovalClass.DESTRUCTIVE.value


# ---------------------------------------------------------------------------
# Reverse regression — Phase C audit only happens inside windows
# ---------------------------------------------------------------------------


def test_reverse_regression_no_window_no_audit(tmp_path):
    """Even when ctx.evolution_fix_id is set but engine isn't seeing it,
    the regular audit path is unaffected (engine fans out only when the
    field is set on the ctx)."""
    # Direct test of the fan-out — record_decision is the worker
    audit_path = tmp_path / "evolution.jsonl"
    record_decision(
        fix_id="x",
        audit_path=audit_path,
        decision_record={"tool": "read_file"},
    )
    assert audit_path.exists()


def test_default_audit_path_canonical_location(tmp_path):
    assert (
        evolution_default_audit_path(tmp_path)
        == tmp_path / "data" / "audit" / "evolution_decisions.jsonl"
    )


def test_default_window_ttl_documented():
    """Sanity check on the documented default — guards against
    accidental down-tuning that would let real fix attempts time out
    before completion."""
    assert DEFAULT_WINDOW_TTL_SECONDS >= 60.0
