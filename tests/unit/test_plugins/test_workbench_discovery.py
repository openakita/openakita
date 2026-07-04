"""Tests for v2 ``WORKBENCH`` manifest discovery in ``PluginManager``.

Implements the loader-side half of the C2 contract codified in ADR-0009.
A plugin opts in by exporting a top-level ``WORKBENCH`` dict; the manager
parses it via :class:`WorkbenchManifest` and exposes the typed result
through ``get_workbench_manifest`` / ``list_workbench_plugins``. Plugins
without the constant — or whose constant fails validation — must still
load and behave as plain tool providers.

These tests anchor that contract so any regression of Phase 4's plugin
loader extension surfaces immediately, before WorkbenchNode-driven flows
break in production.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from openakita.plugins.manager import PluginManager
from openakita.runtime.nodes.manifest import WorkbenchManifest

pytestmark = pytest.mark.asyncio


def _write_plugin(
    base,
    plugin_id: str,
    plugin_body: str,
    *,
    manifest_extra: dict | None = None,
):
    """Write a plugin directory with the given Python body."""
    d = base / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id.replace("-", " ").title(),
        "version": "1.0.0",
        "type": "python",
        "permissions": ["tools.register"],
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    (d / "plugin.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (d / "plugin.py").write_text(textwrap.dedent(plugin_body), encoding="utf-8")
    return d


VALID_PLUGIN_BODY = """\
from openakita.plugins.api import PluginAPI, PluginBase


WORKBENCH = {
    "id": "demo-bench",
    "title": "Demo bench",
    "default_mode": "image",
    "modes": [
        {
            "id": "image",
            "label": "Image",
            "tools": ["demo_t2i", "demo_i2i"],
        },
        {
            "id": "video",
            "label": "Video",
            "tools": ["demo_i2v"],
            "system_prompt_override": "you generate video",
        },
    ],
}


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        api.log("loaded")

    def on_unload(self) -> None:
        pass
"""


PLAIN_PLUGIN_BODY = """\
from openakita.plugins.api import PluginAPI, PluginBase


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        api.log("loaded")

    def on_unload(self) -> None:
        pass
"""


BROKEN_PLUGIN_BODY = """\
from openakita.plugins.api import PluginAPI, PluginBase


WORKBENCH = {
    "id": "broken-bench",
    "modes": [],
}


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        api.log("loaded")

    def on_unload(self) -> None:
        pass
"""


class TestWorkbenchManifestDiscovery:
    """ADR-0009 acceptance: loader extracts WORKBENCH typed manifest."""

    async def test_valid_workbench_is_parsed(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _write_plugin(plugins_dir, "with-bench", VALID_PLUGIN_BODY)
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")

        await mgr.load_all()

        assert mgr.loaded_count == 1
        manifest = mgr.get_workbench_manifest("with-bench")
        assert isinstance(manifest, WorkbenchManifest)
        assert manifest.id == "demo-bench"
        assert manifest.default_mode == "image"
        mode_ids = {mode.id for mode in manifest.modes}
        assert mode_ids == {"image", "video"}

    async def test_plain_plugin_has_no_manifest(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _write_plugin(plugins_dir, "plain", PLAIN_PLUGIN_BODY)
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")

        await mgr.load_all()

        assert mgr.loaded_count == 1
        assert mgr.get_workbench_manifest("plain") is None

    async def test_invalid_workbench_does_not_block_load(self, tmp_path, caplog):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _write_plugin(plugins_dir, "broken-bench", BROKEN_PLUGIN_BODY)
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")

        with caplog.at_level("WARNING", logger="openakita.plugins.manager"):
            await mgr.load_all()

        assert mgr.loaded_count == 1
        assert mgr.get_workbench_manifest("broken-bench") is None
        assert any(
            "broken-bench" in record.message and "WORKBENCH" in record.message
            for record in caplog.records
        )

    async def test_unknown_plugin_returns_none(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()

        assert mgr.get_workbench_manifest("does-not-exist") is None

    async def test_list_workbench_plugins_skips_plain(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _write_plugin(plugins_dir, "alpha-bench", VALID_PLUGIN_BODY)
        _write_plugin(plugins_dir, "plain-zeta", PLAIN_PLUGIN_BODY)
        _write_plugin(plugins_dir, "broken-mid", BROKEN_PLUGIN_BODY)
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")

        await mgr.load_all()
        listing = mgr.list_workbench_plugins()

        assert [pid for pid, _m in listing] == ["alpha-bench"]
        assert listing[0][1].id == "demo-bench"
