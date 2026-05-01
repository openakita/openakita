from __future__ import annotations

import builtins
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_has_self_contained_ui_assets() -> None:
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "ppt-maker"
    assert manifest["ui"]["entry"] == "ui/dist/index.html"
    assert manifest["icon"] == "icon.svg"
    assert manifest["ui"]["icon"] == "icon.svg"
    assert (ROOT / "icon.svg").exists()
    assert (ROOT / "ui" / "dist" / "icon.svg").exists()
    assert "m2.859 2.878l12.57-1.796" in (ROOT / "icon.svg").read_text(encoding="utf-8")
    assert "m2.859 2.878l12.57-1.796" in (ROOT / "ui" / "dist" / "icon.svg").read_text(encoding="utf-8")
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
    assert "table_processing" in list_optional_groups()


def test_exporter_imports_without_python_pptx(monkeypatch, tmp_path) -> None:
    _block_python_pptx(monkeypatch)
    module = _load_module("ppt_exporter_without_pptx", ROOT / "ppt_exporter.py")

    with pytest.raises(module.PptxExportError, match="python-pptx"):
        module.PptxExporter().export({"slides": [{"title": "Demo"}]}, tmp_path / "demo.pptx")


def test_asset_provider_imports_without_python_pptx(monkeypatch, tmp_path) -> None:
    _block_python_pptx(monkeypatch)
    module = _load_module("ppt_asset_provider_without_pptx", ROOT / "ppt_asset_provider.py")

    icon = module.PptAssetProvider(settings={}, data_root=tmp_path).resolve_icon("growth")

    assert icon is not None
    assert icon["keyword"] == "growth"
    assert int(icon["shape"]) > 0


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _block_python_pptx(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pptx" or name.startswith("pptx."):
            raise ModuleNotFoundError("No module named 'pptx'", name="pptx")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

