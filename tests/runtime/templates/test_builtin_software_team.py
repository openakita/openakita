"""Tests for the Software Engineering Team built-in template."""

from __future__ import annotations

from openakita.runtime.models import EdgeKind, NodeType
from openakita.runtime.templates import TemplateRegistry, TemplateSpec
from openakita.runtime.templates.builtin.software_team import software_team


def _registry() -> TemplateRegistry:
    reg = TemplateRegistry()
    reg.bootstrap(factories=[software_team])
    return reg


def test_factory_returns_valid_templatespec() -> None:
    spec = software_team()
    assert isinstance(spec, TemplateSpec)
    spec.validate()
    assert spec.id == "software_team"
    assert spec.category == "engineering"


def test_factory_returns_fresh_instance_each_call() -> None:
    a = software_team()
    b = software_team()
    assert a is not b
    assert a.id == b.id


def test_topology_has_ten_llm_nodes() -> None:
    spec = software_team()
    assert {n.id for n in spec.nodes} == {
        "tech_lead",
        "fe_lead",
        "fe_dev_a",
        "fe_dev_b",
        "be_lead",
        "be_dev_a",
        "be_dev_b",
        "qa",
        "devops_eng",
        "tech_writer",
    }
    assert all(n.type is NodeType.LLM for n in spec.nodes)
    # No node carries a workbench binding — software-team is plain LLM.
    assert all(n.workbench is None for n in spec.nodes)


def test_hierarchy_dag_matches_design_brief() -> None:
    spec = software_team()
    hierarchy = {
        (e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.HIERARCHY
    }
    for r in ("fe_lead", "be_lead", "qa", "devops_eng", "tech_writer"):
        assert ("tech_lead", r) in hierarchy
    for d in ("fe_dev_a", "fe_dev_b"):
        assert ("fe_lead", d) in hierarchy
    for d in ("be_dev_a", "be_dev_b"):
        assert ("be_lead", d) in hierarchy


def test_qa_to_leads_uses_consult_kind() -> None:
    spec = software_team()
    consults = {(e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.CONSULT}
    assert consults == {("qa", "fe_lead"), ("qa", "be_lead")}


def test_devops_collaborates_with_both_leads() -> None:
    spec = software_team()
    collabs = {
        (e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.COLLABORATE
    }
    assert ("fe_lead", "be_lead") in collabs
    assert ("devops_eng", "fe_lead") in collabs
    assert ("devops_eng", "be_lead") in collabs


def test_template_instantiates_with_fresh_ids() -> None:
    reg = _registry()
    org = reg.instantiate("software_team", name="Acme Engineering")
    assert len(org.nodes) == 10
    assert len(org.edges) == 14
    assert all(n.id.startswith("node_") for n in org.nodes)
    assert all(e.id.startswith("edge_") for e in org.edges)
