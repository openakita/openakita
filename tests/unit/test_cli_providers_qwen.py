# tests/unit/test_cli_providers_qwen.py
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


def _profile(**overrides) -> AgentProfile:
    base = {
        "id": "qwen-agent",
        "name": "Qwen",
        "type": AgentType.EXTERNAL_CLI,
        "cli_provider_id": CliProviderId.QWEN,
        "cli_permission_mode": CliPermissionMode.PLAN,
    }
    base.update(overrides)
    return AgentProfile(**base)


def _request(profile, *, cwd=Path("/tmp"), resume_id=None):
    return CliRunRequest(
        message="Explain the repo",
        resume_id=resume_id,
        profile=profile,
        cwd=cwd,
        cancelled=asyncio.Event(),
        session=None,
        system_prompt_extra="",
    )


def test_qwen_registered():
    assert CliProviderId.QWEN in PROVIDERS


def test_qwen_build_argv_base():
    from openakita.agents.cli_providers import qwen

    with patch.object(qwen, "_resolve_binary", return_value="/usr/bin/qwen"):
        argv = PROVIDERS[CliProviderId.QWEN].build_argv(_request(_profile()))

    assert argv[0] == "/usr/bin/qwen"
    assert "chat" in argv
    assert "--json" in argv
    assert argv[-1] == "Explain the repo"


def test_qwen_write_mode_adds_skip_confirm():
    from openakita.agents.cli_providers import qwen

    profile = _profile(cli_permission_mode=CliPermissionMode.WRITE)
    with patch.object(qwen, "_resolve_binary", return_value="/usr/bin/qwen"):
        argv = PROVIDERS[CliProviderId.QWEN].build_argv(_request(profile))

    assert "--skip-confirm" in argv


def test_qwen_resume():
    from openakita.agents.cli_providers import qwen

    with patch.object(qwen, "_resolve_binary", return_value="/usr/bin/qwen"):
        argv = PROVIDERS[CliProviderId.QWEN].build_argv(
            _request(_profile(), resume_id="qwen-s3")
        )

    assert "--session" in argv
    assert argv[argv.index("--session") + 1] == "qwen-s3"


def test_qwen_session_root():
    from openakita.agents.cli_providers import qwen

    assert Path.home() / ".qwen" / "sessions" == qwen.SESSION_ROOT


@pytest.mark.asyncio
async def test_qwen_run_end_to_end(tmp_path):
    from openakita.agents.cli_providers import qwen

    events = [
        {"type": "session", "id": "qwen-s1"},
        {"type": "delta", "text": "Analyzing "},
        {"type": "tool", "name": "read_file"},
        {"type": "delta", "text": "repo."},
        {"type": "final", "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
    ]
    script = "\n".join("echo " + json.dumps(json.dumps(e)) for e in events)
    argv = ["sh", "-c", script]

    result = await qwen.PROVIDER.run(
        _request(_profile(), cwd=tmp_path),
        argv, env={}, on_spawn=lambda _: None,
    )
    assert result.session_id == "qwen-s1"
    assert result.final_text == "Analyzing repo."
    assert result.tools_used == ["read_file"]
    assert result.input_tokens == 12
    assert result.output_tokens == 3
    assert result.exit_reason == ExitReason.COMPLETED


@pytest.mark.asyncio
async def test_qwen_run_marks_error_on_failed_exit(tmp_path):
    from openakita.agents.cli_providers import qwen

    # Emit one invalid JSON line and exit 1.
    argv = ["sh", "-c", "echo '{\"type\":\"final\",\"error\":\"auth\"}'; exit 1"]
    result = await qwen.PROVIDER.run(
        _request(_profile(), cwd=tmp_path),
        argv, env={}, on_spawn=lambda _: None,
    )
    assert result.exit_reason == ExitReason.ERROR
    assert result.errored is True
