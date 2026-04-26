from __future__ import annotations

import json
from pathlib import Path


def test_manifest_is_excel_first() -> None:
    manifest = json.loads((Path(__file__).resolve().parents[1] / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "excel-maker"
    assert "brain.access" in manifest["permissions"]
    assert "excel_build_workbook" in manifest["provides"]["tools"]
    assert "ppt" not in " ".join(manifest["provides"]["tools"])


def test_plugin_registers_excel_tools() -> None:
    import sys
    import types

    api_module = types.ModuleType("openakita.plugins.api")

    class PluginBase:
        pass

    class PluginAPI:
        pass

    api_module.PluginBase = PluginBase
    api_module.PluginAPI = PluginAPI
    sys.modules["openakita.plugins.api"] = api_module

    from plugin import _tool_definitions

    names = {item["name"] for item in _tool_definitions()}

    assert {
        "excel_start_project",
        "excel_import_workbook",
        "excel_profile_workbook",
        "excel_generate_report_plan",
        "excel_build_workbook",
        "excel_audit_workbook",
    }.issubset(names)


def test_ui_asset_exists() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "ui" / "dist" / "index.html").is_file()
    assert (root / "ui" / "dist" / "_assets" / "styles.css").is_file()

