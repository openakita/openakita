"""Cancel cleanup + AbortScope integration tests (plan: v1.28, S3.4).

Covers v1.28 S3 wiring landed on top of v1.27.15:

* ``AbortScope`` parent/child fan-out — root cancel cascades to all live
  children (tool scopes, sub-agent scopes, grandchildren) and a child
  spawned *after* root cancel is born already aborted.
* ``TaskState.cancel_event`` property delegates to ``abort_root.event``
  so 11+ legacy reader sites keep working.  Setter swaps the underlying
  event (mirrors ``reasoning_engine`` retry path).
* ``cancel_cleanup.find_orphan_tool_uses`` and
  ``synthesize_tool_results_for_orphans`` repair the canonical
  "tool_use without tool_result" sequence that Anthropic API 400s on.
* The synthesize helper places the synthetic ``user(tool_result)``
  block right *after* the orphan assistant message — not at the tail —
  so the common cancel-then-"继续" flow ends up well-formed.
* Persistence helpers (``persist_working_messages`` /
  ``load_persisted_working_messages``) round-trip and respect TTL.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from openakita.core.abort_scope import AbortScope, current_abort_scope, root_scope
from openakita.core.agent_state import TaskState
from openakita.core.cancel_cleanup import (
    DEFAULT_INTERRUPT_TEXT,
    cleanup_expired_working_messages,
    clear_persisted_working_messages,
    find_orphan_tool_uses,
    load_persisted_working_messages,
    persist_working_messages,
    synthesize_tool_results_for_orphans,
)


# ── AbortScope tree behaviour ────────────────────────────────────────


class TestAbortScopeTree:
    """``AbortScope`` cancel fan-out semantics."""

    def test_root_abort_cascades_to_existing_children(self) -> None:
        root = root_scope()
        tool = root.create_child("tool:shell")
        sub = root.create_child("subagent:1")
        inner = tool.create_child("tool:shell.subprocess")

        assert not any(s.is_aborted() for s in (root, tool, sub, inner))
        root.abort("user cancel")

        assert all(s.is_aborted() for s in (root, tool, sub, inner))
        # reason propagates down by default
        assert tool.reason == "user cancel"
        assert inner.reason == "user cancel"
        # provenance recorded
        assert inner._aborted_by == tool.name
        assert tool._aborted_by == root.name

    def test_child_spawned_after_root_abort_is_born_aborted(self) -> None:
        root = root_scope()
        root.abort("preempt")
        late = root.create_child("tool:read_file")
        assert late.is_aborted(), (
            "create_child after root abort must produce an already-aborted scope"
        )
        assert late.reason == "preempt"

    def test_child_abort_does_not_bubble_up(self) -> None:
        root = root_scope()
        child = root.create_child("tool:browser")
        child.abort("skip")
        assert child.is_aborted()
        assert not root.is_aborted(), "child cancel must not bubble to root"

    def test_abort_is_idempotent_preserves_first_reason(self) -> None:
        root = root_scope()
        root.abort("first")
        root.abort("second")
        assert root.reason == "first", "second abort must not overwrite reason"

    def test_remove_child_keeps_parent_consistent(self) -> None:
        root = root_scope()
        c1 = root.create_child("a")
        c2 = root.create_child("b")
        root.remove_child(c1)
        assert c1 not in root.children
        assert c2 in root.children
        # Removing twice is a no-op
        root.remove_child(c1)


# ── TaskState ↔ AbortScope back-compat ────────────────────────────────


class TestTaskStateCancelEventBackCompat:
    """The 11+ legacy reader sites (``task.cancel_event.wait()``,
    ``.is_set()``) must keep working unchanged."""

    def test_cancel_event_is_abort_root_event_property(self) -> None:
        t = TaskState(task_id="t1")
        # the property must literally return the same object as
        # ``abort_root.event`` (so existing ``ensure_future(state.cancel_event.wait())``
        # races stay on the right event).
        assert t.cancel_event is t.abort_root.event

    def test_task_cancel_sets_root_event_and_fans_out(self) -> None:
        t = TaskState(task_id="t2")
        tool_scope = t.abort_root.create_child("tool:shell")
        t.cancel("stop")
        assert t.cancel_event.is_set()
        assert tool_scope.is_aborted()
        assert tool_scope.reason == "stop"

    def test_legacy_reset_swaps_underlying_event(self) -> None:
        """`reasoning_engine.py` LLM retry path does
        ``state.cancel_event = asyncio.Event()``.  After the swap the
        property must return the new event and the old children keep
        their own (still-aborted) events untouched."""
        t = TaskState(task_id="t3")
        child = t.abort_root.create_child("tool:browser")
        t.cancel("first")
        assert child.is_aborted()

        fresh = asyncio.Event()
        t.cancel_event = fresh
        assert t.cancel_event is fresh
        assert not t.cancel_event.is_set()
        # critical: child keeps its aborted state — we don't un-cancel
        # an in-flight tool just because the LLM retried.
        assert child.is_aborted()


# ── Orphan tool_use repair ────────────────────────────────────────────


class TestOrphanToolUseRepair:
    """``synthesize_tool_results_for_orphans`` semantics."""

    def test_no_orphans_returns_zero_and_does_not_mutate(self) -> None:
        msgs = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "shell", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                ],
            },
        ]
        snap = list(msgs)
        assert synthesize_tool_results_for_orphans(msgs) == 0
        assert msgs == snap

    def test_synthetic_inserted_right_after_orphan_assistant(self) -> None:
        """The critical placement test: in the cancel + "继续" scenario the
        new user message must end up **after** the synthetic tool_result,
        not interleaved."""
        msgs = [
            {"role": "user", "content": "原任务"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "ok let me try"},
                    {"type": "tool_use", "id": "orphan-1", "name": "shell", "input": {}},
                ],
            },
            # user-typed "continue" *after* cancel
            {"role": "user", "content": "继续"},
        ]
        n = synthesize_tool_results_for_orphans(msgs)
        assert n == 1
        assert len(msgs) == 4
        # Order must be: user, assistant(orphan), synthetic user(tool_result), user("继续")
        assert msgs[2].get("_synthetic") is True
        assert msgs[2]["content"][0]["tool_use_id"] == "orphan-1"
        assert msgs[2]["content"][0]["is_error"] is True
        assert msgs[3]["content"] == "继续"
        # Post-repair must be fully paired
        assert find_orphan_tool_uses(msgs) == []

    def test_synthetic_block_uses_interrupt_text(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t", "name": "shell", "input": {}}],
            },
        ]
        synthesize_tool_results_for_orphans(msgs)
        assert msgs[-1]["content"][0]["content"] == DEFAULT_INTERRUPT_TEXT

    def test_multiple_assistant_messages_each_get_their_own_block(self) -> None:
        msgs = [
            {"role": "user", "content": "A"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "a1", "name": "x", "input": {}},
                    {"type": "tool_use", "id": "a2", "name": "y", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "a1", "content": "ok"},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "b1", "name": "z", "input": {}},
                ],
            },
            {"role": "user", "content": "tail"},
        ]
        n = synthesize_tool_results_for_orphans(msgs)
        # a2 still orphan, b1 still orphan -> total 2
        assert n == 2
        assert find_orphan_tool_uses(msgs) == []
        # synthetic blocks live at positions 3 and 5 (after the respective assistants)
        synthetic_positions = [i for i, m in enumerate(msgs) if m.get("_synthetic")]
        assert len(synthetic_positions) == 2
        # each synthetic is immediately after an assistant
        for pos in synthetic_positions:
            assert msgs[pos - 1].get("role") == "assistant"

    def test_idempotent_second_call(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "x", "name": "n", "input": {}}],
            },
        ]
        assert synthesize_tool_results_for_orphans(msgs) == 1
        assert synthesize_tool_results_for_orphans(msgs) == 0

    def test_malformed_blocks_are_ignored(self) -> None:
        """tool_use without a string id, or with empty id, must be skipped
        gracefully rather than crashing."""
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "", "name": "x", "input": {}},
                    {"type": "tool_use", "name": "y", "input": {}},  # no id at all
                    {"type": "tool_use", "id": 42, "name": "z", "input": {}},  # int id
                    {"type": "tool_use", "id": "real-1", "name": "ok", "input": {}},
                ],
            },
        ]
        n = synthesize_tool_results_for_orphans(msgs)
        # only the real string id counts
        assert n == 1
        assert msgs[-1]["content"][0]["tool_use_id"] == "real-1"


# ── working_messages persistence (future-API smoke) ───────────────────


class TestWorkingMessagesPersistence:
    """The persist/load helpers are not wired into reasoning_engine yet
    (Plan B keeps S3.2 simple: synthesize in-place at turn entry).
    But the helpers are public API and have to behave correctly for
    future use (S4+ "resume with full reasoning context")."""

    @pytest.fixture()
    def base_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield d

    def test_persist_then_load_roundtrip_consumes_file(self, base_dir) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        p = persist_working_messages("conv-x", msgs, base_dir=base_dir)
        assert p is not None and Path(p).exists()
        loaded = load_persisted_working_messages("conv-x", base_dir=base_dir)
        assert loaded == msgs
        # consume=True by default -> file deleted
        assert not Path(p).exists()
        # second load returns None
        assert load_persisted_working_messages("conv-x", base_dir=base_dir) is None

    def test_load_respects_ttl(self, base_dir) -> None:
        msgs = [{"role": "user", "content": "old"}]
        p = persist_working_messages("conv-y", msgs, base_dir=base_dir)
        assert p is not None
        # backdate 25h
        long_ago = time.time() - 90_000
        os.utime(p, (long_ago, long_ago))
        loaded = load_persisted_working_messages("conv-y", base_dir=base_dir, ttl_seconds=86_400)
        assert loaded is None
        # expired file must be cleaned
        assert not Path(p).exists()

    def test_persist_sandbox_blocks_path_escape(self, base_dir) -> None:
        """Crafted conversation_id with ``../`` must not escape the
        ``working_messages/`` folder."""
        p = persist_working_messages("../etc/passwd", [{"x": 1}], base_dir=base_dir)
        assert p is not None
        # realpath of saved file must remain under base_dir/working_messages/
        sandbox = Path(base_dir, "working_messages").resolve()
        assert Path(p).resolve().is_relative_to(sandbox)

    def test_clear_persisted_is_idempotent(self, base_dir) -> None:
        msgs = [{"role": "user", "content": "z"}]
        persist_working_messages("conv-z", msgs, base_dir=base_dir)
        assert clear_persisted_working_messages("conv-z", base_dir=base_dir) is True
        # second call returns False (no file), never raises
        assert clear_persisted_working_messages("conv-z", base_dir=base_dir) is False

    def test_startup_cleanup_removes_expired(self, base_dir) -> None:
        p1 = persist_working_messages("a", [{"r": "u", "c": "1"}], base_dir=base_dir)
        p2 = persist_working_messages("b", [{"r": "u", "c": "2"}], base_dir=base_dir)
        assert p1 and p2
        long_ago = time.time() - 90_000
        os.utime(p1, (long_ago, long_ago))
        n = cleanup_expired_working_messages(base_dir=base_dir, ttl_seconds=86_400)
        assert n == 1
        assert not Path(p1).exists()
        assert Path(p2).exists()

    def test_persist_returns_none_on_empty_inputs(self, base_dir) -> None:
        assert persist_working_messages("", [{"x": 1}], base_dir=base_dir) is None
        assert persist_working_messages("conv", [], base_dir=base_dir) is None


# ── ContextVar publish (smoke for reason_stream / run integration) ────


class TestAbortScopeContextVar:
    """``current_abort_scope`` contextvar publish/get smoke test, including
    the per-asyncio-task isolation guarantee that the rest of S3
    depends on."""

    @pytest.mark.asyncio
    async def test_set_in_one_task_is_invisible_to_sibling_task(self) -> None:
        scope_a = root_scope("task-A")
        scope_b = root_scope("task-B")

        async def _task(scope: AbortScope, sentinel: list):
            current_abort_scope.set(scope)
            await asyncio.sleep(0)
            sentinel.append(current_abort_scope.get())

        result_a: list = []
        result_b: list = []
        await asyncio.gather(_task(scope_a, result_a), _task(scope_b, result_b))
        assert result_a == [scope_a]
        assert result_b == [scope_b]

    @pytest.mark.asyncio
    async def test_nested_await_inherits_scope(self) -> None:
        scope = root_scope()
        current_abort_scope.set(scope)

        async def _inner():
            return current_abort_scope.get()

        got = await _inner()
        assert got is scope
