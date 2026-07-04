"""End-to-end integration smoke test for the Phase 4 node fleet.

Wires :class:`Supervisor` -> :class:`Messenger` -> three concrete
nodes (one of each shape used in production: ToolNode, LLMNode,
WorkbenchNode) and walks a 3-turn delegation cycle that ends with
the brain saying ``is_request_satisfied=True``.

Closes the test side of gate G4: every node type built in Phase 4
participates in a real supervisor loop, with every channel of the
StreamBus producing the events ADR-0006 specifies.

The test is deliberately deterministic — no LLM clients, no tool
backends. The brain is a scripted FakeBrain (matching the one used
in test_supervisor.py); each node's brain / runner is a small async
closure scripted to produce the right shape per turn.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.messenger import (
    InMemoryNodeRegistry,
    Messenger,
    NodeAddress,
)
from openakita.runtime.nodes import (
    BrainResponse,
    LLMNode,
    NodeContext,
    ToolInvocation,
    ToolNode,
    ToolResult,
    WorkbenchManifest,
    WorkbenchNode,
)
from openakita.runtime.stream import StreamBus, StreamEvent
from openakita.runtime.supervisor import (
    DelegationResult,
    FinalOutcome,
    Supervisor,
    SupervisorBrain,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ledger_json(
    *,
    satisfied: bool = False,
    progress: bool = True,
    speaker: str,
    instruction: str,
) -> str:
    return json.dumps(
        {
            "is_request_satisfied":   {"answer": satisfied, "reason": "r"},
            "is_progress_being_made": {"answer": progress, "reason": "r"},
            "is_in_loop":             {"answer": False, "reason": "r"},
            "instruction_or_question":{"answer": instruction, "reason": "r"},
            "next_speaker":           {"answer": speaker, "reason": "r"},
        }
    )


class FakeSupervisorBrain(SupervisorBrain):
    def __init__(self, *, ledgers: list[str]) -> None:
        self._ledgers = list(ledgers)

    async def extract_facts(self, *, task: str, **_kwargs: object) -> str:
        return f"task is: {task}"

    async def draft_plan(self, *, task: str, facts: str, **_kwargs: object) -> str:
        return "1. storyboard 2. image 3. video"

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        recent_outputs: list | None = None,
        **_kwargs: object,
    ) -> str:
        return self._ledgers.pop(0)


class _Collector:
    """Subscribe across all relevant channels for the duration of the test."""

    def __init__(self, stream: StreamBus, *channels: str) -> None:
        self._stream = stream
        self._channels = channels
        self.events: list[StreamEvent] = []
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> _Collector:
        async def consume() -> None:
            async for ev in self._stream.subscribe(*self._channels):
                self.events.append(ev)

        self._task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._stream.close()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()


# ---------------------------------------------------------------------------
# the smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_drives_three_node_types_through_one_pipeline() -> None:
    stream = StreamBus()
    cancel_token = CancellationToken()
    checkpointer = MemoryCheckpointer()

    # --- nodes ------------------------------------------------------------

    # 1) ToolNode: deterministic storyboard step.
    storyboard_invocations: list[ToolInvocation] = []

    async def storyboard_runner(inv: ToolInvocation) -> ToolResult:
        storyboard_invocations.append(inv)
        return ToolResult(
            success=True,
            output=json.dumps({"shots": [{"id": 1, "desc": "horse runs"}]}),
            data={"shots": 1},
        )

    storyboard_node = ToolNode(
        node_id="storyboarder",
        org_id="org-int",
        tool_name="hh_storyboard_decompose",
        tool_runner=storyboard_runner,
        role="storyboarder",
    )

    # 2) LLMNode: the image artist. One brain call decides to call the
    #    image tool, sees the result, returns final answer.
    async def image_runner(inv: ToolInvocation) -> ToolResult:
        return ToolResult(
            success=True,
            output="image-asset-id://abc",
            data={"asset_id": "abc"},
        )

    image_brain_calls = 0

    async def image_brain(prompt) -> BrainResponse:
        nonlocal image_brain_calls
        image_brain_calls += 1
        if image_brain_calls == 1:
            from openakita.runtime.nodes import ToolCallRequest
            return BrainResponse(
                tool_call=ToolCallRequest(
                    tool_name="hh_image_create",
                    arguments={"prompt": "horse runs", "size": "1024*1024"},
                )
            )
        return BrainResponse(answer="image generated, asset_id=abc")

    image_node = LLMNode(
        node_id="image-artist",
        org_id="org-int",
        brain=image_brain,
        tool_runner=image_runner,
        allowed_tools=frozenset({"hh_image_create"}),
        role="image_artist",
    )

    # 3) WorkbenchNode: video animator backed by happyhorse-video manifest.
    manifest = WorkbenchManifest.parse(
        {
            "id": "happyhorse-video",
            "title": "Happy Horse Video Studio",
            "version": 2,
            "ui": {"url": "/p/x.html"},
            "modes": [
                {
                    "id": "video_animator",
                    "label": "Video Animator",
                    "tools": ["hh_i2v"],
                    "system_prompt_override": "You are the video animator.",
                    "ui_panel": "animator",
                },
            ],
            "default_mode": "video_animator",
        }
    )

    async def video_runner(inv: ToolInvocation) -> ToolResult:
        return ToolResult(
            success=True,
            output="video-url://horse-runs.mp4",
            data={"video_url": "video-url://horse-runs.mp4"},
        )

    async def video_brain(prompt) -> BrainResponse:
        from openakita.runtime.nodes import ToolCallRequest

        if len(prompt.transcript) == 1:
            return BrainResponse(
                tool_call=ToolCallRequest(
                    tool_name="hh_i2v",
                    arguments={"prompt": "horse runs", "image_url": "asset://abc"},
                )
            )
        return BrainResponse(answer="video at video-url://horse-runs.mp4")

    video_node = WorkbenchNode(
        node_id="video-animator",
        org_id="org-int",
        manifest=manifest,
        brain=video_brain,
        tool_runner=video_runner,
    )

    # --- registry / messenger / shared ctx --------------------------------

    registry = InMemoryNodeRegistry()
    registry.register(storyboard_node, role="storyboarder")
    registry.register(image_node, role="image_artist")
    registry.register(
        video_node,
        workbench=("happyhorse-video", "video_animator"),
    )

    messenger = Messenger(registry=registry, stream=stream)

    async def deliver(
        speaker: str, instruction: str, progress: ProgressLedger
    ) -> DelegationResult:
        return await messenger.deliver(
            speaker,
            instruction,
            command_id="cmd-int",
            org_id="org-int",
            superstep=progress.turn_id,
            cancel_token=cancel_token,
        )

    # --- supervisor brain script ------------------------------------------

    brain = FakeSupervisorBrain(
        ledgers=[
            _ledger_json(
                speaker="storyboarder",
                instruction='{"story": "a horse runs", "total_duration": 30}',
            ),
            _ledger_json(
                speaker="image_artist",
                instruction="generate hero image for shot 1",
            ),
            _ledger_json(
                speaker="happyhorse-video::video_animator",
                instruction="animate the hero image",
            ),
            _ledger_json(
                satisfied=True,
                speaker="storyboarder",
                instruction="done",
            ),
        ]
    )

    sup = Supervisor(
        command_id="cmd-int",
        org_id="org-int",
        root_node_id="storyboarder",
        task="produce a 30s horse-running clip",
        brain=brain,
        deliver=deliver,
        stream=stream,
        checkpointer=checkpointer,
        cancel_token=cancel_token,
        max_stalls=3,
        max_turns=10,
    )

    async with _Collector(
        stream,
        "lifecycle",
        "tasks",
        "updates",
        "messages",
        "progress_ledger",
        "checkpoints",
    ) as collector:
        # Activate inside the collector so activation lifecycle events
        # are observed; in production the facade does this at command
        # boot time after the supervisor's StreamBus is subscribed.
        for node in (storyboard_node, image_node, video_node):
            ctx = NodeContext(
                node_id=node.node_id,
                org_id="org-int",
                command_id="cmd-int",
                stream=stream,
                cancel_token=cancel_token,
                checkpointer=checkpointer,
            )
            await node.on_activate(ctx)
        outcome = await sup.run()
        await asyncio.sleep(0.05)
        events = list(collector.events)

    # --- assertions -------------------------------------------------------

    assert outcome.outcome is FinalOutcome.DONE
    assert len(storyboard_invocations) == 1, (
        "storyboard tool ran exactly once via ToolNode"
    )

    types = [e.type for e in events]

    # 1) Every node type's lifecycle / activity surfaced through the
    #    expected channel.
    assert "node_activated" in types  # all three BaseNode subclasses
    assert "tool_started" in types  # ToolNode + WorkbenchNode + LLMNode
    assert "tool_completed" in types
    assert "assistant_answer" in types  # LLMNode + WorkbenchNode answers
    assert "workbench_ready" in types  # WorkbenchNode activation
    assert "delegation_dispatched" in types  # Messenger
    assert "delegation_completed" in types

    # 2) Supervisor wrote at least one progress ledger and one checkpoint.
    assert "ledger" in types
    assert any(e.channel == "checkpoints" for e in events)

    # 3) The three delegations went to the three different nodes (no
    #    accidental fan-out / re-delegation).
    delegated_to: list[str] = []
    for ev in events:
        if ev.type == "delegation_dispatched":
            delegated_to.append(ev.payload["node_id"])
    assert delegated_to == ["storyboarder", "image-artist", "video-animator"]

    # 4) Address-style delegation works: the third turn used the
    #    workbench address (plugin::mode) and resolved correctly.
    addr = NodeAddress.parse("happyhorse-video::video_animator")
    assert addr.plugin == "happyhorse-video"
    assert addr.mode == "video_animator"


@pytest.mark.asyncio
async def test_messenger_resolves_role_workbench_and_node_id_addresses() -> None:
    """Sanity check the address resolver against all three node-type registrations."""
    stream = StreamBus()
    registry = InMemoryNodeRegistry()

    async def runner(_i: ToolInvocation) -> ToolResult:
        return ToolResult(success=True, output="x")

    # node_id-style addresses must start with "node_" per NodeAddress.parse.
    by_id = ToolNode(
        node_id="node_just-id",
        org_id="org",
        tool_name="t",
        tool_runner=runner,
    )
    by_role = ToolNode(
        node_id="role-node",
        org_id="org",
        tool_name="t",
        tool_runner=runner,
        role="reviewer",
    )
    manifest = WorkbenchManifest.parse(
        {
            "id": "p",
            "title": "P",
            "modes": [{"id": "m", "tools": ["a"]}],
        }
    )

    async def brain(_p) -> BrainResponse:
        return BrainResponse(answer="x")

    by_workbench = WorkbenchNode(
        node_id="wb-node",
        org_id="org",
        manifest=manifest,
        brain=brain,
        tool_runner=runner,
    )

    registry.register(by_id)
    registry.register(by_role, role="reviewer")
    registry.register(by_workbench, workbench=("p", "m"))

    msg = Messenger(registry=registry, stream=stream)

    addr_a, node_a = msg.resolve("node_just-id")
    addr_b, node_b = msg.resolve("reviewer")
    addr_c, node_c = msg.resolve("p::m")
    assert (node_a.node_id, node_b.node_id, node_c.node_id) == (
        "node_just-id",
        "role-node",
        "wb-node",
    )
    assert addr_a.node_id == "node_just-id"
    assert addr_b.role == "reviewer"
    assert addr_c.plugin == "p"
    assert addr_c.mode == "m"
