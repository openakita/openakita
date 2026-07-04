"""Smoke tests for top-level entry points.

P8.7-fix added these after G-RC-8 audit caught that main.py and mcp_server.py
still imported from the deleted core.agent shim (a regression that escaped
P-RC-7 because these modules were not in the gate selector).

These tests assert that the CLI and MCP server modules can be imported
without raising, guarding against future shim-deletion drift.
"""

import importlib
import shutil
import subprocess
import sys


def test_openakita_main_imports():
    """src/openakita/main.py must import without ImportError after shim deletion."""
    importlib.import_module("openakita.main")


def test_openakita_mcp_server_imports():
    """src/openakita/mcp_server.py must import without ImportError after shim deletion."""
    importlib.import_module("openakita.mcp_server")


def test_openakita_cli_help_smoke():
    """`openakita --help` must exit 0 (catches missing console script entry).

    Prefers ``python -m openakita`` (uses ``src/openakita/__main__.py``) so we
    do not depend on the installed console script being on PATH. Falls back
    to the installed ``openakita`` executable via ``shutil.which`` for
    environments where ``__main__.py`` is missing.
    """
    cmd = [sys.executable, "-m", "openakita", "--help"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        exe = shutil.which("openakita")
        assert exe is not None, (
            "neither `python -m openakita` nor `openakita` console script available"
        )
        result = subprocess.run(
            [exe, "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )

    assert result.returncode == 0, (
        f"openakita --help exited {result.returncode}\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )
    assert "openakita" in (result.stdout + result.stderr).lower()
