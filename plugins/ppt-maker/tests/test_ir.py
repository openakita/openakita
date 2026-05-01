from __future__ import annotations

import json

from ppt_design import DesignBuilder
from ppt_ir import FALLBACK_LAYOUTS, SlideIrBuilder
from ppt_models import DeckMode, SlideType
from ppt_outline import OutlineBuilder


def test_all_slide_types_have_fallbacks() -> None:
    assert {item.value for item in SlideType} <= set(FALLBACK_LAYOUTS)


def test_table_to_deck_ir_contains_required_data_pages(tmp_path) -> None:
    outline = OutlineBuilder().build(
        mode=DeckMode.TABLE_TO_DECK,
        title="KPI report",
        slide_count=2,
        table_insights={"key_findings": ["Revenue grew"]},
    )
    design = DesignBuilder().build(outline=outline)
    ir = SlideIrBuilder().build(
        outline=outline,
        spec_lock=design["spec_lock"],
        table_insights={"key_findings": ["Revenue grew"]},
        chart_specs=[{"type": "line", "x": "month", "y": "revenue"}],
    )
    path = SlideIrBuilder().save(ir, tmp_path)

    slide_types = {slide["slide_type"] for slide in ir["slides"]}
    assert {"data_overview", "metric_cards", "chart_line", "insight_summary"} <= slide_types
    assert json.loads(path.read_text(encoding="utf-8"))["mode"] == "table_to_deck"


def test_template_deck_ir_records_template_id_and_layout_hint() -> None:
    outline = OutlineBuilder().build(mode=DeckMode.TEMPLATE_DECK, title="Proposal", slide_count=3)
    design = DesignBuilder().build(outline=outline)
    ir = SlideIrBuilder().build(
        outline=outline,
        spec_lock=design["spec_lock"],
        template_id="tpl_1",
        layout_map={"cover": {"pptx_layout": "Title Slide", "fallback": "cover", "source": "pptx"}},
    )

    first = ir["slides"][0]
    assert first["template_id"] == "tpl_1"
    assert first["layout_hint"]["pptx_layout"] == "Title Slide"
    assert first["layout_hint"]["source"] == "pptx"

