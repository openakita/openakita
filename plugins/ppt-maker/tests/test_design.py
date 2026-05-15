from __future__ import annotations

import json
from pathlib import Path

from ppt_design import DesignBuilder
from ppt_models import DeckMode
from ppt_outline import OutlineBuilder


def test_design_spec_references_brand_tokens_and_layout_map(tmp_path) -> None:
    outline = OutlineBuilder().build(
        mode=DeckMode.TEMPLATE_DECK,
        title="产品方案",
        slide_count=4,
    )
    design = DesignBuilder().build(
        outline=outline,
        brand_tokens={
            "primary_color": "#111111",
            "secondary_color": "#222222",
            "accent_color": "#333333",
            "font_heading": "Brand Display",
            "font_body": "Brand Text",
        },
        layout_map={"cover": {"pptx_layout": "Title Slide", "fallback": "cover", "source": "pptx"}},
    )
    paths = DesignBuilder().save(design, tmp_path)

    assert "Brand Display" in design["design_spec_markdown"]
    assert design["spec_lock"]["theme"]["primary_color"] == "#111111"
    assert design["spec_lock"]["layout_map"]["cover"]["pptx_layout"] == "Title Slide"
    assert "Slide Plan" in Path(paths["design_spec_path"]).read_text(encoding="utf-8")
    spec_lock = json.loads(Path(paths["spec_lock_path"]).read_text(encoding="utf-8"))
    assert spec_lock["needs_confirmation"] is True


def test_confirm_design_marks_spec_lock_complete() -> None:
    outline = OutlineBuilder().build(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap", slide_count=3)
    design = DesignBuilder().build(outline=outline)
    confirmed = DesignBuilder().confirm(design)

    assert confirmed["confirmed"] is True
    assert confirmed["needs_confirmation"] is False
    assert confirmed["spec_lock"]["confirmed"] is True


def test_swiss_design_preset_uses_locked_visual_rules() -> None:
    outline = OutlineBuilder().build(mode=DeckMode.TOPIC_TO_DECK, title="Swiss Deck", slide_count=3)
    design = DesignBuilder().build(outline=outline, style="swiss_ikb")
    spec_lock = design["spec_lock"]

    assert spec_lock["visual_system"] == "swiss_locked_editable"
    assert spec_lock["theme"]["accent_color"] == "#002FA7"
    assert spec_lock["spacing"]["radius"] == 0
    assert any("one high-saturation accent" in rule for rule in spec_lock["rules"])

