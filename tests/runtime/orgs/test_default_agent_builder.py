"""Sprint-2 P0-1 regression: ``DefaultAgentBuilder`` produces a node agent.

The v13 business-capability audit (``_orgs_business_capability_audit_v2.md``
§5 / §8) found H3 wired the ``AgentPipelineExecutor`` correctly but
pointed it at ``_NullAgentBuilder`` -- 60+ commands, 0 LLM calls. This
file pins the smallest viable replacement:

* ``DefaultAgentBuilder`` returns an agent with the
  ``async run(content) -> str`` shape the executor consumes.
* The agent dispatches to ``Brain.messages_create_async`` exactly once
  per ``run`` and extracts the text reply.
* When the brain provider returns ``None`` (lifespan ordering race) the
  builder raises :class:`BuilderUnavailable`, which the executor catches
  and turns into the v1-parity ``agent_run_failed
  reason=agent_build_failed`` event -- identical observable to the
  legacy ``_NullAgentBuilder``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openakita.orgs._default_agent_builder import (
    BuilderUnavailable,
    DefaultAgentBuilder,
    _BrainBackedNodeAgent,
    _extract_text_from_response,
)
from openakita.orgs._runtime_agent_pipeline import AgentSpec


def _spec(**over: Any) -> AgentSpec:
    base = {
        "org_id": "org_1",
        "node_id": "n1",
        "role": "worker",
        "persona": "concise reviewer",
        # Sprint-5 P0-1 added node-context fields to ``AgentSpec``; these
        # Sprint-2 / Sprint-3 / Sprint-4 regression tests pre-date the
        # D4 tool injection and assert the "tools=[] zero-shot" path.
        # Default the new flags to "no auto tools" so the assertions
        # continue to pin the legacy single-shot behaviour byte-for-
        # byte. Sprint-5 D4 has a dedicated test file with the new
        # ``external_tools`` / ``enable_file_tools`` semantics.
        "enable_file_tools": False,
        "external_tools": (),
    }
    base.update(over)
    return AgentSpec(**base)


def test_default_agent_builder_rejects_non_callable_provider() -> None:
    """case id: p01.builder.requires_callable_provider"""

    with pytest.raises(TypeError):
        DefaultAgentBuilder(brain_provider=None)  # type: ignore[arg-type]


def test_default_agent_builder_raises_when_brain_provider_returns_none() -> None:
    """case id: p01.builder.unavailable_when_brain_missing

    Lifespan race: the API loop comes up before ``main.py`` finishes
    wiring the desktop ``Agent`` into ``app.state.agent``. The builder
    must surface a clear error the executor can convert into
    ``agent_run_failed`` instead of silently returning a half-baked
    object.
    """

    builder = DefaultAgentBuilder(brain_provider=lambda: None)
    with pytest.raises(BuilderUnavailable, match="not yet initialised"):
        builder.build(_spec())


def test_default_agent_builder_raises_when_brain_lacks_messages_create_async() -> None:
    """case id: p01.builder.unavailable_when_brain_shape_unfit

    Custom Brain replacements that do not expose
    ``messages_create_async`` (e.g. a future stream-only shape) must be
    rejected with the same observable as ``brain==None`` rather than
    failing later with ``AttributeError`` deep inside the executor.
    """

    builder = DefaultAgentBuilder(brain_provider=lambda: object())
    with pytest.raises(BuilderUnavailable, match="messages_create_async"):
        builder.build(_spec())


def test_default_agent_builder_propagates_provider_exception_as_unavailable() -> None:
    """case id: p01.builder.provider_exception_wrapped

    A provider that raises (corrupted ``app.state``, broken Brain
    constructor) must not crash the executor's ``get_or_create``; the
    builder converts the exception into :class:`BuilderUnavailable` so
    the executor's existing ``agent_build_failed`` event branch fires.
    """

    def broken() -> Any:
        raise RuntimeError("provider boom")

    builder = DefaultAgentBuilder(brain_provider=broken)
    with pytest.raises(BuilderUnavailable, match="provider boom"):
        builder.build(_spec())


def test_brain_backed_node_agent_calls_messages_create_async_once() -> None:
    """case id: p01.node_agent.invokes_brain_once

    The minimum-viable contract from the v13 audit: a single user
    message + persona-derived system prompt + zero tools. Nodes do
    not get the 76-tool main-chat catalogue (out of scope; multi-node
    sprint).
    """

    fake_response = SimpleNamespace(
        content=[SimpleNamespace(text="hello from node")],
    )
    brain = SimpleNamespace(
        messages_create_async=AsyncMock(return_value=fake_response),
        set_trace_context=lambda ctx: None,
    )
    builder = DefaultAgentBuilder(brain_provider=lambda: brain)
    agent = builder.build(_spec())
    assert isinstance(agent, _BrainBackedNodeAgent)
    out = asyncio.run(agent.run("hi there"))
    assert out == "hello from node"
    brain.messages_create_async.assert_awaited_once()
    kwargs = brain.messages_create_async.await_args.kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "hi there"}]
    # System prompt names the org / node so logs / debug dumps tag
    # the call to the orgs_v2 path (audit L4.1 finding).
    assert "node `n1`" in kwargs["system"]
    assert "organisation `org_1`" in kwargs["system"]
    assert kwargs["tools"] == []


def test_brain_backed_node_agent_handles_empty_content_without_calling_brain() -> None:
    """case id: p01.node_agent.empty_content_short_circuits

    Defence in depth: ``command_service`` rejects blank submits, but
    if a noop somehow lands here we must not bill the LLM for it.
    """

    brain = SimpleNamespace(messages_create_async=AsyncMock(return_value=None))
    builder = DefaultAgentBuilder(brain_provider=lambda: brain)
    agent = builder.build(_spec())
    out = asyncio.run(agent.run("   "))
    assert out == ""
    brain.messages_create_async.assert_not_awaited()


def test_brain_backed_node_agent_tags_trace_context_with_node_identity() -> None:
    """case id: p01.node_agent.brain_trace_tagged

    The v13 audit found 0 LLM debug files attributed to orgs_v2; this
    test pins the trace context flow so the next exploratory pass can
    verify "real LLM calls reach orgs_v2 path" without counting log
    lines by hand.
    """

    seen: list[dict[str, str]] = []

    def fake_set(ctx: dict[str, str]) -> None:
        seen.append(dict(ctx))

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            return_value=SimpleNamespace(content=[SimpleNamespace(text="ok")])
        ),
        set_trace_context=fake_set,
    )
    builder = DefaultAgentBuilder(brain_provider=lambda: brain)
    agent = builder.build(_spec(node_id="screenwriter"))
    asyncio.run(agent.run("draft a scene"))
    # Sprint-5 P0-1 added ``tools_count`` to the trace dict so the LLM
    # debug ``context`` block can be filtered by "did the node have any
    # resolved tools?". With the helper-spec ``enable_file_tools=False``
    # + empty whitelist the count is "0", which is still informative
    # (proves the orgs_v2 node path *did* run the resolver path).
    assert seen == [
        {
            "org_id": "org_1",
            "node_id": "screenwriter",
            "caller": "orgs_v2_node_agent",
            "tools_count": "0",
        }
    ]


def test_extract_text_handles_string_content() -> None:
    """case id: p01.text_extract.string_content"""

    resp = SimpleNamespace(content="plain reply")
    assert _extract_text_from_response(resp) == "plain reply"


def test_extract_text_handles_text_block_list() -> None:
    """case id: p01.text_extract.block_list

    The Anthropic ``Message.content`` is a list of ``TextBlock`` /
    ``ToolUseBlock`` etc. We only surface text blocks; tool blocks
    are left for the multi-node sprint.
    """

    resp = SimpleNamespace(
        content=[
            SimpleNamespace(text="line one"),
            SimpleNamespace(type="tool_use", name="x"),  # ignored
            SimpleNamespace(text="line two"),
        ]
    )
    assert _extract_text_from_response(resp) == "line one\nline two"


def test_extract_text_falls_back_to_str_for_unknown_shape() -> None:
    """case id: p01.text_extract.fallback

    Defensive: if a future LLM frontend returns an unfamiliar shape,
    the executor still gets a non-empty output (executor's
    ``_invoke_agent`` contract; ``None`` would tip the success-path
    test into a crash).
    """

    class _Weird:
        content = None

        def __str__(self) -> str:
            return "fallback text"

    assert _extract_text_from_response(_Weird()) == "fallback text"


def test_default_agent_builder_teardown_is_noop() -> None:
    """case id: p01.builder.teardown_does_not_touch_brain

    Brain references are owned by the desktop Agent; the cache must
    not poke its lifecycle (no close / disconnect on evict).
    """

    builder = DefaultAgentBuilder(brain_provider=lambda: SimpleNamespace())
    builder.teardown(SimpleNamespace())  # Must not raise.
