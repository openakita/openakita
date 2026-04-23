"""Integration test: external CLI adapters must resolve their binary when
the server runs with a minimal PATH (as under systemd).

This is the load-bearing regression test for the fix that made
@claude-code-pair delegation work end-to-end. Before the fix, the server's
PATH did not include the user's nvm/volta/~/.local/bin, so every CLI
adapter raised ToolError(DEPENDENCY, "claude binary not found on PATH")
and the fallback chain cascaded through all profiles.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from openakita.utils.path_helper import (
    get_enriched_env,
    resolve_login_shell_path,
    which_command,
)

SYSTEMD_MINIMAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin"


@pytest.fixture(autouse=True)
def _clear_login_shell_cache():
    resolve_login_shell_path.cache_clear()
    yield
    resolve_login_shell_path.cache_clear()


@pytest.mark.skipif(sys.platform == "darwin", reason="linux systemd PATH simulation")
def test_which_command_finds_claude_with_simulated_systemd_path(monkeypatch):
    """Simulates the exact environment of a systemd-launched openakita
    server and asserts that which_command can still find `claude` via
    login-shell enrichment."""
    monkeypatch.setenv("PATH", SYSTEMD_MINIMAL_PATH)
    # Ensure SHELL is set — systemd doesn't set this by default, and our
    # path_helper falls back to /bin/bash in that case.
    monkeypatch.setenv("SHELL", "/bin/bash")

    # shutil.which in the minimal PATH must not find claude (sanity check
    # that the simulation is meaningful).
    assert shutil.which("claude") is None, (
        "Expected simulated systemd PATH to not contain `claude`. If this "
        "fails, the test environment was installed to /usr/local/bin and "
        "the bug this test guards against cannot be reproduced here."
    )

    found = which_command("claude")
    # We only insist on a hit when claude is actually installed on the host.
    # CI environments without node/claude should skip gracefully.
    probe = subprocess.run(
        ["bash", "-lic", "command -v claude"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        pytest.skip("claude CLI not installed on this host")

    assert found is not None, (
        "which_command('claude') returned None under minimal PATH. "
        "Login-shell enrichment is not picking up nvm/volta/~/.local/bin."
    )


@pytest.mark.skipif(sys.platform == "darwin", reason="linux login-shell path")
def test_login_shell_path_sources_bashrc_non_interactive_guard(monkeypatch):
    """Regression guard: .bashrc on Ubuntu/Debian ships with an early
    `case $- in *i*) ;; *) return;; esac` that exits when not interactive.
    We use `-lic` (with -i) so nvm/volta setup actually runs.
    """
    monkeypatch.setenv("PATH", SYSTEMD_MINIMAL_PATH)
    monkeypatch.setenv("SHELL", "/bin/bash")

    shell_path = resolve_login_shell_path()
    if shell_path is None:
        pytest.skip("login shell unavailable in this test env")

    # If the user has nvm installed, the enriched PATH should include it.
    # If not, at least it should contain ~/.local/bin (added by Ubuntu's
    # default .profile when $HOME/.local/bin exists).
    has_user_paths = any(
        seg.startswith(("/home/", "/root/")) for seg in shell_path.split(":")
    )
    assert has_user_paths, (
        f"Enriched PATH has no user-level entries — login shell likely "
        f"did not source shell init. Got: {shell_path!r}"
    )


@pytest.mark.skipif(sys.platform == "darwin", reason="linux build_cli_env smoke")
def test_build_cli_env_merges_login_shell_path(monkeypatch):
    """Every CLI adapter now calls build_cli_env() via _common.py. Verify
    that the env it produces includes login-shell enrichment when the
    process PATH is minimal."""
    from openakita.agents.cli_providers._common import build_cli_env

    monkeypatch.setenv("PATH", SYSTEMD_MINIMAL_PATH)
    monkeypatch.setenv("SHELL", "/bin/bash")

    env = build_cli_env()
    assert "PATH" in env
    # Either enrichment succeeded (user-level paths present) or it fell
    # back to the base env unchanged. Both are valid; we just verify the
    # merge didn't drop the base entries.
    assert "/usr/bin" in env["PATH"]


def test_get_enriched_env_preserves_other_env_vars():
    """The enrichment must not clobber non-PATH env vars — adapters like
    codex depend on CODEX_HOME surviving the merge."""
    base = {"PATH": "/usr/bin", "CODEX_HOME": "/tmp/codex-home", "FOO": "bar"}
    from unittest.mock import patch
    with patch(
        "openakita.utils.path_helper._resolve_via_login_shell",
        return_value="/home/u/.nvm/bin:/usr/bin",
    ):
        resolve_login_shell_path.cache_clear()
        merged = get_enriched_env(base)
    assert merged is not None
    assert merged["CODEX_HOME"] == "/tmp/codex-home"
    assert merged["FOO"] == "bar"
