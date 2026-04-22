# tests/unit/test_cli_detector.py
from __future__ import annotations

import asyncio

import pytest

from openakita.agents.cli_detector import (
    CliProviderId,
    DetectedCli,
    discover_all,
    reset_cache,
)


def test_provider_ids_are_stable_strings():
    assert CliProviderId.CLAUDE_CODE.value == "claude_code"
    assert CliProviderId.CODEX.value == "codex"
    assert CliProviderId.OPENCODE.value == "opencode"
    assert CliProviderId.GEMINI.value == "gemini"
    assert CliProviderId.COPILOT.value == "copilot"
    assert CliProviderId.DROID.value == "droid"
    assert CliProviderId.CURSOR.value == "cursor"
    assert CliProviderId.QWEN.value == "qwen"
    assert CliProviderId.GOOSE.value == "goose"


def test_detected_cli_is_frozen():
    d = DetectedCli(
        provider_id=CliProviderId.CLAUDE_CODE,
        binary_name="claude",
        binary_path="/usr/bin/claude",
        version="1.0.0",
        error=None,
    )
    with pytest.raises(Exception):
        d.binary_path = "/other"  # frozen


@pytest.mark.asyncio
async def test_discover_all_returns_entry_per_provider(monkeypatch):
    reset_cache()
    monkeypatch.setattr(
        "openakita.agents.cli_detector.which_command",
        lambda name: None,
    )
    result = await discover_all()
    assert set(result.keys()) == set(CliProviderId)
    for entry in result.values():
        assert entry.binary_path is None
        assert entry.error == "not found"
