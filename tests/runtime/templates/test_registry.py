"""Tests for runtime.templates.registry — discovery + instantiate."""

from __future__ import annotations

import pytest

from openakita.runtime.models import EdgeKind, NodeStatus, NodeType, OrgStatus
from openakita.runtime.templates import (
    TEMPLATE_FACTORY_MARK,
    DefaultsSpec,
    EdgeSpec,
    GuardrailSpec,
    NodeRuntimeOverridesSpec,
    NodeSpec,
    TemplateRegistry,
    TemplateSpec,
    TemplateValidationError,
    WorkbenchBindingSpec,
    collect_builtin_factories,
    template,
)
from openakita.runtime.templates.registry import _PENDING


def _spec(*, sid: str = "demo") -> TemplateSpec:
    return TemplateSpec(
        id=sid,
        name="Demo",
        category="content_production",
        description="d",
        version=1,
        nodes=(
            NodeSpec(
                id="art_director",
                type=NodeType.LLM,
                role="art_director",
                label="Art Director",
                persona_prompt="You are AD.",
                runtime=NodeRuntimeOverridesSpec(max_turns=20),
                guardrails=(
                    GuardrailSpec(type="min_length", options={"n": 10}),
                ),
            ),
            NodeSpec(
                id="video_animator",
                type=NodeType.WORKBENCH,
                role="video_animator",
                label="Video Animator",
                workbench=WorkbenchBindingSpec(
                    plugin_id="happyhorse-video",
                    mode="video_animator",
                    capabilities=("i2v", "t2v"),
                ),
            ),
        ),
        edges=(
            EdgeSpec(
                src="art_director",
                dst="video_animator",
                kind=EdgeKind.HIERARCHY,
            ),
        ),
        defaults=DefaultsSpec(max_turns=40, max_stalls=4),
    )


# ---------------------------------------------------------------------------
# CRUD-ish surface
# ---------------------------------------------------------------------------


def test_register_and_get_round_trips() -> None:
    reg = TemplateRegistry()
    spec = _spec()
    reg.register(spec)
    assert "demo" in reg
    assert reg.get("demo") is spec
    assert reg.list() == [spec]
    assert len(reg) == 1


def test_register_validates_spec_and_rejects_bad_input() -> None:
    reg = TemplateRegistry()
    bad = TemplateSpec(
        id="Demo",  # invalid handle: capital D
        name="x",
        category="c",
        description="x",
        version=1,
        nodes=(
            NodeSpec(
                id="x", type=NodeType.LLM, role="x", label="x"
            ),
        ),
    )
    with pytest.raises(TemplateValidationError):
        reg.register(bad)
    assert len(reg) == 0


def test_register_rejects_duplicate_id_with_different_instance() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    other = _spec()  # same id, different dataclass instance
    with pytest.raises(TemplateValidationError, match="already registered"):
        reg.register(other)


def test_register_is_idempotent_for_same_instance() -> None:
    reg = TemplateRegistry()
    spec = _spec()
    reg.register(spec)
    reg.register(spec)  # second call must be a no-op
    assert len(reg) == 1


def test_get_unknown_template_raises_keyerror() -> None:
    reg = TemplateRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


# ---------------------------------------------------------------------------
# Decorator + bootstrap
# ---------------------------------------------------------------------------


def test_template_decorator_queues_and_bootstrap_drains() -> None:
    pending_before = len(_PENDING)

    @template
    def my_template_factory() -> TemplateSpec:
        return _spec(sid="from_decorator")

    assert len(_PENDING) == pending_before + 1
    reg = TemplateRegistry()
    n = reg.bootstrap()
    assert n >= 1  # picks up our factory plus any leftover from earlier
    assert "from_decorator" in reg
    # Bootstrap should have drained the global queue.
    assert len(_PENDING) == 0


def test_bootstrap_with_explicit_factories_does_not_touch_global_queue() -> None:
    pending_before = len(_PENDING)
    reg = TemplateRegistry()
    n = reg.bootstrap(factories=[lambda: _spec(sid="iso_a")])
    assert n == 1
    assert "iso_a" in reg
    assert len(_PENDING) == pending_before


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


def test_instantiate_creates_fresh_org_with_minted_ids_and_carries_template_id() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    org = reg.instantiate("demo", name="My Studio")
    assert org.template_id == "demo"
    assert org.name == "My Studio"
    assert org.status is OrgStatus.CREATED
    assert org.id.startswith("org_")
    assert all(n.id.startswith("node_") for n in org.nodes)
    assert all(e.id.startswith("edge_") for e in org.edges)
    assert {n.role for n in org.nodes} == {"art_director", "video_animator"}
    edge = org.edges[0]
    src_node = next(n for n in org.nodes if n.role == "art_director")
    dst_node = next(n for n in org.nodes if n.role == "video_animator")
    assert edge.src == src_node.id
    assert edge.dst == dst_node.id
    # Workbench binding propagated.
    assert dst_node.workbench is not None
    assert dst_node.workbench.plugin_id == "happyhorse-video"
    assert dst_node.workbench.mode == "video_animator"
    assert dst_node.workbench.capabilities == ("i2v", "t2v")
    # Defaults propagated.
    assert org.defaults.max_turns == 40
    assert org.defaults.max_stalls == 4
    # Live-only fields default cleanly.
    assert all(n.status is NodeStatus.CREATED for n in org.nodes)
    assert all(n.last_seen is None for n in org.nodes)


def test_two_instantiations_yield_independent_orgs() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    a = reg.instantiate("demo", name="A")
    b = reg.instantiate("demo", name="B")
    assert a.id != b.id
    assert {n.id for n in a.nodes}.isdisjoint({n.id for n in b.nodes})


def test_instantiate_applies_defaults_overrides() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    org = reg.instantiate(
        "demo",
        name="custom",
        overrides={"defaults": {"max_turns": 99, "max_stalls": 7}},
    )
    assert org.defaults.max_turns == 99
    assert org.defaults.max_stalls == 7


def test_instantiate_applies_per_node_persona_overrides() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    org = reg.instantiate(
        "demo",
        name="x",
        overrides={"node_persona_prompts": {"art_director": "OVERRIDDEN"}},
    )
    ad = next(n for n in org.nodes if n.role == "art_director")
    assert ad.persona_prompt == "OVERRIDDEN"


def test_instantiate_applies_per_node_runtime_overrides() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    org = reg.instantiate(
        "demo",
        name="x",
        overrides={
            "node_runtime_overrides": {
                "art_director": {"max_turns": 5, "persona_overlay": "test"}
            }
        },
    )
    ad = next(n for n in org.nodes if n.role == "art_director")
    assert ad.runtime_overrides.max_turns == 5
    assert ad.runtime_overrides.persona_overlay == "test"


def test_instantiate_rejects_unknown_override_key() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    with pytest.raises(TemplateValidationError, match="unknown override keys"):
        reg.instantiate("demo", name="x", overrides={"bogus": True})


def test_instantiate_rejects_unknown_defaults_key() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    with pytest.raises(TemplateValidationError, match="defaults override keys"):
        reg.instantiate(
            "demo",
            name="x",
            overrides={"defaults": {"max_task_seconds": 60}},
        )


def test_instantiate_rejects_per_node_override_for_unknown_node() -> None:
    reg = TemplateRegistry()
    reg.register(_spec())
    with pytest.raises(TemplateValidationError, match="unknown node id"):
        reg.instantiate(
            "demo",
            name="x",
            overrides={"node_persona_prompts": {"ghost": "x"}},
        )


# ---------------------------------------------------------------------------
# parent_id derivation from HIERARCHY edges
# ---------------------------------------------------------------------------


def _hierarchy_spec() -> TemplateSpec:
    """A 3-node spec with a real hierarchy: lead -> {writer, editor}."""

    return TemplateSpec(
        id="hier_demo",
        name="Hier Demo",
        category="content_production",
        description="d",
        version=1,
        nodes=(
            NodeSpec(id="lead", type=NodeType.LLM, role="lead", label="Lead"),
            NodeSpec(id="writer", type=NodeType.LLM, role="writer", label="Writer"),
            NodeSpec(id="editor", type=NodeType.LLM, role="editor", label="Editor"),
        ),
        edges=(
            EdgeSpec(src="lead", dst="writer", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="lead", dst="editor", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="writer", dst="editor", kind=EdgeKind.COLLABORATE),
        ),
        defaults=DefaultsSpec(),
    )


def test_instantiate_stamps_parent_id_from_hierarchy_edges() -> None:
    reg = TemplateRegistry()
    reg.register(_hierarchy_spec())
    org = reg.instantiate("hier_demo", name="ok")

    by_role = {n.role: n for n in org.nodes}
    assert by_role["lead"].parent_id is None
    assert by_role["writer"].parent_id == by_role["lead"].id
    assert by_role["editor"].parent_id == by_role["lead"].id

    roots = org.root_nodes()
    assert [n.role for n in roots] == ["lead"]
    children = sorted(n.role for n in org.children_of(by_role["lead"].id))
    assert children == ["editor", "writer"]


def test_instantiate_collaborate_edges_do_not_set_parent() -> None:
    reg = TemplateRegistry()
    reg.register(_hierarchy_spec())
    org = reg.instantiate("hier_demo", name="ok")

    by_role = {n.role: n for n in org.nodes}
    # editor has lead as HIERARCHY parent and writer as COLLABORATE peer;
    # only the HIERARCHY edge should contribute to parent_id.
    assert by_role["editor"].parent_id == by_role["lead"].id


def test_instantiate_rejects_node_with_two_hierarchy_parents() -> None:
    spec = TemplateSpec(
        id="bad",
        name="Bad",
        category="content_production",
        description="d",
        version=1,
        nodes=(
            NodeSpec(id="a", type=NodeType.LLM, role="a", label="A"),
            NodeSpec(id="b", type=NodeType.LLM, role="b", label="B"),
            NodeSpec(id="c", type=NodeType.LLM, role="c", label="C"),
        ),
        edges=(
            EdgeSpec(src="a", dst="c", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="b", dst="c", kind=EdgeKind.HIERARCHY),
        ),
        defaults=DefaultsSpec(),
    )
    reg = TemplateRegistry()
    reg.register(spec)
    with pytest.raises(TemplateValidationError, match="multiple HIERARCHY parents"):
        reg.instantiate("bad", name="ok")


def test_instantiate_idempotent_on_repeat_hierarchy_edges() -> None:
    """Two HIERARCHY edges with the same (src, dst) must collapse to one parent.

    Templates can sometimes encode the same parent relationship twice as a
    side effect of programmatic generation; we should treat that as benign,
    not as a multi-parent violation.
    """

    spec = TemplateSpec(
        id="repeat",
        name="Repeat",
        category="content_production",
        description="d",
        version=1,
        nodes=(
            NodeSpec(id="a", type=NodeType.LLM, role="a", label="A"),
            NodeSpec(id="b", type=NodeType.LLM, role="b", label="B"),
        ),
        edges=(
            EdgeSpec(src="a", dst="b", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="a", dst="b", kind=EdgeKind.HIERARCHY),
        ),
        defaults=DefaultsSpec(),
    )
    reg = TemplateRegistry()
    reg.register(spec)
    org = reg.instantiate("repeat", name="ok")
    by_role = {n.role: n for n in org.nodes}
    assert by_role["b"].parent_id == by_role["a"].id


# ---------------------------------------------------------------------------
# discover_builtins
# ---------------------------------------------------------------------------


def test_discover_builtins_returns_zero_for_missing_package() -> None:
    from openakita.runtime.templates.registry import discover_builtins

    n = discover_builtins(package="openakita.runtime.templates.builtin_does_not_exist")
    assert n == 0


# ---------------------------------------------------------------------------
# collect_builtin_factories — survivable marker-based discovery
# ---------------------------------------------------------------------------


def test_template_decorator_attaches_survivable_factory_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``@template`` must mark the factory so :func:`collect_builtin_factories`
    can find it again even after the lazy queue has been drained."""
    monkeypatch.setattr(
        "openakita.runtime.templates.registry._PENDING", [], raising=False
    )

    @template
    def _t() -> TemplateSpec:
        return _spec(sid="markdemo")

    assert getattr(_t, TEMPLATE_FACTORY_MARK, False) is True


def test_collect_builtin_factories_finds_every_marked_function() -> None:
    """The four flagship templates must all be discoverable via the
    marker even when ``_PENDING`` has been drained earlier."""
    factories = collect_builtin_factories()
    ids = {factory().id for factory in factories}
    assert {"aigc_video_studio", "software_team", "startup_company", "content_ops"} <= ids


def test_collect_builtin_factories_returns_empty_for_missing_package() -> None:
    factories = collect_builtin_factories(
        package="openakita.runtime.templates.builtin_does_not_exist"
    )
    assert factories == []
