"""Tests that GET /api/config/extensions surfaces CLI providers."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openakita.api.routes.config import list_extensions


@pytest.mark.asyncio
async def test_list_extensions_includes_ai_agent_category():
    from openakita.agents.cli_detector import CliProviderId, DetectedCli

    fake_detected = {
        CliProviderId.CLAUDE_CODE: DetectedCli(
            provider_id=CliProviderId.CLAUDE_CODE,
            binary_name="claude",
            binary_path="/usr/local/bin/claude",
            version="1.2.3",
            error=None,
        ),
        CliProviderId.GOOSE: DetectedCli(
            provider_id=CliProviderId.GOOSE,
            binary_name="goose",
            binary_path=None,
            version=None,
            error="not found",
        ),
    }

    with patch(
        "openakita.agents.cli_detector.discover_all",
        new=AsyncMock(return_value=fake_detected),
    ):
        payload = await list_extensions()

    ai_entries = [e for e in payload["extensions"] if e["category"] == "AI Agent"]
    ids = {e["id"] for e in ai_entries}
    # All 9 providers should appear (installed or not).
    assert {"claude_code", "codex", "opencode", "gemini", "copilot",
            "droid", "cursor", "qwen", "goose"} <= ids

    claude = next(e for e in ai_entries if e["id"] == "claude_code")
    assert claude["installed"] is True
    assert claude["path"] == "/usr/local/bin/claude"
    assert claude["cli_provider_id"] == "claude_code"
    assert claude["setup_cmd"] is None
    assert "claude-code" in claude["install_cmd"] or "@anthropic-ai" in claude["install_cmd"]

    goose = next(e for e in ai_entries if e["id"] == "goose")
    assert goose["installed"] is False
    assert goose["path"] is None


@pytest.mark.asyncio
async def test_list_extensions_still_returns_existing_web_desktop_rows():
    """Regression — opencli and cli-anything must still appear."""
    payload = await list_extensions()
    ids = {e["id"] for e in payload["extensions"]}
    assert "opencli" in ids
    assert "cli-anything" in ids
