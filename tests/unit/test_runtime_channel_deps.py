from __future__ import annotations

import sys

from openakita.runtime_channel_deps import ensure_channel_dependencies


def test_channel_deps_no_enabled_channels_is_ok(monkeypatch):
    monkeypatch.setattr("openakita.runtime_channel_deps.inject_module_paths_runtime", lambda: None)
    monkeypatch.setattr("openakita.runtime_channel_deps.patch_simplejson_jsondecodeerror", lambda logger=None: False)

    result = ensure_channel_dependencies(workspace_env={})

    assert result["status"] == "ok"
    assert result["installed"] == []


def test_channel_deps_packaged_mode_rejects_frozen_sys_executable(monkeypatch):
    monkeypatch.setattr("openakita.runtime_channel_deps.inject_module_paths_runtime", lambda: None)
    monkeypatch.setattr("openakita.runtime_channel_deps.patch_simplejson_jsondecodeerror", lambda logger=None: False)
    monkeypatch.setattr("openakita.runtime_channel_deps.IS_FROZEN", True)
    monkeypatch.setattr("openakita.runtime_channel_deps.get_app_python_executable", lambda: None)
    monkeypatch.setattr("openakita.runtime_channel_deps.get_python_executable", lambda: sys.executable)
    monkeypatch.setattr(
        "openakita.runtime_channel_deps.CHANNEL_DEPS",
        {"feishu": [("definitely_missing_openakita_dep", "definitely-missing-openakita-dep")]},
    )

    result = ensure_channel_dependencies(workspace_env={"FEISHU_ENABLED": "true"})

    assert result["status"] == "error"
    assert "托管 Python" in result["message"]
    assert result["missing"] == ["definitely-missing-openakita-dep"]
