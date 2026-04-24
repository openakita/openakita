"""Smoke tests for plugin.py — Pydantic bodies, tool schemas, route surface.

These tests do **not** spin up a real FastAPI server or PluginAPI — they
verify that the public surface declared by ``Plugin`` matches what the
v1.0 plan promises (5 tools, 16 routes, Pydantic 422 on bad mode, etc.)
so a refactor cannot accidentally drop one of those contracts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import plugin as plugin_mod
import pytest
from fastapi import APIRouter
from plugin import (
    PLUGIN_ID,
    ConfigUpdateBody,
    CreateTaskBody,
    OpenFolderBody,
    Plugin,
    StorageCleanupBody,
    SystemInstallBody,
    SystemUninstallBody,
)

# ── Pydantic body validation ─────────────────────────────────────────────


class TestCreateTaskBody:
    def test_accepts_known_mode(self) -> None:
        body = CreateTaskBody(mode="source_review", input_path="/tmp/x.mp4")
        assert body.mode == "source_review"
        assert body.input_path == "/tmp/x.mp4"
        assert body.params == {}

    def test_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValueError, match="unknown mode"):
            CreateTaskBody(mode="not_a_mode", input_path="/tmp/x.mp4")

    @pytest.mark.parametrize(
        "mode",
        ["source_review", "silence_cut", "auto_color", "cut_qc"],
    )
    def test_all_four_modes_pass(self, mode: str) -> None:
        body = CreateTaskBody(mode=mode, input_path="/tmp/x.mp4")
        assert body.mode == mode

    def test_params_dict_is_passed_through(self) -> None:
        body = CreateTaskBody(
            mode="cut_qc",
            input_path="/tmp/x.mp4",
            params={"auto_remux": True, "max_attempts": 2},
        )
        assert body.params == {"auto_remux": True, "max_attempts": 2}


class TestOtherBodies:
    def test_config_update_body(self) -> None:
        body = ConfigUpdateBody(updates={"silence_threshold_db": "-40"})
        assert body.updates == {"silence_threshold_db": "-40"}

    def test_system_install_default_index(self) -> None:
        body = SystemInstallBody()
        assert body.method_index == 0

    def test_system_uninstall_explicit(self) -> None:
        body = SystemUninstallBody(method_index=2)
        assert body.method_index == 2

    def test_storage_cleanup_default(self) -> None:
        body = StorageCleanupBody()
        assert body.dir_type == "cache"

    def test_open_folder_accepts_either_field(self) -> None:
        a = OpenFolderBody(path="/tmp/x")
        b = OpenFolderBody(key="output_dir")
        assert a.path == "/tmp/x"
        assert b.key == "output_dir"


# ── Plugin instance shape ────────────────────────────────────────────────


class TestPluginShape:
    def test_plugin_id_constant(self) -> None:
        assert PLUGIN_ID == "footage-gate"

    def test_tool_definitions_5_distinct_names(self) -> None:
        p = Plugin()
        defs = p._tool_definitions()
        names = [d["name"] for d in defs]
        assert names == [
            "footage_gate_create",
            "footage_gate_status",
            "footage_gate_list",
            "footage_gate_cancel",
            "footage_gate_settings_get",
        ]
        assert len(set(names)) == 5

    def test_each_tool_has_input_schema(self) -> None:
        p = Plugin()
        for d in p._tool_definitions():
            assert "input_schema" in d
            assert d["input_schema"]["type"] == "object"
            assert "description" in d
            assert d["description"].strip()

    def test_create_tool_required_fields(self) -> None:
        p = Plugin()
        defs = {d["name"]: d for d in p._tool_definitions()}
        create = defs["footage_gate_create"]
        assert "mode" in create["input_schema"]["properties"]
        assert "input_path" in create["input_schema"]["properties"]
        assert set(create["input_schema"]["required"]) == {"mode", "input_path"}


# ── Route registration ───────────────────────────────────────────────────


def _make_loaded_plugin(tmp_path: Path) -> Plugin:
    """Helper — set up a Plugin so ``_register_routes`` can run.

    We do not call ``on_load`` because that needs a real PluginAPI; we
    minimally populate the attributes that ``_register_routes`` reads.
    """
    p = Plugin()
    p._api = MagicMock()
    p._uploads_dir = tmp_path / "uploads"
    p._uploads_dir.mkdir()
    p._tasks_dir = tmp_path / "tasks"
    p._tasks_dir.mkdir()
    p._tm = MagicMock()
    p._sysdeps = MagicMock()
    return p


class TestRoutes:
    def test_register_routes_yields_at_least_16_paths(self, tmp_path: Path) -> None:
        """The v1.0 plan §6.2 promises 16 routes (counting the upload-preview GET)."""
        p = _make_loaded_plugin(tmp_path)
        router = APIRouter()
        p._register_routes(router)
        # Each route appears once in router.routes; uploads/{rel_path:path}
        # is registered by add_upload_preview_route too.
        paths: set[tuple[str, str]] = set()
        for r in router.routes:
            for m in getattr(r, "methods", set()) or set():
                paths.add((m, getattr(r, "path", "")))
        # Spot-check: every contractually required route is present.
        required = {
            ("POST", "/tasks"),
            ("GET", "/tasks"),
            ("GET", "/tasks/{task_id}"),
            ("DELETE", "/tasks/{task_id}"),
            ("POST", "/tasks/{task_id}/cancel"),
            ("POST", "/tasks/{task_id}/retry"),
            ("POST", "/upload"),
            ("GET", "/uploads/{rel_path:path}"),
            ("GET", "/system/components"),
            ("GET", "/system/ffmpeg/status"),
            ("POST", "/system/ffmpeg/install"),
            ("POST", "/system/ffmpeg/uninstall"),
            ("GET", "/settings"),
            ("PUT", "/settings"),
            ("GET", "/storage/stats"),
            ("POST", "/storage/cleanup"),
            ("POST", "/storage/open-folder"),
        }
        missing = required - paths
        assert not missing, f"missing required routes: {sorted(missing)}"

    def test_resolve_input_path_rejects_traversal(self, tmp_path: Path) -> None:
        p = _make_loaded_plugin(tmp_path)
        body = CreateTaskBody(mode="source_review", upload_rel="../escape.txt")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            p._resolve_input_path(body)
        assert excinfo.value.status_code == 400

    def test_resolve_input_path_requires_one_field(self, tmp_path: Path) -> None:
        p = _make_loaded_plugin(tmp_path)
        body = CreateTaskBody(mode="source_review")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            p._resolve_input_path(body)
        assert excinfo.value.status_code == 422

    def test_resolve_input_path_via_upload_rel(self, tmp_path: Path) -> None:
        p = _make_loaded_plugin(tmp_path)
        rel = "videos/foo.mp4"
        target = p._uploads_dir / rel
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x")
        body = CreateTaskBody(mode="source_review", upload_rel=rel)
        resolved = p._resolve_input_path(body)
        assert Path(resolved).resolve() == target.resolve()


# ── Helper utilities ─────────────────────────────────────────────────────


class TestDeriveFfprobePath:
    def test_returns_none_without_ffmpeg(self) -> None:
        assert Plugin._derive_ffprobe_path(None) is None

    def test_returns_sibling_when_present(self, tmp_path: Path) -> None:
        ff = tmp_path / "ffmpeg"
        fp = tmp_path / "ffprobe"
        ff.write_bytes(b"")
        fp.write_bytes(b"")
        assert Plugin._derive_ffprobe_path(str(ff)) == str(fp)

    def test_returns_none_when_sibling_missing(self, tmp_path: Path) -> None:
        ff = tmp_path / "ffmpeg"
        ff.write_bytes(b"")
        assert Plugin._derive_ffprobe_path(str(ff)) is None

    def test_handles_exe_suffix_on_windows(self, tmp_path: Path) -> None:
        ff = tmp_path / "ffmpeg.exe"
        fp = tmp_path / "ffprobe.exe"
        ff.write_bytes(b"")
        fp.write_bytes(b"")
        assert Plugin._derive_ffprobe_path(str(ff)) == str(fp)


# ── Module surface ───────────────────────────────────────────────────────


class TestModuleSurface:
    def test_exports_plugin_class(self) -> None:
        assert hasattr(plugin_mod, "Plugin")
        assert isinstance(plugin_mod.Plugin, type)

    def test_module_docstring_mentions_seven_step_load(self) -> None:
        doc = plugin_mod.__doc__ or ""
        assert "seven-step" in doc.lower() or "7-step" in doc.lower()

    @staticmethod
    def _routes_count(p: Plugin) -> int:
        from fastapi import APIRouter

        router = APIRouter()
        p._register_routes(router)
        ops: set[tuple[str, str]] = set()
        for r in router.routes:
            for m in getattr(r, "methods", set()) or set():
                ops.add((m, getattr(r, "path", "")))
        return len(ops)

    def test_route_count_at_least_16(self, tmp_path: Path) -> None:
        p = _make_loaded_plugin(tmp_path)
        # Plan promises 16; we register 17 (the upload-preview GET is
        # registered by add_upload_preview_route on top of the 16
        # bespoke routes), so >=16 is the contract.
        assert self._routes_count(p) >= 16


# ── Lifecycle (handlers wired) ────────────────────────────────────────────


class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_explanatory_string(
        self,
        tmp_path: Path,
    ) -> None:
        p = _make_loaded_plugin(tmp_path)
        result = await p._handle_tool("not_a_tool", {})
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_create_with_unknown_mode_rejected(self, tmp_path: Path) -> None:
        p = _make_loaded_plugin(tmp_path)
        result = await p._handle_tool("footage_gate_create", {"mode": "bogus"})
        assert "unknown mode" in result

    @pytest.mark.asyncio
    async def test_create_with_missing_input_path_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        p = _make_loaded_plugin(tmp_path)
        result = await p._handle_tool(
            "footage_gate_create",
            {"mode": "source_review", "input_path": "/nonexistent/x.mp4"},
        )
        assert "input_path" in result

    @pytest.mark.asyncio
    async def test_status_for_missing_task(self, tmp_path: Path) -> None:
        async def _get_task(tid: str) -> Any:
            return None

        p = _make_loaded_plugin(tmp_path)
        p._tm.get_task = _get_task
        result = await p._handle_tool("footage_gate_status", {"task_id": "ghost"})
        assert "not found" in result
