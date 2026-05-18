"""Tests for the built-in AIGC Video Studio template."""

from __future__ import annotations

import importlib
import sys

import pytest

from openakita.runtime.models import EdgeKind, NodeType
from openakita.runtime.templates import (
    TemplateRegistry,
    TemplateSpec,
    discover_builtins,
)
from openakita.runtime.templates.builtin.aigc_video_studio import (
    PLUGIN_ID,
    aigc_video_studio,
)


def _fresh_registry() -> TemplateRegistry:
    """Return an empty :class:`TemplateRegistry` with the AIGC template
    registered exactly once.

    We avoid touching the global ``GLOBAL_REGISTRY`` so each test is
    isolated even when run alongside other registry tests.
    """
    reg = TemplateRegistry()
    reg.bootstrap(factories=[aigc_video_studio])
    return reg


def test_factory_returns_valid_templatespec() -> None:
    spec = aigc_video_studio()
    assert isinstance(spec, TemplateSpec)
    spec.validate()
    assert spec.id == "aigc_video_studio"
    assert spec.category == "aigc"
    assert spec.version >= 1
    assert spec.name


def test_factory_returns_fresh_instance_each_call() -> None:
    """Built-in templates must be lazily constructed; two calls return
    distinct objects so a registry can never be poisoned by a mutated
    cached singleton."""
    a = aigc_video_studio()
    b = aigc_video_studio()
    assert a is not b
    assert a.id == b.id


def test_node_topology_matches_aigc_studio_shape() -> None:
    spec = aigc_video_studio()
    ids = {n.id for n in spec.nodes}
    expected = {
        "producer",
        "screenwriter",
        "art_director",
        "wb_image",
        "wb_video",
        "wb_human",
        "wb_long",
    }
    assert ids == expected
    by_id = {n.id: n for n in spec.nodes}
    assert by_id["producer"].type is NodeType.LLM
    assert by_id["screenwriter"].type is NodeType.LLM
    assert by_id["art_director"].type is NodeType.LLM
    for nid in ("wb_image", "wb_video", "wb_human", "wb_long"):
        assert by_id[nid].type is NodeType.WORKBENCH
        assert by_id[nid].workbench is not None
        assert by_id[nid].workbench.plugin_id == PLUGIN_ID


def test_workbench_modes_match_happyhorse_manifest() -> None:
    spec = aigc_video_studio()
    by_id = {n.id: n for n in spec.nodes}
    assert by_id["wb_image"].workbench.mode == "image_artist"
    assert by_id["wb_video"].workbench.mode == "video_animator"
    assert by_id["wb_human"].workbench.mode == "portrait_actor"
    # The stitching role rides on the director mode because that is
    # where hh_long_video_create / hh_video_concat live in the
    # happyhorse-video manifest. We narrow with capabilities so that
    # the WorkbenchNode does not over-expose director-level tools.
    long_node = by_id["wb_long"]
    assert long_node.workbench.mode == "art_director"
    assert long_node.workbench.capabilities is not None
    assert set(long_node.workbench.capabilities) == {
        "storyboard",
        "long_video",
        "video_concat",
    }


def test_hierarchy_edges_form_dag_with_expected_paths() -> None:
    spec = aigc_video_studio()
    hierarchy = {
        (e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.HIERARCHY
    }
    assert ("producer", "screenwriter") in hierarchy
    assert ("producer", "art_director") in hierarchy
    for wb in ("wb_image", "wb_video", "wb_human", "wb_long"):
        assert ("art_director", wb) in hierarchy
    # Workbench nodes are leaves in the hierarchy DAG: they never
    # delegate down to anything else.
    for wb in ("wb_image", "wb_video", "wb_human", "wb_long"):
        for src, _ in hierarchy:
            assert src != wb, f"workbench {wb!r} must not appear as a hierarchy src"


def test_collaborate_edges_carry_storyboard_and_concat_handoffs() -> None:
    spec = aigc_video_studio()
    collab = {
        (e.src, e.dst) for e in spec.edges if e.kind is EdgeKind.COLLABORATE
    }
    assert ("screenwriter", "art_director") in collab
    assert ("screenwriter", "wb_long") in collab
    assert ("wb_image", "wb_video") in collab
    assert ("wb_image", "wb_human") in collab
    assert ("wb_video", "wb_long") in collab
    assert ("wb_human", "wb_long") in collab


def test_personas_are_chinese_and_mention_routing_rules() -> None:
    spec = aigc_video_studio()
    by_id = {n.id: n for n in spec.nodes}
    assert "制片人" in by_id["producer"].persona_prompt
    assert "编剧" in by_id["screenwriter"].persona_prompt
    assert "hh_storyboard_decompose" in by_id["screenwriter"].persona_prompt
    art = by_id["art_director"].persona_prompt
    assert "美术指导" in art
    assert "wb_human" in art and "wb_image" in art and "wb_long" in art
    assert "from_asset_ids" in art
    assert "hh_long_video_create" in by_id["wb_long"].persona_prompt
    assert "hh_video_concat" in by_id["wb_long"].persona_prompt


def test_registry_bootstrap_picks_up_template_via_discover_builtins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing :mod:`runtime.templates.builtin` must register the
    template into a private TemplateRegistry without touching the
    global one.

    We force a clean import of the builtin sub-package so the
    ``@template`` decorator runs against a queue we control.
    """
    from openakita.runtime.templates import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_PENDING", [], raising=False)
    for mod in list(sys.modules):
        if mod.startswith("openakita.runtime.templates.builtin"):
            sys.modules.pop(mod)

    found = discover_builtins()
    assert found >= 1, "builtin discovery should import at least the aigc module"

    reg = TemplateRegistry()
    drained = reg.bootstrap()
    assert drained >= 1
    assert "aigc_video_studio" in reg
    spec = reg.get("aigc_video_studio")
    assert spec.id == "aigc_video_studio"


def test_template_instantiates_into_orgv2_with_fresh_ids() -> None:
    reg = _fresh_registry()
    org = reg.instantiate("aigc_video_studio", name="Acme studio")
    # Seven nodes, twelve edges, all with freshly-minted prefixed ids.
    assert len(org.nodes) == 7
    assert len(org.edges) == 12
    assert org.id.startswith("org_")
    for n in org.nodes:
        assert n.id.startswith("node_")
        assert n.org_id == org.id
    for e in org.edges:
        assert e.id.startswith("edge_")
        assert e.org_id == org.id

    # NodeId mapping preserved the workbench bindings so the runtime
    # WorkbenchNode can pick the correct manifest mode.
    by_role = {n.role: n for n in org.nodes}
    assert by_role["image_artist"].workbench is not None
    assert by_role["image_artist"].workbench.mode == "image_artist"
    assert by_role["long_video_director"].workbench.mode == "art_director"


def test_instantiated_org_has_producer_as_sole_root_with_correct_children() -> None:
    """After instantiate(), the AIGC org must look like a real tree.

    Regression for a bug where parent_id was never derived from the
    HIERARCHY edges, so every node reported as a root and the IM
    dispatcher could not walk the org chart. The fix lives in
    ``TemplateRegistry.instantiate``; this test pins the contract.
    """
    reg = _fresh_registry()
    org = reg.instantiate("aigc_video_studio", name="Acme studio")
    by_role = {n.role: n for n in org.nodes}

    roots = org.root_nodes()
    assert [n.role for n in roots] == ["producer"]
    assert by_role["producer"].parent_id is None

    producer_children = sorted(n.role for n in org.children_of(by_role["producer"].id))
    assert producer_children == ["art_director", "screenwriter"]

    art_dir_children = sorted(
        n.role for n in org.children_of(by_role["art_director"].id)
    )
    assert art_dir_children == [
        "image_artist",
        "long_video_director",
        "portrait_actor",
        "video_animator",
    ]


def test_two_instantiations_have_disjoint_node_id_sets() -> None:
    reg = _fresh_registry()
    a = reg.instantiate("aigc_video_studio", name="A")
    b = reg.instantiate("aigc_video_studio", name="B")
    a_ids = {n.id for n in a.nodes}
    b_ids = {n.id for n in b.nodes}
    assert a_ids.isdisjoint(b_ids)
    assert a.id != b.id


def test_persona_override_takes_effect_at_instantiation() -> None:
    reg = _fresh_registry()
    org = reg.instantiate(
        "aigc_video_studio",
        name="Acme",
        overrides={
            "node_persona_prompts": {
                "art_director": "你是新版本的美术指导。"
            }
        },
    )
    art = next(n for n in org.nodes if n.role == "art_director")
    assert art.persona_prompt == "你是新版本的美术指导。"


def test_module_reexports_factory_for_external_use() -> None:
    """``aigc_video_studio`` must be importable directly so external
    callers (UI, docs generator, tests) can introspect the spec
    without going through the registry."""
    mod = importlib.import_module(
        "openakita.runtime.templates.builtin.aigc_video_studio"
    )
    assert callable(mod.aigc_video_studio)
    assert mod.aigc_video_studio().id == "aigc_video_studio"
