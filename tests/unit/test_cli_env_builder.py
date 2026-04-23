"""Tests for build_cli_env -- the per-agent external CLI env builder."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from openakita.agents.cli_providers._common import (
    _resolve_cli_env_value,
    build_cli_env,
)
from openakita.agents.profile import AgentProfile


@pytest.fixture(autouse=True)
def _clear_path_helper_cache():
    from openakita.utils.path_helper import resolve_login_shell_path
    resolve_login_shell_path.cache_clear()
    yield
    resolve_login_shell_path.cache_clear()


def test_excludes_openakita_llm_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-openakita")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    env = build_cli_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env


def test_includes_safe_base_env_vars(monkeypatch):
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/test/.config")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-abc/agent.1")
    env = build_cli_env()
    assert env["HOME"] == "/home/test"
    assert env["USER"] == "tester"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["TERM"] == "xterm-256color"
    assert env["LC_ALL"] == "en_US.UTF-8"
    assert env["XDG_CONFIG_HOME"] == "/home/test/.config"
    assert env["SSH_AUTH_SOCK"] == "/tmp/ssh-abc/agent.1"
    assert "PATH" in env


def test_profile_cli_env_overlays_base(monkeypatch):
    monkeypatch.setenv("HOME", "/home/test")
    profile = AgentProfile(
        id="p", name="P", cli_env={"MY_VAR": "literal", "HOME": "/override"}
    )
    env = build_cli_env(profile)
    assert env["MY_VAR"] == "literal"
    assert env["HOME"] == "/override"


def test_profile_cli_env_can_reintroduce_excluded(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-openakita")
    profile = AgentProfile(
        id="p", name="P", cli_env={"ANTHROPIC_API_KEY": "sk-claude-user"}
    )
    env = build_cli_env(profile)
    assert env["ANTHROPIC_API_KEY"] == "sk-claude-user"


def test_var_expansion_resolves_from_process_env(monkeypatch):
    monkeypatch.setenv("OUTER_KEY", "resolved")
    profile = AgentProfile(
        id="p", name="P", cli_env={"INNER": "prefix-${OUTER_KEY}-suffix"}
    )
    env = build_cli_env(profile)
    assert env["INNER"] == "prefix-resolved-suffix"


def test_var_expansion_missing_becomes_empty(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    profile = AgentProfile(
        id="p", name="P", cli_env={"INNER": "x-${MISSING_VAR}-y"}
    )
    env = build_cli_env(profile)
    assert env["INNER"] == "x--y"


def test_var_expansion_multiple_refs(monkeypatch):
    monkeypatch.setenv("A", "one")
    monkeypatch.setenv("B", "two")
    profile = AgentProfile(id="p", name="P", cli_env={"X": "${A}-${B}"})
    env = build_cli_env(profile)
    assert env["X"] == "one-two"


def test_empty_value_preserved():
    profile = AgentProfile(id="p", name="P", cli_env={"FOO": ""})
    env = build_cli_env(profile)
    assert "FOO" in env
    assert env["FOO"] == ""


def test_empty_key_dropped():
    profile = AgentProfile(id="p", name="P", cli_env={"": "oops", "OK": "yes"})
    env = build_cli_env(profile)
    assert "" not in env
    assert env["OK"] == "yes"


def test_path_still_enriched_from_login_shell(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    with patch(
        "openakita.agents.cli_providers._common.get_enriched_env"
    ) as mock_enrich:
        mock_enrich.return_value = {
            "PATH": "/usr/bin:/bin:/home/test/.nvm/versions/node/v22/bin"
        }
        env = build_cli_env()
    assert "/home/test/.nvm" in env["PATH"]


def test_resolve_value_explicit_environ_map():
    out = _resolve_cli_env_value("a-${K}-b", environ={"K": "v"})
    assert out == "a-v-b"


def test_build_cli_env_no_profile_argument_stays_backwards_compatible():
    """Calling build_cli_env() with no profile still works (adapter legacy path)."""
    env = build_cli_env()
    assert "PATH" in env


def test_build_cli_env_with_empty_profile_cli_env():
    """profile.cli_env == {} is indistinguishable from no profile overlay."""
    profile = AgentProfile(id="p", name="P", cli_env={})
    env = build_cli_env(profile)
    assert "PATH" in env
