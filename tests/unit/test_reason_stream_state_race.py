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

from openakita.core.agent_state import AgentState, TaskState, TaskStatus
from openakita.core.reasoning_engine import ReasoningEngine


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
        src = self._strip_comments(inspect.getsource(ReasoningEngine._reason_stream_impl))
        # Find the main-loop guard: "if state.status != TaskStatus.REASONING:"
        # followed (within a few lines) by `try:` then `state.transition(
        # TaskStatus.REASONING)` then `except ValueError:`.
        pattern = re.compile(
            r"if\s+state\.status\s*!=\s*TaskStatus\.REASONING\s*:\s*"
            r"\n\s*try\s*:\s*"
            r"\n\s*state\.transition\(TaskStatus\.REASONING\)\s*"
            r"\n\s*except\s+ValueError\s*:",
            re.MULTILINE,
        )
        assert pattern.search(src), (
            "issue #572 regression: the main-loop transition(REASONING) in "
            "reason_stream MUST be wrapped in try/except ValueError. A bare "
            "transition() crashes the SSE stream when a concurrent request "
            "already pushed the shared TaskState to a terminal status."
        )

    def test_reason_stream_terminal_branch_yields_graceful_error(self) -> None:
        """When the race-guard catches ValueError AND the state is terminal,
        we must short-circuit with an SSE error+done sequence instead of
        force-overwriting state and continuing into a dead LLM call."""
        src = self._strip_comments(inspect.getsource(ReasoningEngine._reason_stream_impl))
        assert "state.is_terminal" in src, (
            "reason_stream must inspect state.is_terminal in the race-guard "
            "branch (issue #572 fix)."
        )
        # error-event + done-event + return inside the same block
        assert re.search(
            r'state\.is_terminal[\s\S]{0,800}?"type":\s*"error"[\s\S]{0,400}?'
            r'"type":\s*"done"[\s\S]{0,200}?return',
            src,
        ), (
            "When state is terminal mid-stream (concurrent request collision),"
            " reason_stream must yield {error} + {done} and return — not try"
            " to force-continue with a stale state."
        )

    def test_handle_llm_error_model_switch_transition_is_guarded(self) -> None:
        src = self._strip_comments(
            inspect.getsource(ReasoningEngine._handle_llm_error)
        )
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
