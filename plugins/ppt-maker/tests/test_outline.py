from __future__ import annotations

import json

from ppt_models import DeckMode
from ppt_outline import OutlineBuilder


def test_table_to_deck_outline_contains_data_pages(tmp_path) -> None:
    outline = OutlineBuilder().build(
        mode=DeckMode.TABLE_TO_DECK,
        title="经营数据汇报",
        slide_count=5,
        table_insights={"key_findings": ["收入增长 12%"]},
    )
    path = OutlineBuilder().save(outline, tmp_path)

    slide_types = [slide["slide_type"] for slide in outline["slides"]]
    # Cover always comes first; data-oriented pages must follow.
    assert slide_types[0] == "cover"
    assert {"data_overview", "metric_cards", "chart_line", "insight_summary"}.issubset(set(slide_types))
    assert outline["table_insights_summary"] == ["收入增长 12%"]
    assert json.loads(path.read_text(encoding="utf-8"))["needs_confirmation"] is True
    # Rich fallback contract: every slide ships a body and >=1 bullet (cover may be empty).
    for slide in outline["slides"]:
        assert "body" in slide
        if slide["slide_type"] != "cover":
            assert slide["key_points"], f"{slide['slide_type']} 兜底应有 bullets"


def test_template_outline_adds_fallback_confirmation() -> None:
    outline = OutlineBuilder().build(
        mode=DeckMode.TEMPLATE_DECK,
        title="产品方案",
        slide_count=6,
        template_profile={"name": "Brand", "warnings": ["fallback"]},
    )

    assert any("fallback" in question for question in outline["confirmation_questions"])
    assert outline["template_profile_summary"]["name"] == "Brand"


def test_confirm_outline_marks_gate_complete() -> None:
    outline = OutlineBuilder().build(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap", slide_count=3)
    confirmed = OutlineBuilder().confirm(outline, {"audience": "executives"})

    assert confirmed["confirmed"] is True
    assert confirmed["needs_confirmation"] is False
    assert confirmed["audience"] == "executives"

