"""Node addressing and routing for the v2 runtime.

The former ``orgs/messenger.py`` handled message envelope
formatting, agent activation, broadcast coalescing, and a half-dozen
ad-hoc retry / dedup heuristics. The v2 messenger is much smaller
because the supervisor (ADR-0004) and the StreamBus (ADR-0006) own
most of those concerns now. What remains is the piece nobody else
owns:

1. **Address resolution** — turn a ``next_speaker`` string from the
   ProgressLedger ("art_director", "happyhorse-video::image_artist")
   into the concrete :class:`NodeProtocol` instance that should run.
2. **Delivery dispatch** — call the resolved node's ``on_message`` and
   return a :class:`DelegationResult` to the supervisor.
3. **Inbox / outbox** — bounded per-node queues so a node that is
   already busy does not lose messages while it finishes its current
   turn (the supervisor itself is single-flight per command, so this
   is mostly belt-and-braces for future parallel sub-orgs).

This module assumes a :class:`NodeRegistry` it can ask for a node by
id; that registry is populated by the runtime facade. Tests inject a
fake registry directly so the messenger can be exercised without any
real nodes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from .cancel_token import CancellationToken
from .stream import StreamBus
from .supervisor import DelegationResult

__all__ = [
    "Messenger",
    "NodeRegistry",
    "InMemoryNodeRegistry",
    "NodeAddress",
    "NodeAddressResolveError",
    "NodeMessage",
    "MessengerNode",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Address parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeAddress:
    """A parsed ``next_speaker`` address.

    The plain form is ``role`` (matched against
    :attr:`NodeRegistry.find_by_role`). The qualified form is
    ``plugin::mode`` (used by WorkbenchNode for explicit mode pinning,
    see ADR-0009). The runtime form is ``node_<id>`` for direct
    routing by node id when the supervisor already knows it.
    """

    raw: str
    plugin: str | None = None
    role: str | None = None
    mode: str | None = None
    node_id: str | None = None

    @classmethod
    def parse(cls, raw: str) -> NodeAddress:
        text = (raw or "").strip()
        if not text:
            raise NodeAddressResolveError("address is empty")
        if text.startswith("node_"):
            return cls(raw=text, node_id=text)
        if "::" in text:
            plugin, _, mode = text.partition("::")
            if not plugin or not mode:
                raise NodeAddressResolveError(f"qualified address {text!r} must be 'plugin::mode'")
            return cls(raw=text, plugin=plugin, mode=mode)
        return cls(raw=text, role=text)


class NodeAddressResolveError(LookupError):
    """Raised when an address cannot be resolved to a registered node."""


# ---------------------------------------------------------------------------
# Node-side protocol used by the messenger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeMessage:
    """A delegation envelope handed to a node's ``on_message``."""

    speaker: str
    address: NodeAddress
    instruction: str
    correlation_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    issued_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@runtime_checkable
class MessengerNode(Protocol):
    """Subset of :class:`runtime.nodes.NodeProtocol` the messenger uses.

    Avoids a forward import on ``nodes/`` (ADR-0002 layering: messenger
    must not depend on nodes/). Phase 4 ``nodes/base.py`` will satisfy
    this protocol structurally.
    """

    node_id: str

    async def on_message(self, message: NodeMessage) -> DelegationResult: ...

    async def on_cancel(self, reason: str) -> None: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class NodeRegistry(Protocol):
    """Address book of live nodes."""

    def get_by_id(self, node_id: str) -> MessengerNode | None: ...
    def find_by_role(self, role: str) -> MessengerNode | None: ...
    def find_by_workbench(self, plugin: str, mode: str) -> MessengerNode | None: ...


class InMemoryNodeRegistry:
    """Reference :class:`NodeRegistry` used in tests and small deployments."""

    def __init__(self) -> None:
        self._by_id: dict[str, MessengerNode] = {}
        self._by_role: dict[str, MessengerNode] = {}
        self._by_workbench: dict[tuple[str, str], MessengerNode] = {}

    def register(
        self,
        node: MessengerNode,
        *,
        role: str | None = None,
        workbench: tuple[str, str] | None = None,
    ) -> None:
        self._by_id[node.node_id] = node
        if role is not None:
            self._by_role[role] = node
        if workbench is not None:
            self._by_workbench[workbench] = node

    def unregister(self, node_id: str) -> None:
        self._by_id.pop(node_id, None)
        for role, node in list(self._by_role.items()):
            if node.node_id == node_id:
                self._by_role.pop(role, None)
        for key, node in list(self._by_workbench.items()):
            if node.node_id == node_id:
                self._by_workbench.pop(key, None)

    def get_by_id(self, node_id: str) -> MessengerNode | None:
        return self._by_id.get(node_id)

    def find_by_role(self, role: str) -> MessengerNode | None:
        return self._by_role.get(role)

    def find_by_workbench(self, plugin: str, mode: str) -> MessengerNode | None:
        return self._by_workbench.get((plugin, mode))


# ---------------------------------------------------------------------------
# Messenger
# ---------------------------------------------------------------------------


@dataclass
class _NodeInbox:
    """Bounded per-node FIFO queue."""

    queue: asyncio.Queue[NodeMessage]
    busy: bool = False
    last_seen: datetime | None = None


class Messenger:
    """Resolve addresses, queue messages, deliver to nodes.

    Args:
        registry: address resolver. Tests can inject
            :class:`InMemoryNodeRegistry`.
        stream: live event bus for typed updates.
        max_inbox: bounded per-node inbox. Default 16. Excess messages
            block the producer until the inbox drains; we deliberately
            *do not* drop here (unlike StreamBus) because dropping a
            delegation would be a correctness bug.
    """

    def __init__(
        self,
        *,
        registry: NodeRegistry,
        stream: StreamBus,
        max_inbox: int = 16,
    ) -> None:
        self._registry = registry
        self._stream = stream
        self._inboxes: dict[str, _NodeInbox] = {}
        self._lock = asyncio.Lock()
        self._max_inbox = max_inbox
        self._correlation_counter = 0

    # ------------------------------------------------------------------
    # Address resolution
    # ------------------------------------------------------------------

    def resolve(self, raw: str) -> tuple[NodeAddress, MessengerNode]:
        addr = NodeAddress.parse(raw)
        node: MessengerNode | None = None
        if addr.node_id is not None:
            node = self._registry.get_by_id(addr.node_id)
        elif addr.plugin is not None and addr.mode is not None:
            node = self._registry.find_by_workbench(addr.plugin, addr.mode)
        elif addr.role is not None:
            node = self._registry.find_by_role(addr.role)
        if node is None:
            raise NodeAddressResolveError(f"no registered node matches address {raw!r}")
        return addr, node

    # ------------------------------------------------------------------
    # Delivery — the supervisor's deliver callable
    # ------------------------------------------------------------------

    async def deliver(
        self,
        speaker_address: str,
        instruction: str,
        *,
        command_id: str = "",
        org_id: str = "",
        superstep: int = 0,
        cancel_token: CancellationToken | None = None,
        sender: str = "supervisor",
    ) -> DelegationResult:
        """Resolve, dispatch, and return the node's :class:`DelegationResult`.

        Honours cooperative cancel: if the cancel token cancels while
        the node is processing, the messenger calls
        :meth:`MessengerNode.on_cancel` and surfaces a failure result
        so the supervisor can write a cancelled checkpoint.
        """
        addr, node = self.resolve(speaker_address)
        cid = self._next_correlation_id()
        message = NodeMessage(
            speaker=sender,
            address=addr,
            instruction=instruction,
            correlation_id=cid,
        )
        inbox = await self._inbox_for(node.node_id)
        await self._stream.emit(
            "tasks",
            "delegation_dispatched",
            {
                "speaker": sender,
                "address": addr.raw,
                "node_id": node.node_id,
                "correlation_id": cid,
                "instruction": instruction,
            },
            command_id=command_id,
            org_id=org_id,
            superstep=superstep,
        )
        try:
            await asyncio.wait_for(inbox.queue.put(message), timeout=5.0)
        except TimeoutError:
            return DelegationResult(
                success=False,
                speaker=node.node_id,
                message=f"inbox full for node {node.node_id} (max={self._max_inbox})",
                metadata={"address": addr.raw, "correlation_id": cid},
            )
        inbox.busy = True
        try:
            result = await self._dispatch_with_cancel(node, message, cancel_token)
        finally:
            inbox.busy = False
            try:
                inbox.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            inbox.last_seen = datetime.now(UTC)
        await self._stream.emit(
            "updates",
            "delegation_completed",
            {
                "speaker": result.speaker,
                "node_id": node.node_id,
                "success": result.success,
                "correlation_id": cid,
                "message": result.message,
            },
            command_id=command_id,
            org_id=org_id,
            superstep=superstep,
        )
        return result

    async def _dispatch_with_cancel(
        self,
        node: MessengerNode,
        message: NodeMessage,
        cancel_token: CancellationToken | None,
    ) -> DelegationResult:
        if cancel_token is None:
            return await node.on_message(message)
        # Race the node against the cancel token.
        node_task: asyncio.Task[DelegationResult] = asyncio.create_task(node.on_message(message))

        def _cancel_node() -> None:
            if not node_task.done():
                node_task.cancel()
                # Also notify the node so it can save in-flight state.
                asyncio.create_task(node.on_cancel("messenger cancel"))

        cancel_token.add_callback(_cancel_node)
        try:
            return await node_task
        except asyncio.CancelledError:
            return DelegationResult(
                success=False,
                speaker=node.node_id,
                message=f"cancelled: {cancel_token.reason}",
                metadata={"address": message.address.raw, "correlation_id": message.correlation_id},
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _inbox_for(self, node_id: str) -> _NodeInbox:
        async with self._lock:
            inbox = self._inboxes.get(node_id)
            if inbox is None:
                inbox = _NodeInbox(queue=asyncio.Queue(maxsize=self._max_inbox))
                self._inboxes[node_id] = inbox
            return inbox

    def _next_correlation_id(self) -> str:
        self._correlation_counter += 1
        return f"corr_{self._correlation_counter:06d}"

    # ------------------------------------------------------------------
    # Bind helper (returns a deliver callable for the supervisor)
    # ------------------------------------------------------------------

    def bind_for_command(
        self,
        *,
        command_id: str,
        org_id: str,
        cancel_token: CancellationToken | None = None,
    ) -> Callable[..., Awaitable[DelegationResult]]:
        """Return a partial-applied deliver callable for the supervisor.

        Captures the command/org context and the cancel token so the
        supervisor can call ``deliver(speaker, instruction, progress)``
        without the bookkeeping arguments.
        """

        async def _deliver(speaker: str, instruction: str, progress: Any) -> DelegationResult:
            superstep = getattr(progress, "turn_id", 0)
            return await self.deliver(
                speaker,
                instruction,
                command_id=command_id,
                org_id=org_id,
                superstep=int(superstep),
                cancel_token=cancel_token,
            )

        return _deliver
