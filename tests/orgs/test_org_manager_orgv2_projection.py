"""Phase 1 tests for ``OrgManager.as_orgv2`` (Sprint 13 H2 / RC-1 治根).

Why this projection exists: ``OrgManager`` is the SSoT for org
persistence (``data/orgs/<id>/org.json``); the ``/api/v2/orgs-spec``
HTTP facade and the IM canary path both expect the
:class:`OrgV2` wire format. ``as_orgv2`` lets callers read the
SSoT in spec shape without ever round-tripping through the
deprecated ``data/orgs_v2.json`` file -- this is the read-side
half of the RC-1 fix; the write-side is the JsonOrgStore shim
(Phase 2) plus the spec-route migration (Phase 4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.org_models import (
    EdgeType,
    NodeStatus,
    Organization,
    OrgEdge,
    OrgNode,
    OrgStatus,
)
from openakita.runtime.models import (
    EdgeKind,
    NodeType,
    OrgV2,
)
from openakita.runtime.models import (
    NodeStatus as RuntimeNodeStatus,
)
from openakita.runtime.models import (
    OrgStatus as RuntimeOrgStatus,
)


@pytest.fixture
def manager(tmp_path: Path) -> OrgManager:
    """Return a manager rooted at a fresh tmp dir.

    Each test gets its own ``data/`` so cross-test leakage is
    impossible -- the manager auto-creates ``orgs/`` and
    ``org_templates/`` under the supplied root.
    """
    return OrgManager(tmp_path)


def _seed_org(
    manager: OrgManager,
    *,
    name: str = "Acme",
    nodes: list[OrgNode] | None = None,
    edges: list[OrgEdge] | None = None,
    status: OrgStatus = OrgStatus.DORMANT,
    description: str = "team description",
) -> Organization:
    """Create an Organization on disk via OrgManager.create.

    Sticks to ``OrgManager.create`` (the public mint API) so the
    projection is exercised against persisted state -- not just a
    transient in-memory object.
    """
    data: dict[str, object] = {
        "name": name,
        "description": description,
        "status": status.value,
        "nodes": [n.to_dict() for n in (nodes or [])],
        "edges": [e.to_dict() for e in (edges or [])],
    }
    return manager.create(data)


# ---------------------------------------------------------------------------
# Spec contract: unknown -> None
# ---------------------------------------------------------------------------


def test_as_orgv2_returns_none_for_unknown_id(manager: OrgManager) -> None:
    """A miss returns ``None`` (matches OrgManager.get's miss contract)."""
    assert manager.as_orgv2("org_does_not_exist") is None


# ---------------------------------------------------------------------------
# Spec contract: mint org round-trips into OrgV2 shape
# ---------------------------------------------------------------------------


def test_as_orgv2_projects_mint_org_to_orgv2_schema(manager: OrgManager) -> None:
    """A freshly-minted org projects into an OrgV2 with the expected
    id / name / description / status mapping (DORMANT -> CREATED)."""
    org = _seed_org(manager, name="MintCorp", description="growing fast")
    projected = manager.as_orgv2(org.id)
    assert projected is not None
    assert isinstance(projected, OrgV2)
    assert projected.id == org.id
    assert projected.name == "MintCorp"
    assert projected.description == "growing fast"
    assert projected.status == RuntimeOrgStatus.CREATED


def test_as_orgv2_status_mapping_covers_all_legacy_values(
    manager: OrgManager,
) -> None:
    """Every legacy OrgStatus must map to a valid runtime OrgStatus.

    DORMANT -> CREATED, ACTIVE -> ACTIVE, RUNNING -> RUNNING,
    PAUSED -> PAUSED, ARCHIVED -> STOPPED. This is the contract
    the spec list endpoint depends on for v25 leftovers as well
    as fresh mints.
    """
    cases = [
        (OrgStatus.DORMANT, RuntimeOrgStatus.CREATED),
        (OrgStatus.ACTIVE, RuntimeOrgStatus.ACTIVE),
        (OrgStatus.RUNNING, RuntimeOrgStatus.RUNNING),
        (OrgStatus.PAUSED, RuntimeOrgStatus.PAUSED),
        (OrgStatus.ARCHIVED, RuntimeOrgStatus.STOPPED),
    ]
    for legacy_status, expected_v2 in cases:
        org = _seed_org(manager, name=f"Status-{legacy_status.value}", status=legacy_status)
        projected = manager.as_orgv2(org.id)
        assert projected is not None, legacy_status
        assert projected.status == expected_v2, legacy_status


def test_as_orgv2_preserves_required_fields(manager: OrgManager) -> None:
    """Required OrgV2 fields (id, name, created_at, updated_at) are
    populated and round-trip through ``to_jsonable`` without raising."""
    org = _seed_org(manager, name="FieldCheck")
    projected = manager.as_orgv2(org.id)
    assert projected is not None
    assert projected.id and projected.id == org.id
    assert projected.name == "FieldCheck"
    assert projected.created_at is not None
    assert projected.updated_at is not None
    payload = projected.to_jsonable()
    assert payload["id"] == org.id
    assert payload["name"] == "FieldCheck"
    assert "status" in payload
    assert "nodes" in payload and isinstance(payload["nodes"], list)
    assert "edges" in payload and isinstance(payload["edges"], list)


# ---------------------------------------------------------------------------
# Node / edge projection
# ---------------------------------------------------------------------------


def test_as_orgv2_projects_nodes_with_parent_id_from_hierarchy_edges(
    manager: OrgManager,
) -> None:
    """Parent inference walks HIERARCHY edges only; COLLABORATE
    edges must NOT contribute a parent (otherwise NodeV2.parent_id
    would point at a sibling and the entry-point heuristic in
    channel_routing breaks)."""
    root = OrgNode(id="node_root", role_title="CEO", agent_profile_id="default")
    child = OrgNode(id="node_child", role_title="Engineer", agent_profile_id="code-assistant")
    h_edge = OrgEdge(id="edge_h", source="node_root", target="node_child")
    c_edge = OrgEdge(
        id="edge_c",
        source="node_root",
        target="node_child",
        edge_type=EdgeType.COLLABORATE,
    )
    org = _seed_org(manager, name="HierarchyCheck", nodes=[root, child], edges=[h_edge, c_edge])
    projected = manager.as_orgv2(org.id)
    assert projected is not None
    by_id = {n.id: n for n in projected.nodes}
    assert by_id["node_root"].parent_id is None
    assert by_id["node_child"].parent_id == "node_root"


def test_as_orgv2_node_status_mapping_covers_legacy_values(
    manager: OrgManager,
) -> None:
    """Each legacy NodeStatus maps to a v2 NodeStatus -- WAITING /
    FROZEN intentionally collapse to IDLE / OFFLINE since v2
    drops both intermediate states."""
    cases = [
        (NodeStatus.IDLE, RuntimeNodeStatus.IDLE),
        (NodeStatus.BUSY, RuntimeNodeStatus.BUSY),
        (NodeStatus.WAITING, RuntimeNodeStatus.IDLE),
        (NodeStatus.ERROR, RuntimeNodeStatus.ERROR),
        (NodeStatus.OFFLINE, RuntimeNodeStatus.OFFLINE),
        (NodeStatus.FROZEN, RuntimeNodeStatus.OFFLINE),
    ]
    nodes = [
        OrgNode(
            id=f"node_{legacy.value}",
            role_title=legacy.value.title(),
            agent_profile_id="default",
            status=legacy,
        )
        for legacy, _expected in cases
    ]
    org = _seed_org(manager, name="StatusMix", nodes=nodes)
    projected = manager.as_orgv2(org.id)
    assert projected is not None
    by_id = {n.id: n for n in projected.nodes}
    for legacy, expected in cases:
        assert by_id[f"node_{legacy.value}"].status == expected, legacy


def test_as_orgv2_workbench_node_carries_plugin_binding(
    manager: OrgManager,
) -> None:
    """A node with ``plugin_origin`` set projects to NodeType.WORKBENCH
    plus a populated WorkbenchBinding."""
    workbench_node = OrgNode(
        id="node_wb",
        role_title="PPT Maker",
        agent_profile_id="default",
        plugin_origin={"plugin_id": "ppt-maker", "mode": "studio"},
    )
    org = _seed_org(manager, name="WBOrg", nodes=[workbench_node])
    projected = manager.as_orgv2(org.id)
    assert projected is not None
    assert len(projected.nodes) == 1
    n = projected.nodes[0]
    assert n.type == NodeType.WORKBENCH
    assert n.workbench is not None
    assert n.workbench.plugin_id == "ppt-maker"
    assert n.workbench.mode == "studio"


def test_as_orgv2_edges_preserve_id_src_dst_kind(manager: OrgManager) -> None:
    """Edge projection: id / source / target / edge_type all round-trip."""
    edges = [
        OrgEdge(id="edge_h", source="a", target="b", edge_type=EdgeType.HIERARCHY),
        OrgEdge(id="edge_c", source="a", target="b", edge_type=EdgeType.COLLABORATE),
        OrgEdge(id="edge_e", source="b", target="a", edge_type=EdgeType.ESCALATE),
        OrgEdge(id="edge_x", source="a", target="b", edge_type=EdgeType.CONSULT),
    ]
    nodes = [
        OrgNode(id="a", role_title="A", agent_profile_id="default"),
        OrgNode(id="b", role_title="B", agent_profile_id="default"),
    ]
    org = _seed_org(manager, name="EdgeCheck", nodes=nodes, edges=edges)
    projected = manager.as_orgv2(org.id)
    assert projected is not None
    by_id = {e.id: e for e in projected.edges}
    assert by_id["edge_h"].kind == EdgeKind.HIERARCHY
    assert by_id["edge_c"].kind == EdgeKind.COLLABORATE
    assert by_id["edge_e"].kind == EdgeKind.ESCALATE
    assert by_id["edge_x"].kind == EdgeKind.CONSULT
    for eid in ("edge_h", "edge_c", "edge_e", "edge_x"):
        assert by_id[eid].src in {"a", "b"}
        assert by_id[eid].dst in {"a", "b"}


# ---------------------------------------------------------------------------
# Robustness: malformed timestamps / missing optional fields
# ---------------------------------------------------------------------------


def test_as_orgv2_falls_back_when_created_at_is_unparseable(
    manager: OrgManager, tmp_path: Path
) -> None:
    """A legacy org.json with a malformed ``created_at`` string must
    still project successfully -- the helper falls back to file
    mtime / now() so a single bad row cannot wedge the spec API."""
    org = _seed_org(manager, name="BadTS")
    org_path = tmp_path / "orgs" / org.id / "org.json"
    raw = org_path.read_text(encoding="utf-8")
    raw = raw.replace(org.created_at, "not-an-iso-string")
    org_path.write_text(raw, encoding="utf-8")
    manager.invalidate_cache()
    projected = manager.as_orgv2(org.id)
    assert projected is not None
    assert projected.created_at is not None
