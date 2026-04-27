from __future__ import annotations

import json
from pathlib import Path


def test_manifest_is_excel_first() -> None:
    manifest = json.loads((Path(__file__).resolve().parents[1] / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "excel-maker"
    assert manifest["icon"] == "icon.svg"
    assert manifest["ui"]["icon"] == "icon.svg"
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


def test_public_serializers_do_not_expose_server_paths(tmp_path) -> None:
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

    from excel_models import ArtifactKind, ArtifactRecord, WorkbookRecord
    from plugin import Plugin

    plugin = Plugin()
    workbook = WorkbookRecord(
        id="wb_test",
        filename="sales.csv",
        original_path=str(tmp_path / "sales.csv"),
        imported_path=str(tmp_path / "workbooks" / "sales.csv"),
        profile_path=str(tmp_path / "profile.json"),
        created_at=1,
        updated_at=1,
    )
    artifact = ArtifactRecord(
        id="art_test",
        project_id="proj_test",
        kind=ArtifactKind.WORKBOOK,
        path=str(tmp_path / "report.xlsx"),
        created_at=1,
    )

    public_workbook = plugin._public_workbook(workbook)
    public_artifact = plugin._public_artifact(artifact)

    assert "original_path" not in public_workbook
    assert "imported_path" not in public_workbook
    assert "profile_path" not in public_workbook
    assert "path" not in public_artifact
    assert public_artifact["download_url"] == "/artifacts/art_test/download"


def test_ui_asset_exists() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "ui" / "dist" / "index.html").is_file()
    assert (root / "ui" / "dist" / "_assets" / "styles.css").is_file()
    assert (root / "icon.svg").is_file()
    assert (root / "ui" / "dist" / "icon.svg").is_file()


def test_ui_uses_plugin_bridge_and_no_absolute_upload_path() -> None:
    html = (Path(__file__).resolve().parents[1] / "ui" / "dist" / "index.html").read_text(encoding="utf-8")

    assert 'PLUGIN_ID_DEFAULT = "excel-maker"' in html
    assert "bridge:api-request" in html
    assert "/api/plugins/" in html
    assert "workbook_id: wb.id" in html
    assert "wb.original_path" not in html


def test_ui_uses_excel_iconify_icon_and_green_theme() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "ui" / "dist" / "index.html").read_text(encoding="utf-8")
    css = (root / "ui" / "dist" / "_assets" / "styles.css").read_text(encoding="utf-8")
    icon = (root / "icon.svg").read_text(encoding="utf-8")

    assert "M15.12 12h8.13m-8.13-5h8.13" in html
    assert "streamline-ultimate:microsoft-excel-logo" in icon
    assert "#107c41" in css.lower()
    assert "/system/python-deps/${depId}/${op}" in html
    assert "runDep(dep.id, \"uninstall\")" in html
    assert "主产物：可编辑 .xlsx" not in html
    assert "settings-page" in html
    assert "settings-inner" in html
    assert "系统组件与依赖下载" in html
    assert "当前状态与空间占用" in html
    assert "检测可选依赖" in html
    assert "directoryFields" in html
    assert "uploads_dir" in html
    assert "workbooks_dir" in html
    assert "templates_dir" in html
    assert "cache_dir" in html
    assert 'type="color"' in html
    assert "numberFormatOptions" in html
    assert "directory-picker" in html
    assert "应用目录" in html
    assert "自定义..." in html
    assert "custom-value-input" in html
    assert "datalist" not in html
    assert "缺失" in html
    assert "已安装" in html
    assert "Iconify" in icon
    assert "报表生成 · 数据剖析 · 公式说明 · XLSX 导出" in html
    assert "XLSX REPORT / PROFILE / FORMULA / AUDIT" not in html
    assert "width: 34px" in css
    assert "width: 23px" in css
    assert "storage-card-grid" in css
    assert "grid-template-columns: minmax(460px" not in css
    assert 'transform="translate(8 8) scale(2)"' in icon

