"""Tests for plugin installer dependency handling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from openakita.api.routes import plugins as plugin_routes
from openakita.plugins.installer import (
    _pip_subprocess_env,
    deps_appear_installed,
)
from openakita.plugins.state import PluginState


def _write_plugin(path: Path, plugin_id: str = "demo", version: str = "1.0.0", marker: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "plugin.json").write_text(
        f'{{"id":"{plugin_id}","name":"Demo","version":"{version}","type":"python"}}',
        encoding="utf-8",
    )
    if marker:
        (path / "marker.txt").write_text(marker, encoding="utf-8")


def _plugin_request(pm):
    request = type("_Request", (), {})()
    request.app = type("_App", (), {})()
    request.app.state = type("_State", (), {})()
    request.app.state.agent = type("_Agent", (), {"_plugin_manager": pm})()
    return request


def test_pip_subprocess_env_is_utf8_and_isolated(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "C:/leaky/site-packages")
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")

    env = _pip_subprocess_env(sys.executable)

    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in env
    assert os.environ["PYTHONPATH"] == "C:/leaky/site-packages"


def test_deps_appear_installed_requires_matching_dist_info(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    deps_dir = plugin_dir / "deps"
    deps_dir.mkdir(parents=True)
    (deps_dir / "unrelated-1.0.0.dist-info").mkdir()

    requires = {"pip": ["numpy>=1.24.0", "Pillow>=10.0.0"]}
    assert deps_appear_installed(plugin_dir, requires) is False

    (deps_dir / "numpy-1.26.0.dist-info").mkdir()
    assert deps_appear_installed(plugin_dir, requires) is False

    (deps_dir / "Pillow-10.0.0.dist-info").mkdir()
    assert deps_appear_installed(plugin_dir, requires) is True


def test_plugin_state_tracks_disk_only_pending_update(tmp_path: Path) -> None:
    path = tmp_path / "plugin_state.json"
    state = PluginState()
    state.mark_loaded("demo")
    state.mark_pending_update(
        "demo",
        "rev-2",
        pending_path=str(tmp_path / "pending" / "demo"),
        source="https://example.invalid/demo.zip",
    )
    state.save(path)

    restored = PluginState.load(path)
    entry = restored.ensure_entry("demo")
    assert entry.loaded is True
    assert entry.pending_update_revision == "rev-2"
    assert entry.pending_update_path == str(tmp_path / "pending" / "demo")
    assert entry.pending_update_source == "https://example.invalid/demo.zip"
    assert entry.reload_required is True
    assert entry.update_policy == "disk-only"

    restored.mark_loaded("demo")
    entry = restored.ensure_entry("demo")
    assert entry.pending_update_revision == ""
    assert entry.pending_update_path == ""
    assert entry.pending_update_source == ""
    assert entry.reload_required is False


@pytest.mark.asyncio
async def test_loaded_plugin_update_stages_without_touching_live(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path
    live = project_root / "data" / "plugins" / "demo"
    _write_plugin(live, version="1.0.0", marker="live-v1")
    src = tmp_path / "src"
    _write_plugin(src, version="2.0.0", marker="pending-v2")

    class _PM:
        def __init__(self) -> None:
            self.state = PluginState()
            self.state.mark_loaded("demo")
            self.reloaded: list[str] = []

        async def reload_plugin(self, plugin_id: str) -> None:
            self.reloaded.append(plugin_id)

    pm = _PM()
    request = _plugin_request(pm)
    monkeypatch.setattr(plugin_routes.settings, "project_root", str(project_root))

    plugin_id, hot_loaded = await plugin_routes._do_install(
        str(src),
        project_root / "data" / "plugins",
        plugin_routes.InstallProgress(),
        request,
    )

    entry = pm.state.ensure_entry("demo")
    assert plugin_id == "demo"
    assert hot_loaded is False
    assert (live / "marker.txt").read_text(encoding="utf-8") == "live-v1"
    assert entry.reload_required is True
    assert entry.pending_update_path
    assert Path(entry.pending_update_path, "marker.txt").read_text(encoding="utf-8") == "pending-v2"
    assert pm.reloaded == []


@pytest.mark.asyncio
async def test_pending_apply_success_switches_live_and_persists_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path
    monkeypatch.setattr(plugin_routes.settings, "project_root", str(project_root))
    live = project_root / "data" / "plugins" / "demo"
    _write_plugin(live, version="1.0.0", marker="live-v1")
    pending = project_root / "data" / "plugin-updates" / "demo" / "rev-2" / "demo"
    _write_plugin(pending, version="2.0.0", marker="pending-v2")

    class _PM:
        def __init__(self) -> None:
            self.state = PluginState()
            self.state.mark_loaded("demo")
            self.state.mark_pending_update(
                "demo",
                "rev-2",
                pending_path=str(pending),
                source="https://example.invalid/demo.zip",
            )
            self.reloaded: list[str] = []
            self.unloaded: list[str] = []

        async def unload_plugin(self, plugin_id: str) -> None:
            self.unloaded.append(plugin_id)

        async def reload_plugin(self, plugin_id: str) -> None:
            self.reloaded.append(plugin_id)

    pm = _PM()

    result = await plugin_routes.reload_plugin("demo", _plugin_request(pm))

    entry = pm.state.ensure_entry("demo")
    assert result["data"]["applied_pending_update"] is True
    assert (live / "marker.txt").read_text(encoding="utf-8") == "pending-v2"
    assert entry.install_source == "https://example.invalid/demo.zip"
    assert entry.pending_update_path == ""
    assert entry.reload_required is False
    assert not (project_root / "data" / "plugin-updates" / "demo").exists()


@pytest.mark.asyncio
async def test_pending_apply_reload_failure_restores_live_and_keeps_pending(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path
    monkeypatch.setattr(plugin_routes.settings, "project_root", str(project_root))
    live = project_root / "data" / "plugins" / "demo"
    _write_plugin(live, version="1.0.0", marker="live-v1")
    pending = project_root / "data" / "plugin-updates" / "demo" / "rev-2" / "demo"
    _write_plugin(pending, version="2.0.0", marker="pending-v2")

    class _PM:
        def __init__(self) -> None:
            self.state = PluginState()
            self.state.mark_loaded("demo")
            self.state.mark_pending_update(
                "demo",
                "rev-2",
                pending_path=str(pending),
                source="https://example.invalid/demo.zip",
            )

        async def unload_plugin(self, plugin_id: str) -> None:
            return None

        async def reload_plugin(self, plugin_id: str) -> None:
            raise RuntimeError("boom")

    pm = _PM()

    with pytest.raises(HTTPException):
        await plugin_routes.reload_plugin("demo", _plugin_request(pm))

    entry = pm.state.ensure_entry("demo")
    assert (live / "marker.txt").read_text(encoding="utf-8") == "live-v1"
    assert entry.pending_update_revision == "rev-2"
    assert entry.pending_update_path == str(pending)
    assert entry.reload_required is True
    assert pending.is_dir()
    assert (pending / "marker.txt").read_text(encoding="utf-8") == "pending-v2"


@pytest.mark.asyncio
async def test_pending_apply_reload_failure_clears_missing_pending_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path
    monkeypatch.setattr(plugin_routes.settings, "project_root", str(project_root))
    live = project_root / "data" / "plugins" / "demo"
    _write_plugin(live, version="1.0.0", marker="live-v1")
    pending = project_root / "data" / "plugin-updates" / "demo" / "rev-2" / "demo"
    _write_plugin(pending, version="2.0.0", marker="pending-v2")

    class _PM:
        def __init__(self) -> None:
            self.state = PluginState()
            self.state.mark_loaded("demo")
            self.state.mark_pending_update(
                "demo",
                "rev-2",
                pending_path=str(pending),
                source="https://example.invalid/demo.zip",
            )

        async def unload_plugin(self, plugin_id: str) -> None:
            return None

        async def reload_plugin(self, plugin_id: str) -> None:
            raise RuntimeError("boom")

    pm = _PM()
    monkeypatch.setattr(
        plugin_routes,
        "_move_failed_live_back_to_pending",
        lambda plugin_id, live_dir, pending_dir: False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await plugin_routes.reload_plugin("demo", _plugin_request(pm))

    entry = pm.state.ensure_entry("demo")
    assert "pending_preserved=False" in str(exc_info.value.detail)
    assert (live / "marker.txt").read_text(encoding="utf-8") == "live-v1"
    assert entry.pending_update_revision == ""
    assert entry.pending_update_path == ""
    assert entry.reload_required is False
    assert "Pending update package was lost" in entry.last_error


@pytest.mark.asyncio
async def test_second_staged_update_replaces_old_pending_after_success(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path
    monkeypatch.setattr(plugin_routes.settings, "project_root", str(project_root))
    live = project_root / "data" / "plugins" / "demo"
    _write_plugin(live, version="1.0.0", marker="live-v1")
    old_pending = project_root / "data" / "plugin-updates" / "demo" / "old" / "demo"
    _write_plugin(old_pending, version="2.0.0", marker="old-pending")
    new_src = tmp_path / "new-src"
    _write_plugin(new_src, version="3.0.0", marker="new-pending")

    class _PM:
        def __init__(self) -> None:
            self.state = PluginState()
            self.state.mark_loaded("demo")
            self.state.mark_pending_update(
                "demo",
                "old",
                pending_path=str(old_pending),
                source="old-source",
            )

        async def reload_plugin(self, plugin_id: str) -> None:
            raise AssertionError("loaded plugin updates must stage only")

    pm = _PM()
    plugin_id, hot_loaded = await plugin_routes._do_install(
        str(new_src),
        project_root / "data" / "plugins",
        plugin_routes.InstallProgress(),
        _plugin_request(pm),
    )

    entry = pm.state.ensure_entry("demo")
    assert plugin_id == "demo"
    assert hot_loaded is False
    assert not old_pending.exists()
    assert entry.pending_update_path
    assert Path(entry.pending_update_path, "marker.txt").read_text(encoding="utf-8") == "new-pending"


@pytest.mark.asyncio
async def test_manual_update_endpoint_does_not_create_fake_pending(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(plugin_routes.settings, "project_root", str(tmp_path))

    class _PM:
        def __init__(self) -> None:
            self.state = PluginState()
            self.state.mark_loaded("demo")

    pm = _PM()
    result = await plugin_routes.update_plugin("demo", _plugin_request(pm))

    entry = pm.state.ensure_entry("demo")
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_IMPLEMENTED"
    assert entry.pending_update_path == ""
    assert entry.reload_required is False


def test_uninstall_success_cleanup_removes_pending_update(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(plugin_routes.settings, "project_root", str(tmp_path))
    pending = tmp_path / "data" / "plugin-updates" / "demo" / "rev-2" / "demo"
    _write_plugin(pending, version="2.0.0", marker="pending-v2")

    plugin_routes._cleanup_pending_updates("demo")

    assert not (tmp_path / "data" / "plugin-updates" / "demo").exists()


@pytest.mark.asyncio
async def test_sync_new_plugins_does_not_auto_apply_disabled_pending(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(plugin_routes.settings, "project_root", str(tmp_path))
    live = tmp_path / "data" / "plugins" / "demo"
    _write_plugin(live, version="1.0.0", marker="live-v1")
    pending = tmp_path / "data" / "plugin-updates" / "demo" / "rev-2" / "demo"
    _write_plugin(pending, version="2.0.0", marker="pending-v2")

    class _PM:
        def __init__(self) -> None:
            self.state = PluginState()
            self.state.disable("demo", reason="user")
            self.state.mark_pending_update(
                "demo",
                "rev-2",
                pending_path=str(pending),
                source="https://example.invalid/demo.zip",
            )
            self.reloaded: list[str] = []

        def list_loaded(self) -> list[dict[str, str]]:
            return []

        def list_failed(self) -> dict[str, str]:
            return {}

        async def reload_plugin(self, plugin_id: str) -> None:
            self.reloaded.append(plugin_id)

    pm = _PM()
    await plugin_routes._sync_new_plugins(pm, tmp_path / "data" / "plugins")

    assert pm.reloaded == []
    assert pending.is_dir()
    assert (live / "marker.txt").read_text(encoding="utf-8") == "live-v1"
