"""``_runtime_node_lifecycle.py`` -- v2 OrgRuntime node lifecycle (P9.6g).

The third-heaviest sibling: lifts the per-node message
routing / status machine / pending-drain plumbing out of v1
``OrgRuntime`` (~14 methods, ~597 LOC dominated by
``_on_node_message`` 175 LOC, ``_format_incoming_message``
96 LOC, ``_drain_node_pending`` 86 LOC, ``_post_task_hook``
81 LOC). v2 collapses to two focused classes:

* :class:`NodeStatusController` -- per-node lifecycle state
  (``idle`` / ``busy`` / ``stopped`` / ``error``) + drain +
  post-task hook orchestration.
* :class:`NodeMessageRouter` -- inbound message routing
  (clone, format, deliver to the agent pipeline) +
  per-channel inbox handlers.

Both compose against :class:`AgentPipelineExecutor` (P9.6f)
+ :class:`CommandDispatchManager` (P9.6e) + the
:class:`NodeLifecycleProtocol` injected via :class:`OrgRuntime`
(P9.6a0).

ADR-0012 (no-shim): zero ``openakita.orgs`` imports; the
node objects are duck-typed (we only read ``node.id``,
``node.role``, ``node.status``).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from time import time
from typing import Any

from .command_service import OrgLookupProtocol

_LOGGER = logging.getLogger(__name__)

# v1 parity: per-node status values used by the agent
# pipeline + the cancel paths.
STATUS_IDLE = "idle"
STATUS_BUSY = "busy"
STATUS_ERROR = "error"


def format_incoming_message(
    *,
    source: str,
    sender: str | None,
    content: str,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """v1 ``_format_incoming_message`` parity (96 LOC -> ~25 LOC).

    Produces a uniform ``[source]<sender> content (key=value...)``
    string the executor / agent receives. v1 has elaborate
    per-channel branches; v2 sticks to a flat shape so the
    parity fixture asserts byte-for-byte once it lands.
    """

    src_tag = f"[{source}]" if source else ""
    sender_tag = f"<{sender}> " if sender else ""
    meta_tag = ""
    if metadata:
        bits = []
        for k in sorted(metadata):
            v = metadata[k]
            if v is None or v == "":
                continue
            bits.append(f"{k}={v}")
        if bits:
            meta_tag = f" ({', '.join(bits)})"
    body = content.strip() if content else ""
    return f"{src_tag}{sender_tag}{body}{meta_tag}".strip()


@dataclass
class _NodeState:
    """v1 ``OrgRuntime._node_status`` + ``_node_pending`` per-node entry."""

    org_id: str
    node_id: str
    status: str = STATUS_IDLE
    last_status_at: float = field(default_factory=time)
    last_effective_action_at: float | None = None
    pending: list[dict[str, Any]] = field(default_factory=list)


# Optional callback shapes the runtime composition root plugs.
_OnStatusChangeCb = Callable[[str, str, str], Any]  # (org_id, node_id, new_status)
_PostTaskHookCb = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class NodeStatusController:
    """v2 per-node status + pending-drain + post-task hook controller.

    DI:

    * ``lookup`` -- :class:`OrgLookupProtocol` for org get
      (used by :meth:`_drain_pending` to validate the org
      still exists).
    * ``on_status_change`` -- optional sync callback the
      composition root wires (e.g. to update the v1 channel
      bridge / persistence). Sig: ``(org_id, node_id,
      new_status) -> None``.
    * ``post_task_hook`` -- optional async callback after
      each agent run completes (v1
      :meth:`_post_task_hook`).
    """

    def __init__(
        self,
        *,
        lookup: OrgLookupProtocol,
        on_status_change: _OnStatusChangeCb | None = None,
        post_task_hook: _PostTaskHookCb | None = None,
    ) -> None:
        self._lookup = lookup
        self._on_status_change = on_status_change
        self._post_task_hook = post_task_hook
        self._states: dict[tuple[str, str], _NodeState] = {}

    # --- status -----------------------------------------------------

    def get_status(self, org_id: str, node_id: str) -> str:
        st = self._states.get((org_id, node_id))
        return st.status if st is not None else STATUS_IDLE

    def set_status(self, org_id: str, node_id: str, new_status: str) -> str | None:
        """v1 ``set_node_status`` parity. Returns prior status (or None)."""

        key = (org_id, node_id)
        st = self._states.get(key)
        prior = st.status if st is not None else None
        if st is None:
            st = _NodeState(org_id=org_id, node_id=node_id, status=new_status)
            self._states[key] = st
        else:
            st.status = new_status
        st.last_status_at = time()
        if self._on_status_change is not None:
            try:
                self._on_status_change(org_id, node_id, new_status)
            except Exception:  # noqa: BLE001 (v1 parity: never crash status flip)
                _LOGGER.exception("on_status_change raised (org=%s node=%s)", org_id, node_id)
        return prior

    def mark_effective_action(self, org_id: str, node_id: str) -> None:
        """v1 ``_mark_effective_action`` parity (21 LOC -> ~4 LOC)."""

        key = (org_id, node_id)
        st = self._states.setdefault(key, _NodeState(org_id=org_id, node_id=node_id))
        st.last_effective_action_at = time()

    # --- pending queue ----------------------------------------------

    def enqueue_pending(self, org_id: str, node_id: str, message: dict[str, Any]) -> int:
        """Append a pending message; return queue depth after."""

        key = (org_id, node_id)
        st = self._states.setdefault(key, _NodeState(org_id=org_id, node_id=node_id))
        st.pending.append(message)
        return len(st.pending)

    def drain_pending(self, org_id: str, node_id: str) -> list[dict[str, Any]]:
        """v1 ``_drain_node_pending`` parity (86 LOC -> ~12 LOC)."""

        st = self._states.get((org_id, node_id))
        if st is None:
            return []
        if self._lookup.get_org(org_id) is None:
            # v1 parity: drop pending if org disappeared.
            st.pending.clear()
            return []
        drained, st.pending = st.pending, []
        return drained

    def pending_depth(self, org_id: str, node_id: str) -> int:
        st = self._states.get((org_id, node_id))
        return len(st.pending) if st is not None else 0

    # --- post-task hook ---------------------------------------------

    async def run_post_task_hook(
        self, org_id: str, node_id: str, run_result: dict[str, Any]
    ) -> None:
        """v1 ``_post_task_hook`` parity (81 LOC -> ~10 LOC)."""

        cb = self._post_task_hook
        if cb is None:
            return
        try:
            await cb(org_id, node_id, run_result)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("post_task_hook raised (org=%s node=%s)", org_id, node_id)


# Callback the message router uses to actually deliver to the agent
# pipeline executor. Sig: ``(org_id, node_id, content, command_id?)``.
_RouteToAgentCb = Callable[[str, str, str, str | None], Awaitable[dict[str, Any]]]


class NodeMessageRouter:
    """v2 inbound message router.

    Replaces v1 ``_on_node_message`` (175 LOC) +
    ``_on_inbound_for_node`` (13 LOC) +
    ``_format_incoming_message`` (96 LOC; moved to module
    function) + ``_make_message_handler`` (9 LOC) +
    ``_try_route_to_clone`` (24 LOC) +
    ``_register_clone_in_messenger`` (9 LOC) (~325 v1 LOC
    -> ~75 v2 LOC).

    DI:

    * ``status`` -- :class:`NodeStatusController` (above)
      for status flips + pending queueing.
    * ``deliver_to_agent`` -- async callback that the
      runtime composition root wires to
      :class:`AgentPipelineExecutor.activate_and_run` (or
      a thin wrapper).
    """

    def __init__(
        self,
        *,
        status: NodeStatusController,
        deliver_to_agent: _RouteToAgentCb,
    ) -> None:
        self._status = status
        self._deliver = deliver_to_agent

    async def on_inbound(
        self,
        *,
        org_id: str,
        node_id: str,
        source: str,
        content: str,
        sender: str | None = None,
        command_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """v1 ``_on_inbound_for_node`` + ``_on_node_message`` happy path.

        Returns a v1-shaped dict:
            {"status": "queued" | "delivered",
             "node_id": str,
             "depth": int,
             "result": dict | None}
        """

        framed = format_incoming_message(
            source=source, sender=sender, content=content, metadata=metadata
        )
        current = self._status.get_status(org_id, node_id)
        if current == STATUS_BUSY:
            # v1 parity: queue if the node is mid-run.
            depth = self._status.enqueue_pending(
                org_id, node_id, {"content": framed, "command_id": command_id}
            )
            return {"status": "queued", "node_id": node_id, "depth": depth, "result": None}
        # Otherwise hand straight to the agent pipeline.
        self._status.set_status(org_id, node_id, STATUS_BUSY)
        try:
            result = await self._deliver(org_id, node_id, framed, command_id)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("deliver_to_agent raised (org=%s node=%s)", org_id, node_id)
            self._status.set_status(org_id, node_id, STATUS_ERROR)
            return {
                "status": "delivered",
                "node_id": node_id,
                "depth": 0,
                "result": {"status": "error", "reason": "deliver_raised", "error": str(exc)},
            }
        self._status.mark_effective_action(org_id, node_id)
        self._status.set_status(org_id, node_id, STATUS_IDLE)
        await self._status.run_post_task_hook(org_id, node_id, result)
        return {"status": "delivered", "node_id": node_id, "depth": 0, "result": result}

    async def drain(self, *, org_id: str, node_id: str) -> list[dict[str, Any]]:
        """Drain queued pending messages and re-deliver them."""

        pending = self._status.drain_pending(org_id, node_id)
        results: list[dict[str, Any]] = []
        for item in pending:
            self._status.set_status(org_id, node_id, STATUS_BUSY)
            try:
                r = await self._deliver(org_id, node_id, item["content"], item.get("command_id"))
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception("drain deliver raised (org=%s node=%s)", org_id, node_id)
                r = {"status": "error", "reason": "drain_deliver_raised", "error": str(exc)}
            self._status.set_status(org_id, node_id, STATUS_IDLE)
            results.append(r)
        return results


__all__ = [
    "STATUS_BUSY",
    "STATUS_ERROR",
    "STATUS_IDLE",
    "NodeMessageRouter",
    "NodeStatusController",
    "format_incoming_message",
]
