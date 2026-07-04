"""Tests for runtime.nodes.workbench_node — plugin-as-node behaviour."""

from __future__ import annotations

import pytest

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.messenger import NodeAddress, NodeMessage
from openakita.runtime.nodes import (
    BrainPrompt,
    BrainResponse,
    NodeContext,
    NodeRegistration,
    ToolCallRequest,
    ToolInvocation,
    ToolResult,
    WorkbenchManifest,
    WorkbenchNode,
)
from openakita.runtime.stream import StreamBus

from .test_nodes_base import _StreamCollector


def _manifest() -> WorkbenchManifest:
    return WorkbenchManifest.parse(
        {
            "id": "happyhorse-video",
            "title": "Happy Horse Video Studio",
            "version": 2,
            "ui": {"url": "/p/x.html", "min_width": 720},
            "modes": [
                {
                    "id": "art_director",
                    "label": "Art Director",
                    "tools": ["hh_storyboard_decompose", "hh_review"],
                    "system_prompt_override": "You are the Art Director.",
                    "ui_panel": "director",
                },
                {
                    "id": "image_artist",
                    "label": "Image Artist",
                    "tools": ["hh_t2i", "hh_i2i"],
                    "ui_panel": "imagery",
                },
            ],
            "default_mode": "art_director",
        }
    )


def _ctx(
    *, cancel: CancellationToken | None = None
) -> tuple[StreamBus, NodeContext]:
    stream = StreamBus()
    return stream, NodeContext(
        node_id="wb-1",
        org_id="org-1",
        command_id="cmd-1",
        stream=stream,
        cancel_token=cancel or CancellationToken(),
        checkpointer=MemoryCheckpointer(),
    )


def _msg(*, instruction: str = "do work", metadata: dict | None = None) -> NodeMessage:
    return NodeMessage(
        speaker="speaker-x",
        address=NodeAddress.parse("wb-1"),
        instruction=instruction,
        correlation_id="corr-wb",
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_unknown_initial_mode_rejected_at_construction() -> None:
    async def brain(_p: BrainPrompt) -> BrainResponse:
        return BrainResponse(answer="x")

    async def runner(_i: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="x")

    with pytest.raises(ValueError, match="unknown initial_mode"):
        WorkbenchNode(
            node_id="wb-1",
            org_id="org-1",
            manifest=_manifest(),
            brain=brain,
            tool_runner=runner,
            initial_mode="nope",
        )


def test_registration_carries_workbench_tuple() -> None:
    async def brain(_p: BrainPrompt) -> BrainResponse:
        return BrainResponse(answer="x")

    async def runner(_i: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="x")

    node = WorkbenchNode(
        node_id="wb-1",
        org_id="org-1",
        manifest=_manifest(),
        brain=brain,
        tool_runner=runner,
    )
    assert node.registration() == NodeRegistration(
        node_id="wb-1",
        role=None,
        workbench=("happyhorse-video", "art_director"),
    )


# ---------------------------------------------------------------------------
# workbench_ready event surfaces UI + tools at activation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_emits_workbench_ready_with_ui_metadata() -> None:
    async def brain(_p: BrainPrompt) -> BrainResponse:
        return BrainResponse(answer="ok")

    async def runner(_i: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="x")

    stream, ctx = _ctx()
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = WorkbenchNode(
            node_id="wb-1",
            org_id="org-1",
            manifest=_manifest(),
            brain=brain,
            tool_runner=runner,
        )
        await node.on_activate(ctx)
        events = await collector.flush(3)
    types = [e.type for e in events]
    assert "workbench_ready" in types
    ready = next(e for e in events if e.type == "workbench_ready")
    assert ready.payload["plugin_id"] == "happyhorse-video"
    assert ready.payload["mode"] == "art_director"
    assert ready.payload["ui_url"] == "/p/x.html"
    assert ready.payload["ui_panel"] == "director"
    assert ready.payload["tools"] == ["hh_storyboard_decompose", "hh_review"]


# ---------------------------------------------------------------------------
# Tool allow-list per mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_rejected_when_not_in_active_mode_allow_list() -> None:
    seen_invocations: list[ToolInvocation] = []

    async def brain(prompt: BrainPrompt) -> BrainResponse:
        # Step 1: ask for hh_t2i (allowed only in image_artist mode, current
        # is art_director). Step 2: see rejection, answer.
        if len(prompt.transcript) == 1:
            return BrainResponse(
                tool_call=ToolCallRequest(tool_name="hh_t2i", arguments={"prompt": "x"})
            )
        last = prompt.transcript[-1]
        assert last.metadata.get("rejected") is True
        return BrainResponse(answer=f"declined: {last.content}")

    async def runner(inv: ToolInvocation) -> ToolResult:
        seen_invocations.append(inv)
        raise AssertionError("runner should never be called")

    stream, ctx = _ctx()
    node = WorkbenchNode(
        node_id="wb-1",
        org_id="org-1",
        manifest=_manifest(),
        brain=brain,
        tool_runner=runner,
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert result.success
    assert "hh_t2i" in result.message
    assert "art_director" in result.message
    assert seen_invocations == []


@pytest.mark.asyncio
async def test_allowed_tool_in_active_mode_runs_and_completes() -> None:
    seen_invocations: list[ToolInvocation] = []

    async def brain(prompt: BrainPrompt) -> BrainResponse:
        if len(prompt.transcript) == 1:
            return BrainResponse(
                tool_call=ToolCallRequest(
                    tool_name="hh_storyboard_decompose",
                    arguments={"brief": "a horse runs"},
                )
            )
        last = prompt.transcript[-1]
        assert last.metadata.get("success") is True
        return BrainResponse(answer="storyboard ready")

    async def runner(inv: ToolInvocation) -> ToolResult:
        seen_invocations.append(inv)
        return ToolResult(success=True, output="6 shots", data={"shots": 6})

    stream, ctx = _ctx()
    async with _StreamCollector(stream, "tasks") as collector:
        node = WorkbenchNode(
            node_id="wb-1",
            org_id="org-1",
            manifest=_manifest(),
            brain=brain,
            tool_runner=runner,
        )
        await node.on_activate(ctx)
        result = await node.on_message(_msg())
        events = await collector.flush(2)
    assert result.success
    assert result.message == "storyboard ready"
    assert result.metadata["active_mode"] == "art_director"
    assert seen_invocations[0].arguments == {"brief": "a horse runs"}
    types = [e.type for e in events]
    assert "tool_started" in types
    assert "tool_completed" in types


# ---------------------------------------------------------------------------
# Mode switching via brain response metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brain_can_switch_modes_via_metadata_and_use_new_tools() -> None:
    call = 0

    async def brain(prompt: BrainPrompt) -> BrainResponse:
        nonlocal call
        call += 1
        if call == 1:
            # In art_director: ask the runtime to switch.
            return BrainResponse(
                answer="dispatch", metadata={"switch_to": "image_artist"}
            )
        if call == 2:
            # In image_artist now: call an image tool.
            return BrainResponse(
                tool_call=ToolCallRequest(tool_name="hh_t2i", arguments={"prompt": "x"})
            )
        # Saw the tool result.
        return BrainResponse(answer="image done")

    seen: list[str] = []

    async def runner(inv: ToolInvocation) -> ToolResult:
        seen.append(inv.tool_name)
        return ToolResult(success=True, output="image bytes", data={})

    stream, ctx = _ctx()
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = WorkbenchNode(
            node_id="wb-1",
            org_id="org-1",
            manifest=_manifest(),
            brain=brain,
            tool_runner=runner,
        )
        await node.on_activate(ctx)
        result = await node.on_message(_msg())
        events = await collector.flush(5)
    assert result.success
    assert result.message == "image done"
    assert result.metadata["active_mode"] == "image_artist"
    assert node.active_mode.id == "image_artist"
    assert seen == ["hh_t2i"]
    types = [e.type for e in events]
    assert "workbench_mode_switched" in types
    switch_evt = next(e for e in events if e.type == "workbench_mode_switched")
    assert switch_evt.payload["from_mode"] == "art_director"
    assert switch_evt.payload["to_mode"] == "image_artist"


# ---------------------------------------------------------------------------
# msg.metadata.mode forces a switch before the brain runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_message_mode_override_switches_before_brain_runs() -> None:
    async def brain(prompt: BrainPrompt) -> BrainResponse:
        return BrainResponse(answer="ok")

    async def runner(_i: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="x")

    stream, ctx = _ctx()
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = WorkbenchNode(
            node_id="wb-1",
            org_id="org-1",
            manifest=_manifest(),
            brain=brain,
            tool_runner=runner,
        )
        await node.on_activate(ctx)
        result = await node.on_message(_msg(metadata={"mode": "image_artist"}))
        events = await collector.flush(6)
    assert result.metadata["active_mode"] == "image_artist"
    types = [e.type for e in events]
    assert "workbench_mode_switched" in types


# ---------------------------------------------------------------------------
# Unknown switch_to is ignored without crashing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_switch_to_is_ignored_with_warning() -> None:
    calls = 0

    async def brain(prompt: BrainPrompt) -> BrainResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return BrainResponse(
                answer="ignored", metadata={"switch_to": "no-such-mode"}
            )
        return BrainResponse(answer="real answer")

    async def runner(_i: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="x")

    stream, ctx = _ctx()
    node = WorkbenchNode(
        node_id="wb-1",
        org_id="org-1",
        manifest=_manifest(),
        brain=brain,
        tool_runner=runner,
    )
    await node.on_activate(ctx)
    result = await node.on_message(_msg())
    assert result.success
    assert result.message == "real answer"
    assert node.active_mode.id == "art_director"


# ---------------------------------------------------------------------------
# Cooperative cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workbench_cancel_emits_workbench_cancelled_event() -> None:
    async def brain(_p: BrainPrompt) -> BrainResponse:
        return BrainResponse(answer="ok")

    async def runner(_i: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="x")

    stream, ctx = _ctx()
    async with _StreamCollector(stream, "lifecycle") as collector:
        node = WorkbenchNode(
            node_id="wb-1",
            org_id="org-1",
            manifest=_manifest(),
            brain=brain,
            tool_runner=runner,
        )
        await node.on_activate(ctx)
        await node.on_cancel("user stop")
        events = await collector.flush(5)
    types = [e.type for e in events]
    assert "workbench_cancelled" in types
