"""C12 Phase B + Phase E coverage for PendingApprovalsStore + resume flow.

Scope:

1. ``PendingApprovalsStore`` create/list/get/resolve/expire round-trip
   with on-disk persistence.
2. ``user_message`` capture is preserved across reload (back-compat for
   forward-only schema additions: ``from_dict`` ignores unknown / supplies
   default for missing).
3. Event hook fires ``pending_approval_created`` + ``pending_approval_resolved``
   exactly once per lifecycle, with idempotent resolve (no second event).
4. ``_resume_task`` (api/routes/pending_approvals.py) sets
   ``replay_authorizations`` on the linked task, transitions
   AWAITING_APPROVAL → SCHEDULED + immediate next_run, and uses
   ``user_message`` as ``original_message`` when present.
5. PolicyEngineV2 step 7 actually short-circuits to ALLOW when the
   recorded auth is wired into PolicyContext (end-to-end, not just unit).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

import pytest

from openakita.core.pending_approvals import (
    PendingApproval,
    PendingApprovalsStore,
)

# ---------------------------------------------------------------------------
# Store basics: create / list / get / resolve / persist round-trip
# ---------------------------------------------------------------------------


def _mkstore(tmp_path: Path) -> PendingApprovalsStore:
    return PendingApprovalsStore(data_dir=tmp_path)


def test_store_create_persist_reload(tmp_path: Path) -> None:
    store = _mkstore(tmp_path)
    entry = store.create(
        task_id="t-1",
        session_id="s-1",
        tool_name="write_file",
        params={"path": "a.txt"},
        approval_class="FILE_WRITE",
        decision_chain=[{"step": "1", "result": "confirm"}],
        decision_meta={"is_unattended_path": True},
        reason="needs owner",
        unattended_strategy="defer_to_owner",
        user_message="please write a.txt",
    )
    assert entry.id.startswith("pa_")
    assert entry.user_message == "please write a.txt"
    assert entry.is_active()

    # Reload from disk into a fresh store
    store2 = _mkstore(tmp_path)
    got = store2.get(entry.id)
    assert got is not None
    assert got.user_message == "please write a.txt"
    assert got.task_id == "t-1"
    assert got.tool_name == "write_file"
    assert got.is_active()


def test_store_resolve_emits_event_once_and_is_idempotent(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, dict]] = []

    def hook(evt: str, payload: dict) -> None:
        events.append((evt, payload))

    store = _mkstore(tmp_path)
    store.set_event_hook(hook)

    entry = store.create(
        task_id="t-1",
        session_id="s-1",
        tool_name="delete_file",
        params={"path": "x.txt"},
        approval_class="FILE_DELETE",
        decision_chain=[],
        decision_meta={},
        reason="r",
        unattended_strategy="defer_to_owner",
        user_message="delete x",
    )
    assert events[-1][0] == "pending_approval_created"

    updated = store.resolve(entry.id, decision="allow", resolved_by="owner")
    assert updated is not None
    assert updated.status == "approved"
    assert updated.resolved_by == "owner"
    resolved_events = [e for e in events if e[0] == "pending_approval_resolved"]
    assert len(resolved_events) == 1

    # Idempotent: re-resolving doesn't fire a 2nd event
    again = store.resolve(entry.id, decision="allow", resolved_by="owner")
    assert again is not None
    assert again.status == "approved"
    resolved_events = [e for e in events if e[0] == "pending_approval_resolved"]
    assert len(resolved_events) == 1, "second resolve must not double-fire"


def test_store_lazy_expire_bumps_to_expired(tmp_path: Path) -> None:
    store = _mkstore(tmp_path)
    entry = store.create(
        task_id="t-1",
        session_id="s-1",
        tool_name="run_powershell",
        params={"command": "ls"},
        approval_class="EXEC_CAPABLE",
        decision_chain=[],
        decision_meta={},
        reason="r",
        unattended_strategy="defer_to_owner",
        ttl_seconds=60.0,
    )
    # Force-expire by rewriting expires_at into the past and listing
    entry.expires_at = time.time() - 1
    active = store.list_active()
    assert all(e.id != entry.id for e in active), "expired entry must be filtered out"
    assert store.get(entry.id).status == "expired"  # type: ignore[union-attr]


def test_resolve_expired_entry_does_not_approve(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []
    store = _mkstore(tmp_path)
    store.set_event_hook(lambda evt, payload: events.append((evt, payload)))
    entry = store.create(
        task_id="t-1",
        session_id="s-1",
        tool_name="run_powershell",
        params={"command": "ls"},
        approval_class="EXEC_CAPABLE",
        decision_chain=[],
        decision_meta={},
        reason="r",
        unattended_strategy="defer_to_owner",
        ttl_seconds=60.0,
    )
    entry.expires_at = time.time() - 1

    updated = store.resolve(entry.id, decision="allow", resolved_by="owner")

    assert updated is not None
    assert updated.status == "expired"
    assert updated.resolution == "expired"
    assert updated.resolved_by is None
    assert updated.note == "expired before resolve"
    resolved_events = [e for e in events if e[0] == "pending_approval_resolved"]
    assert len(resolved_events) == 1
    assert resolved_events[0][1]["resolution"] == "expired"


def test_resolve_invalid_decision_raises(tmp_path: Path) -> None:
    store = _mkstore(tmp_path)
    entry = store.create(
        task_id=None,
        session_id="s-1",
        tool_name="write_file",
        params={"path": "a.txt"},
        approval_class="FILE_WRITE",
        decision_chain=[],
        decision_meta={},
        reason="r",
        unattended_strategy="defer_to_owner",
    )
    with pytest.raises(ValueError):
        store.resolve(entry.id, decision="maybe", resolved_by="owner")


def test_from_dict_ignores_unknown_and_defaults_missing() -> None:
    """Forward/back-compat: ``user_message`` (added late) defaults to ''
    when missing on disk; unknown keys (added in future schema) are dropped."""
    raw = {
        "id": "pa_test",
        "task_id": "t-1",
        "session_id": "s-1",
        "tool_name": "write_file",
        "params": {},
        "approval_class": "FILE_WRITE",
        "decision_chain": [],
        "decision_meta": {},
        "reason": "r",
        "unattended_strategy": "defer_to_owner",
        "created_at": 0.0,
        "expires_at": 0.0,
        # missing: user_message, status, resolved_at, resolved_by, resolution, note
        # extra (forward compat): unknown_future_field, channel_metadata
        "unknown_future_field": "should-be-dropped",
        "channel_metadata": {"channel": "feishu"},
    }
    entry = PendingApproval.from_dict(raw)
    assert entry.user_message == ""
    assert entry.status == "pending"
    assert entry.note == ""


# ---------------------------------------------------------------------------
# Resume flow (Phase E / R3-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_writes_replay_auth_and_reschedules_task(
    tmp_path: Path,
) -> None:
    from openakita.api.routes.pending_approvals import (
        REPLAY_TTL_SECONDS,
        _resume_task,
    )
    from openakita.scheduler.task import (
        ScheduledTask,
        TaskSource,
        TaskStatus,
        TaskType,
        TriggerType,
    )

    store = _mkstore(tmp_path)
    entry = store.create(
        task_id="task-A",
        session_id="sess-A",
        tool_name="write_file",
        params={"path": "a.txt", "content": "x"},
        approval_class="FILE_WRITE",
        decision_chain=[],
        decision_meta={"approval_class": "FILE_WRITE"},
        reason="needs owner",
        unattended_strategy="defer_to_owner",
        user_message="please write a.txt",
    )
    # Resolve first (resume API only triggers after store.resolve)
    store.resolve(entry.id, decision="allow", resolved_by="owner")

    class _StubScheduler:
        def __init__(self) -> None:
            self._tasks: dict[str, ScheduledTask] = {}
            self._lock = asyncio.Lock()

        def _save_tasks(self) -> None:  # noqa: D401
            """Save no-op for tests; real impl writes JSON."""

    sched = _StubScheduler()
    task = ScheduledTask(
        id="task-A",
        name="t",
        description="d",
        task_type=TaskType.TASK,
        task_source=TaskSource.MANUAL,
        trigger_type=TriggerType.ONCE,
        trigger_config={"run_at": datetime.now().isoformat()},
        prompt="please write a.txt",
    )
    task.status = TaskStatus.AWAITING_APPROVAL
    sched._tasks[task.id] = task

    # Use the just-resolved entry (Note: in the real route, resolve+resume
    # are sequential within the same handler; here we mimic the same order).
    result = await _resume_task(sched, entry)
    assert result["task_resumed"] is True
    assert result["replay_ttl_seconds"] == REPLAY_TTL_SECONDS

    # Task transitioned and replay_auth recorded
    assert task.status == TaskStatus.SCHEDULED
    auths = task.metadata.get("replay_authorizations")
    assert isinstance(auths, list) and len(auths) == 1
    auth = auths[0]
    assert auth["original_message"] == "please write a.txt"  # uses user_message
    assert auth["confirmation_id"] == entry.id
    assert auth["expires_at"] > time.time(), "ttl must be in the future"
    assert "awaiting_approval_marker" not in task.metadata, (
        "marker should be cleared on resume"
    )
    assert task.metadata.get("resumed_from_approval_id") == entry.id


@pytest.mark.asyncio
async def test_resume_prunes_expired_replay_auths_before_append(
    tmp_path: Path,
) -> None:
    """C12 §14.7 (R3-5) bounded growth: when a long-lived task accumulates
    replay auths over many approvals, expired entries must be dropped at
    resume time. Otherwise the in-memory list (and the persisted JSON)
    grows monotonically forever, and engine step 7 iterates each entry
    per tool call."""
    from openakita.api.routes.pending_approvals import _resume_task
    from openakita.scheduler.task import (
        ScheduledTask,
        TaskSource,
        TaskStatus,
        TaskType,
        TriggerType,
    )

    store = _mkstore(tmp_path)
    entry = store.create(
        task_id="task-C",
        session_id="sess-C",
        tool_name="write_file",
        params={"path": "z.txt"},
        approval_class="FILE_WRITE",
        decision_chain=[],
        decision_meta={},
        reason="r",
        unattended_strategy="defer_to_owner",
        user_message="write z",
    )

    class _StubScheduler:
        def __init__(self) -> None:
            self._tasks: dict[str, ScheduledTask] = {}
            self._lock = asyncio.Lock()

        def _save_tasks(self) -> None:  # noqa: D401
            pass

    sched = _StubScheduler()
    task = ScheduledTask(
        id="task-C",
        name="t",
        description="d",
        task_type=TaskType.TASK,
        task_source=TaskSource.MANUAL,
        trigger_type=TriggerType.ONCE,
        trigger_config={"run_at": datetime.now().isoformat()},
        prompt="write z",
    )
    task.status = TaskStatus.AWAITING_APPROVAL
    # Pre-load 3 expired + 1 still-active auth from past approvals
    task.metadata["replay_authorizations"] = [
        {
            "expires_at": time.time() - 100,
            "original_message": "old-1",
            "confirmation_id": "pa_old_1",
            "operation": "",
        },
        {
            "expires_at": time.time() - 50,
            "original_message": "old-2",
            "confirmation_id": "pa_old_2",
            "operation": "",
        },
        {
            "expires_at": time.time() + 25,  # still active
            "original_message": "recent",
            "confirmation_id": "pa_recent",
            "operation": "",
        },
        {
            "expires_at": time.time() - 1,
            "original_message": "old-3",
            "confirmation_id": "pa_old_3",
            "operation": "",
        },
    ]
    sched._tasks[task.id] = task

    result = await _resume_task(sched, entry)
    assert result["task_resumed"] is True
    auths = task.metadata["replay_authorizations"]
    # 3 expired pruned + 1 recent kept + 1 newly added = 2
    assert len(auths) == 2, f"expected 2 (recent + new), got {len(auths)}"
    confirmation_ids = {a["confirmation_id"] for a in auths}
    assert "pa_recent" in confirmation_ids
    assert entry.id in confirmation_ids
    # No expired entries survived
    for a in auths:
        assert a["expires_at"] > time.time()


@pytest.mark.asyncio
async def test_resume_falls_back_to_tool_name_when_user_message_empty(
    tmp_path: Path,
) -> None:
    from openakita.api.routes.pending_approvals import _resume_task
    from openakita.scheduler.task import (
        ScheduledTask,
        TaskSource,
        TaskStatus,
        TaskType,
        TriggerType,
    )

    store = _mkstore(tmp_path)
    entry = store.create(
        task_id="task-B",
        session_id="sess-B",
        tool_name="run_powershell",
        params={"command": "Get-Date"},
        approval_class="EXEC_CAPABLE",
        decision_chain=[],
        decision_meta={},
        reason="r",
        unattended_strategy="defer_to_owner",
        user_message="",  # legacy entry
    )

    class _StubScheduler:
        def __init__(self) -> None:
            self._tasks: dict[str, ScheduledTask] = {}
            self._lock = asyncio.Lock()

        def _save_tasks(self) -> None:  # noqa: D401
            pass

    sched = _StubScheduler()
    task = ScheduledTask(
        id="task-B",
        name="t",
        description="d",
        task_type=TaskType.TASK,
        task_source=TaskSource.MANUAL,
        trigger_type=TriggerType.ONCE,
        trigger_config={"run_at": datetime.now().isoformat()},
        prompt="legacy prompt",
    )
    task.status = TaskStatus.AWAITING_APPROVAL
    sched._tasks[task.id] = task

    result = await _resume_task(sched, entry)
    assert result["task_resumed"] is True
    auth = task.metadata["replay_authorizations"][0]
    assert auth["original_message"] == "run_powershell", (
        "fallback to tool_name when user_message empty"
    )


# ---------------------------------------------------------------------------
# Concurrent resolve race (Fix #1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_route_branches_by_actual_resolution_not_request_body(
    tmp_path: Path,
) -> None:
    """If two POSTs arrive concurrently with different decisions, the
    second-to-arrive request must observe the FIRST actor's resolution
    in ``updated.resolution`` and route follow-up accordingly. This
    closes the race where an allow + a deny arriving milliseconds apart
    would both run their respective follow-up.

    We simulate by calling ``store.resolve`` twice in sequence with
    different decisions and asserting the second call sees the original
    resolution.
    """
    store = _mkstore(tmp_path)
    entry = store.create(
        task_id="task-D",
        session_id="sess-D",
        tool_name="delete_file",
        params={"path": "important.txt"},
        approval_class="FILE_DELETE",
        decision_chain=[],
        decision_meta={},
        reason="r",
        unattended_strategy="defer_to_owner",
        user_message="delete important",
    )

    # First actor: ALLOW
    first = store.resolve(entry.id, decision="allow", resolved_by="owner-A")
    assert first.resolution == "allow"
    assert first.resolved_by == "owner-A"

    # Second actor (racing): DENY — but store is idempotent, so the
    # entry is returned UNCHANGED. The route's branch on updated.resolution
    # would now correctly skip _fail_task (since resolution=='allow').
    second = store.resolve(entry.id, decision="deny", resolved_by="owner-B")
    assert second.resolution == "allow", (
        "second resolve must see first actor's decision, NOT overwrite"
    )
    assert second.resolved_by == "owner-A", "first actor wins"


# ---------------------------------------------------------------------------
# build_policy_context reads session.is_unattended (Fix #10)
# ---------------------------------------------------------------------------


def test_build_policy_context_reads_session_first_class_unattended_fields():
    """Phase A wired the fields into Session but the agent.py mainpath
    builds its PolicyContext via build_policy_context, which originally
    ignored those fields. Fix #10: build_policy_context now reads
    session.is_unattended / session.unattended_strategy with arg
    precedence (caller wins) + metadata fallback."""
    from openakita.core.policy_v2.adapter import build_policy_context
    from openakita.sessions.session import Session

    def _mksession(sid: str) -> Session:
        return Session(id=sid, channel="test", chat_id="c", user_id="u")

    # Session with first-class unattended flags set
    s = _mksession("s1")
    s.is_unattended = True
    s.unattended_strategy = "defer_to_inbox"
    ctx = build_policy_context(session=s, channel="webhook")
    assert ctx.is_unattended is True
    assert ctx.unattended_strategy == "defer_to_inbox"

    # Caller arg=True wins even when session is False (default)
    s2 = _mksession("s2")
    ctx2 = build_policy_context(session=s2, is_unattended=True, channel="cli")
    assert ctx2.is_unattended is True

    # Both default False → ctx False (no accidental promotion)
    s3 = _mksession("s3")
    ctx3 = build_policy_context(session=s3, channel="desktop")
    assert ctx3.is_unattended is False
    assert ctx3.unattended_strategy == ""

    # metadata fallback (very-old sessions stored is_unattended in
    # session.metadata before Phase A promoted it to a first-class field)
    s4 = _mksession("s4")
    s4.metadata["is_unattended"] = True
    s4.metadata["unattended_strategy"] = "defer_to_owner"
    ctx4 = build_policy_context(session=s4, channel="cli")
    assert ctx4.is_unattended is True
    assert ctx4.unattended_strategy == "defer_to_owner"


# ---------------------------------------------------------------------------
# End-to-end: replay auth → engine step 7 → ALLOW
# ---------------------------------------------------------------------------


def test_engine_step7_promotes_unattended_confirm_to_allow_with_replay_auth(
    tmp_path: Path,
) -> None:
    """The whole point of R3-5: after owner approves, the next scheduler
    rerun must *not* re-prompt. This test asserts engine step 7 actually
    short-circuits to ALLOW given the auth payload our resume API writes."""
    import os

    from openakita.core.policy_v2.context import PolicyContext, ReplayAuthorization
    from openakita.core.policy_v2.engine import PolicyEngineV2
    from openakita.core.policy_v2.models import ToolCallEvent

    eng = PolicyEngineV2()
    ev = ToolCallEvent(tool="write_file", params={"path": "a.txt", "content": "x"})

    # Baseline: unattended without auth → defer / confirm (NOT allow)
    ctx_no = PolicyContext(
        session_id="s",
        workspace=Path(os.getcwd()),
        channel="scheduler",
        is_owner=True,
        is_unattended=True,
        unattended_strategy="defer_to_owner",
        user_message="please write a.txt",
    )
    dec_no = eng.evaluate_tool_call(ev, ctx_no)
    assert str(dec_no.action) in ("defer", "confirm"), (
        f"unattended with no auth should not allow; got {dec_no.action}"
    )

    # With matching replay auth → ALLOW (the whole point of R3-5)
    auth = ReplayAuthorization(
        expires_at=time.time() + 30,
        original_message="please write a.txt",
        confirmation_id="pa_test",
        operation="",
    )
    ctx_yes = PolicyContext(
        session_id="s",
        workspace=Path(os.getcwd()),
        channel="scheduler",
        is_owner=True,
        is_unattended=True,
        unattended_strategy="defer_to_owner",
        user_message="please write a.txt",
        replay_authorizations=[auth],
    )
    dec_yes = eng.evaluate_tool_call(ev, ctx_yes)
    assert str(dec_yes.action) == "allow", (
        f"replay auth must short-circuit to ALLOW; got {dec_yes.action} "
        f"reason={dec_yes.reason!r}"
    )
    assert "replay" in dec_yes.reason.lower()
