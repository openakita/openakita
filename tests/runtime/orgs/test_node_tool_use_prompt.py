"""Sprint-7 P0-B regression: node prompt encourages tool use.

Audit ``_orgs_business_capability_audit_v7.md`` §1.1 R.D4 + §5 finding
2 caught that v18 cases explicitly asking for ``write_file`` /
``read_file`` / ``web_search`` / etc. inside a dispatched node often
came back with plain text refusals instead of ``tool_use`` blocks --
3/8 hit rate on R.D4. Root cause: the Sprint-5/6 child-node system
prompt only said "Reply directly to the user instruction below", which
gives the LLM no reason to prefer a tool call when the user's
intent clearly matches one. Sprint-7 P0-B appends a tool-use policy
paragraph to the system prompt **only when the resolved node has at
least one tool** so the encouragement never hallucinates tools that
the external_tools whitelist + plugin manifest did not expose.

This module pins:

* The new tool-use policy text is present in the system prompt when
  ``has_tools=True`` and absent when ``has_tools=False`` (no token
  budget overhead on zero-tool nodes -- byte-for-byte Sprint-6 shape
  preserved).
* The policy is added at both depth 0 (producer) and depth >= 1
  (children) so workbench nodes like ``wb-hh-image`` /
  ``screenwriter`` get the encouragement.
* The wiring inside :class:`_BrainBackedNodeAgent.run` passes the
  correct ``has_tools`` flag based on the resolved tool definitions.
"""

from __future__ import annotations

from typing import Any

import pytest

from openakita.orgs._default_agent_builder import (
    _BrainBackedNodeAgent,
    _persona_system_prompt,
    _tool_use_encouragement,
)
from openakita.orgs._runtime_agent_pipeline import AgentSpec


def _spec(**overrides: object) -> AgentSpec:
    defaults: dict[str, object] = {
        "org_id": "o1",
        "node_id": "wb-hh-image",
        "role": "worker",
        "persona": "image workbench",
        "external_tools": ("write_file",),
        "enable_file_tools": True,
    }
    defaults.update(overrides)
    return AgentSpec(**defaults)


# ---------------------------------------------------------------------------
# Static prompt assembly: has_tools controls the encouragement block.
# ---------------------------------------------------------------------------


def test_persona_prompt_includes_tool_use_policy_when_has_tools() -> None:
    """case id: p07.tool_use_prompt.has_tools_includes_policy"""

    prompt = _persona_system_prompt(_spec(), depth=0, has_tools=True)
    assert "Tool-use policy" in prompt
    assert "tool_use" in prompt
    assert "SHOULD" in prompt
    # The instruction must address LLM intent matching rather than
    # exact wording -- this is the v18 observation that some Chinese
    # prompts named the tool as a label.
    assert "intent" in prompt


def test_persona_prompt_omits_tool_use_policy_when_no_tools() -> None:
    """case id: p07.tool_use_prompt.no_tools_omits_policy

    Zero-tool nodes (legacy chat-only personas) keep the byte-for-byte
    Sprint-6 shape so single-shot LLM calls don't pay the extra token
    cost when no tools could be called anyway.
    """

    prompt = _persona_system_prompt(_spec(), depth=0, has_tools=False)
    assert "Tool-use policy" not in prompt
    assert _tool_use_encouragement() not in prompt


def test_persona_prompt_default_has_tools_is_false_back_compat() -> None:
    """case id: p07.tool_use_prompt.default_has_tools_false

    The Sprint-5 callers (Available-nodes test, parity gates) call
    ``_persona_system_prompt`` without ``has_tools``; the default must
    stay ``False`` so the existing assertion-shape tests do not
    suddenly see an unexpected tool-use block.
    """

    prompt = _persona_system_prompt(_spec(), depth=0)
    assert "Tool-use policy" not in prompt


def test_persona_prompt_child_depth_still_gets_encouragement() -> None:
    """case id: p07.tool_use_prompt.child_depth_has_policy

    A dispatched child node (depth >= 1) is exactly the case the v18
    audit caught: ``wb-hh-image`` / ``screenwriter`` / ``tech-lead``
    activated under the producer and either called a tool or did not.
    The encouragement must be applied at depth >= 1 too.
    """

    prompt = _persona_system_prompt(_spec(), depth=1, has_tools=True)
    assert "Tool-use policy" in prompt
    # _spec() has no reports -> this depth-1 node is a leaf: the tool-use
    # policy is additive on top of the leaf-worker instruction.
    assert "leaf specialist" in prompt


def test_persona_prompt_root_depth_with_tools_keeps_dispatch_block() -> None:
    """case id: p07.tool_use_prompt.root_depth_keeps_dispatch

    Producer (depth 0) with tools must still get the Sprint-4 dispatch
    tutorial -- the tool-use policy is purely additive, not a
    replacement.
    """

    spec = _spec(
        available_nodes=(("screenwriter", "screenwriter"),),
    )
    prompt = _persona_system_prompt(spec, depth=0, has_tools=True)
    assert "dispatch" in prompt.lower()
    assert "DIRECT reports" in prompt
    assert "Tool-use policy" in prompt


# ---------------------------------------------------------------------------
# Wiring: _BrainBackedNodeAgent.run picks up has_tools from tool resolution.
# ---------------------------------------------------------------------------


class _FakeBrain:
    """Captures the ``system`` kwarg of the LLM call for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._resp = type(
            "_Msg",
            (),
            {
                "content": [type("_Block", (), {"text": "ok", "type": "text"})()],
                "stop_reason": "end_turn",
            },
        )()

    async def messages_create_async(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._resp

    def set_trace_context(self, ctx: dict[str, str]) -> None:  # noqa: ARG002
        return None


class _StubToolHost:
    """Returns a single fake tool definition so resolve_node_tools yields >0."""

    def lookup_tool_definition(self, name: str) -> dict[str, Any] | None:
        if name == "write_file":
            return {
                "name": "write_file",
                "description": "Write a file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        return None

    async def execute_tool(self, **kwargs: Any) -> str:  # noqa: ARG002
        return "wrote"


@pytest.mark.asyncio
async def test_node_agent_run_injects_tool_use_policy_when_tools_present() -> None:
    """case id: p07.tool_use_prompt.run_injects_when_tools_resolved

    Wires :class:`_BrainBackedNodeAgent` with a tool host that
    advertises ``write_file`` and a stub brain that captures the
    ``system`` kwarg. The captured prompt must contain the new
    tool-use policy paragraph -- this is the wiring that flips the v18
    behaviour where the prompt never encouraged tool use.
    """

    brain = _FakeBrain()
    # The brain swap to run_with_tools relies on
    # ``messages_create_async``; reuse FakeBrain by stubbing the
    # run-with-tools path to return the same fake response. The
    # simplest route here: build a spec whose tools resolve to one
    # entry via the host, run :meth:`run`, and assert the captured
    # system kwarg contains the encouragement.
    spec = _spec()
    agent = _BrainBackedNodeAgent(
        spec,
        brain,
        dispatch_callback=None,
        event_emitter=None,
        tool_host_provider=lambda: _StubToolHost(),
    )

    # Patch run_with_tools so we don't drag in the full tool-loop
    # machinery for this assertion: we just want to know the system
    # prompt the loop saw.
    from openakita.orgs import _default_agent_builder as mod

    captured: dict[str, Any] = {}

    async def fake_run_with_tools(**kwargs: Any) -> tuple[Any, int]:
        captured["system_prompt"] = kwargs.get("system_prompt")
        captured["tools"] = kwargs.get("tools")
        return brain._resp, 1

    original = mod.run_with_tools
    mod.run_with_tools = fake_run_with_tools  # type: ignore[assignment]
    try:
        await agent.run("write hello to /tmp/x.txt")
    finally:
        mod.run_with_tools = original  # type: ignore[assignment]

    assert captured.get("system_prompt"), "run_with_tools never invoked"
    assert "Tool-use policy" in captured["system_prompt"], (
        "Sprint-7 P0-B: the run() wiring must pass has_tools=True so the "
        "encouragement reaches the LLM when tools are resolved"
    )


@pytest.mark.asyncio
async def test_node_agent_run_omits_tool_use_policy_when_no_tools() -> None:
    """case id: p07.tool_use_prompt.run_omits_when_no_tools

    Zero-tool path -- the encouragement must NOT appear so the prompt
    stays byte-for-byte compatible with the Sprint-4 chat-only call
    shape that legacy nodes still use.
    """

    brain = _FakeBrain()
    spec = _spec(external_tools=(), enable_file_tools=False)
    agent = _BrainBackedNodeAgent(
        spec,
        brain,
        dispatch_callback=None,
        event_emitter=None,
        tool_host_provider=None,
    )
    await agent.run("hello")
    assert brain.calls, "brain.messages_create_async never called"
    system = brain.calls[0].get("system", "")
    assert "Tool-use policy" not in system
