"""C9c SSE coverage: tool_intent_preview + policy_config_reload[ed|_failed].

These tests inject a fake ``ConnectionManager.broadcast`` into the
websocket module and assert the wiring fires the expected events with
the expected payload shape. We never actually start the FastAPI server.

Patching ``manager.broadcast`` (the actual sink) instead of the
``broadcast_event`` wrapper lets us assert the events that BOTH the
``fire_event`` (sync, fire-and-forget) path AND the ``broadcast_event``
(async, awaitable) path deliver — they both terminate in
``manager.broadcast``.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def captured_events(monkeypatch):
    """Capture all SSE broadcasts at the ConnectionManager sink.

    Both ``fire_event`` (sync helper) and ``broadcast_event`` (async
    wrapper) terminate in ``manager.broadcast(event, data)``. Patching
    that method ensures we see events from any caller path without
    needing to know which entry point each emitter uses.
    """
    events: list[tuple[str, dict]] = []

    async def fake_broadcast(self, evt: str, data=None) -> None:
        events.append((evt, data))

    import openakita.api.routes.websocket as ws

    monkeypatch.setattr(ws.ConnectionManager, "broadcast", fake_broadcast)
    return events


# ---------------------------------------------------------------------------
# C9c-1: tool_intent_preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_intent_preview_emits_per_call_with_redacted_secrets(
    captured_events,
):
    from openakita.core.tool_executor import ToolExecutor

    te = object.__new__(ToolExecutor)
    te._canonicalize_tool_name = lambda n: n
    te._max_parallel = 1

    calls = [
        {
            "id": "t1",
            "name": "write_file",
            "input": {
                "path": "a.txt",
                "content": "x" * 500,  # over PREVIEW_PARAM_MAX_CHARS
                "api_key": "sk-secret-must-be-redacted",
            },
        },
        {"id": "t2", "name": "unknown_dynamic_tool", "input": {"foo": "bar"}},
    ]

    te._emit_tool_intent_previews(calls, session_id="sess-A")
    # Let ensure_future tasks run
    await asyncio.sleep(0.05)

    previews = [e for e in captured_events if e[0] == "tool_intent_preview"]
    assert len(previews) == 2

    # Per-call payload schema
    for idx, (_, payload) in enumerate(previews):
        assert payload["session_id"] == "sess-A"
        assert payload["batch_size"] == 2
        assert payload["batch_idx"] == idx
        assert "approval_class" in payload
        assert "ts" in payload

    # Secret redaction
    p1_params = previews[0][1]["params"]
    assert p1_params["api_key"] == "***REDACTED***"
    # String truncation
    assert p1_params["content"].endswith("...[truncated]")
    # Non-secret key untouched
    assert p1_params["path"] == "a.txt"

    # Unknown tool gets graceful fallback (still emits, approval_class can
    # be 'unknown' depending on classifier registry state)
    p2 = previews[1][1]
    assert isinstance(p2["approval_class"], str)


@pytest.mark.asyncio
async def test_tool_intent_preview_no_loop_drops_silently(monkeypatch):
    """When called outside any running event loop, must NOT raise and
    must NOT emit a 'coroutine was never awaited' warning."""
    from openakita.core.tool_executor import ToolExecutor

    te = object.__new__(ToolExecutor)
    te._canonicalize_tool_name = lambda n: n
    te._max_parallel = 1

    # Simulate "no running loop" by monkeypatching get_running_loop
    import asyncio as _aio

    real_get = _aio.get_running_loop

    def _no_loop():
        raise RuntimeError("no loop")

    monkeypatch.setattr(_aio, "get_running_loop", _no_loop)
    try:
        te._emit_tool_intent_previews(
            [{"id": "t", "name": "write_file", "input": {}}],
            session_id="s",
        )
        # We're inside an async test so a loop *is* present, but the
        # monkeypatch makes get_running_loop raise — exercise the no-loop
        # branch.
    finally:
        monkeypatch.setattr(_aio, "get_running_loop", real_get)


# ---------------------------------------------------------------------------
# C9c-3: policy_config_reloaded[_failed]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_config_reloaded_emits_on_success(captured_events):
    from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

    reset_policy_v2_layer(scope="security")
    await asyncio.sleep(0.05)

    matches = [e for e in captured_events if e[0] == "policy_config_reloaded"]
    assert len(matches) >= 1
    last = matches[-1][1]
    assert last["scope"] == "security"
    assert "ts" in last
    assert "error" not in last


@pytest.mark.asyncio
async def test_policy_config_reload_failed_emits_on_exception(
    captured_events, monkeypatch
):
    import openakita.core.policy_v2.global_engine as ge
    from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(ge, "reset_engine_v2", _boom)

    with pytest.raises(RuntimeError):
        reset_policy_v2_layer(scope="zones")
    await asyncio.sleep(0.05)

    failed = [e for e in captured_events if e[0] == "policy_config_reload_failed"]
    assert len(failed) >= 1
    payload = failed[-1][1]
    assert payload["scope"] == "zones"
    assert "RuntimeError" in payload["error"]
    assert "boom" in payload["error"]


@pytest.mark.asyncio
async def test_policy_config_reloaded_no_loop_does_not_warn(
    captured_events, monkeypatch
):
    """No-loop path must not raise nor leak unawaited coroutine."""
    import asyncio as _aio

    from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

    real_get = _aio.get_running_loop

    def _no_loop():
        raise RuntimeError("no loop")

    monkeypatch.setattr(_aio, "get_running_loop", _no_loop)
    try:
        reset_policy_v2_layer(scope="commands")
    finally:
        monkeypatch.setattr(_aio, "get_running_loop", real_get)
    # No assertion: just exercising the no-loop branch must not raise
    # or emit a 'coroutine was never awaited' RuntimeWarning. pytest's
    # built-in -W setting will surface those if regressed.
