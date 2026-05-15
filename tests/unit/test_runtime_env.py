"""
L1 Unit Tests: runtime_env Python interpreter discovery and venv path resolution.

Tests the helper functions in openakita.runtime_env that locate Python executables
and virtual environments across different directory layouts (Linux bin/, Windows Scripts/).
"""

import sys
from pathlib import Path

import pytest

from openakita.runtime_env import (
    IS_FROZEN,
    _find_python_in_dir,
    apply_managed_node_environment,
    apply_runtime_pip_environment,
    apply_subprocess_secret_scrub,
    build_user_subprocess_environment,
    get_bootstrap_manifest_path,
    get_configured_venv_path,
    get_managed_node_seed,
    get_managed_python_seed,
    get_python_executable,
    get_readonly_seed_roots,
    resolve_toolchain_command,
    verify_python_executable,
)
from openakita.runtime_manager import get_runtime_environment_report as manager_runtime_report


class TestFindPythonInDir:
    """Test _find_python_in_dir() across different directory layouts."""

    @pytest.mark.skipif(sys.platform == "win32", reason="Linux/macOS layout")
    def test_finds_python3_in_bin(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        py3 = bin_dir / "python3"
        py3.touch(mode=0o755)
        result = _find_python_in_dir(tmp_path)
        assert result is not None
        assert result.name == "python3"

    @pytest.mark.skipif(sys.platform == "win32", reason="Linux/macOS layout")
    def test_finds_python_in_bin_when_no_python3(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        py = bin_dir / "python"
        py.touch(mode=0o755)
        result = _find_python_in_dir(tmp_path)
        assert result is not None
        assert result.name == "python"

    @pytest.mark.skipif(sys.platform == "win32", reason="Linux/macOS layout")
    def test_prefers_python3_over_python(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python3").touch(mode=0o755)
        (bin_dir / "python").touch(mode=0o755)
        result = _find_python_in_dir(tmp_path)
        assert result is not None
        assert result.name == "python3"

    def test_returns_none_for_empty_dir(self, tmp_path: Path):
        result = _find_python_in_dir(tmp_path)
        assert result is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path: Path):
        result = _find_python_in_dir(tmp_path / "nonexistent")
        assert result is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows layout")
    def test_finds_python_exe_in_scripts(self, tmp_path: Path):
        scripts = tmp_path / "Scripts"
        scripts.mkdir()
        (scripts / "python.exe").touch()
        result = _find_python_in_dir(tmp_path)
        assert result is not None
        assert result.name == "python.exe"


class TestGetPythonExecutable:
    """Test get_python_executable() in the current environment."""

    def test_non_frozen_returns_sys_executable(self):
        if not IS_FROZEN:
            result = get_python_executable()
            assert result == sys.executable

    def test_returns_string_or_none(self):
        result = get_python_executable()
        assert result is None or isinstance(result, str)

    def test_returned_path_is_valid(self):
        result = get_python_executable()
        if result is not None:
            assert Path(result).exists()


class TestGetConfiguredVenvPath:
    """Test get_configured_venv_path() venv detection."""

    def test_returns_none_or_string(self):
        result = get_configured_venv_path()
        assert result is None or isinstance(result, str)

    def test_in_venv_returns_existing_path(self):
        if sys.prefix != sys.base_prefix:
            result = get_configured_venv_path()
            assert result is not None
            assert Path(result).exists()

    def test_not_in_venv_returns_none(self):
        if sys.prefix == sys.base_prefix and not IS_FROZEN:
            result = get_configured_venv_path()
            assert result is None


class TestVerifyPythonExecutable:
    """Test verify_python_executable() validation."""

    def test_current_python_is_valid(self):
        assert verify_python_executable(sys.executable) is True

    def test_nonexistent_path_returns_false(self):
        assert verify_python_executable("/nonexistent/python3") is False

    def test_invalid_binary_returns_false(self, tmp_path: Path):
        fake = tmp_path / "not_python"
        fake.write_text("not a python interpreter")
        if sys.platform != "win32":
            fake.chmod(0o755)
        assert verify_python_executable(str(fake)) is False


def test_runtime_pip_environment_scrubs_python_conda_and_secret_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "C:/conda/site-packages")
    monkeypatch.setenv("PYTHONHOME", "C:/conda")
    monkeypatch.setenv("CONDA_PREFIX", "C:/conda")
    monkeypatch.setenv("PIP_TARGET", "C:/leaky-target")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")

    env = apply_runtime_pip_environment()

    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "CONDA_PREFIX" not in env
    assert "PIP_TARGET" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["OPENAKITA_SUBPROCESS_SECRET_SCRUB"] == "1"


def test_subprocess_secret_scrub_allows_force_prefix():
    env = apply_subprocess_secret_scrub(
        {
            "ANTHROPIC_API_KEY": "blocked",
            "OPENAKITA_FORCE_ANTHROPIC_API_KEY": "allowed",
            "PATH": "base",
        }
    )

    assert env["ANTHROPIC_API_KEY"] == "allowed"
    assert env["PATH"] == "base"


def test_user_subprocess_environment_empty_overrides_inherit_host(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "host-path")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setattr("openakita.runtime_env.get_agent_python_executable", lambda: None)
    monkeypatch.setattr("openakita.runtime_env.get_app_python_executable", lambda: None)
    monkeypatch.setattr("openakita.runtime_env.get_managed_node_seed", lambda: None)
    monkeypatch.setattr("openakita.runtime_env.get_workspace_dependency_cache_root", lambda: tmp_path / "cache")

    env = build_user_subprocess_environment({})

    assert env["PATH"] == "host-path"
    assert env["HOME"] == str(tmp_path / "home")
    assert env["HTTP_PROXY"] == "http://proxy.invalid:8080"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["OPENAKITA_ENV_TRUST_SOURCE"] == "user-subprocess"


def test_user_subprocess_environment_overrides_host_after_merge(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "host-path")
    monkeypatch.setenv("HTTPS_PROXY", "http://host-proxy.invalid:8080")
    monkeypatch.setattr("openakita.runtime_env.get_agent_python_executable", lambda: None)
    monkeypatch.setattr("openakita.runtime_env.get_app_python_executable", lambda: None)
    monkeypatch.setattr("openakita.runtime_env.get_managed_node_seed", lambda: None)
    monkeypatch.setattr("openakita.runtime_env.get_workspace_dependency_cache_root", lambda: tmp_path / "cache")

    env = build_user_subprocess_environment(
        {"PATH": "override-path", "HTTPS_PROXY": "http://override.invalid:8080"}
    )

    assert env["PATH"] == "override-path"
    assert env["HTTPS_PROXY"] == "http://override.invalid:8080"


def test_managed_toolchain_seed_resolvers(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"python_seed": {"path": "python/python.exe"}, "node_seed": {"path": "node/node.exe"}}',
        encoding="utf-8",
    )
    py = tmp_path / "python" / "python.exe"
    node = tmp_path / "node" / "node.exe"
    py.parent.mkdir()
    node.parent.mkdir()
    py.write_text("", encoding="utf-8")
    node.write_text("", encoding="utf-8")

    monkeypatch.setattr("openakita.runtime_env.get_bootstrap_manifest_path", lambda: manifest)
    monkeypatch.setattr("openakita.runtime_env.verify_python_executable", lambda path: path == str(py))

    assert get_managed_python_seed() == str(py)
    assert get_managed_node_seed() == str(node)


def test_bootstrap_manifest_path_prefers_explicit_env(monkeypatch, tmp_path):
    bootstrap = tmp_path / "resources" / "bootstrap"
    bootstrap.mkdir(parents=True)
    manifest = bootstrap / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("OPENAKITA_BOOTSTRAP_DIR", str(bootstrap))

    assert get_bootstrap_manifest_path() == manifest


def test_bootstrap_manifest_path_resolves_dual_venv_home(monkeypatch, tmp_path):
    bootstrap = tmp_path / "OpenAkitaDesktop" / "resources" / "bootstrap"
    bootstrap.mkdir(parents=True)
    manifest = bootstrap / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    venv = tmp_path / "runtime" / "app-venv"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    monkeypatch.delenv("OPENAKITA_BOOTSTRAP_DIR", raising=False)
    (venv / "pyvenv.cfg").write_text(f"home = {bootstrap / 'python'}\n", encoding="utf-8")

    assert get_bootstrap_manifest_path() == manifest


def test_managed_node_environment_uses_runtime_cache(monkeypatch, tmp_path):
    node = tmp_path / "node" / "node.exe"
    node.parent.mkdir()
    node.write_text("", encoding="utf-8")
    monkeypatch.setattr("openakita.runtime_env.get_managed_node_seed", lambda: str(node))
    monkeypatch.setattr("openakita.runtime_env.get_workspace_dependency_cache_root", lambda: tmp_path / "cache")

    env = apply_managed_node_environment({"PATH": "base"})

    assert env["OPENAKITA_NODE"] == str(node)
    assert env["OPENAKITA_NODE_BIN"] == str(node.parent)
    assert env["PATH"].startswith(str(node.parent))
    assert env["NPM_CONFIG_CACHE"] == str(tmp_path / "cache" / "npm-cache")
    assert env["NPM_CONFIG_PREFIX"] == str(tmp_path / "cache" / "npm-prefix")
    assert env["COREPACK_HOME"] == str(tmp_path / "cache" / "corepack")


def test_readonly_seed_roots_and_node_command(monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    node = seed / "node" / ("node.exe" if sys.platform == "win32" else "node")
    node.parent.mkdir(parents=True)
    node.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENAKITA_READONLY_SEED_DIRS", str(seed))
    monkeypatch.setattr("openakita.runtime_env.get_bootstrap_manifest_path", lambda: None)
    monkeypatch.setattr("openakita.runtime_env.get_managed_node_seed", lambda: "")
    monkeypatch.setattr("shutil.which", lambda command: None)

    assert get_readonly_seed_roots() == [seed.resolve()]
    assert resolve_toolchain_command("node") == str(node.resolve())


def test_runtime_manager_facade_exposes_runtime_report():
    assert callable(manager_runtime_report)

