"""Regression: race between two requests on the same conversation_id can push
the shared ``TaskState`` into a terminal status (COMPLETED / FAILED / CANCELLED)
while another ``reason_stream`` iteration is mid-loop. The unprotected
``state.transition(TaskStatus.REASONING)`` at the top of the stream loop would
then raise ``ValueError`` and tear down the SSE stream — exactly the crash
reported in issue #572 ("[Bug] 执行任务系统直接爆炸") whose diagnostic ZIP
shows::

    ERROR - reason_stream error: 非法状态转换: completed -> reasoning.
            合法目标: ['idle', 'cancelled']

These tests pin the contract that:

1. The state machine itself **does** reject the bad transition (so the runtime
   knows there is a race);
2. ``reason_stream`` and ``_switch_model_for_stream`` both wrap their
   ``transition(...)`` calls with ``try/except ValueError`` so a concurrent
   terminal status never crashes the stream.

The check uses ``inspect.getsource`` instead of running the full ``reason_stream``
coroutine — that coroutine has dozens of external dependencies (brain,
tool_executor, supervisor, budget, context manager …) which would make the
mock surface fragile and unrelated to this specific contract.
"""

from __future__ import annotations

import inspect
import re

import pytest

from openakita.core.agent_state import (
    AgentState,
    IllegalReasoningEntry,
    TaskState,
    TaskStatus,
)
from openakita.core.reasoning_engine import ReasoningEngine

# The reason_stream race-guard rewrite (``_reason_stream_impl`` +
# ``ensure_ready_for_reasoning`` + ``IllegalReasoningEntry`` counter +
# content-safety ``agent_voice``) is upstream work NOT ported after the
# ADR-0003 split of ``core/agent.py``. The compat shims keep the import paths
# alive; the behaviour is tracked for a future port. See
# docs/follow-ups/skipped-items-roadmap.md (Batch C — core/agent.py follow-ups).
# The state-machine contract (TestTerminalToReasoningContract) lives in
# ``agent_state`` and stays active.
_NOT_PORTED = (
    "reason_stream race-guard / illegal-reasoning-entry rewrite not ported "
    "after ADR-0003 split; see docs/follow-ups/skipped-items-roadmap.md (Batch C)"
)


class TestTerminalToReasoningContract:
    """State machine MUST reject COMPLETED/FAILED/CANCELLED -> REASONING."""

    @pytest.mark.parametrize(
        "terminal_status",
        [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED],
    )
    def test_terminal_to_reasoning_raises(self, terminal_status: TaskStatus) -> None:
        ts = TaskState(task_id="t1")
        ts.transition(TaskStatus.REASONING)
        ts.transition(terminal_status)
        assert ts.is_terminal is True
        with pytest.raises(ValueError, match="非法状态转换"):
            ts.transition(TaskStatus.REASONING)

    def test_idle_to_reasoning_succeeds(self) -> None:
        """Sanity: IDLE -> REASONING is the happy path after begin_task."""
        ts = TaskState(task_id="t1")
        assert ts.status is TaskStatus.IDLE
        ts.transition(TaskStatus.REASONING)
        assert ts.status is TaskStatus.REASONING

    def test_begin_task_resets_to_idle_after_completed(self) -> None:
        """When previous task ended COMPLETED, begin_task() must give us a
        fresh IDLE state so the next reason_stream iteration is legal."""
        state = AgentState()
        first = state.begin_task(session_id="conv-1")
        first.transition(TaskStatus.REASONING)
        first.transition(TaskStatus.COMPLETED)
        assert first.is_terminal is True

        second = state.begin_task(session_id="conv-1")
        assert second is not first
        assert second.status is TaskStatus.IDLE
        second.transition(TaskStatus.REASONING)


@pytest.mark.skip(reason=_NOT_PORTED)
class TestReasonStreamRaceGuard:
    """``reason_stream`` line 4010 + ``_switch_model_for_stream`` line 8540
    both must guard the bare ``state.transition(...)`` call so a concurrent
    request on the same conversation_id can never crash the SSE stream.

    Issue #572 root cause: the loop-entry transition at ``reason_stream`` line
    ~4010 was the only ``transition(TaskStatus.REASONING)`` call without a
    ``try/except`` — three siblings inside ``run()`` (line 2283 / 2795 / 2826)
    and seven downstream sites inside the same ``reason_stream`` already had
    fallbacks. The fix re-aligns this last hold-out with the rest of the file.
    """

    @staticmethod
    def _strip_comments(src: str) -> str:
        # Drop full-line python comments + trailing-of-line comments so that
        # the contract check is not satisfied by a stray reference inside a
        # comment.
        cleaned: list[str] = []
        for line in src.splitlines():
            stripped = line.split("#", 1)[0]
            cleaned.append(stripped)
        return "\n".join(cleaned)

    def test_reason_stream_main_loop_transition_is_guarded(self) -> None:
        # v1.27.14 (plan S1.5): hotfix 内容现在位于 _reason_stream_impl；
        # reason_stream 是薄的 outer wrapper 只做 settle hook，不含原循环。
        # v1.28.3 S5-A: state.transition(REASONING) is now
        # state.ensure_ready_for_reasoning() — idempotent helper that
        # raises IllegalReasoningEntry on terminal states; the
        # belt-and-suspenders ValueError catch is still present for any
        # other illegal source -> REASONING transition.
        src = self._strip_comments(inspect.getsource(ReasoningEngine._reason_stream_impl))
        pattern = re.compile(
            r"if\s+state\.status\s*!=\s*TaskStatus\.REASONING\s*:\s*"
            r"\n\s*try\s*:\s*"
            r"\n\s*state\.ensure_ready_for_reasoning\(\)\s*"
            r"\n\s*except\s+IllegalReasoningEntry",
            re.MULTILINE,
        )
        assert pattern.search(src), (
            "issue #572 regression: the main-loop reasoning-entry in "
            "reason_stream MUST go through ensure_ready_for_reasoning() "
            "and explicitly catch IllegalReasoningEntry — bare "
            "transition() or silent except ValueError both reintroduce "
            "the original crash + the silent-corruption rotation."
        )

    def test_reason_stream_terminal_branch_yields_graceful_error(self) -> None:
        """When ensure_ready_for_reasoning() raises IllegalReasoningEntry
        (the terminal-state branch), we must short-circuit with an SSE
        error+done sequence including a stable ``code`` for clients to
        match on."""
        src = self._strip_comments(inspect.getsource(ReasoningEngine._reason_stream_impl))
        assert "IllegalReasoningEntry" in src, (
            "reason_stream must catch IllegalReasoningEntry in the "
            "race-guard branch (issue #572 fix, v1.28.3 S5-A)."
        )
        # error event has the stable code, then done, then return
        assert re.search(
            r'IllegalReasoningEntry[\s\S]{0,1500}?"code"\s*:\s*"illegal_state"'
            r'[\s\S]{0,400}?"type":\s*"done"[\s\S]{0,200}?return',
            src,
        ), (
            "When state is terminal mid-stream (concurrent request collision),"
            " reason_stream must yield {error, code=illegal_state} + {done} "
            "and return — not try to force-continue with a stale state."
        )

    def test_reason_stream_increments_illegal_reasoning_entry_counter(self) -> None:
        """v1.28.3 S5-A: the IllegalReasoningEntry catch must call
        inc_illegal_reasoning_entry so ops can pager-alert on it."""
        src = self._strip_comments(inspect.getsource(ReasoningEngine._reason_stream_impl))
        assert "inc_illegal_reasoning_entry" in src, (
            "The IllegalReasoningEntry handler must increment the counter "
            "for ops alerting; this is the only signal that S1's preempt "
            "protocol was bypassed in production."
        )

    def test_handle_llm_error_model_switch_transition_is_guarded(self) -> None:
        src = self._strip_comments(inspect.getsource(ReasoningEngine._handle_llm_error))
        pattern = re.compile(
            r"try\s*:\s*"
            r"\n\s*state\.transition\(TaskStatus\.MODEL_SWITCHING\)\s*"
            r"\n\s*except\s+ValueError\s*:",
            re.MULTILINE,
        )
        assert pattern.search(src), (
            "_handle_llm_error.transition(MODEL_SWITCHING) must also be "
            "guarded — same race surface as reason_stream main loop."
        )


class TestEnsureReadyForReasoning:
    """v1.28.3 S5-A: ``TaskState.ensure_ready_for_reasoning`` is the
    idempotent reasoning-entry helper that replaces the historical
    ``try: state.transition(REASONING); except ValueError: ...`` pattern
    scattered across reasoning_engine.

    Contract:

    * REASONING already → no-op (idempotent for retry / continuation).
    * Non-terminal pre-REASONING → walks through validated
      state-machine transition; illegal source still raises
      ``ValueError`` (also fatal per :meth:`TaskState.transition` docstring).
    * Terminal (COMPLETED / FAILED / CANCELLED) → raises
      :class:`IllegalReasoningEntry`.
    """

    def test_idempotent_when_already_reasoning(self) -> None:
        ts = TaskState(task_id="t1")
        ts.transition(TaskStatus.REASONING)
        ts.ensure_ready_for_reasoning()
        ts.ensure_ready_for_reasoning()
        assert ts.status is TaskStatus.REASONING

    def test_idle_transitions_to_reasoning(self) -> None:
        ts = TaskState(task_id="t1")
        assert ts.status is TaskStatus.IDLE
        ts.ensure_ready_for_reasoning()
        assert ts.status is TaskStatus.REASONING

    def test_observing_transitions_to_reasoning(self) -> None:
        """Mid-loop continuation (OBSERVING -> REASONING) is the canonical
        case ensure_ready_for_reasoning was built for."""
        ts = TaskState(task_id="t1")
        ts.transition(TaskStatus.REASONING)
        ts.transition(TaskStatus.ACTING)
        ts.transition(TaskStatus.OBSERVING)
        ts.ensure_ready_for_reasoning()
        assert ts.status is TaskStatus.REASONING

    @pytest.mark.parametrize(
        "terminal_status",
        [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED],
    )
    def test_terminal_raises_illegal_reasoning_entry(self, terminal_status: TaskStatus) -> None:
        ts = TaskState(task_id="t1")
        ts.transition(TaskStatus.REASONING)
        ts.transition(terminal_status)
        with pytest.raises(IllegalReasoningEntry, match="ensure_ready_for_reasoning"):
            ts.ensure_ready_for_reasoning()

    def test_terminal_exception_does_not_mutate_state(self) -> None:
        """When IllegalReasoningEntry is raised, status MUST remain
        terminal — the helper is the safety check, not a force-set."""
        ts = TaskState(task_id="t1")
        ts.transition(TaskStatus.REASONING)
        ts.transition(TaskStatus.COMPLETED)
        with pytest.raises(IllegalReasoningEntry):
            ts.ensure_ready_for_reasoning()
        assert ts.status is TaskStatus.COMPLETED

    def test_after_begin_task_recovery_path_works(self) -> None:
        """End-to-end S1 preempt-protocol shape: prev task terminal ->
        begin_task() -> new IDLE task -> ensure_ready_for_reasoning()
        succeeds without exception."""
        agent_state = AgentState()
        prev = agent_state.begin_task(session_id="conv-1")
        prev.transition(TaskStatus.REASONING)
        prev.transition(TaskStatus.COMPLETED)
        assert prev.is_terminal

        new_task = agent_state.begin_task(session_id="conv-1")
        assert new_task is not prev
        assert new_task.status is TaskStatus.IDLE
        new_task.ensure_ready_for_reasoning()
        assert new_task.status is TaskStatus.REASONING

    def test_every_non_terminal_status_can_reach_reasoning(self) -> None:
        """Contract guarantee: ``ensure_ready_for_reasoning`` cannot raise
        ``ValueError`` in practice — every non-terminal status has REASONING
        in its valid-transition set (post-v1.28.3 ``_VALID_TRANSITIONS``).
        Terminal states are pre-caught with IllegalReasoningEntry; REASONING
        itself is idempotent.  This pins the invariant so future contributors
        editing ``_VALID_TRANSITIONS`` notice immediately if they break it."""
        from openakita.core.agent_state import _VALID_TRANSITIONS

        for src_status, targets in _VALID_TRANSITIONS.items():
            if src_status is TaskStatus.REASONING:
                continue  # idempotent path
            ts = TaskState(task_id="t-contract")
            ts.status = src_status  # bypass transition for setup
            if src_status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                with pytest.raises(IllegalReasoningEntry):
                    ts.ensure_ready_for_reasoning()
                continue
            # Non-terminal: REASONING must be in the legal targets.
            assert TaskStatus.REASONING in targets, (
                f"{src_status.value} -> REASONING is missing from "
                f"_VALID_TRANSITIONS; ensure_ready_for_reasoning would "
                f"surface a ValueError instead of converging cleanly. "
                f"Either add the transition or update the S5-A contract."
            )
            ts.ensure_ready_for_reasoning()
            assert ts.status is TaskStatus.REASONING


@pytest.mark.skip(reason=_NOT_PORTED)
class TestS5AAuditFixes:
    """v1.28.3-pre audit hot-fixes (FIX-S5A-1 + FIX-S5A-2).

    The original S5-A landing covered the main-loop reasoning-entry but
    left two telemetry holes that the audit surfaced:

    FIX-S5A-1: ``_reason_stream_impl`` outer ``except Exception`` would
    swallow an ``IllegalReasoningEntry`` raised by any future
    ``ensure_ready_for_reasoning()`` callsite added outside the inner
    main-loop try/except.  The outer catch ladder must list
    ``IllegalReasoningEntry`` BEFORE ``Exception`` so the structured
    error event + counter + pager alarm path is preserved.

    FIX-S5A-2: ``_run_impl`` (IM / CLI path) has three hot-fix sites
    (main-loop iter / ask_user reply / ask_user timeout) that
    ``except ValueError: pass`` the REASONING transition.  Without
    telemetry wiring, the inc_illegal_reasoning_entry counter would
    stay at zero for IM users — leaving S5-B's gating gate (2 weeks
    of zero hits) vacuously met irrespective of actual incidence.
    """

    def test_outer_except_catches_illegal_reasoning_entry_before_exception(
        self,
    ) -> None:
        """FIX-S5A-1: ``except IllegalReasoningEntry`` must appear at the
        outer-try ladder of ``_reason_stream_impl`` and BEFORE the broad
        ``except Exception``.  Ordering matters — Python's except ladder
        is sequential, so a broader catch first would swallow our typed
        exception and lose ``code="illegal_state"`` + the counter alarm.

        We anchor the outer catch by the unique ``reason_stream_outer``
        source label (this string only appears in the outer handler we
        just added; inner main-loop uses ``reason_stream_iter``)."""
        src = inspect.getsource(ReasoningEngine._reason_stream_impl)
        outer_marker = src.find("reason_stream_outer")
        assert outer_marker > 0, (
            "FIX-S5A-1: the outer IllegalReasoningEntry handler must "
            "exist with source label `reason_stream_outer`."
        )
        # The except keyword line owning this marker is somewhere before
        # the marker but at most ~1500 chars upstream (handler body +
        # multi-line explanatory comment).
        upstream = src[max(0, outer_marker - 1500) : outer_marker]
        assert "except IllegalReasoningEntry" in upstream, (
            "FIX-S5A-1: the `reason_stream_outer` counter call must live "
            "inside an `except IllegalReasoningEntry` block — otherwise "
            "the typed exception is not the trigger and the label is a lie."
        )
        # Now check that the FIRST `except Exception as e:` after the
        # outer IllegalReasoningEntry handler comes AFTER it (i.e. the
        # outer Exception handler is sibling-after-IllegalReasoningEntry
        # in the same try block).
        outer_illegal_kw = src.rfind("except IllegalReasoningEntry", 0, outer_marker)
        downstream = src[outer_marker:]
        next_exception_offset = downstream.find("except Exception as e:")
        assert next_exception_offset > 0, (
            "FIX-S5A-1: the outer `except Exception as e:` handler must "
            "still exist as the fall-through after IllegalReasoningEntry."
        )
        outer_exception_kw = outer_marker + next_exception_offset
        assert outer_illegal_kw < outer_exception_kw, (
            "FIX-S5A-1: `except IllegalReasoningEntry` MUST appear "
            "before `except Exception as e:` in the outer try ladder — "
            "Python evaluates except clauses sequentially."
        )

    def test_outer_catch_uses_reason_stream_outer_label(self) -> None:
        """FIX-S5A-1: distinct source label lets ops differentiate
        ``reason_stream_iter`` (inner main-loop catch — common path)
        from ``reason_stream_outer`` (defensive net for callsites we
        haven't yet identified)."""
        src = inspect.getsource(ReasoningEngine._reason_stream_impl)
        assert "reason_stream_outer" in src, (
            "FIX-S5A-1: the outer catch must label its counter increment "
            "as `reason_stream_outer` so ops can distinguish 'main-loop "
            "race' (expected) from 'unidentified callsite' (alarm)."
        )

    def test_run_impl_main_loop_hot_fix_increments_counter(self) -> None:
        """FIX-S5A-2: the v1.27.13 hot-fix at the top of _run_impl's
        main loop now emits inc_illegal_reasoning_entry on the
        terminal-state branch.  IM users no longer have 100% blind
        telemetry."""
        src = inspect.getsource(ReasoningEngine._run_impl)
        # The label distinguishes from reason_stream so dashboards can
        # see which channel surfaces races.
        assert "run_impl_main_loop" in src, (
            "FIX-S5A-2: the run() main-loop hot-fix must label its "
            "counter increment as `run_impl_main_loop` so ops can "
            "distinguish IM/CLI vs SSE race incidence."
        )

    def test_run_impl_ask_user_reply_hot_fix_increments_counter(self) -> None:
        src = inspect.getsource(ReasoningEngine._run_impl)
        assert "run_impl_ask_user_reply" in src, (
            "FIX-S5A-2: the run() ask_user-reply hot-fix must label "
            "its counter increment as `run_impl_ask_user_reply`."
        )

    def test_run_impl_ask_user_timeout_hot_fix_increments_counter(self) -> None:
        src = inspect.getsource(ReasoningEngine._run_impl)
        assert "run_impl_ask_user_timeout" in src, (
            "FIX-S5A-2: the run() ask_user-timeout hot-fix must label "
            "its counter increment as `run_impl_ask_user_timeout`."
        )

    def test_all_three_run_impl_hot_fixes_only_count_on_is_terminal(self) -> None:
        """The counter should only fire when state.is_terminal — a
        ValueError on a non-terminal source is the belt-and-suspenders
        case that S5-B will revisit separately.

        For each of the three counter-fire labels (run_impl_main_loop /
        run_impl_ask_user_reply / run_impl_ask_user_timeout) we find the
        anchor in source and walk back up to 1000 chars looking for the
        nearest `if state.is_terminal:` — that guard MUST exist between
        the `except ValueError:` and the counter call."""
        src = inspect.getsource(ReasoningEngine._run_impl)
        for label in (
            "run_impl_main_loop",
            "run_impl_ask_user_reply",
            "run_impl_ask_user_timeout",
        ):
            anchor = f'source="{label}"'
            pos = src.find(anchor)
            assert pos > 0, (
                f"FIX-S5A-2: counter label {label!r} not found in "
                f"_run_impl source — hot-fix wiring is missing."
            )
            window = src[max(0, pos - 1500) : pos]
            assert "if state.is_terminal:" in window, (
                f"FIX-S5A-2: counter call at {label!r} must be guarded "
                f"by `if state.is_terminal:` within the nearest 1500 chars "
                f"upstream — without the guard, the counter fires on "
                f"every ValueError (including the belt-and-suspenders "
                f"non-terminal force-write path), masking real race "
                f"incidence in the dashboard."
            )
            assert "except ValueError:" in window, (
                f"FIX-S5A-2: counter call at {label!r} must live inside "
                f"the `except ValueError:` block — without that, the "
                f"counter fires on the happy path and the metric "
                f"becomes meaningless."
            )

    def test_run_impl_hot_fixes_use_distinct_source_labels(self) -> None:
        """All four source labels (reason_stream_iter +
        reason_stream_outer + 3x run_impl_*) must be unique so ops
        dashboards can pinpoint which code path the race surfaces in."""
        from openakita.core import conversation_metrics as metrics

        metrics.reset_for_tests()
        # Fire each label once.
        for label in [
            "reason_stream_iter",
            "reason_stream_outer",
            "run_impl_main_loop",
            "run_impl_ask_user_reply",
            "run_impl_ask_user_timeout",
        ]:
            metrics.inc_illegal_reasoning_entry(source=label)
        snap = metrics.snapshot()
        seen_labels = {
            s["labels"]["source"] for s in snap if s["name"] == "illegal_reasoning_entry"
        }
        assert seen_labels == {
            "reason_stream_iter",
            "reason_stream_outer",
            "run_impl_main_loop",
            "run_impl_ask_user_reply",
            "run_impl_ask_user_timeout",
        }


@pytest.mark.skip(reason=_NOT_PORTED)
class TestIllegalReasoningEntryAlerts:
    """v1.28.3 S5-A: when IllegalReasoningEntry surfaces in
    ``_reason_stream_impl``, an ``inc_illegal_reasoning_entry`` counter
    fires (pager alert) and the SSE stream emits a stable
    ``code=illegal_state`` error event before closing."""

    def test_counter_is_imported_into_reason_stream_impl_handler(self) -> None:
        """The counter import lives inside the except block to keep
        startup imports lean — verify the contract via source inspection."""
        src = inspect.getsource(ReasoningEngine._reason_stream_impl)
        # Counter import must be co-located with the IllegalReasoningEntry handler.
        assert re.search(
            r"except\s+IllegalReasoningEntry[\s\S]{0,500}?inc_illegal_reasoning_entry",
            src,
        ), (
            "inc_illegal_reasoning_entry must be called inside the "
            "except IllegalReasoningEntry block — that's the only signal "
            "to ops that S1's preempt protocol was bypassed."
        )

    def test_counter_fires_with_expected_label(self) -> None:
        """The fire-site uses source ``reason_stream_iter`` so ops can
        distinguish it from any future hot-path call sites we might add
        (e.g. tool execution or run() iteration entry)."""
        src = inspect.getsource(ReasoningEngine._reason_stream_impl)
        assert "reason_stream_iter" in src

    def test_counter_actually_increments(self) -> None:
        """End-to-end: directly invoke the counter and read it back via
        the in-memory snapshot."""
        from openakita.core import conversation_metrics as metrics

        metrics.reset_for_tests()
        metrics.inc_illegal_reasoning_entry(source="reason_stream_iter")
        snap = metrics.snapshot()
        matching = [
            s
            for s in snap
            if s["name"] == "illegal_reasoning_entry"
            and s["labels"].get("source") == "reason_stream_iter"
        ]
        assert len(matching) == 1
        assert matching[0]["value"] == 1


@pytest.mark.skip(reason=_NOT_PORTED)
class TestAllReasoningTransitionsGuarded:
    """Belt-and-suspenders: every ``state.transition(...)`` inside
    ``reason_stream`` should either be in the ``try/except ValueError`` shape
    or be the very first transition out of IDLE (which cannot race). This
    catches future regressions where someone adds another bare transition.
    """

    def test_no_bare_state_transition_in_reason_stream(self) -> None:
        # v1.27.14 (plan S1.5): hotfix 内容现在位于 _reason_stream_impl；
        # wrapper 只做 settle hook，不含 state.transition 调用。
        src = inspect.getsource(ReasoningEngine._reason_stream_impl)
        lines = src.splitlines()
        bare: list[tuple[int, str]] = []
        for idx, line in enumerate(lines):
            if "state.transition(" not in line:
                continue
            # Walk backwards over comments / blank lines / continuation lines
            # to find the preceding statement. If we see `try:` within the
            # last 5 non-blank lines, this transition is guarded.
            guarded = False
            j = idx - 1
            look_back = 0
            while j >= 0 and look_back < 5:
                prev = lines[j].strip()
                if not prev or prev.startswith("#"):
                    j -= 1
                    continue
                if prev == "try:":
                    guarded = True
                    break
                # Allow one wrapping `if state.status != ...:` line above the
                # try (the canonical pattern in reason_stream).
                if prev.startswith("if state.status"):
                    j -= 1
                    look_back += 1
                    continue
                break
            if not guarded:
                bare.append((idx, line.strip()))
        assert not bare, (
            "Found bare state.transition(...) call(s) in reason_stream "
            "without a `try:` guard within 5 lines. Issue #572 was caused "
            f"by exactly this oversight. Offending lines: {bare}"
        )


@pytest.mark.skip(reason=_NOT_PORTED)
class TestContentSafetyMinimalPromptIdentity:
    def test_run_impl_accepts_agent_voice_for_content_safety_prompt(self) -> None:
        src = inspect.getsource(ReasoningEngine._run_impl)
        assert 'agent_voice: str = ""' in src
        assert '_content_safety_identity = _content_safety_name or "一个 AI 助手"' in src
        assert "你是 {_content_safety_identity}" in src

    def test_reason_stream_accepts_agent_voice_for_content_safety_prompt(self) -> None:
        src = inspect.getsource(ReasoningEngine._reason_stream_impl)
        assert 'agent_voice: str = ""' in src
        assert '_content_safety_identity = _content_safety_name or "一个 AI 助手"' in src
        assert "你是 {_content_safety_identity}" in src
