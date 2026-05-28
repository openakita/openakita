"""Unit tests for :mod:`openakita.runtime.channel_routing`.

These exercise the gateway-facing flag/store/topology decision tree
without touching the 5,000-line channels gateway itself. Phase 6
ships the gateway hook in a separate commit once these guarantees
are nailed.
"""

from __future__ import annotations

import pytest

from openakita.config import settings
from openakita.orgs import reset_default_store, set_default_org_manager
from openakita.orgs.manager import OrgManager
from openakita.orgs.org_models import OrgEdge, OrgNode
from openakita.runtime.channel_routing import (
    RoutingPlan,
    compute_v2_plan_for_org,
    route_inbound_message_to_v2,
)
from openakita.runtime.models import (
    EdgeKind,
    EdgeV2,
    NodeType,
    NodeV2,
    OrgV2,
    new_edge_id,
    new_node_id,
    new_org_id,
)


@pytest.fixture(autouse=True)
def _enable_v2(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Enable v2 + reset the shim AND the SSoT manager for this test run.

    Sprint 13 H2 (RC-1): the shim now reads through ``OrgManager``,
    so wiring just the JSON path is no longer enough; we point the
    process-wide default manager at the same tmp dir so direct
    ``OrgManager.create`` writes are visible to the shim.
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    manager = OrgManager(tmp_path)
    reset_default_store(path=tmp_path / "orgs_v2.json", manager=manager)
    set_default_org_manager(manager)
    yield
    set_default_org_manager(None)


def _mk_org_with_topology() -> OrgV2:
    """Pure OrgV2 fixture (no manager) for the ``compute_v2_plan_for_org`` tests."""
    org_id = new_org_id()
    root = NodeV2(
        id=new_node_id(),
        org_id=org_id,
        type=NodeType.LLM,
        role="producer",
        label="producer",
    )
    child = NodeV2(
        id=new_node_id(),
        org_id=org_id,
        type=NodeType.LLM,
        role="screenwriter",
        label="screenwriter",
        parent_id=root.id,
    )
    return OrgV2(
        id=org_id,
        name="Test Org",
        nodes=[root, child],
        edges=[
            EdgeV2(
                id=new_edge_id(),
                org_id=org_id,
                kind=EdgeKind.HIERARCHY,
                src=root.id,
                dst=child.id,
            )
        ],
    )


def _seed_topology_via_manager(manager: OrgManager) -> tuple[str, str]:
    """Mint an org with a (root -> child) topology directly via OrgManager.

    Returns ``(org_id, root_node_id)``. Used by the routed-when-
    org-has-topology test to validate that the manager-backed
    shim's read path is wired correctly to ``channel_routing``.
    """
    root = OrgNode(id="node_root", role_title="producer", agent_profile_id="default")
    child = OrgNode(
        id="node_child",
        role_title="screenwriter",
        agent_profile_id="default",
    )
    h_edge = OrgEdge(id="edge_h", source="node_root", target="node_child")
    org = manager.create(
        {
            "name": "Test Org",
            "nodes": [root.to_dict(), child.to_dict()],
            "edges": [h_edge.to_dict()],
        }
    )
    return org.id, "node_root"


def test_skipped_when_v2_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "runtime_v2_enabled", False, raising=False)
    plan = route_inbound_message_to_v2(org_id="org_anything")
    assert plan.status == "skipped"
    assert "runtime_v2_enabled" in plan.reason


def test_skipped_when_no_org_bound() -> None:
    plan = route_inbound_message_to_v2(org_id=None)
    assert plan.status == "skipped"
    assert "not bound" in plan.reason
    plan2 = route_inbound_message_to_v2(org_id="")
    assert plan2.status == "skipped"


def test_skipped_when_org_not_in_store() -> None:
    plan = route_inbound_message_to_v2(org_id="org_missing")
    assert plan.status == "skipped"
    assert "not in v2 store" in plan.reason


def test_routed_when_org_has_topology(tmp_path) -> None:
    """Mint an org via OrgManager (the SSoT) -> ``route_inbound_message_to_v2``
    must find it through the manager-backed shim and return ``routed``.

    This is the v25 H2 case in unit form: pre-Sprint-13 the IM
    canary read path always returned ``"skipped: not in v2 store"``
    for any mint org because ``get_default_store()`` couldn't see
    ``data/orgs/<id>/org.json``.
    """
    manager = OrgManager(tmp_path)
    set_default_org_manager(manager)
    try:
        org_id, root_node_id = _seed_topology_via_manager(manager)
        plan = route_inbound_message_to_v2(org_id=org_id)
        assert plan.status == "routed"
        assert plan.next_node_id == root_node_id
        # ``role_title="producer"`` projects to NodeV2.role="producer"
        # (see ``_node_to_v2`` in orgs/manager.py); the IM canary
        # uses this as the user-facing breadcrumb.
        assert plan.next_node_role == "producer"
        assert plan.routed is True
    finally:
        set_default_org_manager(None)


def test_compute_skips_empty_org() -> None:
    org = OrgV2(id="org_empty", name="empty")
    plan = compute_v2_plan_for_org(org)
    assert plan.status == "skipped"
    assert plan.reason == "org has no nodes"


def test_plan_to_jsonable_round_trips_all_fields() -> None:
    plan = RoutingPlan(
        status="routed",
        org_id="org_1",
        next_node_id="node_1",
        next_node_role="producer",
        reason="ok",
    )
    j = plan.to_jsonable()
    assert j == {
        "status": "routed",
        "org_id": "org_1",
        "next_node_id": "node_1",
        "next_node_role": "producer",
        "reason": "ok",
    }


def test_compute_skips_when_compile_throws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any internal exception inside the routing pipeline must be
    caught and converted into a skipped plan so the legacy gateway
    fallthrough is guaranteed."""

    def _boom(*_a, **_kw):
        raise RuntimeError("graph on fire")

    monkeypatch.setattr(
        "openakita.runtime.channel_routing.compile_from_org",
        _boom,
    )
    org = _mk_org_with_topology()
    plan = compute_v2_plan_for_org(org)
    assert plan.status == "skipped"
    assert "graph on fire" in plan.reason
