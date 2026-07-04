"""Tests for runtime.templates.schema — TemplateSpec validation."""

from __future__ import annotations

import pytest

from openakita.runtime.models import EdgeKind, NodeType
from openakita.runtime.templates import (
    DefaultsSpec,
    EdgeSpec,
    GuardrailSpec,
    NodeRuntimeOverridesSpec,
    NodeSpec,
    TemplateSpec,
    TemplateValidationError,
    WorkbenchBindingSpec,
)


def _good_node(**kwargs) -> NodeSpec:
    base: dict = {
        "id": "art_director",
        "type": NodeType.LLM,
        "role": "art_director",
        "label": "Art Director",
    }
    base.update(kwargs)
    return NodeSpec(**base)


def _good_template(**kwargs) -> TemplateSpec:
    nodes = (
        _good_node(),
        _good_node(id="image_artist", role="image_artist", label="Image Artist"),
    )
    edges = (EdgeSpec(src="art_director", dst="image_artist"),)
    base: dict = {
        "id": "t1",
        "name": "T1",
        "category": "content_production",
        "description": "x",
        "version": 1,
        "nodes": nodes,
        "edges": edges,
    }
    base.update(kwargs)
    return TemplateSpec(**base)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_minimal_template_validates() -> None:
    tpl = _good_template()
    tpl.validate()
    assert len(tpl.nodes) == 2
    assert len(tpl.edges) == 1


def test_workbench_node_with_binding_validates() -> None:
    node = NodeSpec(
        id="video_animator",
        type=NodeType.WORKBENCH,
        role="video_animator",
        label="Video Animator",
        workbench=WorkbenchBindingSpec(
            plugin_id="happyhorse-video",
            mode="video_animator",
        ),
    )
    node.validate()


def test_template_to_jsonable_round_trips_keys() -> None:
    tpl = _good_template()
    payload = tpl.to_jsonable()
    assert payload["id"] == "t1"
    assert payload["nodes"][0]["type"] == "llm"
    assert payload["edges"][0]["kind"] == EdgeKind.HIERARCHY.value
    assert payload["defaults"]["max_turns"] == 30


# ---------------------------------------------------------------------------
# id / handle hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["", "Art_Director", "1foo", "with space", "with-dash", "_leading"],
)
def test_node_id_must_match_role_handle_pattern(bad_id: str) -> None:
    with pytest.raises(TemplateValidationError, match="must match"):
        _good_node(id=bad_id).validate()


def test_template_id_must_match_role_handle_pattern() -> None:
    with pytest.raises(TemplateValidationError, match="must match"):
        _good_template(id="With-Dash").validate()


# ---------------------------------------------------------------------------
# Workbench coupling rules
# ---------------------------------------------------------------------------


def test_workbench_node_without_binding_is_invalid() -> None:
    node = NodeSpec(
        id="x",
        type=NodeType.WORKBENCH,
        role="r",
        label="R",
    )
    with pytest.raises(TemplateValidationError, match="workbench binding"):
        node.validate()


def test_non_workbench_node_with_binding_is_invalid() -> None:
    node = NodeSpec(
        id="x",
        type=NodeType.LLM,
        role="r",
        label="R",
        workbench=WorkbenchBindingSpec(plugin_id="p", mode="m"),
    )
    with pytest.raises(TemplateValidationError, match="only allowed for type=workbench"):
        node.validate()


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def test_edge_must_reference_existing_node_ids() -> None:
    tpl = _good_template(
        edges=(EdgeSpec(src="art_director", dst="ghost"),)
    )
    with pytest.raises(TemplateValidationError, match="edge dst"):
        tpl.validate()


def test_edge_self_loop_is_invalid() -> None:
    tpl = _good_template(
        edges=(EdgeSpec(src="art_director", dst="art_director"),)
    )
    with pytest.raises(TemplateValidationError, match="src and dst must differ"):
        tpl.validate()


def test_hierarchy_cycle_is_rejected() -> None:
    tpl = _good_template(
        edges=(
            EdgeSpec(src="art_director", dst="image_artist"),
            EdgeSpec(src="image_artist", dst="art_director"),
        )
    )
    with pytest.raises(TemplateValidationError, match="hierarchy cycle"):
        tpl.validate()


def test_collaborate_cycle_is_allowed() -> None:
    tpl = _good_template(
        edges=(
            EdgeSpec(src="art_director", dst="image_artist", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="image_artist", dst="art_director", kind=EdgeKind.COLLABORATE),
        )
    )
    tpl.validate()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_must_be_positive() -> None:
    tpl = _good_template(defaults=DefaultsSpec(max_turns=0))
    with pytest.raises(TemplateValidationError, match="max_turns"):
        tpl.validate()


def test_defaults_default_channel_set_matches_adr_0006() -> None:
    d = DefaultsSpec()
    expected = {
        "values", "updates", "tasks", "checkpoints",
        "messages", "progress_ledger", "lifecycle",
    }
    assert set(d.stream_channels) == expected


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


def test_guardrail_options_must_be_dict() -> None:
    g = GuardrailSpec(type="min_items", options="oops")  # type: ignore[arg-type]
    with pytest.raises(TemplateValidationError, match="options must be a dict"):
        g.validate()


def test_guardrail_attaches_to_node_validates_via_node_validate() -> None:
    node = _good_node(
        guardrails=(GuardrailSpec(type="min_items", options={"field": "x", "n": 3}),)
    )
    node.validate()


# ---------------------------------------------------------------------------
# Runtime overrides
# ---------------------------------------------------------------------------


def test_runtime_overrides_reject_negative_ints() -> None:
    overrides = NodeRuntimeOverridesSpec(max_iterations=-1)
    with pytest.raises(TemplateValidationError, match="max_iterations"):
        overrides.validate()


def test_runtime_overrides_persona_overlay_must_be_string() -> None:
    overrides = NodeRuntimeOverridesSpec(persona_overlay=42)  # type: ignore[arg-type]
    with pytest.raises(TemplateValidationError, match="persona_overlay"):
        overrides.validate()


def test_runtime_overrides_iter_set_only_yields_present_keys() -> None:
    overrides = NodeRuntimeOverridesSpec(max_turns=15, persona_overlay="x")
    assert dict(overrides.iter_set()) == {"max_turns": 15, "persona_overlay": "x"}


# ---------------------------------------------------------------------------
# Duplicate node ids
# ---------------------------------------------------------------------------


def test_duplicate_node_ids_are_rejected() -> None:
    tpl = _good_template(
        nodes=(_good_node(), _good_node()),
        edges=(),
    )
    with pytest.raises(TemplateValidationError, match="duplicate id"):
        tpl.validate()


# ---------------------------------------------------------------------------
# get_node
# ---------------------------------------------------------------------------


def test_get_node_returns_match_or_raises_keyerror() -> None:
    tpl = _good_template()
    assert tpl.get_node("art_director").label == "Art Director"
    with pytest.raises(KeyError):
        tpl.get_node("ghost")
