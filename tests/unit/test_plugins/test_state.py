"""Tests for openakita.plugins.state — PluginState persistence."""

from __future__ import annotations

from openakita.plugins.state import PluginState, PluginStateEntry

# ---------- ensure_entry ----------


class TestEnsureEntry:
    def test_creates_new_entry(self):
        state = PluginState()
        entry = state.ensure_entry("p1")
        assert isinstance(entry, PluginStateEntry)
        assert entry.plugin_id == "p1"
        assert entry.enabled is True
        assert entry.installed_at > 0

    def test_returns_existing(self):
        state = PluginState()
        first = state.ensure_entry("p1")
        second = state.ensure_entry("p1")
        assert first is second


# ---------- is_enabled ----------


class TestIsEnabled:
    def test_unknown_plugin_defaults_true(self):
        state = PluginState()
        assert state.is_enabled("unknown") is True

    def test_enabled_plugin(self):
        state = PluginState()
        state.ensure_entry("p1")
        assert state.is_enabled("p1") is True

    def test_disabled_plugin(self):
        state = PluginState()
        state.disable("p1", "test")
        assert state.is_enabled("p1") is False


# ---------- enable / disable ----------


class TestEnableDisable:
    def test_disable_sets_false(self):
        state = PluginState()
        state.disable("p1", "user")
        entry = state.get_entry("p1")
        assert entry is not None
        assert entry.enabled is False
        assert entry.disabled_reason == "user"

    def test_enable_after_disable(self):
        state = PluginState()
        state.disable("p1", "user")
        state.enable("p1")
        entry = state.get_entry("p1")
        assert entry.enabled is True
        assert entry.disabled_reason == ""

    def test_toggle_multiple_times(self):
        state = PluginState()
        state.disable("p1")
        assert not state.is_enabled("p1")
        state.enable("p1")
        assert state.is_enabled("p1")
        state.disable("p1", "error")
        assert not state.is_enabled("p1")


# ---------- record_error ----------


class TestRecordError:
    def test_increments_count(self):
        state = PluginState()
        state.record_error("p1", "err1")
        state.record_error("p1", "err2")
        entry = state.get_entry("p1")
        assert entry.error_count == 2
        assert entry.last_error == "err2"
        assert entry.last_error_time > 0


# ---------- active_backends ----------


class TestActiveBackends:
    def test_set_and_get(self):
        state = PluginState()
        state.set_active_backend("memory", "p1")
        assert state.get_active_backend("memory") == "p1"

    def test_get_unknown_returns_none(self):
        state = PluginState()
        assert state.get_active_backend("nonexistent") is None

    def test_overwrite(self):
        state = PluginState()
        state.set_active_backend("memory", "p1")
        state.set_active_backend("memory", "p2")
        assert state.get_active_backend("memory") == "p2"


# ---------- remove_plugin ----------


class TestRemovePlugin:
    def test_removes_from_plugins(self):
        state = PluginState()
        state.ensure_entry("p1")
        state.remove_plugin("p1")
        assert state.get_entry("p1") is None

    def test_removes_from_active_backends(self):
        state = PluginState()
        state.set_active_backend("memory", "p1")
        state.set_active_backend("search", "p2")
        state.remove_plugin("p1")
        assert state.get_active_backend("memory") is None
        assert state.get_active_backend("search") == "p2"

    def test_remove_nonexistent_is_noop(self):
        state = PluginState()
        state.remove_plugin("ghost")


# ---------- save / load roundtrip ----------


class TestSaveLoad:
    def test_roundtrip(self, tmp_path):
        state = PluginState()
        state.ensure_entry("p1")
        state.disable("p2", "broken")
        state.record_error("p2", "some error")
        state.set_active_backend("memory", "p1")
        entry = state.get_entry("p1")
        entry.granted_permissions = ["tools.register", "brain.access"]

        path = tmp_path / "state.json"
        state.save(path)

        loaded = PluginState.load(path)
        assert loaded.is_enabled("p1")
        assert not loaded.is_enabled("p2")
        assert loaded.get_active_backend("memory") == "p1"
        p1 = loaded.get_entry("p1")
        assert "brain.access" in p1.granted_permissions
        p2 = loaded.get_entry("p2")
        assert p2.disabled_reason == "broken"
        assert p2.error_count == 1
        assert p2.last_error == "some error"

    def test_install_source_roundtrip(self, tmp_path):
        # install_source is what lets the "Reload" button re-sync source-code
        # edits without requiring uninstall + reinstall — its persistence is
        # therefore part of the public contract, not just a metadata nicety.
        state = PluginState()
        entry = state.ensure_entry("p1")
        entry.install_source = "/abs/path/to/plugins/avatar-studio"

        path = tmp_path / "state.json"
        state.save(path)
        loaded = PluginState.load(path)
        assert loaded.get_entry("p1").install_source == (
            "/abs/path/to/plugins/avatar-studio"
        )

    def test_install_source_default_empty_for_legacy_files(self, tmp_path):
        # Older state.json files predate install_source; load() must tolerate
        # the missing key without crashing and without inventing a value.
        path = tmp_path / "legacy.json"
        path.write_text(
            '{"schema_version": 2, "plugins": {"old": {"enabled": true}}}',
            encoding="utf-8",
        )
        loaded = PluginState.load(path)
        entry = loaded.get_entry("old")
        assert entry is not None
        assert entry.install_source == ""

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{bad json", encoding="utf-8")
        state = PluginState.load(path)
        assert isinstance(state, PluginState)
        assert len(state.plugins) == 0

    def test_load_missing_file(self, tmp_path):
        path = tmp_path / "missing.json"
        state = PluginState.load(path)
        assert isinstance(state, PluginState)
        assert len(state.plugins) == 0

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "state.json"
        state = PluginState()
        state.ensure_entry("p1")
        state.save(path)
        assert path.exists()

