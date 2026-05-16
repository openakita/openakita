from __future__ import annotations

import sys

from openakita.runtime_channel_deps import (
    _purge_incompatible_websockets,
    ensure_channel_dependencies,
)


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


def test_purge_incompatible_websockets_removes_16_keeps_15(tmp_path):
    """``_purge_incompatible_websockets`` 只清 ``websockets-1[6-9]/2x.dist-info``，
    保留 15.x dist-info、保留 websockets 真实包目录、绝不动其它无关 dist-info。
    """
    (tmp_path / "websockets-16.0.dist-info").mkdir()
    (tmp_path / "websockets-15.0.1.dist-info").mkdir()
    (tmp_path / "websockets-17.5.2.dist-info").mkdir()
    (tmp_path / "websockets").mkdir()
    (tmp_path / "websockets" / "__init__.py").write_text("# package", encoding="utf-8")
    (tmp_path / "lark_oapi-1.6.5.dist-info").mkdir()
    (tmp_path / "dingtalk_stream-0.24.3.dist-info").mkdir()

    removed = _purge_incompatible_websockets(tmp_path)

    assert sorted(removed) == ["websockets-16.0.dist-info", "websockets-17.5.2.dist-info"]
    assert not (tmp_path / "websockets-16.0.dist-info").exists()
    assert not (tmp_path / "websockets-17.5.2.dist-info").exists()
    assert (tmp_path / "websockets-15.0.1.dist-info").exists()
    assert (tmp_path / "websockets" / "__init__.py").exists()
    assert (tmp_path / "lark_oapi-1.6.5.dist-info").exists()
    assert (tmp_path / "dingtalk_stream-0.24.3.dist-info").exists()


def test_purge_incompatible_websockets_missing_dir_is_noop(tmp_path):
    """目标目录不存在时不应抛错，返回空列表。"""
    removed = _purge_incompatible_websockets(tmp_path / "does-not-exist")
    assert removed == []


def test_ensure_channel_deps_returns_install_errors(monkeypatch, tmp_path):
    """模拟 pip 始终失败的场景，验证返回值中 ``errors`` 透传逐包错误尾巴。"""

    monkeypatch.setattr("openakita.runtime_channel_deps.inject_module_paths_runtime", lambda: None)
    monkeypatch.setattr(
        "openakita.runtime_channel_deps.patch_simplejson_jsondecodeerror",
        lambda logger=None: False,
    )
    monkeypatch.setattr("openakita.runtime_channel_deps.IS_FROZEN", False)
    monkeypatch.setattr("openakita.runtime_channel_deps.get_app_python_executable", lambda: sys.executable)
    monkeypatch.setattr("openakita.runtime_channel_deps.get_python_executable", lambda: sys.executable)
    monkeypatch.setattr("openakita.runtime_channel_deps.get_channel_deps_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "openakita.runtime_channel_deps.apply_runtime_pip_environment",
        lambda **_: {},
    )
    monkeypatch.setattr(
        "openakita.runtime_channel_deps.resolve_pip_index",
        lambda: {"url": "https://example.invalid/simple/", "trusted_host": "example.invalid"},
    )
    monkeypatch.setattr(
        "openakita.runtime_channel_deps._probe_python_runtime",
        lambda *a, **kw: (True, ""),
    )
    monkeypatch.setattr(
        "openakita.runtime_channel_deps.CHANNEL_DEPS",
        {"feishu": [("definitely_missing_openakita_dep_for_test", "definitely-missing-openakita-dep-for-test")]},
    )

    import subprocess as _sp

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Could not find a version that satisfies the requirement xyz"

    monkeypatch.setattr(
        "openakita.runtime_channel_deps.subprocess.run",
        lambda *a, **kw: _Result(),
    )
    # 让 TimeoutExpired 仍可被 except 捕获
    monkeypatch.setattr("openakita.runtime_channel_deps.subprocess.TimeoutExpired", _sp.TimeoutExpired)

    result = ensure_channel_dependencies(workspace_env={"FEISHU_ENABLED": "true"})

    assert result["status"] == "error"
    assert result["missing"] == ["definitely-missing-openakita-dep-for-test"]
    assert "errors" in result
    assert "definitely-missing-openakita-dep-for-test" in result["errors"]
    assert "Could not find a version" in result["errors"]["definitely-missing-openakita-dep-for-test"]
