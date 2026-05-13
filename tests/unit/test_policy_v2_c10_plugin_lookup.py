"""C10: PluginManifest ``tool_classes`` + PluginManager → ApprovalClass lookup.

测试维度：
- D1：``tool_classes`` 字段解析（dict[str, str]）+ 归一为 lowercase
- D2：``mutates_params`` 字段解析（list[str]，单字符串自动包成列表）
- D3：非 dict/list 输入的校验错误
- D4：``PluginManager.get_tool_class`` 多插件取严
- D5：``plugin_allows_param_mutation`` gate
- D6：未加载 / 已禁用插件不进入 lookup
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from openakita.core.policy_v2.enums import ApprovalClass, DecisionSource
from openakita.plugins.manager import PluginManager
from openakita.plugins.manifest import PluginManifest


class TestPluginManifestNewFields:
    def test_tool_classes_lowercased(self):
        m = PluginManifest.model_validate(
            {
                "id": "p1",
                "name": "P1",
                "version": "1.0",
                "type": "python",
                "tool_classes": {"my_tool": "DESTRUCTIVE", "x": "ReadOnly_Scoped"},
            }
        )
        assert m.tool_classes == {
            "my_tool": "destructive",
            "x": "readonly_scoped",
        }

    def test_tool_classes_skips_invalid_entries(self):
        m = PluginManifest.model_validate(
            {
                "id": "p1",
                "name": "P1",
                "version": "1.0",
                "type": "python",
                "tool_classes": {
                    "valid": "destructive",
                    "": "ignored_empty_key",
                    "no_value": None,
                },
            }
        )
        assert m.tool_classes == {"valid": "destructive"}

    def test_mutates_params_string_normalized_to_list(self):
        m = PluginManifest.model_validate(
            {
                "id": "p1",
                "name": "P1",
                "version": "1.0",
                "type": "python",
                "mutates_params": "edit_file",
            }
        )
        assert m.mutates_params == ["edit_file"]

    def test_mutates_params_list(self):
        m = PluginManifest.model_validate(
            {
                "id": "p1",
                "name": "P1",
                "version": "1.0",
                "type": "python",
                "mutates_params": ["edit_file", "write_file"],
            }
        )
        assert m.mutates_params == ["edit_file", "write_file"]

    def test_tool_classes_must_be_dict(self):
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(
                {
                    "id": "p1",
                    "name": "P1",
                    "version": "1.0",
                    "type": "python",
                    "tool_classes": ["not", "a", "dict"],
                }
            )

    def test_default_empty_collections(self):
        m = PluginManifest.model_validate(
            {"id": "p1", "name": "P1", "version": "1.0", "type": "python"}
        )
        assert m.tool_classes == {}
        assert m.mutates_params == []


def _write_plugin(tmp: Path, plugin_id: str, manifest: dict) -> Path:
    plugin_dir = tmp / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text(
        "from openakita_plugin_sdk import PluginBase\n"
        "class Plugin(PluginBase):\n    pass\n",
        encoding="utf-8",
    )
    return plugin_dir


@pytest.fixture
def tmp_plugins_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class _FakeLoadedPlugin:
    """Minimal stub matching ``manager._LoadedPlugin.manifest`` access."""

    def __init__(self, manifest: PluginManifest) -> None:
        self.manifest = manifest


class TestPluginManagerGetToolClass:
    def _make_manager_with_plugins(
        self, tmp: Path, *plugin_specs: tuple[str, dict]
    ) -> PluginManager:
        manager = PluginManager(plugins_dir=tmp)
        for plugin_id, manifest_dict in plugin_specs:
            manifest = PluginManifest.model_validate(manifest_dict)
            manager._loaded[plugin_id] = _FakeLoadedPlugin(manifest)
        return manager

    def test_single_plugin_lookup(self, tmp_plugins_dir):
        manager = self._make_manager_with_plugins(
            tmp_plugins_dir,
            (
                "p1",
                {
                    "id": "p1",
                    "name": "P1",
                    "version": "1.0",
                    "type": "python",
                    "tool_classes": {"my_tool": "destructive"},
                },
            ),
        )
        assert manager.get_tool_class("my_tool") == (
            ApprovalClass.DESTRUCTIVE,
            DecisionSource.PLUGIN_PREFIX,
        )

    def test_multiple_plugins_take_strictest(self, tmp_plugins_dir):
        manager = self._make_manager_with_plugins(
            tmp_plugins_dir,
            (
                "soft",
                {
                    "id": "soft",
                    "name": "Soft",
                    "version": "1.0",
                    "type": "python",
                    "tool_classes": {"shared_tool": "readonly_scoped"},
                },
            ),
            (
                "hard",
                {
                    "id": "hard",
                    "name": "Hard",
                    "version": "1.0",
                    "type": "python",
                    "tool_classes": {"shared_tool": "destructive"},
                },
            ),
        )
        klass, src = manager.get_tool_class("shared_tool")
        assert klass == ApprovalClass.DESTRUCTIVE
        assert src == DecisionSource.PLUGIN_PREFIX

    def test_unknown_class_value_skipped(self, tmp_plugins_dir, caplog):
        manager = self._make_manager_with_plugins(
            tmp_plugins_dir,
            (
                "p",
                {
                    "id": "p",
                    "name": "P",
                    "version": "1.0",
                    "type": "python",
                    "tool_classes": {"my_tool": "not_a_class"},
                },
            ),
        )
        with caplog.at_level("WARNING", logger="openakita.plugins.manager"):
            assert manager.get_tool_class("my_tool") is None
        assert any(
            "unknown approval_class" in rec.message for rec in caplog.records
        )

    def test_disabled_plugin_excluded(self, tmp_plugins_dir):
        manager = self._make_manager_with_plugins(
            tmp_plugins_dir,
            (
                "p",
                {
                    "id": "p",
                    "name": "P",
                    "version": "1.0",
                    "type": "python",
                    "tool_classes": {"my_tool": "destructive"},
                },
            ),
        )
        manager._state.disable("p")
        assert manager.get_tool_class("my_tool") is None

    def test_unloaded_tool_returns_none(self, tmp_plugins_dir):
        manager = PluginManager(plugins_dir=tmp_plugins_dir)
        assert manager.get_tool_class("anything") is None


class TestPluginAllowsParamMutation:
    def test_allowed_for_listed_tool(self, tmp_plugins_dir):
        manifest = PluginManifest.model_validate(
            {
                "id": "ed",
                "name": "Editor",
                "version": "1.0",
                "type": "python",
                "mutates_params": ["edit_file"],
            }
        )
        manager = PluginManager(plugins_dir=tmp_plugins_dir)
        manager._loaded["ed"] = _FakeLoadedPlugin(manifest)
        assert manager.plugin_allows_param_mutation("ed", "edit_file") is True
        assert manager.plugin_allows_param_mutation("ed", "other_tool") is False

    def test_denied_when_plugin_unknown(self, tmp_plugins_dir):
        manager = PluginManager(plugins_dir=tmp_plugins_dir)
        assert manager.plugin_allows_param_mutation("ghost", "edit_file") is False
