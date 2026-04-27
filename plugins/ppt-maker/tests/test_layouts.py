"""Smoke tests for the slide-layout registry."""

from __future__ import annotations

import pytest
from ppt_layouts import (
    LAYOUT_REGISTRY,
    LayoutContentBullets,
    describe_layouts_for_prompt,
    fill_required,
    layout_for,
)
from ppt_models import SlideType
from pydantic import ValidationError


def test_every_layout_has_strict_schema_and_is_serializable() -> None:
    for slide_type, model in LAYOUT_REGISTRY.items():
        schema = model.model_json_schema()
        assert isinstance(schema, dict) and schema, f"{slide_type} schema empty"
        # Strict mode: extra keys must be forbidden so the LLM cannot smuggle in
        # unknown fields.
        assert model.model_config.get("extra") == "forbid", f"{slide_type} not strict"


def test_layout_for_resolves_string_and_unknown_falls_back() -> None:
    assert layout_for("cover") is LAYOUT_REGISTRY[SlideType.COVER]
    assert layout_for(SlideType.CONTENT) is LayoutContentBullets
    # Unknown values fall back to the generic content bullets layout.
    assert layout_for("does_not_exist") is LayoutContentBullets


def test_extra_fields_are_rejected() -> None:
    model = LAYOUT_REGISTRY[SlideType.COVER]
    with pytest.raises(ValidationError):
        model.model_validate({"title": "T", "bogus_field": "x"})


def test_fill_required_accepts_partial_payload() -> None:
    raw = {"items": ["A", "B"]}
    payload = fill_required(SlideType.AGENDA, raw)
    assert payload["items"] == ["A", "B"]
    assert payload.get("speaker_note", "") == ""


def test_fill_required_repairs_missing_required_string() -> None:
    # LayoutCover requires `title`; missing → coerced to empty string but still a dict.
    payload = fill_required(SlideType.COVER, {"subtitle": "hi"})
    assert isinstance(payload, dict)
    assert payload.get("subtitle") == "hi"


def test_describe_layouts_lists_all_registered_types() -> None:
    text = describe_layouts_for_prompt()
    assert "Available slide layouts" in text
    for slide_type in LAYOUT_REGISTRY:
        assert slide_type.value in text
