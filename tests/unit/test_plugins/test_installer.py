"""Tests for plugin installer dependency handling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from openakita.plugins.installer import (
    _pip_subprocess_env,
    deps_appear_installed,
)


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
