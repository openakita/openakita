"""V2 channel-to-org routing helper.

Plan §6 (channels swap) calls for ``channels/gateway.py`` to consult
``settings.runtime_v2_enabled`` on every inbound message and, when the
flag is on **and** the session is bound to a v2 org, hand the message
off to the v2 supervisor stack via
:func:`openakita.runtime.state_graph.compile_from_org`.

The gateway is a 5,000-line file with deep entanglement. To keep the
gateway diff trivial (one tiny ``if`` block at the org-binding
resolution point) all of the v2 wiring lives in this module. The
gateway only has to call :func:`route_inbound_message_to_v2` and act
on the returned :class:`RoutingPlan`.

Why a plan dataclass instead of "just dispatch here"
---------------------------------------------------

The v2 runtime owns its own delivery transport (``runtime.messenger``
+ ``runtime.supervisor``), but Phase 6 ships before the full
gateway-supervisor handshake is wired. Returning a structured plan
lets the gateway decide:

* ``status == "skipped"`` → fall through to the legacy path.
* ``status == "routed"`` → emit a UI hint that v2 picked up the
  message; for now this is a no-op breadcrumb the channel writes back
  to the user. Phase 7 wires this into a real supervisor run.

This keeps the gateway side trivially reversible and avoids hiding
behaviour changes inside a feature flag.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openakita.runtime.models import NodeV2, OrgV2
from openakita.runtime.orgs import OrgNotFound, get_default_store
from openakita.runtime.state_graph import StateGraph, compile_from_org

__all__ = [
    "RoutingPlan",
    "compute_v2_plan_for_org",
    "dispatch_inbound_message_to_v2",
    "route_inbound_message_to_v2",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutingPlan:
    """What the gateway should do with an inbound message under v2.

    Attributes:
        status:
            ``"routed"`` if v2 picked it up and the supervisor ran to
            DONE (or was at least handed the message). ``"cancelled"``
            if the supervisor was cooperatively cancelled before it
            could finish (a final checkpoint is still written by the
            supervisor in this case). ``"skipped"`` if the gateway
            should fall through to the legacy path -- reasons include
            v2 disabled, no org bound, org not in the store, empty
            topology, no SupervisorBrain factory wired, or any
            unexpected dispatch-time failure.
        org_id:
            The org the message was routed to (``""`` when skipped).
        next_node_id:
            The graph's entry point — the node the supervisor would
            delegate to first. Empty string when no entry point can
            be derived (e.g. empty org).
        next_node_role:
            Convenience: ``next_node_id``'s ``NodeV2.role`` so the
            gateway can render "正在交给 {role} 处理" without doing a
            second lookup.
        reason:
            Human-readable explanation for the verdict, used by the
            gateway's structured log and by tests.
        result:
            Optional payload set by the async dispatch path. When
            ``status == "routed"`` or ``"cancelled"`` this holds the
            :class:`SupervisorOutcome` returned by ``Supervisor.run``;
            None for ``skipped`` and for the legacy sync helper.
    """

    status: str
    org_id: str
    next_node_id: str
    next_node_role: str
    reason: str
    result: Any | None = None

    @property
    def routed(self) -> bool:
        return self.status == "routed"

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "org_id": self.org_id,
            "next_node_id": self.next_node_id,
            "next_node_role": self.next_node_role,
            "reason": self.reason,
        }


def _entry_node(org: OrgV2, graph: StateGraph) -> NodeV2 | None:
    """Resolve the entry point node from a compiled graph.

    Falls back to the first root node, then the first listed node,
    so a half-defined topology still produces a useful breadcrumb.
    """
    candidates: list[str] = []
    if graph.entry_point:
        candidates.append(graph.entry_point)
    roots = org.root_nodes()
    if roots:
        candidates.extend(n.id for n in roots)
    if org.nodes:
        candidates.append(org.nodes[0].id)
    for nid in candidates:
        node = org.get_node(nid)
        if node is not None:
            return node
    return None


def compute_v2_plan_for_org(org: OrgV2) -> RoutingPlan:
    """Return a :class:`RoutingPlan` for the given org.

    Pure function over the :class:`OrgV2` — no settings lookup, no
    store access. Useful for tests that want to assert routing
    semantics without the flag-gating layer.
    """
    if not org.nodes:
        return RoutingPlan(
            status="skipped",
            org_id=org.id,
            next_node_id="",
            next_node_role="",
            reason="org has no nodes",
        )
    try:
        graph = compile_from_org(org)
    except Exception as exc:  # noqa: BLE001 — defensive: never break the gateway
        logger.warning("[channel_routing] compile_from_org failed for %s: %s", org.id, exc)
        return RoutingPlan(
            status="skipped",
            org_id=org.id,
            next_node_id="",
            next_node_role="",
            reason=f"compile_from_org failed: {exc}",
        )
    entry = _entry_node(org, graph)
    if entry is None:
        return RoutingPlan(
            status="skipped",
            org_id=org.id,
            next_node_id="",
            next_node_role="",
            reason="no entry point could be resolved",
        )
    return RoutingPlan(
        status="routed",
        org_id=org.id,
        next_node_id=entry.id,
        next_node_role=entry.role,
        reason=f"entry point resolved to node {entry.id} ({entry.role})",
    )


def route_inbound_message_to_v2(*, org_id: str | None) -> RoutingPlan:
    """Synchronous probe used by the canary-observability log hook.

    .. deprecated:: P-RC-1
        Use :func:`dispatch_inbound_message_to_v2` instead. This
        helper is kept for backwards compatibility with the original
        Phase 6 ``_maybe_log_v2_routing_plan`` hook (now retired) and
        for tests that only want to inspect plan derivation without
        running the supervisor.

    Gateway entry point. Gated by ``settings.runtime_v2_enabled``.

    Args:
        org_id:
            The org bound to the session. ``None`` or empty means the
            session is not org-attached; v2 has nothing to do.

    Returns:
        A :class:`RoutingPlan` whose ``status`` tells the gateway
        whether v2 took the message (``"routed"``) or the legacy
        path should continue (``"skipped"``).

    The function never raises — every error is caught and turned
    into a ``"skipped"`` plan so the channel gateway is guaranteed
    to fall through to the legacy code path on any v2-side hiccup.
    """
    try:
        from openakita.config import settings

        if not getattr(settings, "runtime_v2_enabled", False):
            return RoutingPlan(
                status="skipped",
                org_id=org_id or "",
                next_node_id="",
                next_node_role="",
                reason="runtime_v2_enabled is False",
            )
    except Exception as exc:  # noqa: BLE001 — settings module shouldn't break IM
        return RoutingPlan(
            status="skipped",
            org_id=org_id or "",
            next_node_id="",
            next_node_role="",
            reason=f"settings load failed: {exc}",
        )

    if not org_id:
        return RoutingPlan(
            status="skipped",
            org_id="",
            next_node_id="",
            next_node_role="",
            reason="session is not bound to an org",
        )

    try:
        org = get_default_store().get(org_id)
    except OrgNotFound:
        return RoutingPlan(
            status="skipped",
            org_id=org_id,
            next_node_id="",
            next_node_role="",
            reason=f"org {org_id} not in v2 store",
        )

    return compute_v2_plan_for_org(org)




# ---------------------------------------------------------------------------
# Async dispatch (P-RC-1)
# ---------------------------------------------------------------------------


async def dispatch_inbound_message_to_v2(
    *,
    session_key: str,
    org_id: str | None,
    message: str,
    attachments: list[Any] | None = None,
    cancel_token: Any | None = None,
    brain: Any | None = None,
    node_registry: Any | None = None,
    stream_bus: Any | None = None,
    checkpointer: Any | None = None,
    supervisor_cls: Any | None = None,
) -> RoutingPlan:
    """Run the v2 supervisor for an inbound IM message.

    Async counterpart of :func:`route_inbound_message_to_v2`; promoted
    from observation-only to a real dispatch path in P-RC-1
    (continuation plan section 2.1). When the routing plan would be
    ``routed``, constructs a :class:`Supervisor`, drives
    ``Supervisor.run``, and returns a :class:`RoutingPlan` whose
    ``result`` holds the :class:`SupervisorOutcome`.

    Contract: NEVER raises to the caller. Any failure becomes
    ``status="skipped"`` so the gateway can always fall back to legacy.

    Keyword Args:
        session_key, org_id, message, attachments: from the gateway.
        cancel_token: cooperative cancel; the IM gateway commit 5 will
            fire ``token.cancel()`` from the user-cancel verb.
        brain: optional :class:`SupervisorBrain`; defaults to the
            degenerate one in :mod:`openakita.agent.supervisor_brain`.
        node_registry / stream_bus / checkpointer / supervisor_cls:
            DI seams. Defaults are an in-memory registry, fresh
            ``StreamBus``, ``MemoryCheckpointer``, and the real
            :class:`Supervisor`. SQLite checkpointer arrives in P-RC-3.
    """
    try:
        if not org_id:
            return RoutingPlan(
                status="skipped",
                org_id="",
                next_node_id="",
                next_node_role="",
                reason="session is not bound to an org",
            )

        try:
            org = get_default_store().get(org_id)
        except OrgNotFound:
            return RoutingPlan(
                status="skipped",
                org_id=org_id,
                next_node_id="",
                next_node_role="",
                reason=f"org {org_id} not in v2 store",
            )

        plan = compute_v2_plan_for_org(org)
        if not plan.routed:
            return plan

        # Local imports keep module-level import cost low.
        import uuid as _uuid

        from openakita.agent.supervisor_brain import default_supervisor_brain
        from openakita.runtime.cancel_token import CancellationToken
        from openakita.runtime.checkpoint import MemoryCheckpointer
        from openakita.runtime.messenger import InMemoryNodeRegistry, Messenger
        from openakita.runtime.stream import StreamBus
        from openakita.runtime.supervisor import FinalOutcome, Supervisor

        resolved_brain = brain or default_supervisor_brain()
        resolved_stream = stream_bus or StreamBus()
        resolved_checkpointer = checkpointer or MemoryCheckpointer()
        resolved_registry = node_registry or InMemoryNodeRegistry()
        resolved_token = cancel_token or CancellationToken()
        supervisor_impl = supervisor_cls or Supervisor

        messenger = Messenger(registry=resolved_registry, stream=resolved_stream)
        command_id = f"cmd_{_uuid.uuid4().hex[:12]}"
        deliver = messenger.bind_for_command(
            command_id=command_id, org_id=org_id, cancel_token=resolved_token,
        )
        supervisor = supervisor_impl(
            command_id=command_id,
            org_id=org_id,
            root_node_id=plan.next_node_id,
            task=message,
            brain=resolved_brain,
            deliver=deliver,
            stream=resolved_stream,
            checkpointer=resolved_checkpointer,
            cancel_token=resolved_token,
        )

        try:
            outcome = await supervisor.run()
        except Exception as exc:  # noqa: BLE001 -- never leak supervisor crash
            logger.warning(
                "[channel_routing] supervisor.run failed for org=%s session=%s: %s",
                org_id, session_key, exc,
            )
            return RoutingPlan(
                status="skipped",
                org_id=org_id,
                next_node_id=plan.next_node_id,
                next_node_role=plan.next_node_role,
                reason=f"supervisor.run raised: {exc}",
            )

        outcome_status = getattr(getattr(outcome, "outcome", None), "value", "")
        status = "cancelled" if outcome_status == FinalOutcome.CANCELLED.value else "routed"
        return RoutingPlan(
            status=status,
            org_id=org_id,
            next_node_id=plan.next_node_id,
            next_node_role=plan.next_node_role,
            reason=f"supervisor outcome={outcome_status or 'unknown'}",
            result=outcome,
        )
    except Exception as exc:  # noqa: BLE001 -- contract: never raise to caller
        logger.warning(
            "[channel_routing] dispatch failed for org=%s session=%s: %s",
            org_id, session_key, exc,
        )
        return RoutingPlan(
            status="skipped",
            org_id=org_id or "",
            next_node_id="",
            next_node_role="",
            reason=f"dispatch failed: {exc}",
        )
