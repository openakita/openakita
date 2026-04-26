from __future__ import annotations

from ppt_design import DesignBuilder
from ppt_exporter import PptxExporter
from ppt_ir import SlideIrBuilder
from ppt_models import DeckMode
from ppt_outline import OutlineBuilder
from pptx import Presentation


def test_exporter_writes_editable_pptx(tmp_path) -> None:
    outline = OutlineBuilder().build(mode=DeckMode.TOPIC_TO_DECK, title="Roadmap", slide_count=3)
    design = DesignBuilder().build(outline=outline)
    ir = SlideIrBuilder().build(outline=outline, spec_lock=design["spec_lock"])

    path = PptxExporter().export(ir, tmp_path / "roadmap.pptx")
    prs = Presentation(str(path))

    assert path.exists()
    assert len(prs.slides) == 3
    assert any("Roadmap" in shape.text for shape in prs.slides[0].shapes if hasattr(shape, "text"))


def test_exporter_supports_table_and_template_brand_tokens(tmp_path) -> None:
    outline = OutlineBuilder().build(
        mode=DeckMode.TABLE_TO_DECK,
        title="KPI",
        slide_count=4,
        table_insights={"key_findings": ["Revenue grew"]},
    )
    design = DesignBuilder().build(
        outline=outline,
        brand_tokens={
            "primary_color": "#111111",
            "secondary_color": "#222222",
            "accent_color": "#333333",
            "font_heading": "Aptos Display",
            "font_body": "Aptos",
        },
    )
    ir = SlideIrBuilder().build(
        outline=outline,
        spec_lock=design["spec_lock"],
        table_insights={"key_findings": ["Revenue grew"]},
        chart_specs=[{"type": "line", "title": "Revenue trend"}],
    )

    path = PptxExporter().export(ir, tmp_path / "kpi.pptx")

    assert Presentation(str(path)).slides

