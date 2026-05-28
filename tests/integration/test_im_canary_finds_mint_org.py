"""Phase 3 regression: IM canary path can see mint orgs (Sprint 13 H2 / RC-1).

Pre-Sprint-13 reproduction (v22 RCA RC-1 § "关联现象"):

* ``OrgManager.create_from_template`` minted an org under
  ``data/orgs/<id>/org.json`` (write site A).
* IM canary's :func:`route_inbound_message_to_v2` /
  :func:`dispatch_inbound_message_to_v2` looked the org up via
  ``get_default_store().get(org_id)``, which read
  ``data/orgs_v2.json`` only (read site B).
* Sites A and B never overlapped, so EVERY mint-created org
  came back ``OrgNotFound``; the canary always returned
  ``RoutingPlan(status="skipped", reason="org X not in v2 store")``
  -- the v25 H2 / feishu / wework_ws / qqbot symptom across 4
  rounds of NOTFIX.

Post-Sprint-13 expectation (this test):

* Mint via :class:`OrgManager` (no JSON store touch).
* The IM canary's same call path resolves the org (the shim
  now routes through :meth:`OrgManager.as_orgv2`).
* ``RoutingPlan.status == "routed"`` -- not ``"skipped"`` --
  proving the read-side half of RC-1 is fixed at the IM canary
  boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.config import settings
from openakita.orgs import reset_default_store, set_default_org_manager
from openakita.orgs.manager import OrgManager
from openakita.orgs.org_models import OrgEdge, OrgNode
from openakita.runtime.channel_routing import (
    RoutingPlan,
    route_inbound_message_to_v2,
)


@pytest.fixture(autouse=True)
def _v2_enabled_with_tmp_manager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> OrgManager:
    """Enable v2 + wire a tmp-rooted OrgManager into the SSoT registry."""
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    manager = OrgManager(tmp_path)
    reset_default_store(path=tmp_path / "orgs_v2.json", manager=manager)
    set_default_org_manager(manager)
    yield manager
    set_default_org_manager(None)
    reset_default_store()


def _mint_topology_org(manager: OrgManager, *, org_id: str = "org_mint_canary") -> str:
    """Mint a (root -> child) hierarchy via OrgManager.create.

    No JSON store touch. The runtime nodes carry minimal metadata
    so the projection has something to expose -- in particular
    a non-empty ``role_title`` so :class:`NodeV2.role` is the
    user-facing breadcrumb the IM canary surfaces in
    ``RoutingPlan.next_node_role``.
    """
    root = OrgNode(id="node_root", role_title="producer", agent_profile_id="default")
    child = OrgNode(
        id="node_child",
        role_title="screenwriter",
        agent_profile_id="default",
    )
    h_edge = OrgEdge(id="edge_h", source="node_root", target="node_child")
    manager.create(
        {
            "id": org_id,
            "name": "Mint Canary",
            "nodes": [root.to_dict(), child.to_dict()],
            "edges": [h_edge.to_dict()],
        }
    )
    return org_id


def test_dispatch_inbound_for_mint_org_does_not_skip(
    _v2_enabled_with_tmp_manager: OrgManager,
) -> None:
    """v25 H2 regression: a mint org is no longer ``"skipped"``.

    This is the unit-level reproduction of v25 H2 ("IM canary --
    feishu / wework_ws / qqbot -- always says ``not in v2 store``").
    Pre-Sprint-13 the assertion ``status == "routed"`` was the
    failing assertion; post-Sprint-13 it must hold.
    """
    org_id = _mint_topology_org(_v2_enabled_with_tmp_manager)

    plan = route_inbound_message_to_v2(org_id=org_id)

    assert isinstance(plan, RoutingPlan)
    assert plan.status != "skipped", (
        f"RC-1 regression: mint org {org_id} skipped via IM canary -- "
        f"reason={plan.reason!r}"
    )
    assert plan.status == "routed"
    assert plan.org_id == org_id
    assert plan.next_node_id == "node_root"
    assert plan.next_node_role == "producer"
    assert plan.routed is True


def test_dispatch_inbound_for_unknown_org_still_skips(
    _v2_enabled_with_tmp_manager: OrgManager,
) -> None:
    """The skip-on-unknown contract is preserved -- the fix only
    closes the false-negative side of RC-1."""
    plan = route_inbound_message_to_v2(org_id="org_does_not_exist")
    assert plan.status == "skipped"
    assert "not in v2 store" in plan.reason


def test_dispatch_for_org_with_only_legacy_json_still_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy ``data/orgs_v2.json`` rows must still route during the
    Sprint-13-H2 deprecation soak window so v25 leftover orgs don't
    suddenly 404 mid-migration -- the union semantics in the shim
    are part of the RC-1 fix's contract, not just an afterthought.
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    import json

    from openakita.runtime.models import EdgeKind, EdgeV2, NodeType, NodeV2, OrgV2

    org_id = "org_v25_legacy"
    legacy_org = OrgV2(
        id=org_id,
        name="Legacy",
        nodes=[
            NodeV2(
                id="node_root",
                org_id=org_id,
                type=NodeType.LLM,
                role="legacy_lead",
                label="Legacy Lead",
            ),
            NodeV2(
                id="node_child",
                org_id=org_id,
                type=NodeType.LLM,
                role="legacy_helper",
                label="Helper",
                parent_id="node_root",
            ),
        ],
        edges=[
            EdgeV2(
                id="edge_h",
                org_id=org_id,
                src="node_root",
                dst="node_child",
                kind=EdgeKind.HIERARCHY,
            )
        ],
    )
    legacy_path = tmp_path / "orgs_v2.json"
    legacy_path.write_text(
        json.dumps({"orgs": {org_id: legacy_org.to_jsonable()}}, ensure_ascii=False),
        encoding="utf-8",
    )
    manager = OrgManager(tmp_path)
    reset_default_store(path=legacy_path, manager=manager)
    set_default_org_manager(manager)
    try:
        plan = route_inbound_message_to_v2(org_id=org_id)
        assert plan.status == "routed", plan.reason
        assert plan.next_node_id == "node_root"
    finally:
        set_default_org_manager(None)
        reset_default_store()
