from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_has_self_contained_ui_assets() -> None:
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "word-maker"
    assert manifest["icon"] == "icon.svg"
    assert manifest["ui"]["entry"] == "ui/dist/index.html"
    assert manifest["ui"]["icon"] == "icon.svg"
    for name in ["bootstrap.js", "styles.css", "icons.js", "i18n.js", "markdown-mini.js"]:
        assert (ROOT / "ui" / "dist" / "_assets" / name).exists()
    assert (ROOT / "icon.svg").is_file()
    assert (ROOT / "ui" / "dist" / "icon.svg").is_file()


def test_plugin_imports_and_defines_tools() -> None:
    import plugin

    instance = plugin.Plugin()
    tool_names = {item["name"] for item in plugin._tool_definitions()}

    assert instance is not None
    assert "word_start_project" in tool_names
    assert "word_export" in tool_names
    assert "word_list_projects" in tool_names


def test_inline_helpers_import() -> None:
    from word_maker_inline.file_utils import safe_name
    from word_maker_inline.llm_json_parser import parse_llm_json_object
    from word_maker_inline.python_deps import list_optional_groups

    assert safe_name("a/b c.docx") == "a_b_c.docx"
    assert parse_llm_json_object("```json\n{\"ok\": true}\n```") == {"ok": True}
    assert "core" in list_optional_groups()


def test_ui_uses_word_iconify_icon_and_blue_theme() -> None:
    html = (ROOT / "ui" / "dist" / "index.html").read_text(encoding="utf-8")
    icon = (ROOT / "icon.svg").read_text(encoding="utf-8")

    assert "M4.998 9V1H19.5L23 4.5V23H4" in html
    assert "grommet-icons:document-word" in icon
    assert "#2563eb" in html.lower()


def test_ui_icons_are_self_contained_svg() -> None:
    html = (ROOT / "ui" / "dist" / "index.html").read_text(encoding="utf-8")

    assert "iconBodies" in html
    assert "svgIcon(" in html
    for text_icon in ["✍", "☰", "▦", "◇", "⚙", "▣"]:
        assert text_icon not in html


def test_ui_header_omits_redundant_health_controls() -> None:
    html = (ROOT / "ui" / "dist" / "index.html").read_text(encoding="utf-8")

    assert "header-right" not in html
    assert "__wmRefresh" not in html

