"""Tests for runtime.nodes.human_review_node — pause-on-human node."""

from __future__ import annotations

import asyncio

import pytest

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.messenger import NodeAddress, NodeMessage
from openakita.runtime.models import NodeStatus
from openakita.runtime.nodes import (
    HumanReviewNode,
    InMemoryReviewQueue,
    NodeContext,
    ReviewDecision,
    ReviewQueue,
    ReviewVerdict,
)
from openakita.runtime.stream import StreamBus

from .test_nodes_base import _StreamCollector


def _ctx(
    *, cancel: CancellationToken | None = None
) -> tuple[StreamBus, NodeContext]:
    stream = StreamBus()
    return stream, NodeContext(
        node_id="hr-1",
        org_id="org-1",
        command_id="cmd-1",
        stream=stream,
        cancel_token=cancel or CancellationToken(),
        checkpointer=MemoryCheckpointer(),
    )


def _msg(
    *,
    instruction: str = "Please review the storyboard",
    metadata: dict | None = None,
) -> NodeMessage:
    return NodeMessage(
        speaker="speaker-x",
        address=NodeAddress.parse("hr-1"),
        instruction=instruction,
        correlation_id="corr-hr",
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# ReviewDecision invariants
# ---------------------------------------------------------------------------


def test_edit_verdict_requires_payload() -> None:
    with pytest.raises(ValueError):
        ReviewDecision(verdict=ReviewVerdict.EDIT)


def test_in_memory_queue_satisfies_protocol() -> None:
    assert isinstance(InMemoryReviewQueue(), ReviewQueue)


# ---------------------------------------------------------------------------
# happy paths: APPROVE / REJECT / EDIT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_verdict_returns_success_and_emits_resolution() -> None:
    queue = InMemoryReviewQueue()
    stream, ctx = _ctx()
    async with _StreamCollector(stream, "messages", "lifecycle") as collector:
        node = HumanReviewNode(node_id="hr-1", org_id="org-1", queue=queue)
        await node.on_activate(ctx)
        msg_task = asyncio.create_task(node.on_message(_msg()))
        # Wait for the request to land in the queue.
        for _ in range(50):
            if (await queue.pending()):
                break
            await asyncio.sleep(0.01)
        await queue.resolve(
            "corr-hr",
            ReviewDecision(verdict=ReviewVerdict.APPROVE, decided_by="alice"),
        )
        result = await msg_task
        events = await collector.flush(6)
    assert result.success
    assert result.metadata["verdict"] == ReviewVerdict.APPROVE.value
    types = [e.type for e in events]
    assert "human_review_requested" in types
    assert "human_review_resolved" in types


@pytest.mark.asyncio
async def test_reject_verdict_returns_failure() -> None:
    queue = InMemoryReviewQueue()
    stream, ctx = _ctx()
    node = HumanReviewNode(node_id="hr-1", org_id="org-1", queue=queue)
    await node.on_activate(ctx)
    msg_task = asyncio.create_task(node.on_message(_msg()))
    for _ in range(50):
        if (await queue.pending()):
            break
        await asyncio.sleep(0.01)
    await queue.resolve(
        "corr-hr",
        ReviewDecision(
            verdict=ReviewVerdict.REJECT,
            reason="not safe",
            decided_by="alice",
        ),
    )
    result = await msg_task
    assert not result.success
    assert result.message == "not safe"
    assert result.metadata["verdict"] == ReviewVerdict.REJECT.value


@pytest.mark.asyncio
async def test_edit_verdict_carries_payload() -> None:
    queue = InMemoryReviewQueue()
    stream, ctx = _ctx()
    node = HumanReviewNode(node_id="hr-1", org_id="org-1", queue=queue)
    await node.on_activate(ctx)
    edited = {"title": "Fixed title", "body": "Cleaner body"}
    msg_task = asyncio.create_task(node.on_message(_msg()))
    for _ in range(50):
        if (await queue.pending()):
            break
        await asyncio.sleep(0.01)
    await queue.resolve(
        "corr-hr",
        ReviewDecision(
            verdict=ReviewVerdict.EDIT,
            edited_payload=edited,
            reason="title was wrong",
            decided_by="alice",
        ),
    )
    result = await msg_task
    assert result.success
    assert result.metadata["verdict"] == ReviewVerdict.EDIT.value
    assert result.metadata["edited_payload"] == edited


# ---------------------------------------------------------------------------
# Pending state surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_status_becomes_suspect_while_awaiting() -> None:
    queue = InMemoryReviewQueue()
    stream, ctx = _ctx()
    node = HumanReviewNode(node_id="hr-1", org_id="org-1", queue=queue)
    await node.on_activate(ctx)
    msg_task = asyncio.create_task(node.on_message(_msg()))
    for _ in range(50):
        if node.status is NodeStatus.SUSPECT:
            break
        await asyncio.sleep(0.01)
    assert node.status is NodeStatus.SUSPECT
    pending = await queue.pending()
    assert len(pending) == 1
    assert pending[0]["question"] == "Please review and choose a verdict."
    await queue.resolve(
        "corr-hr",
        ReviewDecision(verdict=ReviewVerdict.APPROVE, decided_by="alice"),
    )
    await msg_task


@pytest.mark.asyncio
async def test_message_metadata_can_supply_question_and_payload() -> None:
    queue = InMemoryReviewQueue()
    stream, ctx = _ctx()
    node = HumanReviewNode(node_id="hr-1", org_id="org-1", queue=queue)
    await node.on_activate(ctx)
    payload = {"video_url": "http://example.com/x.mp4", "duration": 12}
    msg = _msg(metadata={"question": "Approve this video?", "payload": payload})
    msg_task = asyncio.create_task(node.on_message(msg))
    for _ in range(50):
        if (await queue.pending()):
            break
        await asyncio.sleep(0.01)
    pending = await queue.pending()
    assert pending[0]["question"] == "Approve this video?"
    assert pending[0]["payload"] == payload
    await queue.resolve(
        "corr-hr",
        ReviewDecision(verdict=ReviewVerdict.APPROVE, decided_by="alice"),
    )
    await msg_task


# ---------------------------------------------------------------------------
# Cooperative cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_while_awaiting_human_returns_failure() -> None:
    queue = InMemoryReviewQueue()
    cancel = CancellationToken()
    stream, ctx = _ctx(cancel=cancel)
    node = HumanReviewNode(node_id="hr-1", org_id="org-1", queue=queue)
    await node.on_activate(ctx)
    msg_task = asyncio.create_task(node.on_message(_msg()))
    for _ in range(50):
        if (await queue.pending()):
            break
        await asyncio.sleep(0.01)
    cancel.cancel("user pressed stop")
    result = await msg_task
    assert not result.success
    assert "cancelled" in result.message.lower()
    assert (await queue.pending()) == []


# ---------------------------------------------------------------------------
# Duplicate request guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_request_with_same_correlation_id_is_rejected() -> None:
    queue = InMemoryReviewQueue()
    await queue.request(
        correlation_id="x", question="q", payload={"a": 1}
    )
    with pytest.raises(ValueError):
        await queue.request(
            correlation_id="x", question="q2", payload={"a": 2}
        )
