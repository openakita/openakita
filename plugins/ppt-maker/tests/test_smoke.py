from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_has_self_contained_ui_assets() -> None:
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "ppt-maker"
    assert manifest["ui"]["entry"] == "ui/dist/index.html"
    for name in ["bootstrap.js", "styles.css", "icons.js", "i18n.js", "markdown-mini.js"]:
        assert (ROOT / "ui" / "dist" / "_assets" / name).exists()


def test_plugin_imports_and_defines_tools() -> None:
    import plugin

    instance = plugin.Plugin()
    tool_names = {item["name"] for item in plugin._tool_definitions()}

    assert instance is not None
    assert "ppt_start_project" in tool_names
    assert "ppt_export" in tool_names
    assert "ppt_list_projects" in tool_names


def test_inline_helpers_import() -> None:
    from ppt_maker_inline.file_utils import safe_name
    from ppt_maker_inline.llm_json_parser import parse_llm_json_object
    from ppt_maker_inline.python_deps import list_optional_groups

    assert safe_name("a/b c.pptx") == "b_c.pptx"
    assert parse_llm_json_object("```json\n{\"ok\": true}\n```") == {"ok": True}
    assert "table" in list_optional_groups()

