"""Tests for runtime.nodes.llm_node — brain-driven reasoning node."""

from __future__ import annotations

from typing import Any

import pytest

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.messenger import NodeAddress, NodeMessage
from openakita.runtime.nodes import (
    BrainPrompt,
    BrainResponse,
    LLMNode,
    NodeContext,
    ToolCallRequest,
    ToolInvocation,
    ToolResult,
)
from openakita.runtime.stream import StreamBus

from .test_nodes_base import _StreamCollector


def _make_ctx(
    *, cancel: CancellationToken | None = None
) -> tuple[StreamBus, NodeContext]:
    stream = StreamBus()
    return stream, NodeContext(
        node_id="llm-1",
        org_id="org-1",
        command_id="cmd-1",
        stream=stream,
        cancel_token=cancel or CancellationToken(),
        checkpointer=MemoryCheckpointer(),
    )


def _msg(*, instruction: str = "do it", metadata: dict[str, Any] | None = None) -> NodeMessage:
    return NodeMessage(
        speaker="speaker-x",
        address=NodeAddress.parse("llm-1"),
        instruction=instruction,
        correlation_id="corr-llm",
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Brain response invariants
# ---------------------------------------------------------------------------


def test_brain_response_requires_exactly_one_of_answer_or_tool_call() -> None:
    with pytest.raises(ValueError):
        BrainResponse()
    with pytest.raises(ValueError):
        BrainResponse(answer="x", tool_call=ToolCallRequest("t", {}))


# ---------------------------------------------------------------------------
# Direct answer (zero tool calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_answer_returns_success_and_emits_assistant_message() -> None:
    async def brain(prompt: BrainPrompt) -> BrainResponse:
        assert prompt.instruction == "what is 2+2"
        assert prompt.transcript[0].speaker == "user"
        return BrainResponse(answer="4")

    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "messages") as collector:
        node = LLMNode(node_id="llm-1", org_id="org-1", brain=brain)
        await node.on_activate(ctx)
        result = await node.on_message(_msg(instruction="what is 2+2"))
        events = await collector.flush(1)
    assert result.success
    assert result.message == "4"
    assert result.metadata["tool_calls"] == 0
    assert events[0].type == "assistant_answer"
    assert events[0].payload["preview"] == "4"


# ---------------------------------------------------------------------------
# Tool loop converges to an answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brain_tool_loop_converges_to_answer() -> None:
    seen_transcripts: list[int] = []

    async def brain(prompt: BrainPrompt) -> BrainResponse:
        seen_transcripts.append(len(prompt.transcript))
        # Step 1: first tool call. Step 2: second tool call. Step 3: answer.
        if len(prompt.transcript) == 1:
            return BrainResponse(
                tool_call=ToolCallRequest(
                    tool_name="search",
                    arguments={"q": "weather"},
                    rationale="need fresh data",
                )
            )
        if len(prompt.transcript) == 3:
            return BrainResponse(
                tool_call=ToolCallRequest(
                    tool_name="search",
                    arguments={"q": "tomorrow"},
                ),
            )
        return BrainResponse(answer="The weather is fine.")

    seen_invocations: list[ToolInvocation] = []

    async def runner(inv: ToolInvocation) -> ToolResult:
        seen_invocations.append(inv)
        return ToolResult(
            success=True,
            output=f"results for {inv.arguments['q']}",
            data={"q": inv.arguments["q"]},
        )

    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "tasks", "messages", "lifecycle") as collector:
        node = LLMNode(
            node_id="llm-1",
            org_id="org-1",
            brain=brain,
            tool_runner=runner,
            allowed_tools=frozenset({"search"}),
        )
        await node.on_activate(ctx)
        result = await node.on_message(_msg(instruction="weather"))
        events = await collector.flush(11)
    assert result.success
    assert result.message == "The weather is fine."
    assert result.metadata["tool_calls"] == 2
    assert seen_transcripts == [1, 3, 5]
    assert [inv.arguments["q"] for inv in seen_invocations] == ["weather", "tomorrow"]
    types = [e.type for e in events]
    assert types.count("tool_started") == 2
    assert types.count("tool_completed") == 2
    assert "assistant_answer" in types


# ---------------------------------------------------------------------------
# Allow-list rejects tools not in scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disallowed_tool_is_rejected_in_transcript_then_brain_recovers() -> None:
    async def brain(prompt: BrainPrompt) -> BrainResponse:
        if len(prompt.transcript) == 1:
            return BrainResponse(
                tool_call=ToolCallRequest(tool_name="rm_rf", arguments={"path": "/"})
            )
        # Brain sees rejection turn and bails out.
        last = prompt.transcript[-1]
        assert last.speaker == "tool"
        assert last.metadata.get("rejected") is True
        return BrainResponse(answer=f"declined: {last.content}")

    async def runner(inv: ToolInvocation) -> ToolResult:
        raise AssertionError("runner should never be called for rejected tool")

    stream, ctx = _make_ctx()
    node = LLMNode(
        node_id="llm-1",
        org_id="org-1",
        brain=brain,
        tool_runner=runner,
        allowed_tools=frozenset({"search"}),
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert result.success
    assert "declined" in result.message
    assert "rm_rf" in result.message
    assert "search" in result.message  # allow-list rendered in rejection text


# ---------------------------------------------------------------------------
# No tool runner configured at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brain_requesting_tool_without_runner_gets_clean_rejection() -> None:
    async def brain(prompt: BrainPrompt) -> BrainResponse:
        if len(prompt.transcript) == 1:
            return BrainResponse(
                tool_call=ToolCallRequest(tool_name="search", arguments={"q": "x"})
            )
        last = prompt.transcript[-1]
        return BrainResponse(answer=f"ok: {last.content}")

    stream, ctx = _make_ctx()
    node = LLMNode(node_id="llm-1", org_id="org-1", brain=brain)
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert result.success
    assert "no tool runner" in result.message


# ---------------------------------------------------------------------------
# Tool budget guards against infinite loops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brain_loop_terminated_when_max_tool_calls_exceeded() -> None:
    async def brain(prompt: BrainPrompt) -> BrainResponse:
        return BrainResponse(
            tool_call=ToolCallRequest(tool_name="loop", arguments={})
        )

    async def runner(inv: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="more")

    stream, ctx = _make_ctx()
    node = LLMNode(
        node_id="llm-1",
        org_id="org-1",
        brain=brain,
        tool_runner=runner,
        max_tool_calls=3,
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert not result.success
    assert "tool call budget exhausted" in result.message
    assert result.metadata["reason"] == "tool_budget_exhausted"
    assert result.metadata["tool_calls"] == 3


# ---------------------------------------------------------------------------
# Cooperative cancel is observed between brain calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_between_brain_calls_returns_failure() -> None:
    cancel = CancellationToken()
    calls = 0

    async def brain(prompt: BrainPrompt) -> BrainResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return BrainResponse(
                tool_call=ToolCallRequest(tool_name="t", arguments={})
            )
        raise AssertionError("should have been cancelled")

    async def runner(inv: ToolInvocation) -> ToolResult:
        # Cancel mid-flight; the next iteration of the while loop checks the token.
        cancel.cancel("user stop")
        return ToolResult(success=True, output="done")

    stream, ctx = _make_ctx(cancel=cancel)
    node = LLMNode(
        node_id="llm-1",
        org_id="org-1",
        brain=brain,
        tool_runner=runner,
        allowed_tools=frozenset({"t"}),
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert not result.success
    assert "user stop" in result.message
    assert calls == 1


# ---------------------------------------------------------------------------
# Brain receives org/command ids and the live cancel token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brain_prompt_carries_runtime_identifiers_and_cancel_token() -> None:
    captured: list[BrainPrompt] = []

    async def brain(prompt: BrainPrompt) -> BrainResponse:
        captured.append(prompt)
        return BrainResponse(answer="ok")

    cancel = CancellationToken()
    stream, ctx = _make_ctx(cancel=cancel)
    node = LLMNode(node_id="llm-1", org_id="org-1", brain=brain)
    await node.on_activate(ctx)
    await node.on_message(_msg(instruction="hi"))
    assert captured[0].org_id == "org-1"
    assert captured[0].command_id == "cmd-1"
    assert captured[0].cancel_token is cancel
    assert captured[0].instruction == "hi"
