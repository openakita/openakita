"""Pregel-style state graph for v2 runtime routing.

Implements ADR-0002 §"State graph" and the routing-half of ADR-0007.
The :class:`StateGraph` here is intentionally smaller than LangGraph's
``StateGraph`` — we own only routing (which node runs next), not state
reduction. State reduction is the supervisor's job (the
``ProgressLedger`` history) and the messenger's job (per-node inbox).

Why the runtime needs this module
---------------------------------

The dual-ledger :class:`Supervisor` decides *what* to do next via the
brain's ``next_speaker_name`` field. But several v2 templates encode
**deterministic** topology that the LLM should not be allowed to
override mid-run:

* ``art_director → image_artist → video_animator`` always runs in that
  order.
* A :class:`ConditionNode` returns a label and the graph maps it to a
  branch; the brain has no business choosing here.
* WorkbenchNode mode switching is a graph hop, not a free-form LLM
  hint.

Without this module today, ``ConditionNode.metadata['next_address']``
is populated and **nothing reads it**. This module is exactly the
component that turns that hint into a routing decision.

Design
------

Mirrors LangGraph's three primitives:

* ``add_node(node_id)`` — declare a node id is allowed as an edge
  endpoint.
* ``add_edge(src, dst)`` — unconditional next-hop after ``src``.
  ``dst`` may be :data:`END` to stop the graph.
* ``add_conditional_edges(src, router, mapping)`` — given a
  :class:`DelegationResult`, the router returns a label that the
  mapping resolves to a node id (or :data:`END`).

A graph also has a single **entry point** (set by
``add_edge(START, dst)`` or ``set_entry_point(dst)``) which the
supervisor uses on the first turn before any node has run.

The graph does *not* execute nodes itself. That stays with the
messenger / node runtime. The graph's role is to answer the question
"given that ``src`` produced ``result``, what address should the
supervisor delegate to next?" via :meth:`StateGraph.route`.

Cycle handling
--------------

Cycles are allowed (e.g. an editor → screenwriter ping-pong). The
supervisor's :class:`StallDetector` already breaks runaway loops via
turn caps; the graph itself enforces no acyclicity constraint.

Layering
--------

This module imports from :mod:`runtime.models` (leaf) and
:mod:`runtime.supervisor` (for :class:`DelegationResult`). It does
**not** import from :mod:`runtime.nodes`, the messenger, or the brain.
That keeps the graph testable in isolation and prevents an import
cycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .models import EdgeKind, EdgeV2, NodeV2, OrgV2
from .supervisor import DelegationResult

__all__ = [
    "END",
    "ConditionalRouter",
    "START",
    "StateGraph",
    "StateGraphError",
    "compile_from_org",
]


class StateGraphError(ValueError):
    """Raised when the graph is misconfigured or routing fails."""


# Sentinels matching LangGraph's ``langgraph.graph.START`` / ``END``.
# Strings rather than enum members so they can sit alongside node ids
# in the same dict without an isinstance dance.
START = "__start__"
END = "__end__"


ConditionalRouter = Callable[
    [DelegationResult], "str | Awaitable[str]"
]
"""Router signature: ``router(result) -> branch_label`` (sync or async)."""


# ---------------------------------------------------------------------------
# StateGraph
# ---------------------------------------------------------------------------


@dataclass
class _ConditionalEdge:
    router: ConditionalRouter
    mapping: dict[str, str]


@dataclass
class StateGraph:
    """Routing-only graph. See module docstring for the contract.

    Construct empty, register nodes via :meth:`add_node`, declare
    topology via :meth:`add_edge` and :meth:`add_conditional_edges`,
    then call :meth:`route` once per turn.
    """

    nodes: set[str] = field(default_factory=set)
    static_edges: dict[str, str] = field(default_factory=dict)
    conditional_edges: dict[str, _ConditionalEdge] = field(default_factory=dict)
    entry_point: str | None = None

    # ------------------------------------------------------------------
    # Topology mutators
    # ------------------------------------------------------------------

    def add_node(self, node_id: str) -> None:
        """Declare that ``node_id`` is a valid edge endpoint."""
        if not isinstance(node_id, str) or not node_id:
            raise StateGraphError(
                f"node id must be a non-empty string, got {node_id!r}"
            )
        if node_id in (START, END):
            raise StateGraphError(
                f"{node_id!r} is reserved; cannot register as a node"
            )
        self.nodes.add(node_id)

    def set_entry_point(self, node_id: str) -> None:
        """Pin the graph's entry node. Equivalent to ``add_edge(START, node_id)``."""
        self._validate_target(node_id, allow_end=False)
        self.entry_point = node_id

    def add_edge(self, src: str, dst: str) -> None:
        """Unconditional next-hop edge.

        Special-cases ``src == START`` as :meth:`set_entry_point`.
        Raises :class:`StateGraphError` when ``src`` is already wired
        with conditional edges, or when either endpoint is unknown.
        """
        if src == START:
            self.set_entry_point(dst)
            return
        self._validate_source(src)
        self._validate_target(dst, allow_end=True)
        if src in self.conditional_edges:
            raise StateGraphError(
                f"{src!r} already has conditional edges; "
                "static and conditional edges are mutually exclusive"
            )
        existing = self.static_edges.get(src)
        if existing is not None and existing != dst:
            raise StateGraphError(
                f"{src!r} already has a static edge to {existing!r}; "
                f"cannot also point at {dst!r}"
            )
        self.static_edges[src] = dst

    def add_conditional_edges(
        self,
        src: str,
        router: ConditionalRouter,
        mapping: Mapping[str, str],
    ) -> None:
        """Branch from ``src`` based on a router-produced label.

        Each value in ``mapping`` must be a registered node id or
        :data:`END`. The router may be sync or async; :meth:`route`
        awaits accordingly.
        """
        self._validate_source(src)
        if not callable(router):
            raise StateGraphError("router must be callable")
        if not mapping:
            raise StateGraphError(
                f"conditional edges from {src!r} need at least one mapping"
            )
        for label, target in mapping.items():
            if not isinstance(label, str) or not label:
                raise StateGraphError(
                    f"branch label must be a non-empty string, got {label!r}"
                )
            self._validate_target(target, allow_end=True)
        if src in self.static_edges:
            raise StateGraphError(
                f"{src!r} already has a static edge; "
                "static and conditional edges are mutually exclusive"
            )
        self.conditional_edges[src] = _ConditionalEdge(
            router=router, mapping=dict(mapping)
        )

    # ------------------------------------------------------------------
    # Topology accessors
    # ------------------------------------------------------------------

    def has_outgoing(self, src: str) -> bool:
        """True when ``src`` has at least one (static or conditional) edge."""
        return src in self.static_edges or src in self.conditional_edges

    def successors(self, src: str) -> list[str]:
        """All possible next-hop targets from ``src`` (static + conditional).

        :data:`END` is included if it appears as a target. Useful for
        validating templates and for telemetry.
        """
        out: list[str] = []
        if src in self.static_edges:
            out.append(self.static_edges[src])
        if src in self.conditional_edges:
            out.extend(self.conditional_edges[src].mapping.values())
        # Stable, dedup-preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for item in out:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def route(
        self, src: str, result: DelegationResult
    ) -> str | None:
        """Decide where to go after ``src`` produced ``result``.

        Resolution order:

        1. **Conditional edges on src**: invoke the router with
           ``result`` and look the returned label up in the mapping.
           A label that the mapping does not contain raises
           :class:`StateGraphError` — never a silent fall-through.
        2. **Static edge on src**: return its target.
        3. **DelegationResult hint**: ``result.metadata['next_address']``
           overrides nothing static; it only fills in when the graph
           has no opinion. This lets a node like
           :class:`ConditionNode` work even before a template wires up
           formal conditional edges.
        4. **None**: graph defers to the supervisor's brain.

        Returns ``None`` when the chosen target is :data:`END`,
        signalling "stop"; the supervisor interprets that as a
        terminal node and closes the run on the next progress-ledger
        evaluation.
        """
        cond = self.conditional_edges.get(src)
        if cond is not None:
            outcome = cond.router(result)
            if hasattr(outcome, "__await__"):
                label = await outcome  # type: ignore[misc]
            else:
                label = outcome  # type: ignore[assignment]
            if not isinstance(label, str):
                raise StateGraphError(
                    f"conditional router for {src!r} must return str, "
                    f"got {type(label).__name__}"
                )
            if label not in cond.mapping:
                raise StateGraphError(
                    f"conditional router for {src!r} returned unknown "
                    f"label {label!r}; expected one of "
                    f"{sorted(cond.mapping.keys())!r}"
                )
            target = cond.mapping[label]
            return None if target == END else target

        static = self.static_edges.get(src)
        if static is not None:
            return None if static == END else static

        hint = result.metadata.get("next_address") if result.metadata else None
        if isinstance(hint, str) and hint:
            return None if hint == END else hint

        return None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Raise :class:`StateGraphError` when the graph is malformed.

        Cheap structural checks only; we do not enforce reachability
        of every node, because a template may declare bench-only nodes
        (e.g. an opt-in ``human_review`` branch) that only activate via
        conditional edges or runtime escalation.
        """
        if self.entry_point is None:
            raise StateGraphError(
                "graph has no entry point; call add_edge(START, <node>) "
                "or set_entry_point(<node>)"
            )
        if self.entry_point not in self.nodes:
            raise StateGraphError(
                f"entry point {self.entry_point!r} is not a registered node"
            )
        for src, dst in self.static_edges.items():
            if src not in self.nodes:
                raise StateGraphError(
                    f"static edge src {src!r} is not a registered node"
                )
            if dst != END and dst not in self.nodes:
                raise StateGraphError(
                    f"static edge dst {dst!r} from {src!r} is not registered"
                )
        for src, edge in self.conditional_edges.items():
            if src not in self.nodes:
                raise StateGraphError(
                    f"conditional edge src {src!r} is not a registered node"
                )
            for label, dst in edge.mapping.items():
                if dst != END and dst not in self.nodes:
                    raise StateGraphError(
                        f"conditional edge mapping {src!r}/{label!r} "
                        f"-> {dst!r} is not registered"
                    )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_source(self, src: str) -> None:
        if src in (START, END):
            raise StateGraphError(
                f"{src!r} cannot be the source of an edge"
            )
        if src not in self.nodes:
            raise StateGraphError(
                f"{src!r} is not a registered node; call add_node first"
            )

    def _validate_target(self, dst: str, *, allow_end: bool = True) -> None:
        if not isinstance(dst, str) or not dst:
            raise StateGraphError(
                f"edge target must be a non-empty string, got {dst!r}"
            )
        if dst == END:
            if not allow_end:
                raise StateGraphError(f"{dst!r} not allowed here")
            return
        if dst == START:
            raise StateGraphError(
                f"{dst!r} cannot be an edge target; it is reserved as the source-only sentinel"
            )
        if dst not in self.nodes:
            raise StateGraphError(
                f"target {dst!r} is not a registered node"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compile_from_org(
    org: OrgV2,
    *,
    entry_point: str | None = None,
    conditional_routers: Mapping[str, tuple[ConditionalRouter, Mapping[str, str]]]
    | None = None,
) -> StateGraph:
    """Project an :class:`OrgV2` topology into a :class:`StateGraph`.

    Fast path for templates that only have hierarchical / collaborate
    edges (no conditional routing): every :class:`EdgeV2` becomes an
    ``add_edge`` call. The first node by ``created_at`` is the default
    entry point unless ``entry_point`` is supplied.

    For templates that need branching, callers pass
    ``conditional_routers`` mapping a source node id to
    ``(router, label_mapping)``. When a node id appears both as the
    source of a static edge and in ``conditional_routers``, the
    conditional wiring wins and the static edge is ignored — the
    caller is expected to have arranged this deliberately.

    Edge kinds (:class:`EdgeKind`) are preserved as-is in the org
    definition; the state graph collapses them into a single "next"
    relation because the supervisor (not the topology) decides whether
    a hop is a delegation, a consult, or a hierarchy escalation.
    """
    graph = StateGraph()
    for node in org.nodes:
        graph.add_node(node.id)

    cond_sources = set(conditional_routers or {})

    for edge in org.edges:
        if edge.src in cond_sources:
            # Static topology will be replaced by the conditional
            # wiring below. Skip silently.
            continue
        # Skip CONSULT edges: they are point-in-time advisory hops
        # the supervisor brain may issue, not part of the deterministic
        # routing graph. HIERARCHY and COLLABORATE both translate to a
        # default next-hop; if a node has multiple HIERARCHY children,
        # the *first* declaration wins for the static edge — additional
        # children stay reachable via the brain's next_speaker hint
        # plus the messenger's role/workbench resolution.
        if edge.kind == EdgeKind.CONSULT:
            continue
        if edge.src in graph.static_edges:
            continue
        graph.add_edge(edge.src, edge.dst)

    if conditional_routers:
        for src, (router, mapping) in conditional_routers.items():
            graph.add_conditional_edges(src, router, dict(mapping))

    if entry_point is not None:
        graph.set_entry_point(entry_point)
    elif org.nodes:
        # Fall back to the earliest-created node for stability across
        # serialisation round-trips. Matches OrgV2's storage order.
        first = _earliest_node(org.nodes)
        graph.set_entry_point(first.id)

    return graph


def _earliest_node(nodes: list[NodeV2]) -> NodeV2:
    """Return the node with the smallest ``created_at`` timestamp.

    Used by :func:`compile_from_org` to pick a deterministic default
    entry point. Ties (same timestamp) fall back to the order in the
    list, which matches the org's storage order.
    """
    earliest = nodes[0]
    for node in nodes[1:]:
        if node.created_at < earliest.created_at:
            earliest = node
    return earliest


# ``EdgeV2`` is re-exported for callers that want to construct ad-hoc
# topology in tests without importing two modules; we pin the symbol so
# linters don't flag the import as unused.
_ = (EdgeKind, EdgeV2, Any)
