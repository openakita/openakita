"""Tests for the Startup Company built-in template."""

from __future__ import annotations

from openakita.runtime.models import EdgeKind, NodeType
from openakita.runtime.templates import TemplateRegistry, TemplateSpec
from openakita.runtime.templates.builtin.startup_company import startup_company


def _registry() -> TemplateRegistry:
    reg = TemplateRegistry()
    reg.bootstrap(factories=[startup_company])
    return reg


def test_factory_returns_valid_templatespec() -> None:
    spec = startup_company()
    assert isinstance(spec, TemplateSpec)
    spec.validate()
    assert spec.id == "startup_company"
    assert spec.category == "company"


def test_factory_returns_fresh_instance_each_call() -> None:
    a = startup_company()
    b = startup_company()
    assert a is not b
    assert a.id == b.id


def test_sixteen_node_topology() -> None:
    spec = startup_company()
    assert {n.id for n in spec.nodes} == {
        "ceo",
        "cto",
        "architect",
        "dev_a",
        "dev_b",
        "devops",
        "cpo",
        "pm",
        "ui_designer",
        "cmo",
        "content_op",
        "seo",
        "social_media",
        "cfo",
        "hr",
        "legal",
    }
    assert all(n.type is NodeType.LLM for n in spec.nodes)


def test_ceo_owns_four_directors() -> None:
    spec = startup_company()
    hierarchy = {
        (e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.HIERARCHY
    }
    assert ("ceo", "cto") in hierarchy
    assert ("ceo", "cpo") in hierarchy
    assert ("ceo", "cmo") in hierarchy
    assert ("ceo", "cfo") in hierarchy


def test_cross_department_collaborate_edges() -> None:
    spec = startup_company()
    collabs = {
        (e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.COLLABORATE
    }
    assert ("cpo", "cto") in collabs
    assert ("pm", "dev_a") in collabs
    assert ("pm", "dev_b") in collabs
    assert ("content_op", "seo") in collabs


def test_template_instantiates_with_fresh_ids() -> None:
    reg = _registry()
    org = reg.instantiate("startup_company", name="Acme Inc.")
    assert len(org.nodes) == 16
    assert len(org.edges) == 19
    assert all(n.id.startswith("node_") for n in org.nodes)
    assert all(e.id.startswith("edge_") for e in org.edges)
