from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.agents.orchestrator import AgentOrchestrator
from openakita.sessions.session import Session, SessionConfig, SessionContext
from openakita.tools.handlers.agent import AgentToolHandler


def _make_session(session_id: str = "async-it") -> Session:
    ctx = SessionContext()
    ctx.agent_profile_id = "default"
    return Session(
        id=session_id,
        channel="desktop",
        chat_id=session_id,
        user_id="desktop_user",
        context=ctx,
        config=SessionConfig(),
    )


@pytest.mark.asyncio
async def test_delegate_parallel_launches_without_waiting_for_completion():
    session = _make_session("parallel-it")
    parent_agent = MagicMock()
    parent_agent._is_sub_agent_call = False
    parent_agent._current_session = session

    orchestrator = AgentOrchestrator()
    started = asyncio.Event()
    release = asyncio.Event()
    completed_count = 0

    async def fake_dispatch(_session, message, *args, **kwargs):
        nonlocal completed_count
        started.set()
        await release.wait()
        completed_count += 1
        return f"done:{message[-1]}"

    orchestrator._ensure_deps = MagicMock()
    orchestrator._dispatch = AsyncMock(side_effect=fake_dispatch)
    handler = AgentToolHandler(parent_agent)
    # Disable browser manager to skip isolated context creation
    parent_agent.browser_manager = None

    try:
        with patch.object(handler, "_get_orchestrator", return_value=orchestrator):
            before = time.monotonic()
            result = await handler.handle(
                "delegate_parallel",
                {
                    "tasks": [
                        {"agent_id": "helper-a", "message": "A"},
                        {"agent_id": "helper-b", "message": "B"},
                    ],
                },
            )
            elapsed = time.monotonic() - before

        assert elapsed < 0.25
        assert "async_batch_launched" in result
        assert completed_count == 0

        await asyncio.wait_for(started.wait(), timeout=1.0)
        release.set()

        for record in list(orchestrator._delegations.values()):
            await orchestrator.task_queue.wait_for(
                record.queue_task_id,
                timeout=1.0,
                consume=False,
            )

        assert completed_count == 2
        notifications = [
            message for message in session.context.messages
            if message.get("message_type") == "sub_agent_notification"
        ]
        assert len(notifications) == 2
        assert all(message["role"] == "assistant" for message in notifications)
    finally:
        await orchestrator.task_queue.stop()


@pytest.mark.asyncio
async def test_background_completion_marks_session_dirty_and_persists_notification():
    session = _make_session("dirty-it")
    orchestrator = AgentOrchestrator()
    mark_dirty = MagicMock()
    orchestrator._gateway = MagicMock()
    orchestrator._gateway.session_manager = MagicMock()
    orchestrator._gateway.session_manager.mark_dirty = mark_dirty
    orchestrator._ensure_deps = MagicMock()
    orchestrator._dispatch = AsyncMock(return_value="sub result")

    try:
        launch = await orchestrator.start_delegation(
            session=session,
            from_agent="default",
            to_agent="helper",
            message="[Task Instruction]\ndo work",
            reason="integration",
        )

        record = orchestrator._delegations[launch["delegation_id"]]
        await orchestrator.task_queue.wait_for(
            record.queue_task_id,
            timeout=1.0,
            consume=False,
        )

        assert mark_dirty.called is True
        notification = session.context.messages[-1]
        assert notification["role"] == "assistant"
        assert notification["message_type"] == "sub_agent_notification"
        assert notification["delegation_id"] == launch["delegation_id"]
        assert notification["status"] == "completed"
    finally:
        await orchestrator.task_queue.stop()


@pytest.mark.asyncio
async def test_cancel_batch_updates_state_and_releases_resources():
    session = _make_session("cancel-it")
    orchestrator = AgentOrchestrator()
    started = asyncio.Event()

    async def fake_dispatch(*args, **kwargs):
        started.set()
        await asyncio.sleep(10)
        return "never"

    orchestrator._ensure_deps = MagicMock()
    orchestrator._dispatch = AsyncMock(side_effect=fake_dispatch)

    try:
        launch = await orchestrator.start_delegation_batch(
            session=session,
            from_agent="default",
            tasks=[
                {"agent_id": "helper-a", "message": "[Task Instruction]\nA"},
                {"agent_id": "helper-b", "message": "[Task Instruction]\nB"},
            ],
        )

        await asyncio.wait_for(started.wait(), timeout=1.0)
        result = await orchestrator.cancel_delegation_target(
            {"type": "batch", "id": launch["batch_id"]},
            reason="integration cancel",
        )

        assert result["status"] == "cancelled"
        assert len(result["cancelled"]) == 2

        for item in result["cancelled"]:
            status = orchestrator.get_delegation_status(
                {"type": "delegation", "id": item["delegation_id"]}
            )
            assert status["status"] == "cancelled"
            assert status["terminal"]["cancel_reason"] == "integration cancel"

        for record in orchestrator._delegations.values():
            record.expires_at = time.time() - 1

        removed = await orchestrator.cleanup_expired_delegations()
        assert len(removed) == 2
    finally:
        await orchestrator.task_queue.stop()


def test_restart_restores_running_async_states_as_interrupted(tmp_path: Path):
    first = AgentOrchestrator()
    first._log_dir = tmp_path / "logs"
    first._log_dir.mkdir()

    first._sub_agent_states["sess:helper:abc"] = {
        "agent_id": "helper",
        "profile_id": "helper",
        "session_id": "sess",
        "chat_id": "sess",
        "status": "running",
        "delegation_id": "dlg_abc",
    }
    first._persist_sub_states()

    second = AgentOrchestrator()
    second._log_dir = tmp_path / "logs"
    second._load_sub_states()

    restored = second._sub_agent_states["sess:helper:abc"]
    assert restored["status"] == "interrupted"
    assert restored["delegation_id"] == "dlg_abc"
