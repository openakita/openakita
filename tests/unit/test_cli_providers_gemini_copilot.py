from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from openakita.agents.cli_providers import PROVIDERS
from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_runner import CliRunRequest, ExitReason, ProviderRunResult
from openakita.agents.profile import AgentProfile, AgentType, CliPermissionMode


def _profile(provider_id, permission=CliPermissionMode.PLAN) -> AgentProfile:
    return AgentProfile(
        id=f"{provider_id.value}-agent",
        name=f"{provider_id.value} agent",
        type=AgentType.EXTERNAL_CLI,
        cli_provider_id=provider_id,
        cli_permission_mode=permission,
    )


def _request(profile, *, cwd=Path("/tmp"), resume_id=None):
    return CliRunRequest(
        message="Summarize this folder",
        resume_id=resume_id,
        profile=profile,
        cwd=cwd,
        cancelled=asyncio.Event(),
        session=None,
        system_prompt_extra="",
    )


# --- Gemini -------------------------------------------------------------------

def test_gemini_registered():
    assert CliProviderId.GEMINI in PROVIDERS


def test_gemini_build_argv_base():
    from openakita.agents.cli_providers import gemini

    with patch.object(gemini, "_resolve_binary", return_value="/usr/bin/gemini"):
        argv = PROVIDERS[CliProviderId.GEMINI].build_argv(_request(_profile(CliProviderId.GEMINI)))

    assert argv[0] == "/usr/bin/gemini"
    # Gemini CLI emits a single JSON object with --output-format json.
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--prompt" in argv
    assert argv[argv.index("--prompt") + 1] == "Summarize this folder"


def test_gemini_build_argv_write_mode_adds_yolo():
    from openakita.agents.cli_providers import gemini

    profile = _profile(CliProviderId.GEMINI, CliPermissionMode.WRITE)
    with patch.object(gemini, "_resolve_binary", return_value="/usr/bin/gemini"):
        argv = PROVIDERS[CliProviderId.GEMINI].build_argv(_request(profile))

    assert "--yolo" in argv


def test_gemini_build_argv_resume():
    from openakita.agents.cli_providers import gemini

    with patch.object(gemini, "_resolve_binary", return_value="/usr/bin/gemini"):
        argv = PROVIDERS[CliProviderId.GEMINI].build_argv(
            _request(_profile(CliProviderId.GEMINI), resume_id="gem-s9")
        )

    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "gem-s9"


def test_gemini_session_root():
    from openakita.agents.cli_providers import gemini

    assert gemini.SESSION_ROOT == Path.home() / ".gemini" / "sessions"


@pytest.mark.asyncio
async def test_gemini_run_parses_single_json_blob(tmp_path):
    from openakita.agents.cli_providers import gemini

    blob = {
        "session_id": "gem-s1",
        "response": {
            "text": "Summary complete.",
            "tool_calls": [{"name": "Read"}],
        },
        "usage": {"input_tokens": 15, "output_tokens": 4},
    }
    # Gemini emits the blob on stdout as one JSON object followed by EOF.
    argv = ["sh", "-c", "echo " + json.dumps(json.dumps(blob))]

    result = await gemini.PROVIDER.run(
        _request(_profile(CliProviderId.GEMINI), cwd=tmp_path),
        argv, env={}, on_spawn=lambda _: None,
    )
    assert isinstance(result, ProviderRunResult)
    assert result.session_id == "gem-s1"
    assert result.final_text == "Summary complete."
    assert result.tools_used == ["Read"]
    assert result.input_tokens == 15
    assert result.output_tokens == 4
    assert result.exit_reason == ExitReason.COMPLETED


# --- Copilot ------------------------------------------------------------------

def test_copilot_registered():
    assert CliProviderId.COPILOT in PROVIDERS


def test_copilot_build_argv_base():
    from openakita.agents.cli_providers import copilot

    with patch.object(copilot, "_resolve_binary", return_value="/usr/bin/copilot"):
        argv = PROVIDERS[CliProviderId.COPILOT].build_argv(_request(_profile(CliProviderId.COPILOT)))

    assert argv[0] == "/usr/bin/copilot"
    assert "--no-color" in argv
    # Message passed on stdin; argv uses `--input-file -`
    assert "--input-file" in argv
    assert argv[argv.index("--input-file") + 1] == "-"


def test_copilot_session_root():
    from openakita.agents.cli_providers import copilot

    assert copilot.SESSION_ROOT == Path.home() / ".copilot" / "sessions"


def test_copilot_ansi_strip():
    from openakita.agents.cli_providers.copilot import _strip_ansi

    raw = "\x1b[31mHello\x1b[0m world"
    assert _strip_ansi(raw) == "Hello world"


@pytest.mark.asyncio
async def test_copilot_run_strips_ansi_and_extracts_tools(tmp_path):
    from openakita.agents.cli_providers import copilot

    # Copilot emits tool markers as ANSI-escaped "[tool: <name>]" lines.
    lines = [
        "\x1b[34m[tool: read_file]\x1b[0m",
        "\x1b[0mRead complete.\x1b[0m",
        "\x1b[32m[session: cp-s2]\x1b[0m",
    ]
    argv = ["sh", "-c", "; ".join("printf '" + ln + "\\n'" for ln in lines)]

    # Copilot receives message on stdin; we skip the stdin-write path in this
    # test by passing an already-wired-up message via env COPILOT_MESSAGE —
    # the adapter reads this as a fallback when argv contains '--input-file -'
    # *and* the caller has pre-staged a stdin-pipe. The unit test exercises
    # the parser, not the stdin write; end-to-end stdin-write is verified in
    # the integration suite.
    req = _request(_profile(CliProviderId.COPILOT), cwd=tmp_path)
    result = await copilot.PROVIDER.run(
        req, argv, env={"COPILOT_SUPPRESS_STDIN": "1"}, on_spawn=lambda _: None,
    )
    assert "Read complete." in result.final_text
    assert result.tools_used == ["read_file"]
    assert result.session_id == "cp-s2"
    assert result.exit_reason == ExitReason.COMPLETED
