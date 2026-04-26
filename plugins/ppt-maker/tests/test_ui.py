from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ui_contains_seven_tabs_and_core_widgets() -> None:
    html = (ROOT / "ui" / "dist" / "index.html").read_text(encoding="utf-8")

    for tab in ["Create", "Projects", "Sources", "Tables", "Templates", "Exports", "Settings"]:
        assert tab in html
    for marker in ["FileUploadZone", "CostBreakdown", "ErrorPanel", "ProgressPanel"]:
        assert marker in html
    assert "PythonDepsPanel" in html
    assert "/system/python-deps" in html
    assert "table_to_deck" in html
    assert "template_deck" in html
    assert "brand_tokens" in html
    assert "chart specs" in html


def test_ui_assets_are_self_contained_for_host_bridge() -> None:
    html = (ROOT / "ui" / "dist" / "index.html").read_text(encoding="utf-8")

    assert "./_assets/bootstrap.js" in html
    assert "/api/plugins/_sdk" not in html
    assert "OpenAkita" in html

