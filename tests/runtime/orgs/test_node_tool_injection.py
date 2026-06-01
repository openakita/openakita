"""Sprint-5 P0-1 regression: D4 node-level tool injection.

Pins the behaviour audit ``_orgs_business_capability_audit_v5.md`` §5.2
flagged as missing: workbench / mid-tier nodes activated by D3-ext but
``tools_count == 0`` in LLM debug. After Sprint-5 the orgs_v2 node
agent path resolves the node's ``external_tools`` whitelist into real
Anthropic-shaped tool dicts, hands them to
:meth:`Brain.messages_create_async`, and runs one round of
``tool_use -> tool_result -> final LLM call`` when the LLM emits a
``tool_use`` block.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openakita.orgs._default_agent_builder import (
    DefaultAgentBuilder,
)
from openakita.orgs._runtime_agent_pipeline import AgentSpec
from openakita.orgs._runtime_node_tools import (
    MAX_TOOL_ROUNDS,
    execute_node_tool,
    extract_tool_use_blocks,
    resolve_node_tools,
    run_with_tools,
)

# ---------------------------------------------------------------------------
# resolve_node_tools -- maps OrgNode.external_tools to LLM tool dicts
# ---------------------------------------------------------------------------


def test_resolve_node_tools_expands_research_category() -> None:
    """case id: p05.tools.resolve.expands_category

    ``research`` is the category alias for ``web_search``,
    ``news_search``, ``web_fetch``. The resolver must expand the alias
    so the LLM gets concrete tool names, never the alias itself.
    """

    tools = resolve_node_tools(external_tools=("research",), enable_file_tools=False)
    names = {t["name"] for t in tools}
    assert "web_search" in names
    assert "web_fetch" in names
    assert "research" not in names  # Alias never leaks through.


def test_resolve_node_tools_auto_merges_basic_file_tools() -> None:
    """case id: p05.tools.resolve.file_tools_auto_merged

    OrgNode.enable_file_tools=True (the v1 default) auto-merges the
    four basic file tools so a non-filesystem-explicit role can still
    drop deliverables. ``write_file`` is the canonical signal here.
    """

    tools = resolve_node_tools(external_tools=(), enable_file_tools=True)
    names = {t["name"] for t in tools}
    assert "write_file" in names
    assert "read_file" in names


def test_resolve_node_tools_drops_unknown_plugin_names() -> None:
    """case id: p05.tools.resolve.drops_plugin_tools_until_wired

    ``hh_*`` workbench tools live in a separate plugin manifest not
    yet bridged into ``default_handler_registry``. We must drop them
    silently so the node still gets the standard subset instead of
    crashing with a missing-definition error.
    """

    tools = resolve_node_tools(
        external_tools=("hh_image_create", "hh_image_edit", "filesystem"),
        enable_file_tools=False,
    )
    names = {t["name"] for t in tools}
    assert "hh_image_create" not in names
    assert "hh_image_edit" not in names
    # ``filesystem`` category did resolve so the node still has tools.
    assert "write_file" in names


def test_resolve_node_tools_returns_anthropic_shape() -> None:
    """case id: p05.tools.resolve.shape

    Brain.messages_create_async expects ``{name, description,
    input_schema}``. Extra fields (``examples`` / ``category``) must be
    stripped so the prompt budget is not wasted on metadata.
    """

    tools = resolve_node_tools(external_tools=("memory",), enable_file_tools=False)
    assert tools, "memory category must resolve to at least one tool"
    for t in tools:
        assert set(t.keys()) == {"name", "description", "input_schema"}


def test_resolve_node_tools_handles_empty_input() -> None:
    """case id: p05.tools.resolve.empty_safe"""

    assert resolve_node_tools(external_tools=(), enable_file_tools=False) == []
    assert resolve_node_tools(external_tools=None, enable_file_tools=False) == []


# ---------------------------------------------------------------------------
# extract_tool_use_blocks -- LLM response inspection
# ---------------------------------------------------------------------------


def test_extract_tool_use_blocks_returns_empty_for_text_only() -> None:
    """case id: p05.tools.extract.text_only

    No tool_use -> empty list -> no second round.
    """

    resp = SimpleNamespace(content=[SimpleNamespace(text="hello")])
    assert extract_tool_use_blocks(resp) == []


def test_extract_tool_use_blocks_handles_attribute_shape() -> None:
    """case id: p05.tools.extract.sdk_attribute_shape"""

    resp = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="thinking"),
            SimpleNamespace(
                type="tool_use",
                id="tool_1",
                name="web_search",
                input={"query": "x"},
            ),
        ]
    )
    blocks = extract_tool_use_blocks(resp)
    assert blocks == [{"id": "tool_1", "name": "web_search", "input": {"query": "x"}}]


def test_extract_tool_use_blocks_handles_dict_shape() -> None:
    """case id: p05.tools.extract.provider_dict_shape

    Some provider shims return plain dicts for content blocks; the
    extractor must accept both.
    """

    resp = SimpleNamespace(
        content=[
            {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
            {"type": "text", "text": "ok"},
        ]
    )
    blocks = extract_tool_use_blocks(resp)
    assert blocks == [{"id": "t1", "name": "search", "input": {}}]


# ---------------------------------------------------------------------------
# execute_node_tool -- handler dispatch + safety net
# ---------------------------------------------------------------------------


def test_execute_node_tool_emits_called_and_completed_events(monkeypatch) -> None:
    """case id: p05.tools.execute.emits_lifecycle_events

    The events.jsonl reader needs ``node_tool_called`` and
    ``node_tool_completed`` (or ``_failed``) so the v17 audit can
    confirm tools really fired.
    """

    captured: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_name: str, payload: dict[str, Any]) -> None:
        captured.append((event_name, payload))

    async def fake_run(tool_name: str, params: dict[str, Any]) -> str:
        return "tool ran ok"

    import openakita.tools.handlers as handlers_mod

    monkeypatch.setattr(
        handlers_mod.default_handler_registry,
        "execute_by_tool",
        fake_run,
    )

    result, is_error = asyncio.run(
        execute_node_tool(
            tool_name="web_search",
            tool_input={"query": "openakita"},
            org_id="o1",
            node_id="screenwriter",
            command_id="cmd_a",
            emit=emit,
        )
    )
    assert is_error is False
    assert result == "tool ran ok"
    names = [name for name, _ in captured]
    assert names == ["node_tool_called", "node_tool_completed"]
    called_payload = captured[0][1]
    assert called_payload["node_id"] == "screenwriter"
    assert called_payload["command_id"] == "cmd_a"
    assert called_payload["tool_name"] == "web_search"


def test_execute_node_tool_inlines_handler_error(monkeypatch) -> None:
    """case id: p05.tools.execute.error_inlined

    A handler that raises must NOT crash the node agent: we surface
    the error inline as the ``tool_result`` content so the LLM can
    decide how to proceed in the next round.
    """

    async def boom(tool_name: str, params: dict[str, Any]) -> str:
        raise RuntimeError("boom")

    import openakita.tools.handlers as handlers_mod

    monkeypatch.setattr(
        handlers_mod.default_handler_registry,
        "execute_by_tool",
        boom,
    )

    result, is_error = asyncio.run(
        execute_node_tool(
            tool_name="web_search",
            tool_input={},
            org_id="o1",
            node_id="n1",
            command_id=None,
            emit=None,
        )
    )
    assert is_error is True
    assert "boom" in result


def test_execute_node_tool_propagates_cancel(monkeypatch) -> None:
    """case id: p05.tools.execute.cancel_propagates

    User pressed cancel mid-tool: CancelledError must propagate out so
    the surrounding node-agent ``run`` resolves to the v1-parity
    cancelled outcome (Sprint-3 P0-2) instead of being mistaken for a
    tool error.
    """

    async def cancelled_handler(tool_name: str, params: dict[str, Any]) -> str:
        raise asyncio.CancelledError()

    import openakita.tools.handlers as handlers_mod

    monkeypatch.setattr(
        handlers_mod.default_handler_registry,
        "execute_by_tool",
        cancelled_handler,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            execute_node_tool(
                tool_name="x",
                tool_input={},
                org_id="o1",
                node_id="n1",
                command_id=None,
                emit=None,
            )
        )


# ---------------------------------------------------------------------------
# run_with_tools -- one-round tool_use loop
# ---------------------------------------------------------------------------


def test_run_with_tools_no_tool_use_returns_immediately() -> None:
    """case id: p05.tools.loop.zero_round_when_text_only

    First LLM call returned plain text -> zero tool rounds, single
    brain call. Matches the Sprint-4 default behaviour for the
    "node has tools but didn't use them" case.
    """

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            return_value=SimpleNamespace(content=[SimpleNamespace(text="done")])
        )
    )
    response, rounds = asyncio.run(
        run_with_tools(
            brain=brain,
            system_prompt="sys",
            user_content="hi",
            tools=[{"name": "x", "description": "x", "input_schema": {}}],
            org_id="o1",
            node_id="n1",
            command_id="c1",
        )
    )
    assert rounds == 0
    brain.messages_create_async.assert_awaited_once()
    assert response.content[0].text == "done"


def test_run_with_tools_runs_one_round_when_tool_use_emitted(monkeypatch) -> None:
    """case id: p05.tools.loop.one_round_on_tool_use

    LLM emits a tool_use -> we run the tool -> we ask for a final
    answer. Two brain calls total; the second receives the assistant
    + tool_result history.
    """

    captured_calls: list[dict[str, Any]] = []

    async def fake_brain_call(
        *,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        cancel_event: Any = None,  # Sprint-13 H1: accept plumbed kwarg
    ) -> SimpleNamespace:
        captured_calls.append({"messages": messages, "tools": tools})
        if len(captured_calls) == 1:
            # First round: emit a tool_use block.
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="tu_1",
                        name="web_search",
                        input={"query": "openakita"},
                    )
                ]
            )
        # Second round: plain text final answer.
        return SimpleNamespace(content=[SimpleNamespace(text="final answer")])

    brain = SimpleNamespace(messages_create_async=fake_brain_call)

    async def fake_handler(tool_name: str, params: dict[str, Any]) -> str:
        return "search results: ..."

    import openakita.tools.handlers as handlers_mod

    monkeypatch.setattr(
        handlers_mod.default_handler_registry,
        "execute_by_tool",
        fake_handler,
    )

    response, rounds = asyncio.run(
        run_with_tools(
            brain=brain,
            system_prompt="sys",
            user_content="search please",
            tools=[
                {
                    "name": "web_search",
                    "description": "search",
                    "input_schema": {"type": "object"},
                }
            ],
            org_id="o1",
            node_id="n1",
            command_id="c1",
        )
    )
    assert rounds == 1
    assert response.content[0].text == "final answer"
    # Two brain calls: original + final.
    assert len(captured_calls) == 2
    # Second call's messages must contain the tool_result.
    second_messages = captured_calls[1]["messages"]
    last_user = next(m for m in reversed(second_messages) if m["role"] == "user")
    assert isinstance(last_user["content"], list)
    assert last_user["content"][0]["type"] == "tool_result"
    assert last_user["content"][0]["tool_use_id"] == "tu_1"
    assert "search results" in last_user["content"][0]["content"]


def test_max_tool_rounds_is_bounded_multi_round() -> None:
    """case id: p05.tools.loop.max_rounds_constant

    Quality root-fix (test7 RCA 2026-06): the Sprint-5 ``== 1`` bound made
    nodes deliver their raw mid-reasoning. We now ship a BOUNDED
    multi-round ReAct loop. The bound must be >1 (so nodes can iterate)
    but stay finite (cost guard) and be paired with a tool-execution
    budget. This test catches an accidental unbounded / disabled bump.
    """

    from openakita.orgs._runtime_node_tools import MAX_TOOL_CALLS

    assert 1 < MAX_TOOL_ROUNDS <= 12
    assert 1 <= MAX_TOOL_CALLS <= 40


def test_run_with_tools_loops_until_text_then_stops(monkeypatch) -> None:
    """case id: p05.tools.loop.multi_round_iterates

    The LLM emits two successive tool_use rounds (search, then search
    again) before returning a text answer. The loop must run BOTH tool
    rounds and return the final text -- the Sprint-5 single-round bound
    would have returned the 2nd tool_use turn's (empty) text as "output".
    """

    calls: list[dict[str, Any]] = []

    async def fake_brain_call(
        *,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        cancel_event: Any = None,
    ) -> SimpleNamespace:
        calls.append({"messages": messages, "tools": tools})
        n = len(calls)
        if n <= 2:
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id=f"tu_{n}",
                        name="web_search",
                        input={"query": f"q{n}"},
                    )
                ]
            )
        return SimpleNamespace(content=[SimpleNamespace(text="final integrated answer")])

    brain = SimpleNamespace(messages_create_async=fake_brain_call)

    async def fake_handler(tool_name: str, params: dict[str, Any]) -> str:
        return "results"

    import openakita.tools.handlers as handlers_mod

    monkeypatch.setattr(
        handlers_mod.default_handler_registry, "execute_by_tool", fake_handler
    )

    response, rounds = asyncio.run(
        run_with_tools(
            brain=brain,
            system_prompt="sys",
            user_content="search please",
            tools=[{"name": "web_search", "description": "s", "input_schema": {"type": "object"}}],
            org_id="o1",
            node_id="n1",
            command_id="c1",
        )
    )
    assert rounds == 2
    assert response.content[0].text == "final integrated answer"
    # 3 brain calls: round1 tool_use, round2 tool_use, round3 final text.
    assert len(calls) == 3


def test_run_with_tools_forces_text_when_budget_exhausted(monkeypatch) -> None:
    """case id: p05.tools.loop.budget_forces_final_answer

    If the LLM keeps emitting tool_use forever, the loop must stop at the
    round/call budget and make ONE final tools=[] call so the node returns
    a clean text answer rather than a leaked tool_use / thinking turn.
    """

    from openakita.orgs import _runtime_node_tools as nt

    monkeypatch.setattr(nt, "MAX_TOOL_ROUNDS", 3)
    monkeypatch.setattr(nt, "MAX_TOOL_CALLS", 99)

    calls: list[dict[str, Any]] = []

    async def fake_brain_call(
        *,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        cancel_event: Any = None,
    ) -> SimpleNamespace:
        calls.append({"tools": tools})
        # Always wants another tool UNLESS no tools are offered (forced final).
        if tools:
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use", id=f"tu_{len(calls)}", name="web_search", input={}
                    )
                ]
            )
        return SimpleNamespace(content=[SimpleNamespace(text="forced final")])

    brain = SimpleNamespace(messages_create_async=fake_brain_call)

    async def fake_handler(tool_name: str, params: dict[str, Any]) -> str:
        return "r"

    import openakita.tools.handlers as handlers_mod

    monkeypatch.setattr(
        handlers_mod.default_handler_registry, "execute_by_tool", fake_handler
    )

    response, rounds = asyncio.run(
        run_with_tools(
            brain=brain,
            system_prompt="sys",
            user_content="go",
            tools=[{"name": "web_search", "description": "s", "input_schema": {"type": "object"}}],
            org_id="o1",
            node_id="n1",
            command_id="c1",
        )
    )
    assert rounds == 3
    assert response.content[0].text == "forced final"
    # The final call must have been made with tools=[] (the force).
    assert calls[-1]["tools"] == []


# ---------------------------------------------------------------------------
# _BrainBackedNodeAgent integration -- D4 end-to-end through the builder
# ---------------------------------------------------------------------------


def test_node_agent_passes_resolved_tools_to_brain(monkeypatch) -> None:
    """case id: p05.tools.agent.tools_passed

    The node agent's ``run`` must call ``messages_create_async`` with
    the resolved tool dicts (not the empty Sprint-4 ``tools=[]``).
    """

    seen: list[Any] = []

    async def fake_brain_call(**kwargs: Any) -> SimpleNamespace:
        seen.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text="ok")])

    brain = SimpleNamespace(
        messages_create_async=fake_brain_call,
        set_trace_context=lambda _ctx: None,
    )
    builder = DefaultAgentBuilder(brain_provider=lambda: brain)
    spec = AgentSpec(
        org_id="o1",
        node_id="screenwriter",
        role="screenwriter",
        external_tools=("research",),
        enable_file_tools=False,
    )
    agent = builder.build(spec)
    out = asyncio.run(agent.run("draft a scene"))
    assert out == "ok"
    assert len(seen) == 1
    tools_passed = seen[0]["tools"]
    assert tools_passed, "tools list must be non-empty"
    assert any(t["name"] == "web_search" for t in tools_passed)


def test_node_agent_tags_tools_count_in_trace_context() -> None:
    """case id: p05.tools.agent.trace_tools_count

    The LLM debug ``context`` block must carry ``tools_count`` so the
    v17 audit can grep for ``tools_count > 0`` and confirm workbench
    activations are now real tool-using runs.
    """

    seen_ctx: list[dict[str, str]] = []

    def fake_set_trace(ctx: dict[str, str]) -> None:
        seen_ctx.append(dict(ctx))

    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            return_value=SimpleNamespace(content=[SimpleNamespace(text="ok")])
        ),
        set_trace_context=fake_set_trace,
    )
    builder = DefaultAgentBuilder(brain_provider=lambda: brain)
    spec = AgentSpec(
        org_id="o1",
        node_id="screenwriter",
        role="screenwriter",
        external_tools=("filesystem",),
        enable_file_tools=False,
    )
    agent = builder.build(spec)
    asyncio.run(agent.run("draft"))
    assert len(seen_ctx) == 1
    assert seen_ctx[0]["caller"] == "orgs_v2_node_agent"
    # filesystem category resolves to >= 1 tool (write_file etc).
    assert int(seen_ctx[0]["tools_count"]) >= 1
