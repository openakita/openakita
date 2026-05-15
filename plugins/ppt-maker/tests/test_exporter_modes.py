from __future__ import annotations

from ppt_creative_exporter import CreativeImageExporter
from ppt_pptxgenjs_exporter import PptxGenJsExporter


def test_pptxgenjs_exporter_falls_back_when_node_renderer_missing(tmp_path) -> None:
    exporter = PptxGenJsExporter(renderer_dir=tmp_path / "missing")
    ir = {
        "slides": [
            {
                "id": "slide_01",
                "index": 1,
                "title": "Fallback",
                "slide_type": "content",
                "content": {"body": "Body", "bullets": ["A"]},
                "theme": {},
            }
        ]
    }

    path = exporter.export(render_model={}, legacy_slides_ir=ir, output_path=tmp_path / "fallback.pptx")

    assert path.exists()


def test_creative_image_exporter_writes_image_backed_pptx(tmp_path) -> None:
    ir = {
        "slides": [
            {
                "id": "slide_01",
                "index": 1,
                "title": "Creative",
                "slide_type": "content",
                "content": {"body": "A stronger visual slide.", "bullets": ["One", "Two"]},
                "theme": {
                    "primary_color": "#3457D5",
                    "secondary_color": "#172033",
                    "accent_color": "#FFB000",
                    "background_color": "#FFFFFF",
                },
            }
        ]
    }

    path = CreativeImageExporter().export(ir, tmp_path / "creative.pptx")

    assert path.exists()
    assert (tmp_path / "creative_creative_assets" / "slide_01.png").exists()

