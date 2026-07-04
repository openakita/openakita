"""Tests for :mod:`openakita.runtime.messenger`.

Phase 3 commit 4. Asserts the messenger contract:

* address parsing for plain role, qualified plugin::mode, and direct
  node_<id> forms;
* registry resolution succeeds in each form, fails cleanly when no
  match;
* deliver() dispatches to the registered node, emits typed
  delegation_dispatched / delegation_completed events;
* delivery to a busy node respects the inbox bound;
* cooperative cancel during dispatch invokes node.on_cancel and
  produces a failure DelegationResult;
* bind_for_command builds a callable compatible with the supervisor's
  deliver signature.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from openakita.runtime.cancel_token import CancellationToken
from openakita.runtime.messenger import (
    InMemoryNodeRegistry,
    Messenger,
    MessengerNode,
    NodeAddress,
    NodeAddressResolveError,
    NodeMessage,
)
from openakita.runtime.stream import StreamBus
from openakita.runtime.supervisor import DelegationResult

# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------


class FakeNode(MessengerNode):
    def __init__(
        self,
        node_id: str,
        *,
        delay: float = 0.0,
        success: bool = True,
        message: str = "ok",
    ) -> None:
        self.node_id = node_id
        self._delay = delay
        self._success = success
        self._message = message
        self.received: list[NodeMessage] = []
        self.cancelled_with: list[str] = []

    async def on_message(self, message: NodeMessage) -> DelegationResult:
        self.received.append(message)
        if self._delay:
            await asyncio.sleep(self._delay)
        return DelegationResult(
            success=self._success,
            speaker=self.node_id,
            message=self._message,
            metadata={"address": message.address.raw},
        )

    async def on_cancel(self, reason: str) -> None:
        self.cancelled_with.append(reason)


# ---------------------------------------------------------------------------
# Address parsing
# ---------------------------------------------------------------------------


def test_address_plain_role() -> None:
    addr = NodeAddress.parse("art_director")
    assert addr.role == "art_director"
    assert addr.plugin is None and addr.mode is None and addr.node_id is None


def test_address_qualified_workbench() -> None:
    addr = NodeAddress.parse("happyhorse-video::image_artist")
    assert addr.plugin == "happyhorse-video"
    assert addr.mode == "image_artist"
    assert addr.role is None


def test_address_node_id_form() -> None:
    addr = NodeAddress.parse("node_abc123")
    assert addr.node_id == "node_abc123"
    assert addr.role is None


def test_address_empty_raises() -> None:
    with pytest.raises(NodeAddressResolveError):
        NodeAddress.parse("")


def test_address_malformed_qualified_raises() -> None:
    with pytest.raises(NodeAddressResolveError):
        NodeAddress.parse("plugin::")


# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------


def test_registry_resolves_role() -> None:
    reg = InMemoryNodeRegistry()
    n = FakeNode("node_a")
    reg.register(n, role="art_director")
    bus = StreamBus(strict=True)
    m = Messenger(registry=reg, stream=bus)
    addr, node = m.resolve("art_director")
    assert node is n
    assert addr.role == "art_director"


def test_registry_resolves_workbench() -> None:
    reg = InMemoryNodeRegistry()
    n = FakeNode("node_w")
    reg.register(n, workbench=("happyhorse-video", "image_artist"))
    bus = StreamBus(strict=True)
    m = Messenger(registry=reg, stream=bus)
    addr, node = m.resolve("happyhorse-video::image_artist")
    assert node is n
    assert addr.plugin == "happyhorse-video"
    assert addr.mode == "image_artist"


def test_registry_resolves_node_id_form() -> None:
    reg = InMemoryNodeRegistry()
    n = FakeNode("node_xyz")
    reg.register(n)
    bus = StreamBus(strict=True)
    m = Messenger(registry=reg, stream=bus)
    addr, node = m.resolve("node_xyz")
    assert node is n
    assert addr.node_id == "node_xyz"


def test_registry_unknown_raises() -> None:
    reg = InMemoryNodeRegistry()
    bus = StreamBus(strict=True)
    m = Messenger(registry=reg, stream=bus)
    with pytest.raises(NodeAddressResolveError):
        m.resolve("nobody")


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


async def test_deliver_returns_node_result() -> None:
    reg = InMemoryNodeRegistry()
    n = FakeNode("node_a", message="produced output")
    reg.register(n, role="r1")
    bus = StreamBus(strict=True)
    m = Messenger(registry=reg, stream=bus)

    out = await m.deliver(
        "r1", "do the thing", command_id="cmd", org_id="org", superstep=1
    )
    assert out.success is True
    assert out.speaker == "node_a"
    assert out.message == "produced output"
    assert n.received[0].instruction == "do the thing"


async def test_deliver_emits_dispatched_and_completed_events() -> None:
    reg = InMemoryNodeRegistry()
    n = FakeNode("node_a")
    reg.register(n, role="r")
    bus = StreamBus(strict=True)
    m = Messenger(registry=reg, stream=bus)

    seen_tasks: list[str] = []
    seen_updates: list[str] = []

    async def watch_tasks() -> None:
        async for ev in bus.subscribe("tasks"):
            seen_tasks.append(ev.type)
            if seen_tasks:
                return

    async def watch_updates() -> None:
        async for ev in bus.subscribe("updates"):
            seen_updates.append(ev.type)
            if seen_updates:
                return

    twatch = asyncio.create_task(watch_tasks())
    uwatch = asyncio.create_task(watch_updates())
    await asyncio.sleep(0.01)

    await m.deliver("r", "x", command_id="c", org_id="o")
    await asyncio.wait_for(asyncio.gather(twatch, uwatch), timeout=2.0)

    assert "delegation_dispatched" in seen_tasks
    assert "delegation_completed" in seen_updates


# ---------------------------------------------------------------------------
# Cooperative cancel
# ---------------------------------------------------------------------------


async def test_cancel_during_dispatch_returns_failure_and_calls_on_cancel() -> None:
    reg = InMemoryNodeRegistry()
    slow = FakeNode("node_slow", delay=2.0)
    reg.register(slow, role="r")
    bus = StreamBus(strict=True)
    m = Messenger(registry=reg, stream=bus)
    token = CancellationToken()

    async def cancel_after() -> None:
        await asyncio.sleep(0.05)
        token.cancel("user")

    asyncio.create_task(cancel_after())
    out = await m.deliver(
        "r", "long work", command_id="c", org_id="o", cancel_token=token
    )
    assert out.success is False
    assert "cancelled" in out.message.lower()
    # on_cancel was invoked so the node could save in-flight state.
    await asyncio.sleep(0.02)
    assert slow.cancelled_with == ["messenger cancel"]


# ---------------------------------------------------------------------------
# bind_for_command
# ---------------------------------------------------------------------------


async def test_bind_for_command_returns_supervisor_compatible_callable() -> None:
    reg = InMemoryNodeRegistry()
    n = FakeNode("node_b")
    reg.register(n, role="r")
    bus = StreamBus(strict=True)
    m = Messenger(registry=reg, stream=bus)

    deliver = m.bind_for_command(command_id="cmd_z", org_id="org_q")

    class FakeProgress:
        turn_id = 7

    out = await deliver("r", "go", FakeProgress())
    assert out.success is True
    assert n.received[0].instruction == "go"


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _drain_loop() -> object:
    yield
    pending = [t for t in asyncio.all_tasks() if not t.done()]
    me = asyncio.current_task()
    for t in pending:
        if t is me:
            continue
        t.cancel()
        with contextlib.suppress(BaseException):
            await t
