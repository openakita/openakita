from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from ppt_template_manager import TemplateDiagnosticError, TemplateManager


def make_template(path) -> None:
    presentation = """
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>
"""
    theme = """
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <a:srgbClr val="3457D5"/><a:srgbClr val="172033"/><a:srgbClr val="FFB000"/>
  <a:latin typeface="Aptos Display"/><a:latin typeface="Aptos"/>
</a:theme>
"""
    title_layout = """
<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld name="Title Slide"><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr></p:sp></p:spTree></p:cSld>
</p:sldLayout>
"""
    content_layout = """
<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld name="Title and Content"><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr></p:sp></p:spTree></p:cSld>
</p:sldLayout>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/theme/theme1.xml", theme)
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", title_layout)
        archive.writestr("ppt/slideLayouts/slideLayout2.xml", content_layout)


def test_diagnose_template_extracts_brand_and_layouts(tmp_path) -> None:
    pptx = tmp_path / "brand.pptx"
    make_template(pptx)

    profile = TemplateManager().diagnose(pptx)
    tokens = TemplateManager().brand_tokens(profile)
    layout_map = TemplateManager().layout_map(profile)

    assert profile["slide_size"] == {"width": 13.333, "height": 7.5, "unit": "inch"}
    assert profile["has_title_layout"] is True
    assert profile["has_content_layout"] is True
    assert tokens["primary_color"] == "#3457D5"
    assert tokens["font_heading"] == "Aptos Display"
    assert layout_map["cover"]["source"] == "pptx"
    assert layout_map["chart"]["source"] == "builtin"


def test_diagnose_to_files_writes_profile_brand_and_layout_map(tmp_path) -> None:
    pptx = tmp_path / "brand.pptx"
    make_template(pptx)

    result = TemplateManager().diagnose_to_files(pptx, tmp_path / "template")

    profile = json.loads(Path(result["paths"]["profile_path"]).read_text(encoding="utf-8"))
    brand = json.loads(Path(result["paths"]["brand_tokens_path"]).read_text(encoding="utf-8"))
    layout_map = json.loads(Path(result["paths"]["layout_map_path"]).read_text(encoding="utf-8"))
    assert profile["layout_count"] == 2
    assert brand["accent_color"] == "#FFB000"
    assert "content" in layout_map


def test_invalid_template_fails(tmp_path) -> None:
    bad = tmp_path / "bad.pptx"
    bad.write_text("not a zip", encoding="utf-8")

    with pytest.raises(TemplateDiagnosticError):
        TemplateManager().diagnose(bad)


def test_builtin_templates_available() -> None:
    builtin = TemplateManager().builtin_templates()

    assert len(builtin) == 5
    assert {item["category"] for item in builtin} >= {"business", "tech"}

