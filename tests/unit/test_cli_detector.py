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
    with pytest.raises(Exception):  # noqa: B017
        d.binary_path = "/other"  # frozen — exact exception type is an impl detail


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


class _FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", rc: int = 0):
        self._out, self._err, self.returncode = stdout, stderr, rc

    async def communicate(self):
        return self._out, self._err

    def kill(self):
        self.returncode = -9


@pytest.mark.asyncio
async def test_discover_all_success_path(monkeypatch):
    reset_cache()
    monkeypatch.setattr(
        "openakita.agents.cli_detector.which_command",
        lambda name: f"/usr/bin/{name}",
    )

    async def fake_exec(*args, **kwargs):
        return _FakeProc(stdout=f"{args[0]} 1.2.3\n".encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await discover_all()
    assert result[CliProviderId.CLAUDE_CODE].version == "/usr/bin/claude 1.2.3"
    assert result[CliProviderId.CODEX].error is None
    assert result[CliProviderId.GOOSE].binary_path == "/usr/bin/goose"


@pytest.mark.asyncio
async def test_discover_all_caches_under_ttl(monkeypatch):
    reset_cache()
    calls = {"n": 0}

    def fake_which(name: str):
        calls["n"] += 1
        return None

    monkeypatch.setattr(
        "openakita.agents.cli_detector.which_command", fake_which
    )
    await discover_all()
    await discover_all()  # cached — no new calls
    assert calls["n"] == len(CliProviderId)  # one sweep only
