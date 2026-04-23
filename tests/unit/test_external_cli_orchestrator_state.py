from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.orchestrator import AgentOrchestrator
from openakita.agents.profile import AgentProfile, AgentType


@pytest.mark.asyncio
async def test_call_agent_raises_for_external_cli_error_result():
    profile = AgentProfile(
        id="claude-code-pair",
        name="Claude Code Pair",
        type=AgentType.EXTERNAL_CLI,
        cli_provider_id=CliProviderId.CLAUDE_CODE,
    )

    class FailingCliAgent:
        _agent_profile = profile
        agent_state = None

        async def chat_with_session(self, **kwargs):
            return {
                "text": "auth failed",
                "tools_used": [],
                "artifacts": [],
                "exit_reason": "error",
            }

    session = MagicMock()
    session.id = "sid-1"
    session.context.get_messages.return_value = []

    with pytest.raises(RuntimeError, match="external cli agent failed"):
        await AgentOrchestrator._call_agent(FailingCliAgent(), session, "work")


@pytest.mark.asyncio
async def test_call_agent_preserves_custom_agent_error_result_behavior():
    profile = AgentProfile(
        id="custom-helper",
        name="Custom Helper",
        type=AgentType.CUSTOM,
    )

    class CustomAgent:
        _agent_profile = profile
        agent_state = None

        async def chat_with_session(self, **kwargs):
            return {
                "text": "reported recoverable issue",
                "tools_used": [],
                "artifacts": [],
                "exit_reason": "error",
            }

    session = MagicMock()
    session.id = "sid-1"
    session.context.get_messages.return_value = []

    result = await AgentOrchestrator._call_agent(CustomAgent(), session, "work")

    assert "reported recoverable issue" in result


@pytest.mark.asyncio
async def test_sub_state_broadcast_includes_cli_metadata(monkeypatch):
    sent = []

    async def fake_broadcast(event, payload):
        sent.append((event, payload))

    monkeypatch.setattr(
        "openakita.api.routes.websocket.broadcast_event",
        fake_broadcast,
    )

    orch = AgentOrchestrator()
    orch._broadcast_sub_state_change(
        "sid:claude:1",
        "running",
        {
            "session_id": "sid",
            "chat_id": "sid",
            "agent_id": "claude-code-pair",
            "profile_id": "claude-code-pair",
            "name": "Claude Code Pair",
            "icon": "C",
            "agent_type": "external_cli",
            "cli_provider_id": "claude_code",
        },
    )
    await asyncio.sleep(0)

    assert sent[0][0] == "agents:sub_state"
    assert sent[0][1]["agent_type"] == "external_cli"
    assert sent[0][1]["cli_provider_id"] == "claude_code"


@pytest.mark.asyncio
async def test_handle_sub_agent_live_progress_updates_state_and_broadcasts_stream(monkeypatch):
    sent = []

    async def fake_broadcast(event, payload):
        sent.append((event, payload))

    monkeypatch.setattr(
        "openakita.api.routes.websocket.broadcast_event",
        fake_broadcast,
    )

    orch = AgentOrchestrator()
    orch._sub_agent_states["sid:claude:1"] = {
        "session_id": "sid",
        "chat_id": "sid",
        "agent_id": "claude-code-pair",
        "profile_id": "claude-code-pair",
        "name": "Claude Code Pair",
        "icon": "C",
        "status": "running",
        "live_entries": [],
    }

    await orch._handle_sub_agent_live_progress(
        "sid:claude:1",
        "append",
        {"kind": "thinking", "text": "reviewing", "ts_ms": 123},
    )

    assert orch._sub_agent_states["sid:claude:1"]["live_entries"] == [
        {"kind": "thinking", "text": "reviewing", "ts_ms": 123}
    ]
    assert sent == [
        (
            "agents:sub_stream",
            {
                "session_id": "sid",
                "chat_id": "sid",
                "agent_id": "claude-code-pair",
                "profile_id": "claude-code-pair",
                "name": "Claude Code Pair",
                "icon": "C",
                "status": "running",
                "op": "append",
                "entry": {"kind": "thinking", "text": "reviewing", "ts_ms": 123},
            },
        )
    ]


@pytest.mark.asyncio
async def test_progress_timeout_marks_external_cli_error_as_error_state(monkeypatch):
    profile = AgentProfile(
        id="claude-code-pair",
        name="Claude Code Pair",
        type=AgentType.EXTERNAL_CLI,
        cli_provider_id=CliProviderId.CLAUDE_CODE,
    )

    class ProfileStore:
        def get(self, profile_id):
            if profile_id == profile.id:
                return profile
            return None

    class Pool:
        async def get_or_create(self, session_id, requested_profile):
            return FailingCliAgent()

    class FailingCliAgent:
        _agent_profile = profile
        agent_state = None

        async def chat_with_session(self, **kwargs):
            return {
                "text": "auth failed",
                "tools_used": [],
                "artifacts": [],
                "exit_reason": "error",
            }

    sent = []

    async def fake_broadcast(event, payload):
        sent.append((event, payload))

    monkeypatch.setattr("openakita.agents.orchestrator.CHECK_INTERVAL", 0)
    monkeypatch.setattr(
        "openakita.api.routes.websocket.broadcast_event",
        fake_broadcast,
    )

    orch = AgentOrchestrator()
    orch._profile_store = ProfileStore()
    orch._pool = Pool()

    session = MagicMock()
    session.id = "sid-1"
    session.chat_id = "sid-1"
    session.context.get_messages.return_value = []

    with pytest.raises(RuntimeError, match="external cli agent failed"):
        await orch._run_with_progress_timeout(session, "work", profile.id)
    await asyncio.sleep(0)

    state = next(iter(orch._sub_agent_states.values()))
    statuses = [payload["status"] for _, payload in sent]

    assert state["status"] == "error"
    assert statuses[-1] == "error"
    assert "completed" not in statuses

    for cleanup_task in orch._sub_cleanup_tasks.values():
        cleanup_task.cancel()
