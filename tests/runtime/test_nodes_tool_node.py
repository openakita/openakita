"""Tests for runtime.nodes.tool_node — deterministic single-tool node."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.messenger import NodeAddress, NodeMessage
from openakita.runtime.nodes import (
    NodeContext,
    ToolInvocation,
    ToolNode,
    ToolResult,
)
from openakita.runtime.stream import StreamBus, StreamEvent

from .test_nodes_base import _StreamCollector


def _make_ctx() -> tuple[StreamBus, NodeContext]:
    stream = StreamBus()
    return stream, NodeContext(
        node_id="t-1",
        org_id="org-1",
        command_id="cmd-1",
        stream=stream,
        cancel_token=CancellationToken(),
        checkpointer=MemoryCheckpointer(),
    )


def _msg(*, instruction: str, metadata: dict[str, Any] | None = None) -> NodeMessage:
    return NodeMessage(
        speaker="speaker-x",
        address=NodeAddress.parse("t-1"),
        instruction=instruction,
        correlation_id="corr-1",
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# successful invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_called_with_parsed_arguments_and_returns_success() -> None:
    captured: list[ToolInvocation] = []

    async def runner(inv: ToolInvocation) -> ToolResult:
        captured.append(inv)
        return ToolResult(success=True, output="ok", data={"echo": inv.arguments})

    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "tasks") as collector:
        node = ToolNode(
            node_id="t-1",
            org_id="org-1",
            tool_name="run_shell",
            tool_runner=runner,
        )
        await node.on_activate(ctx)
        result = await node.on_message(
            _msg(instruction='{"cmd": "echo hi", "timeout": 5}')
        )
        events = await collector.flush(2)
    assert result.success
    assert result.message == "ok"
    assert result.metadata["tool_name"] == "run_shell"
    assert result.metadata["data"]["echo"] == {"cmd": "echo hi", "timeout": 5}
    assert len(captured) == 1
    assert captured[0].arguments == {"cmd": "echo hi", "timeout": 5}
    assert captured[0].correlation_id == "corr-1"
    types = [e.type for e in events]
    assert "tool_started" in types
    assert "tool_completed" in types


# ---------------------------------------------------------------------------
# argument parsing variants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_json_instruction_wraps_into_input_field() -> None:
    captured: list[ToolInvocation] = []

    async def runner(inv: ToolInvocation) -> ToolResult:
        captured.append(inv)
        return ToolResult(success=True, output="done")

    stream, ctx = _make_ctx()
    node = ToolNode(
        node_id="t-1",
        org_id="org-1",
        tool_name="search",
        tool_runner=runner,
    )
    await node.on_activate(ctx)
    await node.on_message(_msg(instruction="hello world"))
    assert captured[0].arguments == {"input": "hello world"}


@pytest.mark.asyncio
async def test_metadata_tool_arguments_overrides_instruction() -> None:
    captured: list[ToolInvocation] = []

    async def runner(inv: ToolInvocation) -> ToolResult:
        captured.append(inv)
        return ToolResult(success=True, output="ok")

    stream, ctx = _make_ctx()
    node = ToolNode(
        node_id="t-1",
        org_id="org-1",
        tool_name="search",
        tool_runner=runner,
    )
    await node.on_activate(ctx)
    await node.on_message(
        _msg(
            instruction="ignored body",
            metadata={"tool_arguments": {"q": "real query", "k": 5}},
        )
    )
    assert captured[0].arguments == {"q": "real query", "k": 5}


@pytest.mark.asyncio
async def test_malformed_json_falls_back_to_input_wrapping() -> None:
    captured: list[ToolInvocation] = []

    async def runner(inv: ToolInvocation) -> ToolResult:
        captured.append(inv)
        return ToolResult(success=True, output="ok")

    stream, ctx = _make_ctx()
    node = ToolNode(
        node_id="t-1",
        org_id="org-1",
        tool_name="search",
        tool_runner=runner,
    )
    await node.on_activate(ctx)
    bogus = "{not valid json}"
    await node.on_message(_msg(instruction=bogus))
    assert captured[0].arguments == {"input": bogus}


# ---------------------------------------------------------------------------
# failure semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_failure_propagates_via_delegation_result() -> None:
    async def runner(inv: ToolInvocation) -> ToolResult:
        return ToolResult(success=False, output="", error="quota exceeded")

    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "tasks") as collector:
        node = ToolNode(
            node_id="t-1",
            org_id="org-1",
            tool_name="generate_image",
            tool_runner=runner,
        )
        await node.on_activate(ctx)
        result = await node.on_message(_msg(instruction="draw a cat"))
        events = await collector.flush(2)
    assert not result.success
    assert "quota exceeded" in result.message
    types = [e.type for e in events]
    assert "tool_failed" in types
    assert "tool_completed" not in types


@pytest.mark.asyncio
async def test_runner_exception_promotes_node_to_error_state() -> None:
    async def runner(inv: ToolInvocation) -> ToolResult:
        raise RuntimeError("network down")

    stream, ctx = _make_ctx()
    node = ToolNode(
        node_id="t-1",
        org_id="org-1",
        tool_name="fetch_url",
        tool_runner=runner,
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg(instruction="https://example.com"))
    assert not result.success
    assert "RuntimeError" in result.message
    assert "network down" in result.message


# ---------------------------------------------------------------------------
# cooperative cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_before_runner_short_circuits_tool_call() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def runner(inv: ToolInvocation) -> ToolResult:
        started.set()
        finished.set()
        return ToolResult(success=True, output="never seen")

    stream = StreamBus()
    cancel = CancellationToken()
    ctx = NodeContext(
        node_id="t-1",
        org_id="org-1",
        command_id="cmd-1",
        stream=stream,
        cancel_token=cancel,
        checkpointer=MemoryCheckpointer(),
    )
    node = ToolNode(
        node_id="t-1",
        org_id="org-1",
        tool_name="long_op",
        tool_runner=runner,
    )
    await node.on_activate(ctx)
    cancel.cancel("user cancel")
    result = await node.on_message(_msg(instruction="x"))
    assert not result.success
    assert "user cancel" in result.message
    assert not started.is_set()


# ---------------------------------------------------------------------------
# stream payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_started_event_payload_includes_arguments_and_correlation_id() -> None:
    async def runner(inv: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="x" * 1024, data={"len": 1024})

    stream, ctx = _make_ctx()
    async with _StreamCollector(stream, "tasks") as collector:
        node = ToolNode(
            node_id="t-1",
            org_id="org-1",
            tool_name="long_text_tool",
            tool_runner=runner,
        )
        await node.on_activate(ctx)
        await node.on_message(_msg(instruction='{"q": "hello"}'))
        events: list[StreamEvent] = await collector.flush(2)
    started = next(e for e in events if e.type == "tool_started")
    completed = next(e for e in events if e.type == "tool_completed")
    assert started.payload["arguments"] == {"q": "hello"}
    assert started.payload["correlation_id"] == "corr-1"
    assert completed.payload["correlation_id"] == "corr-1"
    assert len(completed.payload["output_preview"]) == 512
