"""Tests for ``runtime.state_graph`` — Pregel-style routing graph.

Closes the lingering Phase 3 caveat documented in
``docs/revamp/PLAN_AUDIT.md``: ``ConditionNode`` populated
``next_address`` on its results, but no engine consumed it. The
:class:`StateGraph` here is the consumer.

Three behavioural anchors in this file:

1. **Topology mutators** — ``add_node`` / ``add_edge`` /
   ``add_conditional_edges`` reject malformed input deterministically.
2. **Routing** — :meth:`StateGraph.route` honours conditional > static
   > delegation hint > supervisor-defer order, and never silently
   falls through on an unknown branch label.
3. **Org compilation** — :func:`compile_from_org` produces a graph
   isomorphic to the org's edges, dropping CONSULT (advisory) hops
   per ADR-0007 §"Cooperation with the supervisor".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openakita.runtime.models import (
    DefaultsSpec,
    EdgeKind,
    EdgeV2,
    NodeType,
    NodeV2,
    OrgStatus,
    OrgV2,
)
from openakita.runtime.state_graph import (
    END,
    START,
    StateGraph,
    StateGraphError,
    compile_from_org,
)
from openakita.runtime.supervisor import DelegationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(message: str = "ok", **metadata) -> DelegationResult:
    return DelegationResult(
        success=True, speaker="src", message=message, metadata=metadata
    )


def _build_demo_graph() -> StateGraph:
    g = StateGraph()
    g.add_node("a")
    g.add_node("b")
    g.add_node("c")
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", END)
    return g


# ---------------------------------------------------------------------------
# Topology mutators
# ---------------------------------------------------------------------------


class TestTopology:
    def test_register_node_and_set_entry(self) -> None:
        g = StateGraph()
        g.add_node("root")
        g.set_entry_point("root")
        assert g.entry_point == "root"
        assert "root" in g.nodes

    def test_add_edge_with_start_sets_entry(self) -> None:
        g = StateGraph()
        g.add_node("first")
        g.add_edge(START, "first")
        assert g.entry_point == "first"

    def test_add_edge_to_unregistered_target_fails(self) -> None:
        g = StateGraph()
        g.add_node("a")
        with pytest.raises(StateGraphError, match="not a registered node"):
            g.add_edge("a", "ghost")

    def test_add_edge_from_unregistered_source_fails(self) -> None:
        g = StateGraph()
        g.add_node("a")
        with pytest.raises(StateGraphError, match="not a registered node"):
            g.add_edge("ghost", "a")

    def test_reserved_sentinels_rejected_as_nodes(self) -> None:
        g = StateGraph()
        with pytest.raises(StateGraphError, match="reserved"):
            g.add_node(START)
        with pytest.raises(StateGraphError, match="reserved"):
            g.add_node(END)

    def test_static_and_conditional_are_mutually_exclusive(self) -> None:
        g = StateGraph()
        g.add_node("a")
        g.add_node("b")
        g.add_edge("a", "b")
        with pytest.raises(StateGraphError, match="mutually exclusive"):
            g.add_conditional_edges("a", lambda _r: "ok", {"ok": "b"})

    def test_idempotent_static_edge_is_allowed(self) -> None:
        g = StateGraph()
        g.add_node("a")
        g.add_node("b")
        g.add_edge("a", "b")
        g.add_edge("a", "b")
        assert g.static_edges == {"a": "b"}

    def test_conflicting_static_edge_fails(self) -> None:
        g = StateGraph()
        g.add_node("a")
        g.add_node("b")
        g.add_node("c")
        g.add_edge("a", "b")
        with pytest.raises(StateGraphError, match="already has a static edge"):
            g.add_edge("a", "c")

    def test_conditional_edges_validate_targets(self) -> None:
        g = StateGraph()
        g.add_node("a")
        g.add_node("b")
        with pytest.raises(StateGraphError, match="not a registered node"):
            g.add_conditional_edges(
                "a", lambda _r: "x", {"x": "b", "y": "ghost"}
            )

    def test_conditional_edges_require_mapping(self) -> None:
        g = StateGraph()
        g.add_node("a")
        with pytest.raises(StateGraphError, match="at least one mapping"):
            g.add_conditional_edges("a", lambda _r: "x", {})


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestRouting:
    @pytest.mark.asyncio
    async def test_static_edge_routes_to_target(self) -> None:
        g = _build_demo_graph()
        nxt = await g.route("a", _ok())
        assert nxt == "b"

    @pytest.mark.asyncio
    async def test_static_edge_to_end_returns_none(self) -> None:
        g = _build_demo_graph()
        assert await g.route("c", _ok()) is None

    @pytest.mark.asyncio
    async def test_no_outgoing_returns_none(self) -> None:
        g = StateGraph()
        g.add_node("a")
        g.set_entry_point("a")
        assert await g.route("a", _ok()) is None

    @pytest.mark.asyncio
    async def test_delegation_hint_used_when_no_outgoing(self) -> None:
        g = StateGraph()
        g.add_node("a")
        g.set_entry_point("a")
        result = _ok(next_address="messenger_role::editor")
        nxt = await g.route("a", result)
        assert nxt == "messenger_role::editor"

    @pytest.mark.asyncio
    async def test_delegation_hint_end_returns_none(self) -> None:
        g = StateGraph()
        g.add_node("a")
        g.set_entry_point("a")
        nxt = await g.route("a", _ok(next_address=END))
        assert nxt is None

    @pytest.mark.asyncio
    async def test_static_edge_overrides_hint(self) -> None:
        g = _build_demo_graph()
        result = _ok(next_address="ghost")
        nxt = await g.route("a", result)
        assert nxt == "b"

    @pytest.mark.asyncio
    async def test_conditional_router_picks_branch(self) -> None:
        g = StateGraph()
        g.add_node("decide")
        g.add_node("happy")
        g.add_node("sad")
        g.set_entry_point("decide")

        def router(result: DelegationResult) -> str:
            return result.metadata["mood"]

        g.add_conditional_edges(
            "decide", router, {"happy": "happy", "sad": "sad"}
        )

        nxt = await g.route("decide", _ok(mood="happy"))
        assert nxt == "happy"
        nxt2 = await g.route("decide", _ok(mood="sad"))
        assert nxt2 == "sad"

    @pytest.mark.asyncio
    async def test_async_conditional_router_supported(self) -> None:
        g = StateGraph()
        g.add_node("decide")
        g.add_node("done")
        g.set_entry_point("decide")

        async def router(result: DelegationResult) -> str:
            return "done" if result.success else "fail"

        g.add_conditional_edges(
            "decide", router, {"done": "done", "fail": END}
        )
        assert await g.route("decide", _ok()) == "done"
        assert await g.route(
            "decide",
            DelegationResult(success=False, speaker="x", message="bad"),
        ) is None

    @pytest.mark.asyncio
    async def test_unknown_branch_label_raises(self) -> None:
        g = StateGraph()
        g.add_node("decide")
        g.add_node("only")
        g.set_entry_point("decide")
        g.add_conditional_edges("decide", lambda _r: "rogue", {"only": "only"})
        with pytest.raises(StateGraphError, match="unknown.*label"):
            await g.route("decide", _ok())

    @pytest.mark.asyncio
    async def test_router_must_return_string(self) -> None:
        g = StateGraph()
        g.add_node("decide")
        g.add_node("only")
        g.set_entry_point("decide")
        g.add_conditional_edges("decide", lambda _r: 42, {"only": "only"})
        with pytest.raises(StateGraphError, match="must return str"):
            await g.route("decide", _ok())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_passes_on_well_formed_graph(self) -> None:
        g = _build_demo_graph()
        g.validate()

    def test_validate_requires_entry_point(self) -> None:
        g = StateGraph()
        g.add_node("a")
        with pytest.raises(StateGraphError, match="entry point"):
            g.validate()

    def test_successors_lists_static_and_conditional(self) -> None:
        g = StateGraph()
        for n in ("a", "b", "c", "d"):
            g.add_node(n)
        g.set_entry_point("a")
        g.add_edge("a", "b")
        g.add_conditional_edges(
            "b", lambda _r: "left", {"left": "c", "right": "d"}
        )
        assert g.successors("a") == ["b"]
        assert sorted(g.successors("b")) == ["c", "d"]


# ---------------------------------------------------------------------------
# compile_from_org
# ---------------------------------------------------------------------------


def _make_node(node_id: str, *, created: datetime, parent: str | None = None) -> NodeV2:
    return NodeV2(
        id=node_id,
        org_id="org_test",
        type=NodeType.LLM,
        role=node_id,
        label=node_id.title(),
        parent_id=parent,
        created_at=created,
    )


def _make_org(nodes: list[NodeV2], edges: list[EdgeV2]) -> OrgV2:
    return OrgV2(
        id="org_test",
        name="Test",
        template_id="t",
        description="",
        nodes=nodes,
        edges=edges,
        defaults=DefaultsSpec(),
        status=OrgStatus.ACTIVE,
        created_at=datetime(2026, 5, 18, tzinfo=UTC),
        updated_at=datetime(2026, 5, 18, tzinfo=UTC),
    )


class TestCompileFromOrg:
    def test_static_edges_become_static_routes(self) -> None:
        a = _make_node("a", created=datetime(2026, 5, 18, 10, 0, tzinfo=UTC))
        b = _make_node(
            "b",
            created=datetime(2026, 5, 18, 10, 1, tzinfo=UTC),
            parent="a",
        )
        org = _make_org(
            [a, b],
            [
                EdgeV2(
                    id="e1",
                    org_id="org_test",
                    src="a",
                    dst="b",
                    kind=EdgeKind.HIERARCHY,
                )
            ],
        )
        g = compile_from_org(org)
        assert g.entry_point == "a"
        assert g.static_edges == {"a": "b"}

    def test_default_entry_is_earliest_created(self) -> None:
        late = _make_node("late", created=datetime(2026, 5, 18, 12, tzinfo=UTC))
        early = _make_node("early", created=datetime(2026, 5, 18, 9, tzinfo=UTC))
        org = _make_org([late, early], [])
        g = compile_from_org(org)
        assert g.entry_point == "early"

    def test_explicit_entry_point_overrides_default(self) -> None:
        a = _make_node("a", created=datetime(2026, 5, 18, 9, tzinfo=UTC))
        b = _make_node("b", created=datetime(2026, 5, 18, 10, tzinfo=UTC))
        org = _make_org([a, b], [])
        g = compile_from_org(org, entry_point="b")
        assert g.entry_point == "b"

    def test_consult_edges_are_dropped(self) -> None:
        a = _make_node("a", created=datetime(2026, 5, 18, 9, tzinfo=UTC))
        b = _make_node("b", created=datetime(2026, 5, 18, 10, tzinfo=UTC))
        org = _make_org(
            [a, b],
            [
                EdgeV2(
                    id="e",
                    org_id="org_test",
                    src="a",
                    dst="b",
                    kind=EdgeKind.CONSULT,
                )
            ],
        )
        g = compile_from_org(org)
        assert g.static_edges == {}

    def test_conditional_routers_replace_static(self) -> None:
        a = _make_node("a", created=datetime(2026, 5, 18, 9, tzinfo=UTC))
        b = _make_node("b", created=datetime(2026, 5, 18, 10, tzinfo=UTC))
        c = _make_node("c", created=datetime(2026, 5, 18, 11, tzinfo=UTC))
        org = _make_org(
            [a, b, c],
            [
                EdgeV2(
                    id="e1",
                    org_id="org_test",
                    src="a",
                    dst="b",
                    kind=EdgeKind.HIERARCHY,
                )
            ],
        )
        g = compile_from_org(
            org,
            conditional_routers={
                "a": (lambda _r: "yes", {"yes": "b", "no": "c"})
            },
        )
        assert "a" not in g.static_edges
        assert "a" in g.conditional_edges
        assert g.entry_point == "a"
