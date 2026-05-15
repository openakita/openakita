"""OrgManager.update workbench-node leaf-only constraint.

Workbench nodes (``plugin_origin`` set) MUST be leaves — when a parent
saves edges that point downstream from a workbench node, OrgManager
should refuse the save with a ``ValueError`` so the API layer returns 400.
Runtime _create_node_agent would otherwise drop ``allowed_external``
to empty for coordinator nodes (see runtime.py L2155-2159), making the
workbench's plugin tools unreachable.
"""

from __future__ import annotations

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.models import EdgeType, OrgEdge, OrgNode, Organization


def _build_org_with_workbench(role_title: str = "通义生图") -> Organization:
    parent = OrgNode(id="ceo", role_title="CEO", level=0)
    workbench = OrgNode(
        id="wb1",
        role_title=role_title,
        level=1,
        external_tools=["tongyi_image_create"],
        plugin_origin={
            "plugin_id": "tongyi-image",
            "template_id": "workbench:tongyi-image",
            "version": "0.3.0",
        },
    )
    return Organization(
        id="org_wb",
        name="wb-org",
        nodes=[parent, workbench],
        edges=[OrgEdge(source="ceo", target="wb1", edge_type=EdgeType.HIERARCHY)],
    )


def test_update_rejects_workbench_with_children(tmp_path):
    mgr = OrgManager(tmp_path)
    org = mgr.create(_build_org_with_workbench().to_dict())

    # Append a child under the workbench node — this is the forbidden
    # topology and must be caught by OrgManager.update before disk write.
    child = OrgNode(id="child", role_title="子节点", level=2).to_dict()
    payload = org.to_dict()
    payload["nodes"].append(child)
    payload["edges"].append(
        OrgEdge(source="wb1", target="child", edge_type=EdgeType.HIERARCHY).to_dict()
    )

    with pytest.raises(ValueError, match="工作台节点必须是叶子节点"):
        mgr.update(org.id, payload)


def test_update_accepts_workbench_as_leaf(tmp_path):
    mgr = OrgManager(tmp_path)
    org = mgr.create(_build_org_with_workbench().to_dict())

    payload = org.to_dict()
    # touch a benign field to trigger a real persist
    payload["description"] = "edited"
    updated = mgr.update(org.id, payload)
    assert updated.description == "edited"
    # plugin_origin survives the round-trip via from_dict whitelist
    wb_node = next(n for n in updated.nodes if n.id == "wb1")
    assert wb_node.plugin_origin == {
        "plugin_id": "tongyi-image",
        "template_id": "workbench:tongyi-image",
        "version": "0.3.0",
    }
