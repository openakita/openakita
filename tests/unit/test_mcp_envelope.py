"""Unit tests for the MCP tool result envelope.

Mirrors how `web_fetch` emits `[OPENAKITA_SOURCE]`: the chat route can
parse the marker into a structured event without scraping prose.
"""

from __future__ import annotations

import json

from openakita.tools.handlers.mcp import _build_mcp_envelope


def test_envelope_marks_success_with_server_and_tool():
    line = _build_mcp_envelope(
        status="ok",
        server="github",
        tool="list_repos",
        auto_connected=True,
        reconnected=False,
    )
    assert line.startswith("[OPENAKITA_MCP] ")
    payload = json.loads(line[len("[OPENAKITA_MCP] "):])
    assert payload == {
        "status": "ok",
        "server": "github",
        "tool": "list_repos",
        "auto_connected": True,
        "reconnected": False,
    }


def test_envelope_includes_error_for_failures():
    line = _build_mcp_envelope(
        status="error",
        server="github",
        tool="list_repos",
        error="connection refused",
    )
    payload = json.loads(line[len("[OPENAKITA_MCP] "):])
    assert payload["status"] == "error"
    assert payload["error"] == "connection refused"
    assert payload["auto_connected"] is False


def test_envelope_truncates_overly_long_errors():
    long = "x" * 1000
    line = _build_mcp_envelope(status="error", server="s", tool="t", error=long)
    payload = json.loads(line[len("[OPENAKITA_MCP] "):])
    assert len(payload["error"]) == 400
