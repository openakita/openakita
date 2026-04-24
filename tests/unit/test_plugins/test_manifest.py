"""Tests for openakita.plugins.manifest — plugin.json parsing and validation."""

from __future__ import annotations

import json
import logging

import pytest

from openakita.plugins.manifest import (
    ALL_PERMISSIONS,
    ManifestError,
    PluginManifest,
    parse_manifest,
)

MINIMAL_MANIFEST = {
    "id": "test-plugin",
    "name": "Test Plugin",
    "version": "1.0.0",
    "type": "python",
}


def _write_manifest(plugin_dir, data: dict) -> None:
    (plugin_dir / "plugin.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


# ---------- Valid manifest ----------


class TestParseManifestValid:
    def test_minimal_manifest(self, tmp_path):
        _write_manifest(tmp_path, MINIMAL_MANIFEST)
        m = parse_manifest(tmp_path)
        assert isinstance(m, PluginManifest)
        assert m.id == "test-plugin"
        assert m.name == "Test Plugin"
        assert m.version == "1.0.0"
        assert m.plugin_type == "python"

    def test_all_types(self, tmp_path):
        for ptype in ("python", "mcp", "skill"):
            data = {**MINIMAL_MANIFEST, "type": ptype}
            _write_manifest(tmp_path, data)
            m = parse_manifest(tmp_path)
            assert m.plugin_type == ptype

    def test_raw_field_preserves_original(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "custom_key": "hello"}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.raw["custom_key"] == "hello"

    def test_permissions_kept(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "permissions": ["tools.register", "hooks.basic"]}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.permissions == ["tools.register", "hooks.basic"]


# ---------- Missing / Invalid ----------


class TestParseManifestErrors:
    def test_missing_plugin_json(self, tmp_path):
        with pytest.raises(ManifestError, match="Missing plugin.json"):
            parse_manifest(tmp_path)

    def test_invalid_json(self, tmp_path):
        (tmp_path / "plugin.json").write_text("{bad json", encoding="utf-8")
        with pytest.raises(ManifestError, match="Invalid JSON"):
            parse_manifest(tmp_path)

    def test_missing_required_id(self, tmp_path):
        data = {"name": "X", "version": "1", "type": "python"}
        _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="Missing required fields"):
            parse_manifest(tmp_path)

    def test_missing_required_name(self, tmp_path):
        data = {"id": "x", "version": "1", "type": "python"}
        _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="Missing required fields"):
            parse_manifest(tmp_path)

    def test_missing_required_version(self, tmp_path):
        data = {"id": "x", "name": "X", "type": "python"}
        _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="Missing required fields"):
            parse_manifest(tmp_path)

    def test_missing_required_type(self, tmp_path):
        data = {"id": "x", "name": "X", "version": "1"}
        _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="Missing required fields"):
            parse_manifest(tmp_path)

    def test_invalid_plugin_type(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "type": "ruby"}
        _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="Invalid plugin type"):
            parse_manifest(tmp_path)


# ---------- Unknown permissions ----------


class TestUnknownPermissions:
    def test_unknown_permission_logged_and_filtered(self, tmp_path, caplog):
        data = {
            **MINIMAL_MANIFEST,
            "permissions": ["tools.register", "not.real.permission"],
        }
        _write_manifest(tmp_path, data)
        with caplog.at_level(logging.WARNING):
            m = parse_manifest(tmp_path)
        assert "tools.register" in m.permissions
        assert "not.real.permission" not in m.permissions
        assert "unknown permissions" in caplog.text.lower()

    def test_all_known_permissions_kept(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "permissions": list(ALL_PERMISSIONS)}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert set(m.permissions) == ALL_PERMISSIONS


# ---------- max_permission_level ----------


class TestMaxPermissionLevel:
    def test_basic_only(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "permissions": ["tools.register", "hooks.basic"]}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.max_permission_level == "basic"

    def test_has_advanced(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "permissions": ["tools.register", "brain.access"]}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.max_permission_level == "advanced"

    def test_has_system(self, tmp_path):
        data = {
            **MINIMAL_MANIFEST,
            "permissions": ["tools.register", "brain.access", "hooks.all"],
        }
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.max_permission_level == "system"

    def test_no_permissions(self, tmp_path):
        _write_manifest(tmp_path, MINIMAL_MANIFEST)
        m = parse_manifest(tmp_path)
        assert m.max_permission_level == "basic"


# ---------- Default entries ----------


class TestDefaultEntries:
    def test_python_default_entry(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "type": "python"}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.entry == "plugin.py"

    def test_mcp_default_entry(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "type": "mcp"}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.entry == "mcp_config.json"

    def test_skill_default_entry(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "type": "skill"}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.entry == "SKILL.md"

    def test_custom_entry(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "entry": "main.py"}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.entry == "main.py"


# ---------- Timeouts ----------


class TestTimeouts:
    def test_default_timeouts(self, tmp_path):
        _write_manifest(tmp_path, MINIMAL_MANIFEST)
        m = parse_manifest(tmp_path)
        assert m.load_timeout == 10.0
        assert m.hook_timeout == 5.0
        assert m.retrieve_timeout == 3.0

    def test_custom_load_timeout(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "load_timeout": 30}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.load_timeout == 30.0

    def test_custom_hook_timeout(self, tmp_path):
        data = {**MINIMAL_MANIFEST, "hook_timeout": 15}
        _write_manifest(tmp_path, data)
        m = parse_manifest(tmp_path)
        assert m.hook_timeout == 15.0

