from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openakita.agents.cli_runner import CliRunRequest, ExitReason
from openakita.agents.profile import AgentProfile


def _request(progress):
    return CliRunRequest(
        message="hello",
        resume_id=None,
        profile=AgentProfile(id="p", name="P"),
        cwd=Path.cwd(),
        cancelled=asyncio.Event(),
        session=None,
        system_prompt_extra="",
        on_progress=progress,
    )


@pytest.mark.asyncio
async def test_claude_provider_emits_text_and_tool_progress(monkeypatch):
    from openakita.agents.cli_providers.claude_code import PROVIDER

    events = []
    stderr_callbacks = []

    async def progress(kind, **payload):
        events.append((kind, payload))

    async def fake_stream(argv, env, cwd, cancelled, *, on_spawn, on_stderr=None):
        assert on_stderr is not None
        stderr_callbacks.append(on_stderr)
        yield b'{"type":"assistant","message":{"content":[{"type":"text","text":"Working"}]}}\n'
        yield b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit"}]}}\n'
        yield b'{"type":"result","usage":{"input_tokens":1,"output_tokens":2}}\n'

    monkeypatch.setattr(
        "openakita.agents.cli_providers.claude_code.stream_cli_subprocess",
        fake_stream,
    )
    monkeypatch.setattr(
        "openakita.agents.cli_providers.claude_code._git_diff_names",
        lambda cwd: set(),
    )

    result = await PROVIDER.run(
        _request(progress),
        ["claude"],
        {},
        on_spawn=lambda proc: None,
    )

    assert result.final_text == "Working"
    assert ("assistant_text", {"text": "Working"}) in events
    assert ("tool_use", {"tool_name": "Edit"}) in events
    assert len(stderr_callbacks) == 1
    assert callable(stderr_callbacks[0])


@pytest.mark.asyncio
async def test_claude_provider_ignores_progress_callback_runtime_error(monkeypatch):
    from openakita.agents.cli_providers.claude_code import PROVIDER

    stderr_callbacks = []

    async def progress(kind, **payload):
        raise RuntimeError("progress sink failed")

    async def fake_stream(argv, env, cwd, cancelled, *, on_spawn, on_stderr=None):
        assert on_stderr is not None
        stderr_callbacks.append(on_stderr)
        yield b'{"type":"assistant","message":{"content":[{"type":"text","text":"Working"}]}}\n'
        yield b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit"}]}}\n'
        yield b'{"type":"result","usage":{"input_tokens":1,"output_tokens":2}}\n'

    monkeypatch.setattr(
        "openakita.agents.cli_providers.claude_code.stream_cli_subprocess",
        fake_stream,
    )
    monkeypatch.setattr(
        "openakita.agents.cli_providers.claude_code._git_diff_names",
        lambda cwd: set(),
    )

    result = await PROVIDER.run(
        _request(progress),
        ["claude"],
        {},
        on_spawn=lambda proc: None,
    )

    assert result.exit_reason == ExitReason.COMPLETED
    assert result.errored is False
    assert result.final_text == "Working"
    assert result.tools_used == ["Edit"]
    assert len(stderr_callbacks) == 1


@pytest.mark.asyncio
async def test_claude_provider_propagates_progress_callback_cancelled_error(monkeypatch):
    from openakita.agents.cli_providers.claude_code import PROVIDER

    stderr_callbacks = []

    async def progress(kind, **payload):
        raise asyncio.CancelledError

    async def fake_stream(argv, env, cwd, cancelled, *, on_spawn, on_stderr=None):
        assert on_stderr is not None
        stderr_callbacks.append(on_stderr)
        yield b'{"type":"assistant","message":{"content":[{"type":"text","text":"Working"}]}}\n'
        yield b'{"type":"result","usage":{"input_tokens":1,"output_tokens":2}}\n'

    monkeypatch.setattr(
        "openakita.agents.cli_providers.claude_code.stream_cli_subprocess",
        fake_stream,
    )
    monkeypatch.setattr(
        "openakita.agents.cli_providers.claude_code._git_diff_names",
        lambda cwd: set(),
    )

    with pytest.raises(asyncio.CancelledError):
        await PROVIDER.run(
            _request(progress),
            ["claude"],
            {},
            on_spawn=lambda proc: None,
        )

    assert len(stderr_callbacks) == 1


@pytest.mark.asyncio
async def test_codex_provider_emits_text_and_tool_progress(monkeypatch):
    from openakita.agents.cli_providers.codex import PROVIDER

    events = []
    stderr_callbacks = []

    async def progress(kind, **payload):
        events.append((kind, payload))

    async def fake_stream(argv, env, cwd, cancelled, *, on_spawn, on_stderr=None):
        assert on_stderr is not None
        stderr_callbacks.append(on_stderr)
        yield b'{"type":"assistant_delta","text":"Working"}\n'
        yield b'{"type":"tool_call","name":"shell"}\n'
        yield b'{"type":"turn_end","usage":{"input_tokens":1,"output_tokens":2}}\n'

    monkeypatch.setattr(
        "openakita.agents.cli_providers.codex.stream_cli_subprocess",
        fake_stream,
    )

    result = await PROVIDER.run(
        _request(progress),
        ["codex"],
        {},
        on_spawn=lambda proc: None,
    )

    assert result.final_text == "Working"
    assert ("assistant_text", {"text": "Working"}) in events
    assert ("tool_use", {"tool_name": "shell"}) in events
    assert len(stderr_callbacks) == 1
    assert callable(stderr_callbacks[0])


@pytest.mark.asyncio
async def test_codex_provider_ignores_progress_callback_runtime_error(monkeypatch):
    from openakita.agents.cli_providers.codex import PROVIDER

    stderr_callbacks = []

    async def progress(kind, **payload):
        raise RuntimeError("progress sink failed")

    async def fake_stream(argv, env, cwd, cancelled, *, on_spawn, on_stderr=None):
        assert on_stderr is not None
        stderr_callbacks.append(on_stderr)
        yield b'{"type":"assistant_delta","text":"Working"}\n'
        yield b'{"type":"tool_call","name":"shell"}\n'
        yield b'{"type":"turn_end","usage":{"input_tokens":1,"output_tokens":2}}\n'

    monkeypatch.setattr(
        "openakita.agents.cli_providers.codex.stream_cli_subprocess",
        fake_stream,
    )

    result = await PROVIDER.run(
        _request(progress),
        ["codex"],
        {},
        on_spawn=lambda proc: None,
    )

    assert result.exit_reason == ExitReason.COMPLETED
    assert result.errored is False
    assert result.final_text == "Working"
    assert result.tools_used == ["shell"]
    assert len(stderr_callbacks) == 1


@pytest.mark.asyncio
async def test_codex_provider_propagates_progress_callback_cancelled_error(monkeypatch):
    from openakita.agents.cli_providers.codex import PROVIDER

    stderr_callbacks = []

    async def progress(kind, **payload):
        raise asyncio.CancelledError

    async def fake_stream(argv, env, cwd, cancelled, *, on_spawn, on_stderr=None):
        assert on_stderr is not None
        stderr_callbacks.append(on_stderr)
        yield b'{"type":"assistant_delta","text":"Working"}\n'
        yield b'{"type":"turn_end","usage":{"input_tokens":1,"output_tokens":2}}\n'

    monkeypatch.setattr(
        "openakita.agents.cli_providers.codex.stream_cli_subprocess",
        fake_stream,
    )

    with pytest.raises(asyncio.CancelledError):
        await PROVIDER.run(
            _request(progress),
            ["codex"],
            {},
            on_spawn=lambda proc: None,
        )

    assert len(stderr_callbacks) == 1
