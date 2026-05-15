from __future__ import annotations

from ppt_layout_catalog import LayoutCatalog


def test_layout_catalog_loads_theme_and_layout_indexes() -> None:
    catalog = LayoutCatalog()

    assert "tech_business" in catalog.themes()
    assert "chart" in catalog.layouts()
    assert catalog.as_prompt_context()["selection_rules"]["do_not_use_cover_after_first_slide"] is True


def test_layout_catalog_selects_content_aware_layouts() -> None:
    catalog = LayoutCatalog()

    assert catalog.pick_layout(slide_type="content", has_image=True) == "split_image_text"
    assert catalog.pick_layout(slide_type="chart_bar", has_data=True) == "chart"
    assert catalog.pick_layout(slide_type="summary") == "conclusion_first"

