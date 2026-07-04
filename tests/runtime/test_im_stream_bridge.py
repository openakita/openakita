"""Tests for :mod:`openakita.runtime.im_stream_bridge`.

P-RC-1 commit 3. Covers the renderer's translation matrix plus the
relay's resilience against send-side failures. The bridge runs as
a background task in production; the tests use a real in-memory
``StreamBus`` so the async iteration semantics are exercised end
to end.
"""

from __future__ import annotations

import asyncio

import pytest

from openakita.runtime.im_stream_bridge import ImStreamBridge
from openakita.runtime.stream import StreamBus


async def _drive_bridge(bridge: ImStreamBridge, send) -> asyncio.Task:
    """Spawn the relay loop and yield once so the subscription registers."""
    task = asyncio.create_task(bridge.relay_to(send, session_key="sess"))
    await asyncio.sleep(0)  # let the iterator subscribe
    return task


async def _drain(bus: StreamBus, task: asyncio.Task, expected: int) -> None:
    """Wait for ``expected`` messages to be processed or the bus to close."""
    for _ in range(50):
        await asyncio.sleep(0)
        if task.done():
            break
    await bus.close()
    try:
        await asyncio.wait_for(task, timeout=1)
    except (TimeoutError, asyncio.CancelledError):
        pass


async def test_lifecycle_started_renders_to_im_text() -> None:
    bus = StreamBus()
    bridge = ImStreamBridge(stream_bus=bus, channels=("lifecycle",))
    captured: list[tuple[str, str]] = []

    async def send(key: str, text: str) -> None:
        captured.append((key, text))

    task = await _drive_bridge(bridge, send)
    await bus.emit("lifecycle", "started", {"task": "hi"})
    await _drain(bus, task, expected=1)

    assert captured == [("sess", "已收到任务，正在思考…")]
    assert bridge.relayed == 1
    assert bridge.suppressed == 0


async def test_lifecycle_cancelled_includes_reason() -> None:
    bus = StreamBus()
    bridge = ImStreamBridge(stream_bus=bus, channels=("lifecycle",))
    captured: list[str] = []

    async def send(key: str, text: str) -> None:
        captured.append(text)

    task = await _drive_bridge(bridge, send)
    await bus.emit("lifecycle", "cancelled", {"reason": "user_cancel_via_im"})
    await _drain(bus, task, expected=1)

    assert captured == ["已应你的请求停止任务。user_cancel_via_im。"]


async def test_messages_delegation_renders_with_speaker() -> None:
    bus = StreamBus()
    bridge = ImStreamBridge(stream_bus=bus, channels=("messages",))
    captured: list[str] = []

    async def send(key: str, text: str) -> None:
        captured.append(text)

    task = await _drive_bridge(bridge, send)
    await bus.emit("messages", "delegation", {"speaker": "art_director"})
    await _drain(bus, task, expected=1)

    assert captured == ["正在交给 art_director 处理…"]


async def test_progress_ledger_awaiting_human_emits_wait_prompt() -> None:
    bus = StreamBus()
    bridge = ImStreamBridge(stream_bus=bus, channels=("progress_ledger",))
    captured: list[str] = []

    async def send(key: str, text: str) -> None:
        captured.append(text)

    task = await _drive_bridge(bridge, send)
    await bus.emit("progress_ledger", "awaiting_human", {})
    await _drain(bus, task, expected=1)

    assert captured == ["等待用户确认…"]


async def test_unknown_event_is_suppressed_without_send() -> None:
    bus = StreamBus()
    bridge = ImStreamBridge(stream_bus=bus, channels=("lifecycle",))
    captured: list[str] = []

    async def send(key: str, text: str) -> None:
        captured.append(text)

    task = await _drive_bridge(bridge, send)
    await bus.emit("lifecycle", "made_up_event", {})
    await _drain(bus, task, expected=0)

    assert captured == []
    assert bridge.relayed == 0
    assert bridge.suppressed == 1


async def test_send_failure_is_swallowed_and_counted() -> None:
    bus = StreamBus()
    bridge = ImStreamBridge(stream_bus=bus, channels=("lifecycle",))

    async def send(key: str, text: str) -> None:
        raise RuntimeError("adapter exploded")

    task = await _drive_bridge(bridge, send)
    await bus.emit("lifecycle", "started", {"task": "x"})
    await _drain(bus, task, expected=1)

    assert bridge.relayed == 0
    assert bridge.suppressed == 1


def test_constructor_rejects_empty_channels() -> None:
    bus = StreamBus()
    with pytest.raises(ValueError, match="at least one channel"):
        ImStreamBridge(stream_bus=bus, channels=())
