from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openakita.orgs._default_agent_builder import _BrainBackedNodeAgent
from openakita.orgs._runtime_agent_pipeline import AgentSpec, current_command_id_var
from openakita.orgs._runtime_artifact_flow import artifact_ledger, record_tool_result
from openakita.orgs._runtime_delegation import (
    DelegationExecutionResult,
    current_delegation_assignment_var,
    current_delegation_media_spec_var,
    current_delegation_requests_var,
    current_delegation_targets_var,
    delegation_key,
    delegation_ledger,
    queue_delegation,
)


def _text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _tool_response(*blocks: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id=block["id"],
                name="org_delegate_task",
                input=block["input"],
            )
            for block in blocks
        ]
    )


def _spec() -> AgentSpec:
    return AgentSpec(
        org_id="org",
        node_id="producer",
        role="producer",
        external_tools=(),
        enable_file_tools=False,
        available_nodes=(("video", "video workbench"),),
    )


def _dag_spec() -> AgentSpec:
    return AgentSpec(
        org_id="org",
        node_id="producer",
        role="producer",
        external_tools=(),
        enable_file_tools=False,
        available_nodes=(("writer", "writer"), ("director", "director")),
    )


@pytest.mark.asyncio
async def test_xml_dispatch_is_inert_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAKITA_ORG_LEGACY_TEXT_DISPATCH", raising=False)
    callback = AsyncMock(return_value="must not run")
    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            return_value=_text_response('<dispatch target="video">example</dispatch>')
        ),
        set_trace_context=lambda _context: None,
    )
    agent = _BrainBackedNodeAgent(_spec(), brain, dispatch_callback=callback)

    token = current_command_id_var.set("cmd")
    try:
        output = await agent.run("make a video")
    finally:
        current_command_id_var.reset(token)

    callback.assert_not_awaited()
    assert '<dispatch target="video">' in output


@pytest.mark.asyncio
async def test_structured_delegation_executes_once_and_suppresses_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAKITA_ORG_LEGACY_TEXT_DISPATCH", raising=False)
    delegation_ledger.clear()
    callback = AsyncMock(return_value="video ready")
    request = {
        "target": "video",
        "instruction": "generate segment one",
        "segment_id": "seg_01",
        "tool_name": "hh_t2v",
    }
    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            side_effect=[
                _tool_response(
                    {"id": "call-1", "input": request},
                    {"id": "call-2", "input": {**request, "instruction": "duplicate wording"}},
                ),
                _text_response("delegation queued"),
            ]
        ),
        set_trace_context=lambda _context: None,
    )
    agent = _BrainBackedNodeAgent(_spec(), brain, dispatch_callback=callback)

    token = current_command_id_var.set("cmd")
    try:
        output = await agent.run("make a video")
    finally:
        current_command_id_var.reset(token)
        delegation_ledger.clear()

    callback.assert_awaited_once()
    assert callback.await_args.kwargs["child_node_id"] == "video"
    assert "video ready" in output


def test_assignment_slot_suppresses_renamed_retry_but_allows_requested_variants() -> None:
    delegation_ledger.clear()
    requests = []
    targets_token = current_delegation_targets_var.set(frozenset({"video"}))
    requests_token = current_delegation_requests_var.set(requests)
    assignment_token = current_delegation_assignment_var.set("assignment-1")
    try:
        first, _ = queue_delegation(
            {
                "target": "video",
                "instruction": "generate the requested video",
                "segment_id": "SHOT_001",
                "tool_name": "hh_t2v",
                "output_slot": "final-1",
            },
            org_id="org",
            command_id="cmd",
        )
        assert first is not None
        delegation_ledger.finish("org", "cmd", delegation_key(first), success=True)

        renamed_retry, detail = queue_delegation(
            {
                "target": "video",
                "instruction": "generate the requested video again",
                "segment_id": "SHOT01",
                "tool_name": "hh_t2v",
                "output_slot": "final-1",
            },
            org_id="org",
            command_id="cmd",
        )
        second_variant, _ = queue_delegation(
            {
                "target": "video",
                "instruction": "generate the second requested variant",
                "segment_id": "SHOT01",
                "tool_name": "hh_t2v",
                "output_slot": "variant-2",
                "expected_outputs": 2,
            },
            org_id="org",
            command_id="cmd",
        )
    finally:
        current_delegation_assignment_var.reset(assignment_token)
        current_delegation_requests_var.reset(requests_token)
        current_delegation_targets_var.reset(targets_token)
        delegation_ledger.clear()

    assert renamed_retry is not None
    assert renamed_retry.reuse_completed is True
    assert "reuse completed delegation" in detail
    assert second_variant is not None


def test_multiple_outputs_require_explicit_slots() -> None:
    delegation_ledger.clear()
    requests_token = current_delegation_requests_var.set([])
    targets_token = current_delegation_targets_var.set(frozenset({"video"}))
    try:
        request, detail = queue_delegation(
            {
                "target": "video",
                "instruction": "generate three variants",
                "tool_name": "hh_t2v",
                "expected_outputs": 3,
            },
            org_id="org",
            command_id="cmd",
        )
    finally:
        current_delegation_targets_var.reset(targets_token)
        current_delegation_requests_var.reset(requests_token)
        delegation_ledger.clear()

    assert request is None
    assert "distinct output_slot" in detail


@pytest.mark.asyncio
async def test_delegation_dag_runs_dependency_after_upstream_and_injects_output() -> None:
    delegation_ledger.clear()
    calls: list[tuple[str, str]] = []

    async def callback(**kwargs: Any) -> DelegationExecutionResult:
        target = kwargs["child_node_id"]
        content = kwargs["child_content"]
        calls.append((target, content))
        if target == "writer":
            return DelegationExecutionResult.completed("storyboard seg_001 ready")
        upstream = kwargs["upstream_context"].to_dict()
        assert upstream["dependencies"][0]["step_id"] == "storyboard"
        assert upstream["dependencies"][0]["output"] == "storyboard seg_001 ready"
        return DelegationExecutionResult.completed("video ready")

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            side_effect=[
                _tool_response(
                    {
                        "id": "call-storyboard",
                        "input": {
                            "target": "writer",
                            "instruction": "create storyboard",
                            "step_id": "storyboard",
                            "output_slot": "storyboard",
                        },
                    },
                    {
                        "id": "call-video",
                        "input": {
                            "target": "director",
                            "instruction": "create video from the storyboard",
                            "step_id": "video",
                            "depends_on": ["storyboard"],
                            "output_slot": "final-video",
                        },
                    },
                ),
                _text_response("plan queued"),
            ]
        ),
        set_trace_context=lambda _context: None,
    )
    agent = _BrainBackedNodeAgent(_dag_spec(), brain, dispatch_callback=callback)
    command_token = current_command_id_var.set("cmd-dag")
    assignment_token = current_delegation_assignment_var.set("root-assignment")
    try:
        output = await agent.run("create a video")
    finally:
        current_delegation_assignment_var.reset(assignment_token)
        current_command_id_var.reset(command_token)
        delegation_ledger.clear()

    assert [target for target, _content in calls] == ["writer", "director"]
    assert "video ready" in output


@pytest.mark.asyncio
async def test_dependency_injects_structured_artifact_ledger_and_media_spec_context() -> None:
    delegation_ledger.clear()
    artifact_ledger.clear()
    seen_director_context: dict[str, Any] = {}

    async def callback(**kwargs: Any) -> DelegationExecutionResult:
        nonlocal seen_director_context
        if kwargs["child_node_id"] == "writer":
            record_tool_result(
                org_id="org",
                command_id="cmd-ledger",
                source_node_id="writer",
                tool_name="storyboard_tool",
                tool_input={},
                result={
                    "ok": True,
                    "segments": [{"segment_id": "shot-1", "duration": 2}],
                },
            )
            return DelegationExecutionResult.completed("storyboard ready")
        seen_director_context = kwargs["upstream_context"].to_dict()
        media_spec = current_delegation_media_spec_var.get()
        assert media_spec is not None
        assert media_spec.resolution == "720P"
        return DelegationExecutionResult.completed("video ready")

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            side_effect=[
                _tool_response(
                    {
                        "id": "call-storyboard",
                        "input": {
                            "target": "writer",
                            "instruction": "create storyboard",
                            "step_id": "storyboard",
                            "output_slot": "storyboard",
                        },
                    },
                    {
                        "id": "call-video",
                        "input": {
                            "target": "director",
                            "instruction": "declare media DAG",
                            "step_id": "video",
                            "depends_on": ["storyboard"],
                            "segment_id": "shot-1",
                            "tool_name": "video_tool",
                            "output_slot": "final-video",
                            "media_spec": {
                                "kind": "video",
                                "output_group": "final-video",
                                "aspect_ratio": "16:9",
                                "resolution": "720P",
                                "width": 1280,
                                "height": 720,
                                "duration_s": 2,
                            },
                        },
                    },
                ),
                _text_response("plan queued"),
            ]
        ),
        set_trace_context=lambda _context: None,
    )
    agent = _BrainBackedNodeAgent(_dag_spec(), brain, dispatch_callback=callback)
    command_token = current_command_id_var.set("cmd-ledger")
    try:
        await agent.run("create a video")
    finally:
        current_command_id_var.reset(command_token)
        artifact_ledger.clear()
        delegation_ledger.clear()

    dependency = seen_director_context["dependencies"][0]
    assert dependency["step_id"] == "storyboard"
    assert dependency["evidence"]["records"][0]["segments"][0]["segment_id"] == "shot-1"


@pytest.mark.asyncio
async def test_delegation_plan_rejects_mixed_pixels_in_one_output_group() -> None:
    delegation_ledger.clear()
    callback = AsyncMock(return_value="must not run")
    common = {
        "kind": "video",
        "output_group": "final-video",
        "aspect_ratio": "16:9",
    }
    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            side_effect=[
                _tool_response(
                    {
                        "id": "call-a",
                        "input": {
                            "target": "writer",
                            "instruction": "clip one",
                            "step_id": "a",
                            "segment_id": "a",
                            "tool_name": "video_tool",
                            "output_slot": "a",
                            "media_spec": {
                                **common,
                                "resolution": "720P",
                                "width": 1280,
                                "height": 720,
                                "duration_s": 3,
                            },
                        },
                    },
                    {
                        "id": "call-b",
                        "input": {
                            "target": "director",
                            "instruction": "clip two",
                            "step_id": "b",
                            "segment_id": "b",
                            "tool_name": "video_tool",
                            "output_slot": "b",
                            "media_spec": {
                                **common,
                                "resolution": "1080P",
                                "width": 1920,
                                "height": 1080,
                                "duration_s": 3,
                            },
                        },
                    },
                ),
                _text_response("invalid plan"),
            ]
        ),
        set_trace_context=lambda _context: None,
    )
    agent = _BrainBackedNodeAgent(_dag_spec(), brain, dispatch_callback=callback)
    token = current_command_id_var.set("cmd-media-conflict")
    try:
        output = await agent.run("create clips")
    finally:
        current_command_id_var.reset(token)
        delegation_ledger.clear()

    callback.assert_not_awaited()
    assert "media_spec mismatch" in output


@pytest.mark.asyncio
async def test_delegation_dag_rejects_forward_dependency_before_dispatch() -> None:
    delegation_ledger.clear()
    callback = AsyncMock(return_value="must not run")
    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            side_effect=[
                _tool_response(
                    {
                        "id": "call-a",
                        "input": {
                            "target": "writer",
                            "instruction": "step a",
                            "step_id": "a",
                            "depends_on": ["b"],
                            "output_slot": "a",
                        },
                    },
                    {
                        "id": "call-b",
                        "input": {
                            "target": "director",
                            "instruction": "step b",
                            "step_id": "b",
                            "depends_on": ["a"],
                            "output_slot": "b",
                        },
                    },
                ),
                _text_response("cyclic plan"),
            ]
        ),
        set_trace_context=lambda _context: None,
    )
    agent = _BrainBackedNodeAgent(_dag_spec(), brain, dispatch_callback=callback)
    command_token = current_command_id_var.set("cmd-cycle")
    try:
        output = await agent.run("run cycle")
    finally:
        current_command_id_var.reset(command_token)
        delegation_ledger.clear()

    callback.assert_not_awaited()
    assert output == "cyclic plan"
    correction_messages = brain.messages_create_async.await_args_list[1].kwargs["messages"]
    correction_context = str(correction_messages)
    assert "unknown local steps" in correction_context
    assert "parent-plan step ids" in correction_context


@pytest.mark.asyncio
async def test_delegation_dag_blocks_downstream_when_dependency_fails() -> None:
    delegation_ledger.clear()
    calls: list[str] = []

    async def callback(**kwargs: Any) -> DelegationExecutionResult:
        calls.append(kwargs["child_node_id"])
        return DelegationExecutionResult.failed(reason_code="media_validation_failed")

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            side_effect=[
                _tool_response(
                    {
                        "id": "call-a",
                        "input": {
                            "target": "writer",
                            "instruction": "step a",
                            "step_id": "a",
                            "output_slot": "a",
                        },
                    },
                    {
                        "id": "call-b",
                        "input": {
                            "target": "director",
                            "instruction": "step b",
                            "step_id": "b",
                            "depends_on": ["a"],
                            "output_slot": "b",
                        },
                    },
                ),
                _text_response("dependent plan"),
            ]
        ),
        set_trace_context=lambda _context: None,
    )
    agent = _BrainBackedNodeAgent(_dag_spec(), brain, dispatch_callback=callback)
    command_token = current_command_id_var.set("cmd-blocked")
    try:
        output = await agent.run("run dependent plan")
    finally:
        current_command_id_var.reset(command_token)
        delegation_ledger.clear()

    assert calls == ["writer"]
    assert "[blocked:" in output
