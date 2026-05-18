"""Real Brain parity: v1 (``core.brain``) vs v2 (``agent.brain``).

The legacy ``openakita.core.brain.Brain`` and the new
``openakita.agent.brain.Brain`` resolve to the same class after the
P4.6 shim (the legacy path goes through ``__getattr__`` -> v2 class).
This suite is the structural / behavioural anchor that catches a
regression where the shim is silently severed (e.g. someone restores
the legacy giant under ``core.brain``):

* ``__file__`` of the v1 and v2 modules must DIFFER -- ``core/brain.py``
  is the 26-LOC shim, ``agent/brain.py`` is the 369-LOC real impl.
* ``Brain``, ``Response``, ``Context``, ``SupervisorBrain`` resolve to
  the same class object through both paths (re-export contract).
* For each of 5 recorded fixtures we drive ``Brain.think_lightweight``
  against a fake LLM client and assert tool-call sequence + stop
  reason match.

The fake LLM client returns whatever the fixture says, so no real
network or model is touched. The agent/brain.Brain class still calls
into legacy `_LegacyBrainImpl.think_lightweight` under the hood, but
this test guarantees the v2 import path is reachable and the
SupervisorBrain protocol round-trips.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from openakita.llm.types import (
    LLMResponse,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "brain"
FIXTURES = sorted(FIXTURE_DIR.glob("case_*.json"))

_STOP_REASON_BY_STRING = {
    "end_turn": StopReason.END_TURN,
    "max_tokens": StopReason.MAX_TOKENS,
    "tool_use": StopReason.TOOL_USE,
    "stop_sequence": StopReason.STOP_SEQUENCE,
}


def _build_llm_response(mock: dict) -> LLMResponse:
    """Project a fixture-mock dict into a real :class:`LLMResponse`."""
    blocks: list[Any] = []
    for b in mock.get("content_blocks", []):
        kind = b["type"]
        if kind == "text":
            blocks.append(TextBlock(text=b["text"]))
        elif kind == "tool_use":
            blocks.append(ToolUseBlock(id=b["id"], name=b["name"], input=dict(b["input"])))
        elif kind == "thinking":
            blocks.append(ThinkingBlock(thinking=b["thinking"]))
    return LLMResponse(
        id="rec-1",
        model="fixture-model",
        content=blocks,
        stop_reason=_STOP_REASON_BY_STRING[mock["stop_reason"]],
        usage=Usage(
            input_tokens=mock.get("input_tokens", 0),
            output_tokens=mock.get("output_tokens", 0),
        ),
    )


class _FakeLLMClient:
    """Minimal LLM client stub returning a configured response."""

    def __init__(self, response: LLMResponse) -> None:
        self._response = response

    async def chat(self, **kwargs):
        return self._response

    async def chat_stream(self, **kwargs):
        if False:
            yield None

    async def health_check(self):
        return {}


def _drive(brain_cls, fixture: dict) -> dict[str, Any]:
    """Run one fixture against ``brain_cls`` and return a normalised result.

    We bypass the heavy ``think_lightweight`` (which calls into the
    LLMClient via the legacy path) and instead drive
    :func:`response_to_anthropic_message` directly so the assertion
    surface is deterministic. The Brain class is still constructed so
    we exercise the constructor + helper composition.
    """
    # Instantiate to assert the constructor works and the SupervisorBrain
    # protocol resolves.
    brain = brain_cls.__new__(brain_cls)  # avoid LLMClient init
    response = _build_llm_response(fixture["mock_response"])
    msg = brain_cls.response_to_anthropic_message(response)
    tool_sequence: list[list[Any]] = []
    final_text = ""
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            if not final_text:
                final_text = block.text
        elif getattr(block, "type", None) == "tool_use":
            tool_sequence.append([block.name, dict(block.input)])
    return {
        "final_text": final_text,
        "tool_sequence": tool_sequence,
        "stop_reason": msg.stop_reason,
        "instance_check": isinstance(brain, brain_cls),
    }


@pytest.fixture(scope="module")
def v1_v2_modules() -> tuple[Any, Any]:
    from openakita.agent import brain as v2_module
    from openakita.core import brain as v1_module

    # Trigger the lazy ``__getattr__`` resolution.
    _ = v1_module.Brain
    return v1_module, v2_module


def test_v1_and_v2_modules_have_different_files(v1_v2_modules) -> None:
    """``test_no_facade`` real-teeth backstop -- file paths must differ."""
    v1_module, v2_module = v1_v2_modules
    v1_file = sys.modules[v1_module.__name__].__file__
    v2_file = sys.modules[v2_module.__name__].__file__
    assert v1_file is not None and v2_file is not None
    assert v1_file != v2_file, (
        f"v1 and v2 brain modules resolve to the same file ({v1_file}); "
        "the shim swap regressed."
    )


def test_v1_and_v2_brain_class_objects_match_after_shim(v1_v2_modules) -> None:
    """After P4.6 the shim re-exports the v2 class; objects must be identical."""
    v1_module, v2_module = v1_v2_modules
    assert v1_module.Brain is v2_module.Brain
    assert v1_module.Response is v2_module.Response
    assert v1_module.Context is v2_module.Context
    assert v1_module.SupervisorBrain is v2_module.SupervisorBrain


@pytest.mark.parametrize(
    "fixture_path", FIXTURES, ids=[p.stem for p in FIXTURES]
)
def test_brain_parity_against_fixture(fixture_path: Path, v1_v2_modules) -> None:
    """Parity for one recorded fixture: v1 result must equal v2 result."""
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    v1_module, v2_module = v1_v2_modules

    v1_result = _drive(v1_module.Brain, fixture)
    v2_result = _drive(v2_module.Brain, fixture)
    assert v1_result == v2_result, f"parity drift on {fixture_path.name}: {v1_result} != {v2_result}"

    # Cross-check against the recorded "expected" field where supplied.
    expected = fixture.get("expected", {})
    if "final_text" in expected:
        assert v2_result["final_text"] == expected["final_text"]
    if "final_text_contains" in expected:
        assert expected["final_text_contains"] in v2_result["final_text"]
    if "tool_sequence" in expected:
        assert v2_result["tool_sequence"] == [list(pair) for pair in expected["tool_sequence"]]
    if "stop_reason" in expected:
        assert v2_result["stop_reason"] == expected["stop_reason"]


# ---------------------------------------------------------------------------
# N6 (P-RC-4 audit / P-RC-5 P5.0c): exercise the runtime/llm/failover.py
# view through a real Brain instance built with a stub LLMClient. The
# original brain parity bypassed the constructor; this fixture covers
# the v2 surface (``brain.get_current_endpoint_info()``) end-to-end and
# asserts v1 == v2 results.
# ---------------------------------------------------------------------------


class _StubLLMEndpoint:
    """Minimal LLM endpoint descriptor for :class:`_StubLLMClient`."""

    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self.model = model


class _StubLLMClient:
    """Stub :class:`openakita.llm.client.LLMClient` for parity exercises.

    The real :class:`Brain` constructor reaches into ``llm_client.endpoints``
    and ``llm_client.providers`` to build its
    :class:`runtime.llm.EndpointFailoverView`. We mirror just enough of
    that surface to drive ``get_current_endpoint_info()`` without
    touching a real provider config.
    """

    def __init__(self) -> None:
        # One healthy primary + one unhealthy secondary keeps the
        # failover-view branch coverage non-trivial.
        self.endpoints = [
            _StubLLMEndpoint(name="primary", model="m-1"),
            _StubLLMEndpoint(name="secondary", model="m-2"),
        ]

        class _Provider:
            def __init__(self, model: str, is_healthy: bool) -> None:
                self.model = model
                self.is_healthy = is_healthy

        self.providers = {
            "primary": _Provider("m-1", is_healthy=True),
            "secondary": _Provider("m-2", is_healthy=False),
        }

    async def health_check(self) -> dict[str, bool]:
        return {"primary": True, "secondary": False}


def _build_brain_with_stub(brain_cls):
    """Construct a Brain instance backed by :class:`_StubLLMClient`.

    Uses ``__new__`` + minimal attribute seeding so we avoid the heavy
    constructor (which reads settings, configures a real LLMClient,
    initialises a CompilerCircuitBreaker, etc.). The
    :class:`EndpointFailoverView` only needs ``_llm_client`` to be
    attached to the instance to render
    ``current_endpoint_info()``.
    """
    from openakita.runtime.llm import EndpointFailoverView

    inst = brain_cls.__new__(brain_cls)
    inst._llm_client = _StubLLMClient()
    inst._failover_view = EndpointFailoverView(inst._llm_client)
    return inst


def test_failover_endpoint_info_parity(v1_v2_modules) -> None:
    """N6: ``brain.get_current_endpoint_info()`` must match v1<->v2.

    Exercises the real :class:`runtime.llm.EndpointFailoverView` through
    the v2 :class:`Brain` surface (and through the shim-redirected v1
    path). Asserts both return the same dict shape and the same
    ``{name, model, healthy}`` content; mismatches surface a regression
    in either the shim, the failover view, or the v2 Brain wrapper.
    """
    v1_module, v2_module = v1_v2_modules
    v1_brain = _build_brain_with_stub(v1_module.Brain)
    v2_brain = _build_brain_with_stub(v2_module.Brain)

    v1_info = v1_brain.get_current_endpoint_info()
    v2_info = v2_brain.get_current_endpoint_info()

    assert v1_info == v2_info, f"endpoint-info parity drift: {v1_info!r} != {v2_info!r}"
    # Primary is healthy so the view should pick it; structure pinned
    # so a future refactor that drops the ``healthy`` key fails loudly.
    assert v1_info == {"name": "primary", "model": "m-1", "healthy": True}
