from __future__ import annotations

import zipfile

import pytest
from ppt_source_loader import SourceLoader, SourceParseError


def test_detect_source_kinds() -> None:
    loader = SourceLoader()

    assert loader.detect_kind("https://example.com/report") == "url"
    assert loader.detect_kind("brief.md") == "markdown"
    assert loader.detect_kind("data.csv") == "csv"
    assert loader.detect_kind("deck.pptx") == "pptx"
    assert loader.detect_kind("workbook.xlsx") == "xlsx"


@pytest.mark.asyncio
async def test_parse_markdown_and_csv(tmp_path) -> None:
    markdown = tmp_path / "需求.md"
    markdown.write_text("# 标题\n\n要点一", encoding="utf-8")
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("month,revenue\nJan,100\nFeb,120\n", encoding="utf-8")
    loader = SourceLoader()

    md_result = await loader.parse(markdown)
    csv_result = await loader.parse(csv_path)

    assert md_result.kind == "markdown"
    assert "要点一" in md_result.text
    assert csv_result.metadata["columns"] == ["month", "revenue"]
    assert "Jan | 100" in csv_result.text


@pytest.mark.asyncio
async def test_parse_pptx_extracts_slide_text(tmp_path) -> None:
    pptx_path = tmp_path / "sample.pptx"
    slide_xml = """
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Hello PPT</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>
"""
    with zipfile.ZipFile(pptx_path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide_xml)

    result = await SourceLoader().parse(pptx_path)

    assert result.kind == "pptx"
    assert "Hello PPT" in result.text
    assert result.metadata["slides"] == 1


@pytest.mark.asyncio
async def test_parse_unknown_or_missing_file_fails(tmp_path) -> None:
    loader = SourceLoader()

    with pytest.raises(SourceParseError):
        await loader.parse(tmp_path / "missing.md")
    unknown = tmp_path / "data.bin"
    unknown.write_bytes(b"abc")
    with pytest.raises(SourceParseError):
        await loader.parse(unknown)

