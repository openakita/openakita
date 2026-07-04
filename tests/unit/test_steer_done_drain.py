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
from types import SimpleNamespace

import openakita.core.agent as agent_module
from openakita.core.agent import Agent
from openakita.core.agent_state import AgentState, TaskState
from openakita.core.reasoning_engine import ReasoningEngine
from tests.fixtures.mock_llm import MockResponse


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


class TestExecuteTaskWiringContract:
    """The Ralph loop (Agent.execute_task) also drains inserts only after a
    tool round, so it strands a message steered in during the final answer the
    same way the streaming loop did. It must reuse the same done-drain helper
    before the terminal break."""

    def test_execute_task_calls_drain_steer_before_finish(self) -> None:
        from openakita.core.agent import Agent

        src = inspect.getsource(Agent.execute_task)
        assert "_drain_steer_before_finish(" in src, (
            "Agent.execute_task must reuse the done-drain helper before the "
            "no-tool-call terminal break, otherwise an insert steered into a "
            "background/IM task during its final answer is dropped."
        )

    def test_execute_task_done_drain_runs_before_terminal_break(self) -> None:
        from openakita.core.agent import Agent

        src = inspect.getsource(Agent.execute_task)
        drain_at = src.find("_drain_steer_before_finish(")
        # the terminal break is anchored by its unique preceding comment
        break_at = src.find("追问次数用尽，任务完成")
        assert drain_at > 0 and break_at > 0
        assert drain_at < break_at, (
            "the done-drain must be evaluated before the '追问次数用尽' break; "
            "running it after the break cannot rescue the steered message."
        )

    def test_execute_task_converts_iteration_to_zero_based(self) -> None:
        """execute_task's loop counter is 1-based (incremented at the top),
        so it must pass iteration-1 to keep the helper's '>= max-1' ceiling
        aligned with the actual last iteration."""
        from openakita.core.agent import Agent

        src = inspect.getsource(Agent.execute_task)
        block = src[
            src.find("_drain_steer_before_finish(") : src.find("_drain_steer_before_finish(") + 600
        ]
        assert "iteration - 1" in block, (
            "execute_task must convert its 1-based loop counter to 0-based "
            "(iteration - 1) so the anti-hang ceiling fires on the true last "
            "iteration, not one early."
        )


# ──────────────────────────────────────────────────────────────────────────
# End-to-end behavioural coverage of the Ralph loop (Agent.execute_task)
#
# The contract tests above pin *that* the wiring exists; these drive the real
# loop to prove it *behaves*: a message steered in during the final answer is
# (1) actually carried into the very next LLM call, and (2) the loop still
# terminates instead of looping forever.
#
# The streaming loop (_reason_stream_impl) cannot be run in-process (no
# MockBrain.messages_create_stream; ~300 lines of pre-loop collaborators) — see
# tests/unit/test_reason_stream_state_race.py. execute_task, by contrast, calls
# the LLM through the single overridable seam ``_cancellable_llm_call`` (the
# same seam exercised by test_agent_execute_task_result_contract.py), so the
# whole Ralph loop runs for real with only that seam scripted.
# ──────────────────────────────────────────────────────────────────────────


def _final_answer(n: int) -> MockResponse:
    """A substantive, NON-progress final answer (no tool calls).

    Must be report-shaped (heading + bullets) so _looks_like_progress_only_task_text
    treats it as a real result rather than a "我来执行…" progress sentence, and
    >=10 chars so execute_task does not fall into its summary-request branch.
    """
    return MockResponse(
        content=(f"## 任务结果\n\n- 已完成第 {n} 轮处理\n- 结果已整理完毕，可供查看。")
    )


def _make_loop_agent(session_id: str = "task:e2e") -> Agent:
    """A real Agent whose execute_task loop runs for real, with only the LLM
    seam and context-compression stubbed and a real AgentState so the
    done-drain operates on a genuine TaskState.pending_user_inserts queue."""
    agent = Agent.__new__(Agent)
    agent._initialized = True
    agent._current_session_id = None
    agent._context = SimpleNamespace(system="")
    agent._tools = []
    agent._is_sub_agent_call = False
    agent._agent_tool_names = set()
    # _task_cancelled / _cancel_reason are read-only properties derived from
    # agent_state; a fresh (non-cancelled) task makes them False / "".
    # avoid scheduling a real desktop-notification background task on completion
    agent._suppress_desktop_task_notification = True
    agent.brain = SimpleNamespace(
        model="test-model",
        max_tokens=1000,
        get_fallback_model=lambda _session_id=None: None,
        restore_default_model=lambda **_kwargs: None,
    )
    # real state → real pending_user_inserts the done-drain reads/clears
    agent.agent_state = AgentState()
    agent.agent_state.begin_task(session_id=session_id)
    # the done-drain helper lives on the engine; expose the real staticmethod
    agent.reasoning_engine = SimpleNamespace(
        _drain_steer_before_finish=ReasoningEngine._drain_steer_before_finish
    )

    async def _passthrough_compress(messages, system_prompt=""):
        return messages

    agent._compress_context = _passthrough_compress
    return agent


class TestExecuteTaskDoneDrainEndToEnd:
    """Drive the real Ralph loop and observe the steered follow-up survive."""

    async def test_steered_message_reaches_the_next_llm_call_and_task_completes(
        self,
    ) -> None:
        agent = _make_loop_agent()
        task_state = agent.agent_state.current_task

        seen_messages: list[list[tuple[str, str]]] = []
        calls = {"n": 0}

        async def _scripted_llm(cancel_event, **kwargs):
            calls["n"] += 1
            n = calls["n"]
            msgs = kwargs.get("messages", [])
            seen_messages.append([(m.get("role"), str(m.get("content"))) for m in msgs])
            if n == 1:
                # the user steers a follow-up in the instant the final answer
                # is being produced — exactly the race the done-drain closes
                await task_state.add_user_insert("再顺手把结论翻译成英文")
            return _final_answer(n).to_llm_response()

        agent._cancellable_llm_call = _scripted_llm

        result = await agent.execute_task_from_message("整理今天的资讯")

        # the loop CONTINUED for the steered follow-up instead of finishing
        # after the first final answer: exactly two LLM calls, no more.
        assert calls["n"] == 2
        assert result.success is True
        assert result.iterations == 2

        # the first call never saw the steer (it hadn't arrived yet) …
        assert not any("[用户插入消息]" in content for _role, content in seen_messages[0])
        # … and the second call DID — proving the message was carried forward
        # into the next turn's context rather than dropped.
        assert any(
            role == "user" and "[用户插入消息]" in content and "再顺手把结论翻译成英文" in content
            for role, content in seen_messages[1]
        )
        # the just-finished answer was folded in as a settled assistant turn
        # ahead of the steer, so the model sees "answer, then follow-up".
        assert any(role == "assistant" for role, _content in seen_messages[1])
        # the steer queue is drained, not duplicated
        assert task_state.pending_user_inserts == []

    async def test_relentless_steering_still_terminates_at_the_ceiling(self, monkeypatch) -> None:
        """Pathological client: a fresh follow-up is steered in on EVERY final
        answer. The real loop must still stop — bounded by max_iterations — and
        return a normal success result instead of hanging."""
        monkeypatch.setattr(agent_module.settings, "max_iterations", 3)

        agent = _make_loop_agent(session_id="task:ceiling")
        task_state = agent.agent_state.current_task
        calls = {"n": 0}

        async def _scripted_llm(cancel_event, **kwargs):
            calls["n"] += 1
            n = calls["n"]
            # every turn gets a new steer right as it tries to finish
            await task_state.add_user_insert(f"还有一点 #{n}")
            return _final_answer(n).to_llm_response()

        agent._cancellable_llm_call = _scripted_llm

        result = await agent.execute_task_from_message("做一件没完没了的事")

        # max_iterations granted continuations on iters 1 and 2 (0-based 0,1 <
        # max-1=2) and refused on iter 3 (0-based 2 == max-1) → exactly 3 calls.
        assert calls["n"] == 3
        assert result.success is True
        assert result.iterations == 3
        # the follow-up that arrived at the ceiling is preserved (not silently
        # dropped from a context we abandoned) — graceful, bounded termination.
        assert task_state.pending_user_inserts == ["还有一点 #3"]
