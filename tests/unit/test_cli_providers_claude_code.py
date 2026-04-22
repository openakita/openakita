# tests/unit/test_cli_providers_claude_code.py
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from openakita.agents.cli_providers import PROVIDERS
from openakita.agents.cli_detector import CliProviderId
from openakita.agents.cli_runner import CliRunRequest, ExitReason
from openakita.agents.profile import (
    AgentProfile,
    AgentType,
    CliPermissionMode,
)


def _make_profile(**overrides) -> AgentProfile:
    base = dict(
        id="cc-pair",
        name="Claude Code Pair",
        type=AgentType.EXTERNAL_CLI,
        cli_provider_id=CliProviderId.CLAUDE_CODE,
        cli_permission_mode=CliPermissionMode.PLAN,
    )
    base.update(overrides)
    return AgentProfile(**base)


def _make_request(profile, *, cwd=Path("/tmp"), resume_id=None,
                  system_prompt_extra="", mcp_servers=()):
    return CliRunRequest(
        message="Hello",
        resume_id=resume_id,
        profile=profile,
        cwd=cwd,
        cancelled=asyncio.Event(),
        session=None,
        system_prompt_extra=system_prompt_extra,
        mcp_servers=mcp_servers,
    )


def test_provider_registered_under_claude_code():
    assert CliProviderId.CLAUDE_CODE in PROVIDERS


def test_build_argv_base_flags_for_plan_mode():
    from openakita.agents.cli_providers import claude_code

    with patch.object(claude_code, "_resolve_binary", return_value="/usr/bin/claude"):
        argv = PROVIDERS[CliProviderId.CLAUDE_CODE].build_argv(
            _make_request(_make_profile())
        )

    assert argv[0] == "/usr/bin/claude"
    assert "--print" in argv
    assert "--verbose" in argv
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    # plan mode → no --dangerously-skip-permissions
    assert "--dangerously-skip-permissions" not in argv
    # No resume on first turn
    assert "--resume" not in argv
    # Message is the trailing positional
    assert argv[-1] == "Hello"


def test_build_argv_write_mode_adds_skip_permissions():
    from openakita.agents.cli_providers import claude_code

    profile = _make_profile(cli_permission_mode=CliPermissionMode.WRITE)
    with patch.object(claude_code, "_resolve_binary", return_value="/usr/bin/claude"):
        argv = PROVIDERS[CliProviderId.CLAUDE_CODE].build_argv(_make_request(profile))

    assert "--dangerously-skip-permissions" in argv


def test_build_argv_resume_includes_session_id():
    from openakita.agents.cli_providers import claude_code

    with patch.object(claude_code, "_resolve_binary", return_value="/usr/bin/claude"):
        argv = PROVIDERS[CliProviderId.CLAUDE_CODE].build_argv(
            _make_request(_make_profile(), resume_id="sess-abc")
        )

    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "sess-abc"


def test_build_argv_first_turn_injects_system_prompt():
    from openakita.agents.cli_providers import claude_code

    with patch.object(claude_code, "_resolve_binary", return_value="/usr/bin/claude"):
        argv = PROVIDERS[CliProviderId.CLAUDE_CODE].build_argv(
            _make_request(_make_profile(), system_prompt_extra="Org blackboard: X")
        )

    assert "--system-prompt" in argv
    assert argv[argv.index("--system-prompt") + 1] == "Org blackboard: X"


def test_build_argv_resume_turn_still_injects_system_prompt():
    """Claude Code >= 2.x honours --system-prompt on --resume (verified against
    `claude --help` -- no mutual-exclusion caveat). The adapter passes it
    whenever `system_prompt_extra` is set so fresh org blackboard facts reach
    the model on every turn."""
    from openakita.agents.cli_providers import claude_code

    with patch.object(claude_code, "_resolve_binary", return_value="/usr/bin/claude"):
        argv = PROVIDERS[CliProviderId.CLAUDE_CODE].build_argv(
            _make_request(
                _make_profile(),
                resume_id="sess-abc",
                system_prompt_extra="Org blackboard: refreshed",
            )
        )

    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "sess-abc"
    assert "--system-prompt" in argv
    assert argv[argv.index("--system-prompt") + 1] == "Org blackboard: refreshed"


def test_build_argv_raises_dependency_error_when_binary_missing():
    from openakita.agents.cli_providers import claude_code
    from openakita.tools.errors import ErrorType, ToolError

    with patch.object(claude_code, "_resolve_binary", return_value=None):
        with pytest.raises(ToolError) as ex:
            PROVIDERS[CliProviderId.CLAUDE_CODE].build_argv(_make_request(_make_profile()))

    assert ex.value.error_type == ErrorType.DEPENDENCY


def test_session_root_is_claude_projects():
    from openakita.agents.cli_providers import claude_code

    assert claude_code.SESSION_ROOT == Path.home() / ".claude" / "projects"


def test_parse_stream_line_init_extracts_session_id():
    from openakita.agents.cli_providers.claude_code import _parse_stream_line

    line = json.dumps({
        "type": "system",
        "subtype": "init",
        "session_id": "sess-xyz-123",
    }).encode() + b"\n"
    ev = _parse_stream_line(line)

    assert ev is not None
    assert ev.kind == "init"
    assert ev.session_id == "sess-xyz-123"


def test_parse_stream_line_assistant_text_accumulates():
    from openakita.agents.cli_providers.claude_code import _parse_stream_line

    line = json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world"},
            ],
        },
    }).encode() + b"\n"
    ev = _parse_stream_line(line)

    assert ev is not None
    assert ev.kind == "assistant_text"
    assert ev.text == "Hello world"


def test_parse_stream_line_tool_use_extracts_name():
    from openakita.agents.cli_providers.claude_code import _parse_stream_line

    line = json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x"}},
            ],
        },
    }).encode() + b"\n"
    ev = _parse_stream_line(line)

    assert ev is not None
    assert ev.kind == "tool_use"
    assert ev.tool_name == "Edit"


def test_parse_stream_line_result_extracts_usage():
    from openakita.agents.cli_providers.claude_code import _parse_stream_line

    line = json.dumps({
        "type": "result",
        "usage": {"input_tokens": 100, "output_tokens": 42},
        "is_error": False,
    }).encode() + b"\n"
    ev = _parse_stream_line(line)

    assert ev is not None
    assert ev.kind == "result"
    assert ev.input_tokens == 100
    assert ev.output_tokens == 42


def test_parse_stream_line_error_result_flags_error():
    from openakita.agents.cli_providers.claude_code import _parse_stream_line

    line = json.dumps({
        "type": "result",
        "is_error": True,
        "result": "rate limit exceeded",
    }).encode() + b"\n"
    ev = _parse_stream_line(line)

    assert ev is not None
    assert ev.kind == "error"
    assert "rate limit" in ev.error_message


def test_parse_stream_line_invalid_json_returns_none():
    from openakita.agents.cli_providers.claude_code import _parse_stream_line

    assert _parse_stream_line(b"not-json\n") is None
    assert _parse_stream_line(b"\n") is None
    assert _parse_stream_line(b"") is None
