"""Steer done-drain: rescue a message steered in (insert_user_message) during
the *final-answer* generation so the turn does not terminate while the message
is still sitting un-read in ``TaskState.pending_user_inserts``.

Background
----------
``process_post_tool_signals`` only drains ``pending_user_inserts`` after a tool
round. When the model produces a final answer with **no tool calls** that drain
never runs, so a follow-up the desktop client steers in the moment the turn
appears to finish would be dropped. ``_drain_steer_before_finish`` closes that
race at the loop's termination point.

The behavioural surface (``_drain_steer_before_finish`` + the
``build_user_insert_message`` wording) is unit-tested directly against a real
:class:`TaskState`. The wiring into ``_reason_stream_impl`` — which has dozens of
external dependencies and cannot be run in a unit test — is pinned by source
inspection, the same convention used by ``tests/unit/test_reason_stream_state_race.py``.

The most important property here is **termination**: the done-drain must never
turn a finishing turn into an unbounded loop. ``test_*ceiling*`` /
``test_*never_loops_forever*`` pin that the helper stops granting continuations
at ``max_iterations`` regardless of how many messages keep arriving.
"""

from __future__ import annotations

import inspect
import re

from openakita.agent import ReasoningEngine
from openakita.core.agent_state import TaskState


class TestBuildUserInsertMessage:
    """The canonical insert wording is shared by the post-tool drain and the
    final-answer done-drain so the two paths can never disagree."""

    def test_shape_and_markers(self) -> None:
        msg = TaskState.build_user_insert_message("帮我把标题也改成中文")
        assert msg["role"] == "user"
        assert "[用户插入消息]" in msg["content"]
        assert "帮我把标题也改成中文" in msg["content"]
        # the disambiguation hint (补充 vs 全新任务) must survive the refactor
        assert "ask_user" in msg["content"]

    def test_post_tool_drain_uses_the_same_wording(self) -> None:
        """Regression for the extraction: process_post_tool_signals must keep
        injecting inserts through build_user_insert_message."""
        src = inspect.getsource(TaskState.process_post_tool_signals)
        assert "build_user_insert_message" in src


class TestDrainSteerBeforeFinishBehaviour:
    async def test_no_state_returns_empty(self) -> None:
        wm: list[dict] = []
        out = await ReasoningEngine._drain_steer_before_finish(
            state=None,
            working_messages=wm,
            final_text="done.",
            iteration=0,
            max_iterations=10,
        )
        assert out == []
        assert wm == []

    async def test_no_pending_returns_empty_and_does_not_touch_messages(self) -> None:
        ts = TaskState(task_id="t1")
        wm: list[dict] = [{"role": "user", "content": "original"}]
        out = await ReasoningEngine._drain_steer_before_finish(
            state=ts,
            working_messages=wm,
            final_text="done.",
            iteration=0,
            max_iterations=10,
        )
        assert out == []
        assert wm == [{"role": "user", "content": "original"}]

    async def test_pending_with_budget_drains_and_folds_answer_in(self) -> None:
        ts = TaskState(task_id="t1")
        await ts.add_user_insert("再补一句：顺便翻译成英文")
        wm: list[dict] = [{"role": "user", "content": "原始任务"}]

        out = await ReasoningEngine._drain_steer_before_finish(
            state=ts,
            working_messages=wm,
            final_text="这是我的最终回答。",
            iteration=2,
            max_iterations=10,
        )

        assert out == ["再补一句：顺便翻译成英文"]
        # pending queue is now empty (drained, not duplicated)
        assert ts.pending_user_inserts == []
        # the just-finished answer is folded in as a settled assistant turn …
        assert wm[1] == {
            "role": "assistant",
            "content": [{"type": "text", "text": "这是我的最终回答。"}],
        }
        # … followed by the steered message in canonical wording
        assert wm[2]["role"] == "user"
        assert "[用户插入消息]" in wm[2]["content"]
        assert "再补一句：顺便翻译成英文" in wm[2]["content"]

    async def test_blank_final_text_drains_without_empty_assistant_block(self) -> None:
        """The empty-content / model-glitch exit can return "". Folding an
        empty text block would be rejected by strict providers, so the helper
        must still drain + inject the steer but skip the assistant fold."""
        ts = TaskState(task_id="t1")
        await ts.add_user_insert("继续上一个请求")
        wm: list[dict] = [{"role": "user", "content": "原始任务"}]

        out = await ReasoningEngine._drain_steer_before_finish(
            state=ts,
            working_messages=wm,
            final_text="   ",  # whitespace-only / blank answer
            iteration=1,
            max_iterations=10,
        )

        assert out == ["继续上一个请求"]
        assert ts.pending_user_inserts == []
        # no empty assistant block was inserted …
        assert all(
            not (m["role"] == "assistant" and not str(m["content"][0]["text"]).strip())
            for m in wm
            if m["role"] == "assistant"
        )
        # … and the steered message is still present
        assert any("[用户插入消息]" in m["content"] for m in wm if m["role"] == "user")

    async def test_multiple_pending_all_drained_in_order(self) -> None:
        ts = TaskState(task_id="t1")
        await ts.add_user_insert("first")
        await ts.add_user_insert("second")
        wm: list[dict] = []

        out = await ReasoningEngine._drain_steer_before_finish(
            state=ts,
            working_messages=wm,
            final_text="answer",
            iteration=0,
            max_iterations=5,
        )

        assert out == ["first", "second"]
        assert ts.pending_user_inserts == []
        # assistant answer + 2 inserts
        assert wm[0]["role"] == "assistant"
        assert "first" in wm[1]["content"]
        assert "second" in wm[2]["content"]


class TestDrainSteerCeilingTermination:
    """The anti-hang guarantee: the helper must refuse to continue on the last
    allowed iteration, even when a message is pending — otherwise a client that
    keeps steering on every final answer could loop forever."""

    async def test_last_iteration_does_not_continue(self) -> None:
        ts = TaskState(task_id="t1")
        await ts.add_user_insert("steered at the very end")
        wm: list[dict] = []

        out = await ReasoningEngine._drain_steer_before_finish(
            state=ts,
            working_messages=wm,
            final_text="answer",
            iteration=9,  # == max_iterations - 1
            max_iterations=10,
        )

        assert out == []
        # the un-drained message is preserved (not appended to a context we are
        # about to abandon), so nothing is silently mutated
        assert ts.pending_user_inserts == ["steered at the very end"]
        assert wm == []

    async def test_past_last_iteration_does_not_continue(self) -> None:
        ts = TaskState(task_id="t1")
        await ts.add_user_insert("x")
        out = await ReasoningEngine._drain_steer_before_finish(
            state=ts,
            working_messages=[],
            final_text="answer",
            iteration=12,
            max_iterations=10,
        )
        assert out == []
        assert ts.pending_user_inserts == ["x"]

    async def test_never_loops_forever_even_if_inserts_keep_arriving(self) -> None:
        """Simulate the pathological client: a new message is steered in on
        *every* final answer. The loop driven by the helper must still stop —
        the number of granted continuations is bounded by max_iterations."""
        max_iterations = 6
        ts = TaskState(task_id="t1")
        continuations = 0

        for iteration in range(max_iterations):
            # a fresh steer arrives right before this turn would finish
            await ts.add_user_insert(f"follow-up #{iteration}")
            wm: list[dict] = []
            out = await ReasoningEngine._drain_steer_before_finish(
                state=ts,
                working_messages=wm,
                final_text=f"answer {iteration}",
                iteration=iteration,
                max_iterations=max_iterations,
            )
            if out:
                continuations += 1
            else:
                # helper refused to continue → loop would terminate here
                break

        # It granted continuations for every iteration except the last one,
        # and crucially it DID terminate (the for-loop is itself bounded, and
        # the helper returned [] at the ceiling).
        assert continuations == max_iterations - 1


class TestReasonStreamWiringContract:
    """Pin the wiring into the real streaming loop without running it."""

    def test_impl_calls_drain_steer_before_finish(self) -> None:
        # Local keeps the canonical monolithic ``reason_stream`` (ADR-0003 split
        # lives in ``openakita.agent``; upstream's extra ``_reason_stream_impl``
        # extraction was not adopted), so the done-drain is wired into
        # ``reason_stream`` itself.
        src = inspect.getsource(ReasoningEngine.reason_stream)
        assert "_drain_steer_before_finish(" in src, (
            "the done-drain helper must be invoked from reason_stream's "
            "final-answer termination block, otherwise steered messages that "
            "land during final-answer generation are silently dropped."
        )

    def test_done_drain_runs_before_terminal_completion(self) -> None:
        """The drain check must happen BEFORE the turn is finalised — calling
        it after the COMPLETED transition / done event would be pointless."""
        src = inspect.getsource(ReasoningEngine.reason_stream)
        drain_at = src.find("_drain_steer_before_finish(")
        # unique anchor for the terminal finalisation of the final-answer block
        finalize_at = src.find("is_verify_incomplete = final_exit_reason")
        assert drain_at > 0 and finalize_at > 0
        assert drain_at < finalize_at, (
            "done-drain must be evaluated before the terminal completion path; "
            "running it after finalisation cannot rescue the steered message."
        )

    def test_continue_path_resets_force_retry_budget(self) -> None:
        """When continuing for a steered follow-up, the per-answer retry
        counters reset so the new user ask gets a clean budget."""
        src = inspect.getsource(ReasoningEngine.reason_stream)
        # within the _steered continue block, all three counters reset to 0
        block = src[src.find("if _steered:") : src.find("if _steered:") + 2200]
        assert "no_tool_call_count = 0" in block
        assert "verify_incomplete_count = 0" in block
        assert "no_confirmation_text_count = 0" in block
        assert "continue" in block

    def test_helper_has_hard_iteration_ceiling(self) -> None:
        src = inspect.getsource(ReasoningEngine._drain_steer_before_finish)
        assert re.search(r"iteration\s*>=\s*max_iterations\s*-\s*1", src), (
            "the helper MUST refuse to continue on the last iteration — this "
            "is the anti-hang ceiling that guarantees termination."
        )
        assert "drain_user_inserts" in src
