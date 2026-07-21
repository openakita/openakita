"""Tests for runtime/state_graph/guards/tool_filters."""

from __future__ import annotations

import json

import pytest

from openakita.runtime.state_graph.guards import tool_filters as tf


@pytest.fixture
def legacy_aliases():
    """Pin parity against the legacy private aliases in reasoning_engine."""
    from openakita.core import _reasoning_engine_legacy as re_module

    return {
        "get_mode_ruleset": re_module._get_mode_ruleset,
        "filter_mode": re_module._filter_tools_by_mode,
        "is_shell_write": re_module._is_shell_write_command,
        "should_block": re_module._should_block_tool,
        "shell_pat": re_module._SHELL_WRITE_PATTERNS,
    }


def test_get_mode_ruleset_dispatch(legacy_aliases) -> None:
    for mode in ("plan", "ask", "coordinator", "agent", "unknown"):
        assert tf.get_mode_ruleset(mode) is legacy_aliases["get_mode_ruleset"](mode)


def test_filter_tools_by_mode_agent_passes_through() -> None:
    tools = [{"name": "run_shell"}, {"name": "write_file"}]
    assert tf.filter_tools_by_mode(tools, "agent") is tools


def test_filter_tools_by_mode_ask_drops_writes(legacy_aliases) -> None:
    tools = [
        {"name": "run_shell"},
        {"name": "write_file"},
        {"name": "read_file"},
        {"name": "ask_user"},
    ]
    new_v2 = tf.filter_tools_by_mode(list(tools), "ask")
    new_v1 = legacy_aliases["filter_mode"](list(tools), "ask")
    assert new_v2 == new_v1


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("ls -la", False),
        ("cat file.txt", False),
        ("rm -rf /tmp/x", True),
        ("echo hi > /tmp/out", True),
        ("rm important.txt", True),
        ("pip install requests", True),
        ("Remove-Item file", True),
        ("ls > out.txt", True),
    ],
)
def test_is_shell_write_behaviour(cmd: str, expected: bool) -> None:
    assert tf.is_shell_write_command(cmd) is expected


def test_should_block_tool_returns_none_when_unrestricted() -> None:
    assert tf.should_block_tool("run_shell", {"command": "ls"}, None, "agent") is None


def test_should_block_tool_blocks_unknown_tool_in_mode() -> None:
    msg = tf.should_block_tool("write_file", {}, {"read_file"}, "ask")
    assert msg is not None
    assert "write_file" in msg
    assert "ask" in msg


def test_should_block_tool_allows_readonly_shell_in_ask() -> None:
    assert tf.should_block_tool("run_shell", {"command": "ls -la"}, {"run_shell"}, "ask") is None


def test_should_block_tool_blocks_write_shell_in_ask() -> None:
    msg = tf.should_block_tool("run_shell", {"command": "rm -rf /tmp"}, {"run_shell"}, "ask")
    assert msg is not None
    assert "ask" in msg


def test_should_block_tool_parses_json_string_input() -> None:
    args = json.dumps({"command": "rm file"})
    msg = tf.should_block_tool("run_shell", args, {"run_shell"}, "plan")
    assert msg is not None


def test_legacy_aliases_are_same_objects(legacy_aliases) -> None:
    assert legacy_aliases["shell_pat"] is tf.SHELL_WRITE_PATTERNS
    assert legacy_aliases["get_mode_ruleset"] is tf.get_mode_ruleset
    assert legacy_aliases["filter_mode"] is tf.filter_tools_by_mode
    assert legacy_aliases["is_shell_write"] is tf.is_shell_write_command
    assert legacy_aliases["should_block"] is tf.should_block_tool
