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
    get_configured_venv_path,
    get_python_executable,
    verify_python_executable,
)


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
