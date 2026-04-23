"""Unit tests for openakita.utils.path_helper.

Focus: the Linux login-shell PATH enrichment path that was added to fix
external CLI sub-agent delegation failures when the server runs under
systemd with a minimal PATH.
"""
from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

from openakita.utils import path_helper
from openakita.utils.path_helper import (
    _merge_paths,
    get_enriched_env,
    resolve_login_shell_path,
    which_command,
)


def _clear_cache():
    resolve_login_shell_path.cache_clear()


def test_which_command_uses_shutil_first(tmp_path):
    _clear_cache()
    fake_bin = tmp_path / "mytool"
    fake_bin.write_text("#!/bin/sh\necho hi\n")
    fake_bin.chmod(0o755)

    with patch.dict("os.environ", {"PATH": str(tmp_path)}, clear=False):
        assert which_command("mytool") == str(fake_bin)


def test_which_command_falls_back_to_login_shell_on_miss(monkeypatch):
    _clear_cache()
    monkeypatch.setenv("PATH", "/nonexistent-path-for-test")

    enriched = "/home/u/.nvm/versions/node/v20/bin:/usr/bin"

    def fake_which(cmd, path=None):
        if path == enriched:
            return f"{enriched.split(':')[0]}/{cmd}"
        return None

    # Put a fake binary at a place reachable from `enriched`.
    with patch.object(
        path_helper, "_resolve_via_login_shell", return_value=enriched
    ), patch("shutil.which", side_effect=fake_which):
        assert which_command("claude") == f"{enriched.split(':')[0]}/claude"


def test_which_command_respects_explicit_extra_path_and_skips_enrichment(monkeypatch):
    _clear_cache()
    called = {"enrichment": False}

    def fake_login_shell():
        called["enrichment"] = True
        return "/home/u/.nvm/bin"

    with patch.object(path_helper, "_resolve_via_login_shell", side_effect=fake_login_shell):
        # Explicit extra_path → caller is being explicit, no enrichment.
        assert which_command("bogus", extra_path="/definitely/not/here") is None
    assert called["enrichment"] is False


def test_resolve_login_shell_path_parses_sentinel_output():
    _clear_cache()
    stdout = (
        "Some shell init noise\n"
        "__AKITA_PATH__\n"
        "/home/u/.nvm/bin:/usr/local/bin:/usr/bin\n"
        "__AKITA_PATH__\n"
    )
    completed = MagicMock(returncode=0, stdout=stdout, stderr="")
    with patch.object(subprocess, "run", return_value=completed):
        assert resolve_login_shell_path() == "/home/u/.nvm/bin:/usr/local/bin:/usr/bin"


def test_resolve_login_shell_path_handles_timeout():
    _clear_cache()
    with patch.object(
        subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd=["bash"], timeout=10),
    ):
        if sys.platform == "darwin":
            # macOS falls back to /usr/libexec/path_helper; we don't stub it
            # here, so the result may or may not be None.
            resolve_login_shell_path()
        else:
            assert resolve_login_shell_path() is None


def test_resolve_login_shell_path_returns_none_on_nonzero_exit():
    _clear_cache()
    completed = MagicMock(returncode=1, stdout="", stderr="boom")
    with patch.object(subprocess, "run", return_value=completed):
        if sys.platform == "darwin":
            resolve_login_shell_path()
        else:
            assert resolve_login_shell_path() is None


def test_resolve_login_shell_path_uses_interactive_flag_on_linux():
    """The bugfix: `-lic` (with -i) so .bashrc's non-interactive early
    return doesn't skip nvm/volta sourcing."""
    _clear_cache()
    recorded: dict = {}

    def capture(cmd, **kwargs):
        recorded["cmd"] = cmd
        return MagicMock(returncode=0, stdout="__AKITA_PATH__\n/x\n__AKITA_PATH__\n", stderr="")

    with patch.object(subprocess, "run", side_effect=capture):
        resolve_login_shell_path()

    # cmd[1] is the flags arg. On Linux we require `i` (interactive) so
    # .bashrc/.zshrc are sourced past their non-interactive guard.
    if sys.platform != "darwin":
        assert "i" in recorded["cmd"][1]


def test_get_enriched_env_merges_path_into_existing_env():
    _clear_cache()
    with patch.object(path_helper, "_resolve_via_login_shell",
                      return_value="/home/u/.nvm/bin:/usr/bin"):
        env = get_enriched_env({"PATH": "/usr/local/bin", "FOO": "bar"})
    assert env is not None
    assert env["FOO"] == "bar"
    # Merged, deduplicated, order-preserving: primary first, then new entries.
    assert env["PATH"].startswith("/usr/local/bin")
    assert "/home/u/.nvm/bin" in env["PATH"]


def test_get_enriched_env_returns_base_unchanged_when_login_shell_fails():
    _clear_cache()
    with patch.object(path_helper, "_resolve_via_login_shell", return_value=None):
        base = {"PATH": "/usr/bin", "X": "y"}
        assert get_enriched_env(base) == base


def test_get_enriched_env_handles_none_base_env():
    _clear_cache()
    with patch.object(path_helper, "_resolve_via_login_shell", return_value="/a:/b"):
        env = get_enriched_env(None)
    assert env is not None
    assert env["PATH"] == "/a:/b"


def test_merge_paths_deduplicates_preserving_order():
    assert _merge_paths("/a:/b", "/b:/c") == "/a:/b:/c"
    assert _merge_paths("/a", "") == "/a"
    assert _merge_paths("", "/a") == "/a"


def test_deprecated_aliases_point_to_new_names():
    # Callers like shell.py / mcp.py / third-party plugins may still import
    # these — they must resolve to the new generalized implementations.
    assert path_helper.resolve_macos_login_shell_path is resolve_login_shell_path
    assert path_helper.get_macos_enriched_env is get_enriched_env
