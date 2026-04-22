# tests/unit/test_cli_providers_goose_opencode.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_providers import PROVIDERS
from openakita.agents.cli_runner import CliRunRequest, ExitReason
from openakita.agents.profile import AgentProfile, AgentType, CliPermissionMode


def _profile(provider_id, permission=CliPermissionMode.PLAN) -> AgentProfile:
    return AgentProfile(
        id=f"{provider_id.value}-agent",
        name=f"{provider_id.value} agent",
        type=AgentType.EXTERNAL_CLI,
        cli_provider_id=provider_id,
        cli_permission_mode=permission,
    )


def _request(profile, *, cwd=Path("/tmp"), resume_id=None, system_prompt_extra=""):
    return CliRunRequest(
        message="Say hello",
        resume_id=resume_id,
        profile=profile,
        cwd=cwd,
        cancelled=asyncio.Event(),
        session=None,
        system_prompt_extra=system_prompt_extra,
    )


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def test_session_roots_are_paths():
    from openakita.agents.cli_providers import goose, opencode

    assert isinstance(goose.SESSION_ROOT, Path)
    assert isinstance(opencode.SESSION_ROOT, Path)


# ---------------------------------------------------------------------------
# Goose
# ---------------------------------------------------------------------------

def test_goose_registered():
    assert CliProviderId.GOOSE in PROVIDERS


def test_goose_build_argv_base():
    from openakita.agents.cli_providers import goose

    with patch.object(goose, "_resolve_binary", return_value="/usr/bin/goose"):
        argv = PROVIDERS[CliProviderId.GOOSE].build_argv(
            _request(_profile(CliProviderId.GOOSE))
        )

    assert argv[0] == "/usr/bin/goose"
    assert "session" in argv
    assert "--stream" in argv
    assert argv[-1] == "Say hello"


def test_goose_build_argv_resume_passes_session_name():
    from openakita.agents.cli_providers import goose

    with patch.object(goose, "_resolve_binary", return_value="/usr/bin/goose"):
        argv = PROVIDERS[CliProviderId.GOOSE].build_argv(
            _request(_profile(CliProviderId.GOOSE), resume_id="goose-sess-12")
        )

    assert "--name" in argv
    assert argv[argv.index("--name") + 1] == "goose-sess-12"
    assert "--resume" in argv


def test_goose_build_argv_write_mode():
    """Goose has no dedicated skip-permissions flag; verify WRITE doesn't crash."""
    from openakita.agents.cli_providers import goose

    profile = _profile(CliProviderId.GOOSE, CliPermissionMode.WRITE)
    with patch.object(goose, "_resolve_binary", return_value="/usr/bin/goose"):
        argv = PROVIDERS[CliProviderId.GOOSE].build_argv(_request(profile))

    assert "--stream" in argv
    assert argv[-1] == "Say hello"


def test_goose_missing_binary_raises_dependency_error():
    from openakita.agents.cli_providers import goose
    from openakita.tools.errors import ErrorType, ToolError

    with patch.object(goose, "_resolve_binary", return_value=None), \
            pytest.raises(ToolError) as exc_info:
        PROVIDERS[CliProviderId.GOOSE].build_argv(
            _request(_profile(CliProviderId.GOOSE))
        )

    assert exc_info.value.error_type == ErrorType.DEPENDENCY


def test_goose_session_root():
    from openakita.agents.cli_providers import goose

    assert Path.home() / ".local" / "share" / "goose" / "sessions" == goose.SESSION_ROOT


@pytest.mark.asyncio
async def test_goose_run_cancelled_returns_cancelled_reason(tmp_path):
    from openakita.agents.cli_providers import goose

    profile = _profile(CliProviderId.GOOSE)
    req = _request(profile, cwd=tmp_path)
    req.cancelled.set()
    argv = ["sh", "-c", "for i in $(seq 1 100); do echo '{}'; sleep 0.1; done"]

    result = await goose.PROVIDER.run(req, argv, env={}, on_spawn=lambda _: None)
    assert result.exit_reason == ExitReason.CANCELLED


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

def test_opencode_registered():
    assert CliProviderId.OPENCODE in PROVIDERS


def test_opencode_build_argv_base():
    from openakita.agents.cli_providers import opencode

    with patch.object(opencode, "_resolve_binary", return_value="/usr/bin/opencode"):
        argv = PROVIDERS[CliProviderId.OPENCODE].build_argv(
            _request(_profile(CliProviderId.OPENCODE))
        )

    assert argv[0] == "/usr/bin/opencode"
    assert "run" in argv
    assert "--json" in argv
    assert argv[-1] == "Say hello"


def test_opencode_build_argv_write_mode_adds_yes():
    """In write mode OpenCode auto-approves tool calls with --yes."""
    from openakita.agents.cli_providers import opencode

    profile = _profile(CliProviderId.OPENCODE, CliPermissionMode.WRITE)
    with patch.object(opencode, "_resolve_binary", return_value="/usr/bin/opencode"):
        argv = PROVIDERS[CliProviderId.OPENCODE].build_argv(_request(profile))

    assert "--yes" in argv


def test_opencode_build_argv_resume():
    from openakita.agents.cli_providers import opencode

    with patch.object(opencode, "_resolve_binary", return_value="/usr/bin/opencode"):
        argv = PROVIDERS[CliProviderId.OPENCODE].build_argv(
            _request(_profile(CliProviderId.OPENCODE), resume_id="oc-s1")
        )

    assert "--continue" in argv
    assert "--session" in argv
    assert argv[argv.index("--session") + 1] == "oc-s1"


def test_opencode_missing_binary_raises_dependency_error():
    from openakita.agents.cli_providers import opencode
    from openakita.tools.errors import ErrorType, ToolError

    with patch.object(opencode, "_resolve_binary", return_value=None), \
            pytest.raises(ToolError) as exc_info:
        PROVIDERS[CliProviderId.OPENCODE].build_argv(
            _request(_profile(CliProviderId.OPENCODE))
        )

    assert exc_info.value.error_type == ErrorType.DEPENDENCY


def test_opencode_session_root():
    from openakita.agents.cli_providers import opencode

    assert Path.home() / ".local" / "share" / "opencode" / "sessions" == opencode.SESSION_ROOT


@pytest.mark.asyncio
async def test_opencode_run_end_to_end(tmp_path):
    from openakita.agents.cli_providers import opencode

    events = [
        {"type": "session_init", "session_id": "oc-s1"},
        {"type": "text", "content": "Hi "},
        {"type": "tool_use", "name": "Read"},
        {"type": "text", "content": "there."},
        {"type": "end", "usage": {"input_tokens": 4, "output_tokens": 2}},
    ]
    script = "\n".join("echo " + json.dumps(json.dumps(e)) for e in events)
    argv = ["sh", "-c", script]

    result = await opencode.PROVIDER.run(
        _request(_profile(CliProviderId.OPENCODE), cwd=tmp_path),
        argv, env={}, on_spawn=lambda _: None,
    )
    assert result.session_id == "oc-s1"
    assert result.final_text == "Hi there."
    assert result.tools_used == ["Read"]
    assert result.exit_reason == ExitReason.COMPLETED
