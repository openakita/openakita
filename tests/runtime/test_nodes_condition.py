"""Tests for runtime.nodes.condition_node — deterministic branch routing."""

from __future__ import annotations

from typing import Any

import pytest

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.messenger import NodeAddress, NodeMessage
from openakita.runtime.nodes import (
    BranchInputs,
    ConditionNode,
    NodeContext,
)
from openakita.runtime.stream import StreamBus

from .test_nodes_base import _StreamCollector


def _ctx() -> tuple[StreamBus, NodeContext]:
    stream = StreamBus()
    return stream, NodeContext(
        node_id="cond-1",
        org_id="org-1",
        command_id="cmd-1",
        stream=stream,
        cancel_token=CancellationToken(),
        checkpointer=MemoryCheckpointer(),
    )


def _msg(*, instruction: str = "", metadata: dict[str, Any] | None = None) -> NodeMessage:
    return NodeMessage(
        speaker="speaker-x",
        address=NodeAddress.parse("cond-1"),
        instruction=instruction,
        correlation_id="corr-cond",
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# construction-time validation
# ---------------------------------------------------------------------------


def test_branches_must_be_non_empty() -> None:
    with pytest.raises(ValueError):
        ConditionNode(
            node_id="c", org_id="o", predicate=lambda _i: "x", branches={}
        )


def test_branch_label_and_target_must_be_non_empty_strings() -> None:
    with pytest.raises(ValueError):
        ConditionNode(
            node_id="c",
            org_id="o",
            predicate=lambda _i: "ok",
            branches={"": "node-a"},
        )
    with pytest.raises(ValueError):
        ConditionNode(
            node_id="c",
            org_id="o",
            predicate=lambda _i: "ok",
            branches={"ok": ""},
        )


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_predicate_routes_to_named_branch() -> None:
    stream, ctx = _ctx()

    def pick(inputs: BranchInputs) -> str:
        return "approve" if inputs.get("score", 0) >= 0.8 else "review"

    async with _StreamCollector(stream, "updates") as collector:
        node = ConditionNode(
            node_id="cond-1",
            org_id="org-1",
            predicate=pick,
            branches={"approve": "publisher", "review": "human-1"},
        )
        await node.on_activate(ctx)
        result = await node.on_message(_msg(metadata={"score": 0.9}))
        events = await collector.flush(1)

    assert result.success
    assert result.message == "approve"
    assert result.metadata["next_address"] == "publisher"
    assert events[0].type == "branch_selected"
    assert events[0].payload == {
        "node_id": "cond-1",
        "label": "approve",
        "next_address": "publisher",
        "correlation_id": "corr-cond",
    }


@pytest.mark.asyncio
async def test_async_predicate_is_awaited() -> None:
    stream, ctx = _ctx()

    async def pick(inputs: BranchInputs) -> str:
        return "low" if inputs.get("text") else "high"

    node = ConditionNode(
        node_id="cond-1",
        org_id="org-1",
        predicate=pick,
        branches={"low": "n-low", "high": "n-high"},
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg(instruction="some text"))
    assert result.success
    assert result.message == "low"
    assert result.metadata["next_address"] == "n-low"


# ---------------------------------------------------------------------------
# JSON instruction body folds into inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_json_instruction_is_merged_into_predicate_inputs() -> None:
    stream, ctx = _ctx()
    seen: list[BranchInputs] = []

    def pick(inputs: BranchInputs) -> str:
        seen.append(inputs)
        return "yes"

    node = ConditionNode(
        node_id="cond-1",
        org_id="org-1",
        predicate=pick,
        branches={"yes": "n-yes"},
    )
    await node.on_activate(ctx)
    await node.on_message(
        _msg(instruction='{"category": "video", "duration": 30}')
    )
    inputs = seen[0]
    assert inputs["category"] == "video"
    assert inputs["duration"] == 30
    assert "text" in inputs


# ---------------------------------------------------------------------------
# Metadata wins over instruction-derived keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_overrides_instruction_keys() -> None:
    stream, ctx = _ctx()
    seen: list[BranchInputs] = []

    def pick(inputs: BranchInputs) -> str:
        seen.append(inputs)
        return "ok"

    node = ConditionNode(
        node_id="cond-1",
        org_id="org-1",
        predicate=pick,
        branches={"ok": "n-ok"},
    )
    await node.on_activate(ctx)
    await node.on_message(
        _msg(
            instruction='{"score": 0.1}',
            metadata={"score": 0.99},
        )
    )
    assert seen[0]["score"] == 0.99


# ---------------------------------------------------------------------------
# Predicate returning unknown / non-string label is a deterministic failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_branch_label_is_a_failure() -> None:
    stream, ctx = _ctx()
    node = ConditionNode(
        node_id="cond-1",
        org_id="org-1",
        predicate=lambda _i: "bogus",
        branches={"approve": "n-a"},
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert not result.success
    assert "unknown branch label" in result.message
    assert result.metadata["valid"] == ["approve"]


@pytest.mark.asyncio
async def test_non_string_label_is_a_failure() -> None:
    stream, ctx = _ctx()
    node = ConditionNode(
        node_id="cond-1",
        org_id="org-1",
        predicate=lambda _i: 42,  # type: ignore[return-value]
        branches={"x": "n-x"},
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert not result.success
    assert "non-string label" in result.message
