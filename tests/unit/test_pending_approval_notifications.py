from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openakita.agent.pending_approval_notifications import (
    build_pending_approval_event_hook,
    notify_pending_approval_im,
    resolve_approval_notification_target,
)


def _payload(**overrides):
    payload = {
        "id": "pa_123",
        "task_id": "task_123",
        "session_id": "session_123",
        "tool_name": "setup_organization",
        "reason": "owner approval required",
    }
    payload.update(overrides)
    return payload


class _Scheduler:
    def __init__(self, task=None):
        self.task = task

    def get_task(self, task_id):
        return self.task if task_id == "task_123" else None


class _Loop:
    def __init__(self):
        self.callbacks = []

    def call_soon_threadsafe(self, callback):
        self.callbacks.append(callback)


@pytest.mark.asyncio
async def test_event_hook_notifies_only_for_created_events():
    loop = _Loop()
    fired = []
    notify_owner = AsyncMock()
    hook = build_pending_approval_event_hook(
        loop=loop,
        fire_event=lambda event, payload: fired.append((event, payload)),
        notify_owner=notify_owner,
    )

    hook("pending_approval_resolved", {"id": "pa_resolved"})
    hook("pending_approval_created", {"id": "pa_created"})

    assert [event for event, _payload in fired] == [
        "pending_approval_resolved",
        "pending_approval_created",
    ]
    assert len(loop.callbacks) == 1
    loop.callbacks[0]()
    await asyncio.sleep(0)
    notify_owner.assert_awaited_once_with({"id": "pa_created"})


def test_resolve_target_prefers_scheduled_task_owner_route():
    task = SimpleNamespace(
        channel_id="feishu:owner",
        chat_id="chat-owner",
        user_id="user-owner",
        name="Daily research",
    )
    unrelated_session = SimpleNamespace(
        channel="telegram:other",
        chat_id="chat-other",
        user_id="user-other",
    )
    gateway = SimpleNamespace(
        session_manager=SimpleNamespace(get_session_by_id=lambda _id: unrelated_session)
    )

    target = resolve_approval_notification_target(
        _payload(), scheduler=_Scheduler(task), gateway=gateway
    )

    assert target is not None
    assert (target.channel, target.chat_id, target.user_id) == (
        "feishu:owner",
        "chat-owner",
        "user-owner",
    )
    assert target.task_name == "Daily research"


def test_resolve_target_uses_exact_origin_session_without_global_fallback():
    session = SimpleNamespace(
        channel="telegram:owner",
        chat_id="chat-owner",
        user_id="user-owner",
    )
    session_manager = SimpleNamespace(
        get_session_by_id=lambda session_id: session if session_id == "session_123" else None
    )
    gateway = SimpleNamespace(session_manager=session_manager)

    target = resolve_approval_notification_target(
        _payload(task_id=None), scheduler=_Scheduler(), gateway=gateway
    )

    assert target is not None
    assert (target.channel, target.chat_id) == ("telegram:owner", "chat-owner")


def test_resolve_target_rejects_desktop_and_missing_owner_routes():
    task = SimpleNamespace(
        channel_id="desktop",
        chat_id="conversation-1",
        user_id="user-owner",
        name="Desktop task",
    )
    unrelated_session = SimpleNamespace(
        channel="telegram:other",
        chat_id="chat-other",
        user_id="user-other",
    )
    gateway = SimpleNamespace(
        session_manager=SimpleNamespace(get_session_by_id=lambda _id: unrelated_session)
    )

    target = resolve_approval_notification_target(
        _payload(), scheduler=_Scheduler(task), gateway=gateway
    )

    assert target is None


@pytest.mark.asyncio
async def test_notify_sends_redacted_message_to_owner_route():
    task = SimpleNamespace(
        channel_id="feishu:owner",
        chat_id="chat-owner",
        user_id="user-owner",
        name="Daily research",
    )
    gateway = SimpleNamespace(
        session_manager=None,
        send_text_reliably=AsyncMock(return_value=True),
    )
    payload = _payload(params={"secret": "must-not-leak"})

    delivered = await notify_pending_approval_im(
        payload, scheduler=_Scheduler(task), gateway=gateway
    )

    assert delivered is True
    kwargs = gateway.send_text_reliably.await_args.kwargs
    assert kwargs["channel"] == "feishu:owner"
    assert kwargs["chat_id"] == "chat-owner"
    assert kwargs["user_id"] == "user-owner"
    assert "Daily research" in kwargs["text"]
    assert "setup_organization" in kwargs["text"]
    assert "pa_123" in kwargs["text"]
    assert "must-not-leak" not in kwargs["text"]
    assert kwargs["metadata"]["approval_id"] == "pa_123"


@pytest.mark.asyncio
async def test_notify_failure_isolated_from_approval_creation():
    task = SimpleNamespace(
        channel_id="telegram:owner",
        chat_id="chat-owner",
        user_id="user-owner",
        name="Task",
    )
    gateway = SimpleNamespace(
        session_manager=None,
        send_text_reliably=AsyncMock(side_effect=RuntimeError("offline")),
    )

    delivered = await notify_pending_approval_im(
        _payload(), scheduler=_Scheduler(task), gateway=gateway
    )

    assert delivered is False


@pytest.mark.asyncio
async def test_notify_target_resolution_failure_isolated_from_approval_creation():
    def fail_get_task(_task_id):
        raise RuntimeError("bad")

    scheduler = SimpleNamespace(get_task=fail_get_task)
    gateway = SimpleNamespace(
        session_manager=None,
        send_text_reliably=AsyncMock(return_value=True),
    )

    delivered = await notify_pending_approval_im(_payload(), scheduler=scheduler, gateway=gateway)

    assert delivered is False
    gateway.send_text_reliably.assert_not_awaited()
