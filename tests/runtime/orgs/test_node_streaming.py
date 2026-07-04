"""Token-level streaming for no-tools (writer/leaf) node agents.

Proves the new ``_BrainBackedNodeAgent`` streaming branch:

* a no-tools node streams its reply via ``brain.messages_create_stream`` +
  ``StreamAccumulator`` and emits throttled ``node_run_delta`` events;
* token usage is recorded (billing parity with the non-streaming path);
* ANY stream failure transparently falls back to ``messages_create_async``;
* with no event emitter wired (or an unsupported brain) streaming is skipped
  and the resilient non-streaming path runs unchanged.

These exercise the branch directly because in real orgs nodes default to
``enable_file_tools=True`` (so they carry the four file tools and take the
tool-use path); a genuinely no-tools node is the only one that streams.
"""

from __future__ import annotations

from typing import Any

import pytest

from openakita.orgs._default_agent_builder import _BrainBackedNodeAgent, _clean_thinking
from openakita.orgs._runtime_agent_pipeline import AgentSpec, current_command_id_var


def _spec() -> AgentSpec:
    # external_tools empty + enable_file_tools False => zero resolved tools =>
    # the streaming (no-tools) branch.
    return AgentSpec(
        org_id="org-stream",
        node_id="writer-a",
        role="文案写手",
        persona="资深文案",
        external_tools=(),
        enable_file_tools=False,
    )


class _FakeBrain:
    def __init__(
        self,
        *,
        deltas: list[str],
        fail_stream: bool = False,
        thinking: list[str] | None = None,
    ) -> None:
        self._deltas = deltas
        self._fail_stream = fail_stream
        self._thinking = thinking or []
        self.async_called = False
        self.recorded_usage: list[Any] = []

    def set_trace_context(self, ctx: dict[str, str]) -> None:  # noqa: D401
        pass

    def get_current_endpoint_info(self) -> dict[str, str]:
        return {"name": "fake-ep", "model": "fake-model"}

    def _record_usage(self, response: Any) -> None:
        self.recorded_usage.append(response.usage)

    async def messages_create_stream(self, **_kw: Any):
        if self._fail_stream:
            raise RuntimeError("stream boom")
        yield {"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 0}}}
        # Optional thinking block first (index 0) so reasoning streams before the
        # visible text — mirrors a model with extended thinking enabled.
        if self._thinking:
            yield {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            }
            for t in self._thinking:
                yield {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": t},
                }
            yield {"type": "content_block_stop", "index": 0}
        text_idx = 1 if self._thinking else 0
        yield {
            "type": "content_block_start",
            "index": text_idx,
            "content_block": {"type": "text", "text": ""},
        }
        for d in self._deltas:
            yield {
                "type": "content_block_delta",
                "index": text_idx,
                "delta": {"type": "text_delta", "text": d},
            }
        yield {"type": "content_block_stop", "index": text_idx}
        yield {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"input_tokens": 12, "output_tokens": 34},
        }
        yield {"type": "message_stop"}

    async def messages_create_async(self, **_kw: Any) -> Any:
        self.async_called = True

        class _Block:
            type = "text"
            text = "FALLBACK-TEXT"

        class _Resp:
            content = [_Block()]

        return _Resp()


class _Capture:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, payload))

    @property
    def deltas(self) -> list[dict[str, Any]]:
        return [p for n, p in self.events if n == "node_run_delta"]

    @property
    def thinking_events(self) -> list[dict[str, Any]]:
        return [p for n, p in self.events if n == "node_thinking"]


@pytest.mark.asyncio
async def test_no_tools_node_streams_and_records_usage() -> None:
    cap = _Capture()
    brain = _FakeBrain(deltas=["剑来", "动画", "宣传文案：", "少年扛剑而行。"])
    agent = _BrainBackedNodeAgent(_spec(), brain, event_emitter=cap)

    token = current_command_id_var.set("cmd-stream-1")
    try:
        result = await agent.run("写一段宣传文案")
    finally:
        current_command_id_var.reset(token)

    # Final text is the concatenated stream.
    assert result == "剑来动画宣传文案：少年扛剑而行。"
    # We streamed (no fallback).
    assert brain.async_called is False
    # node_run_delta events were emitted, the last one marked done.
    assert cap.deltas, "expected node_run_delta events"
    assert cap.deltas[-1]["done"] is True
    assert any(d["text"] for d in cap.deltas)
    # Billing recorded with the streamed usage.
    assert brain.recorded_usage
    assert brain.recorded_usage[-1].input_tokens == 12
    assert brain.recorded_usage[-1].output_tokens == 34


@pytest.mark.asyncio
async def test_stream_failure_falls_back_to_non_streaming() -> None:
    cap = _Capture()
    brain = _FakeBrain(deltas=["x"], fail_stream=True)
    agent = _BrainBackedNodeAgent(_spec(), brain, event_emitter=cap)

    token = current_command_id_var.set("cmd-stream-2")
    try:
        result = await agent.run("写一段宣传文案")
    finally:
        current_command_id_var.reset(token)

    # Stream raised -> resilient non-streaming path produced the reply.
    assert result == "FALLBACK-TEXT"
    assert brain.async_called is True
    # No (or no usable) deltas leaked from the failed stream.
    assert all(d.get("done") is not True for d in cap.deltas)


@pytest.mark.asyncio
async def test_no_tools_node_streams_thinking_live_and_persists_once() -> None:
    """图4: a no-tools node surfaces its REASONING live in ``node_run_delta``
    (``thinking`` field) and persists it exactly ONCE at the end as a
    ``node_thinking`` event — without disturbing the deliverable text/usage."""
    cap = _Capture()
    brain = _FakeBrain(
        deltas=["剑来动画", "宣传文案：少年扛剑而行。"],
        thinking=["让我先梳理结构：", "开头—卖点—行动号召。"],
    )
    agent = _BrainBackedNodeAgent(_spec(), brain, event_emitter=cap)

    token = current_command_id_var.set("cmd-think-1")
    try:
        result = await agent.run("写一段宣传文案")
    finally:
        current_command_id_var.reset(token)

    # Deliverable text is unaffected by the reasoning channel.
    assert result == "剑来动画宣传文案：少年扛剑而行。"
    assert brain.async_called is False

    # Reasoning shows up LIVE on the rolling deltas.
    live_thinking = "".join(d.get("thinking", "") for d in cap.deltas)
    assert "梳理结构" in live_thinking

    # Exactly one persisted node_thinking event carrying the final reasoning.
    assert len(cap.thinking_events) == 1
    persisted = cap.thinking_events[0]["thinking"]
    assert "行动号召" in persisted
    assert cap.thinking_events[0]["command_id"] == "cmd-think-1"
    assert cap.thinking_events[0]["node_id"] == "writer-a"


def test_clean_thinking_strips_noise_prefixes_and_tags() -> None:
    """The reasoning cleaner drops ``<thinking>`` tags + noise prefixes so the
    UI snippet reads as clean Chinese reasoning (避免污染 思维链 / 文件名)."""
    assert _clean_thinking("<thinking>分析需求</thinking>") == "分析需求"
    assert _clean_thinking("thinking: 先看用户意图") == "先看用户意图"
    assert _clean_thinking("思考：拆解任务") == "拆解任务"
    assert _clean_thinking("") == ""
    assert _clean_thinking(None) == ""
    # collapse excessive blank lines
    assert _clean_thinking("a\n\n\n\nb") == "a\n\nb"


@pytest.mark.asyncio
async def test_no_emitter_skips_streaming() -> None:
    # Without an event emitter there is nobody to receive deltas, so streaming
    # is skipped and the non-streaming path runs unchanged.
    brain = _FakeBrain(deltas=["x", "y"])
    agent = _BrainBackedNodeAgent(_spec(), brain, event_emitter=None)

    token = current_command_id_var.set("cmd-stream-3")
    try:
        result = await agent.run("写一段宣传文案")
    finally:
        current_command_id_var.reset(token)

    assert result == "FALLBACK-TEXT"
    assert brain.async_called is True
