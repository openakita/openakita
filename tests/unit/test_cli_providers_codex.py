# tests/unit/test_cli_providers_codex.py
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_providers import PROVIDERS
from openakita.agents.cli_runner import CliRunRequest, ExitReason, ProviderRunResult
from openakita.agents.profile import AgentProfile, AgentType, CliPermissionMode


def _make_profile(**overrides) -> AgentProfile:
    base = {
        "id": "codex-writer",
        "name": "Codex Writer",
        "type": AgentType.EXTERNAL_CLI,
        "cli_provider_id": CliProviderId.CODEX,
        "cli_permission_mode": CliPermissionMode.PLAN,
    }
    base.update(overrides)
    return AgentProfile(**base)


def _make_request(profile, *, cwd=Path("/tmp"), resume_id=None,
                  system_prompt_extra="", mcp_servers=(), message="Refactor module X"):
    return CliRunRequest(
        message=message,
        resume_id=resume_id,
        profile=profile,
        cwd=cwd,
        cancelled=asyncio.Event(),
        session=None,
        system_prompt_extra=system_prompt_extra,
        mcp_servers=mcp_servers,
    )


def test_provider_registered_under_codex():
    assert CliProviderId.CODEX in PROVIDERS


def test_build_argv_base_flags():
    from openakita.agents.cli_providers import codex

    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        argv = PROVIDERS[CliProviderId.CODEX].build_argv(
            _make_request(_make_profile())
        )

    assert argv[0] == "/usr/bin/codex"
    assert "exec" in argv
    assert "--json" in argv
    assert argv[argv.index("--cd") + 1] == "/tmp"
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    # Message is the trailing positional
    assert argv[-1] == "Refactor module X"


def test_build_argv_write_mode_adds_skip_checks():
    """In WRITE mode Codex skips the git-repo-dirty check so it can edit files."""
    from openakita.agents.cli_providers import codex

    profile = _make_profile(cli_permission_mode=CliPermissionMode.WRITE)
    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        argv = PROVIDERS[CliProviderId.CODEX].build_argv(_make_request(profile))

    assert "--skip-git-repo-check" in argv
    assert "--full-auto" in argv
    assert "--cd" in argv
    assert "--add-dir" not in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_build_argv_write_mode_adds_explicit_temp_target_root(tmp_path):
    from openakita.agents.cli_providers import codex

    cwd = tmp_path / "workspace"
    cwd.mkdir()
    profile = _make_profile(cli_permission_mode=CliPermissionMode.WRITE)
    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        argv = PROVIDERS[CliProviderId.CODEX].build_argv(
            _make_request(profile, cwd=cwd, message="Create /tmp/codex-test.py")
        )

    assert argv[argv.index("--add-dir") + 1] == tempfile.gettempdir()


def test_build_argv_write_mode_adds_explicit_project_root(tmp_path):
    from openakita.agents.cli_providers import codex

    cwd = tmp_path / "main"
    cwd.mkdir()
    other_project = tmp_path / "other"
    target_dir = other_project / "src" / "pkg"
    target_dir.mkdir(parents=True)
    (other_project / "pyproject.toml").write_text("[project]\nname='other'\n")

    profile = _make_profile(cli_permission_mode=CliPermissionMode.WRITE)
    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        argv = PROVIDERS[CliProviderId.CODEX].build_argv(
            _make_request(
                profile,
                cwd=cwd,
                message=f"Update {target_dir / 'module.py'}",
            )
        )

    assert argv[argv.index("--cd") + 1] == str(cwd)
    assert argv[argv.index("--add-dir") + 1] == str(other_project)


def test_build_argv_write_mode_ignores_unscoped_absolute_paths(tmp_path):
    from openakita.agents.cli_providers import codex

    cwd = tmp_path / "main"
    cwd.mkdir()
    profile = _make_profile(cli_permission_mode=CliPermissionMode.WRITE)
    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        argv = PROVIDERS[CliProviderId.CODEX].build_argv(
            _make_request(
                profile,
                cwd=cwd,
                message="Do not edit /etc/passwd",
            )
        )

    assert "--add-dir" not in argv


def test_build_argv_resume_uses_session_id():
    from openakita.agents.cli_providers import codex

    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        argv = PROVIDERS[CliProviderId.CODEX].build_argv(
            _make_request(_make_profile(), resume_id="codex-session-abc")
        )

    assert argv[:3] == ["/usr/bin/codex", "exec", "resume"]
    assert "--session" not in argv
    assert "--json" in argv
    assert "--sandbox" not in argv
    assert "--cd" not in argv
    assert argv[-2] == "codex-session-abc"
    assert argv[-1] == "Refactor module X"


def test_build_argv_folds_system_prompt_extra_into_prompt():
    from openakita.agents.cli_providers import codex

    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        argv = PROVIDERS[CliProviderId.CODEX].build_argv(
            _make_request(_make_profile(), system_prompt_extra="ORG FACT")
        )

    assert argv[-1] == "ORG FACT\n\nRefactor module X"


def test_build_argv_adds_mcp_config_overrides():
    from openakita.agents.cli_providers import codex

    fake_info = MagicMock(command="npx", args=["-y", "pkg"], env={"TOKEN": "abc"})
    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"), \
         patch("openakita.agents.cli_providers.codex.MCPCatalog") as Catalog:
        Catalog.return_value.get_server = MagicMock(return_value=fake_info)
        argv = PROVIDERS[CliProviderId.CODEX].build_argv(
            _make_request(_make_profile(), mcp_servers=("web-search",))
        )

    joined = "\n".join(argv)
    assert 'mcp_servers.web_search.command="npx"' in joined
    assert 'mcp_servers.web_search.args=["-y", "pkg"]' in joined
    assert 'mcp_servers.web_search.env={ TOKEN = "abc" }' in joined


def test_build_env_preserves_subscription_codex_home_default(monkeypatch):
    from openakita.agents.cli_providers import codex

    monkeypatch.delenv("CODEX_HOME", raising=False)
    profile = _make_profile()
    req = _make_request(profile)
    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        env = PROVIDERS[CliProviderId.CODEX].build_env(req)

    assert "CODEX_HOME" not in env


def test_build_env_preserves_explicit_codex_home(monkeypatch):
    from openakita.agents.cli_providers import codex

    monkeypatch.delenv("CODEX_HOME", raising=False)
    profile = _make_profile(cli_env={"CODEX_HOME": "/tmp/codex-real-home"})
    req = _make_request(profile)
    with patch.object(codex, "_resolve_binary", return_value="/usr/bin/codex"):
        env = PROVIDERS[CliProviderId.CODEX].build_env(req)

    assert env["CODEX_HOME"] == "/tmp/codex-real-home"


def test_write_mcp_config_toml_contains_server_sections(tmp_path):
    from openakita.agents.cli_providers._common import write_mcp_config

    fake_info = MagicMock(command="npx", args=["-y", "pkg"], env={})
    with patch("openakita.agents.cli_providers._common.MCPCatalog") as Catalog:
        Catalog.return_value.get_server = MagicMock(return_value=fake_info)
        path = write_mcp_config(tmp_path, ("web-search", "github"), fmt="toml")

    assert path is not None
    text = path.read_text()
    assert "[mcp_servers.web_search]" in text or "[mcp_servers.web-search]" in text
    assert "[mcp_servers.github]" in text


def test_write_mcp_config_json_contains_server_keys(tmp_path):
    from openakita.agents.cli_providers._common import write_mcp_config

    fake_info = MagicMock(command="npx", args=["-y", "pkg"], env={})
    with patch("openakita.agents.cli_providers._common.MCPCatalog") as Catalog:
        Catalog.return_value.get_server = MagicMock(return_value=fake_info)
        path = write_mcp_config(tmp_path, ("web-search", "github"), fmt="json")

    assert path is not None
    obj = json.loads(path.read_text())
    assert "mcpServers" in obj
    assert set(obj["mcpServers"].keys()) == {"web-search", "github"}


def test_write_mcp_config_returns_none_for_empty():
    from openakita.agents.cli_providers._common import write_mcp_config

    assert write_mcp_config(Path("/tmp"), (), fmt="toml") is None
    assert write_mcp_config(Path("/tmp"), (), fmt="json") is None


def test_session_root_is_codex_sessions():
    from openakita.agents.cli_providers import codex

    assert Path.home() / ".codex" / "sessions" == codex.SESSION_ROOT


@pytest.mark.asyncio
async def test_run_streams_events_into_provider_run_result(tmp_path):
    from openakita.agents.cli_providers import codex

    events = [
        {"type": "session_start", "session_id": "codex-sess-7"},
        {"type": "assistant_delta", "text": "Refactoring "},
        {"type": "tool_call", "name": "write_file"},
        {"type": "assistant_delta", "text": "complete."},
        {"type": "turn_end", "usage": {"input_tokens": 8, "output_tokens": 2}},
    ]
    script = "\n".join("echo " + json.dumps(json.dumps(e)) for e in events)
    argv = ["sh", "-c", script]

    profile = _make_profile()
    req = _make_request(profile, cwd=tmp_path)
    result = await codex.PROVIDER.run(
        req, argv, env={}, on_spawn=lambda _: None,
    )

    assert isinstance(result, ProviderRunResult)
    assert result.session_id == "codex-sess-7"
    assert result.final_text == "Refactoring complete."
    assert result.tools_used == ["write_file"]
    assert result.input_tokens == 8
    assert result.output_tokens == 2
    assert result.exit_reason == ExitReason.COMPLETED


@pytest.mark.asyncio
async def test_run_honors_cancellation(tmp_path):
    from openakita.agents.cli_providers import codex

    profile = _make_profile()
    req = _make_request(profile, cwd=tmp_path)
    req.cancelled.set()
    argv = ["sh", "-c", "for i in $(seq 1 100); do echo '{}'; sleep 0.1; done"]

    result = await codex.PROVIDER.run(req, argv, env={}, on_spawn=lambda _: None)
    assert result.exit_reason == ExitReason.CANCELLED


@pytest.mark.asyncio
async def test_run_preserves_codex_home_env(tmp_path):
    from openakita.agents.cli_providers import codex

    captured_home: dict[str, str] = {}
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    argv = [
        "sh", "-c",
        'echo {\\"type\\":\\"turn_end\\",\\"usage\\":{\\"input_tokens\\":1,\\"output_tokens\\":1}}',
    ]

    from openakita.agents.cli_providers import _common as common_mod

    async def fake_stream(argv, env, cwd, cancelled, *, on_spawn, on_stderr=None):
        captured_home["home"] = env.get("CODEX_HOME", "")
        on_spawn(type("P", (), {"stderr": None, "returncode": 0,
                                "wait": lambda self: asyncio.sleep(0)})())
        yield b'{"type":"turn_end","usage":{"input_tokens":1,"output_tokens":1}}\n'

    with patch.object(common_mod, "stream_cli_subprocess", fake_stream), \
         patch("openakita.agents.cli_providers.codex.stream_cli_subprocess", fake_stream):
        profile = _make_profile(cli_permission_mode=CliPermissionMode.WRITE)
        req = _make_request(profile, cwd=tmp_path)
        await codex.PROVIDER.run(
            req,
            argv,
            env={"CODEX_HOME": str(codex_home)},
            on_spawn=lambda _: None,
        )

    assert captured_home["home"] == str(codex_home)
    assert codex_home.exists()


@pytest.mark.asyncio
async def test_run_returns_classified_stderr_on_failure(tmp_path):
    from openakita.agents.cli_providers import codex

    profile = _make_profile()
    req = _make_request(profile, cwd=tmp_path)
    argv = ["sh", "-c", "printf 'codex login required' >&2; exit 1"]

    result = await codex.PROVIDER.run(req, argv, env={}, on_spawn=lambda _: None)

    assert result.exit_reason == ExitReason.ERROR
    assert result.errored is True
    assert result.error_message is not None
    assert "auth_permanent: codex login required" in result.error_message
