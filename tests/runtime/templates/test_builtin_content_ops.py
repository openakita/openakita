"""Tests for the Content Operations Team built-in template."""

from __future__ import annotations

from openakita.runtime.models import EdgeKind, NodeType
from openakita.runtime.templates import TemplateRegistry, TemplateSpec
from openakita.runtime.templates.builtin.content_ops import content_ops


def _registry() -> TemplateRegistry:
    reg = TemplateRegistry()
    reg.bootstrap(factories=[content_ops])
    return reg


def test_factory_returns_valid_templatespec() -> None:
    spec = content_ops()
    assert isinstance(spec, TemplateSpec)
    spec.validate()
    assert spec.id == "content_ops"
    assert spec.category == "content"


def test_factory_returns_fresh_instance_each_call() -> None:
    a = content_ops()
    b = content_ops()
    assert a is not b


def test_seven_node_topology() -> None:
    spec = content_ops()
    assert {n.id for n in spec.nodes} == {
        "editor_in_chief",
        "planner",
        "writer_a",
        "writer_b",
        "visual",
        "seo_opt",
        "data_analyst",
    }
    assert all(n.type is NodeType.LLM for n in spec.nodes)


def test_planner_owns_writers_and_visual() -> None:
    spec = content_ops()
    hierarchy = {
        (e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.HIERARCHY
    }
    assert ("planner", "writer_a") in hierarchy
    assert ("planner", "writer_b") in hierarchy
    assert ("planner", "visual") in hierarchy


def test_data_loop_uses_collaborate_edge() -> None:
    spec = content_ops()
    collabs = {
        (e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.COLLABORATE
    }
    assert ("data_analyst", "planner") in collabs
    assert ("writer_a", "seo_opt") in collabs
    assert ("writer_b", "seo_opt") in collabs


def test_template_instantiates_with_fresh_ids() -> None:
    reg = _registry()
    org = reg.instantiate("content_ops", name="Acme Editorial")
    assert len(org.nodes) == 7
    assert len(org.edges) == 11
    assert all(n.id.startswith("node_") for n in org.nodes)
    assert all(e.id.startswith("edge_") for e in org.edges)
