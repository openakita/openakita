from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from openakita.channels.base import ChannelDeliveryUnavailable
from openakita.scheduler.executor import TaskExecutor
from openakita.scheduler.scheduler import TaskScheduler
from openakita.scheduler.task import ScheduledTask


@dataclass
class _FakeTaskResult:
    success: bool
    data: str | None = None
    error: str | None = None


class _FailingAgent:
    async def initialize(self, *args, **kwargs):
        return None

    async def execute_task_from_message(self, message: str):
        return _FakeTaskResult(success=False, error="Invalid function response")

    async def shutdown(self):
        return None


class _SuccessfulAgent:
    async def initialize(self, *args, **kwargs):
        return None

    async def execute_task_from_message(self, message: str):
        return _FakeTaskResult(success=True, data="daily report")

    async def shutdown(self):
        return None


class _SilentGateway:
    async def send(self, **kwargs):
        return None


class _QueuedGateway:
    async def send(self, **kwargs):
        return ""


class _ReliableGateway:
    def __init__(self):
        self.sent_text = ""

    async def send_text_reliably(self, **kwargs):
        self.sent_text = kwargs["text"]
        return True


class _FallbackGateway:
    def __init__(self, tmp_path):
        self.calls: list[tuple[str, str]] = []
        self.session_manager = SimpleNamespace(
            storage_path=tmp_path,
            list_sessions=lambda: [
                SimpleNamespace(channel="feishu:bot", chat_id="chat-2"),
            ],
        )

    async def send_text_reliably(self, **kwargs):
        pair = (kwargs["channel"], kwargs["chat_id"])
        self.calls.append(pair)
        return pair == ("feishu:bot", "chat-2")


class _UnavailableGateway:
    async def send(self, **kwargs):
        raise ChannelDeliveryUnavailable(
            "unavailable",
            channel=kwargs["channel"],
            chat_id=kwargs["chat_id"],
            reason="context rejected",
        )

    async def send_text_reliably(self, **kwargs):
        raise ChannelDeliveryUnavailable(
            "unavailable",
            channel=kwargs["channel"],
            chat_id=kwargs["chat_id"],
            reason="context rejected",
        )


class _EndUnavailableGateway:
    def __init__(self):
        self.start_calls = 0
        self.reliable_calls = 0

    async def send(self, **kwargs):
        self.start_calls += 1
        return "start-msg"

    async def send_text_reliably(self, **kwargs):
        self.reliable_calls += 1
        raise ChannelDeliveryUnavailable(
            "unavailable",
            channel=kwargs["channel"],
            chat_id=kwargs["chat_id"],
            reason="context rejected",
        )


async def _make_scheduler(tmp_path, executor) -> TaskScheduler:
    scheduler = TaskScheduler(
        storage_path=tmp_path,
        executor=executor,
        check_interval_seconds=60,
        advance_seconds=0,
    )
    scheduler._semaphore = asyncio.Semaphore(1)
    return scheduler


def _make_task(**kwargs) -> ScheduledTask:
    return ScheduledTask.create_cron(
        name="daily research",
        description="run research and deliver the result",
        cron_expression="0 19 * * *",
        prompt="research",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_agent_failure_marks_scheduled_task_failed(tmp_path):
    executor = TaskExecutor(agent_factory=lambda: _FailingAgent())
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task()
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "failed"
    assert execution.error == "Invalid function response"
    stored = scheduler.get_task(task_id)
    assert stored is not None
    assert stored.fail_count == 1
    assert stored.metadata["last_error"] == "Invalid function response"


@pytest.mark.asyncio
async def test_result_delivery_failure_marks_scheduled_task_failed(tmp_path):
    executor = TaskExecutor(agent_factory=lambda: _SuccessfulAgent(), gateway=_SilentGateway())
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task(channel_id="qqbot:xiababy", chat_id="chat-1")
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "failed"
    assert execution.error == "任务已完成，但结果通知发送失败，请检查 IM 通道连接状态。"
    stored = scheduler.get_task(task_id)
    assert stored is not None
    assert stored.fail_count == 1


@pytest.mark.asyncio
async def test_start_notification_channel_unavailable_skips_before_agent_creation(tmp_path):
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return _SuccessfulAgent()

    executor = TaskExecutor(agent_factory=factory, gateway=_UnavailableGateway())
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task(channel_id="wechat:test", chat_id="chat-1")
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "failed"
    assert execution.error == "IM 通道不可投递：微信会话或 context_token 已失效，请在微信中发送一条新消息刷新会话，或重新扫码登录。"
    assert factory_calls == 0
    stored = scheduler.get_task(task_id)
    assert stored is not None
    assert stored.fail_count == 0
    assert stored.metadata["last_channel_unavailable"] == execution.error


@pytest.mark.asyncio
async def test_successful_task_with_channel_unavailable_result_does_not_increment_fail_count(
    tmp_path,
):
    gateway = _EndUnavailableGateway()
    executor = TaskExecutor(agent_factory=lambda: _SuccessfulAgent(), gateway=gateway)
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task(channel_id="wechat:test", chat_id="chat-1")
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "failed"
    assert execution.error == "IM 通道不可投递：微信会话或 context_token 已失效，请在微信中发送一条新消息刷新会话，或重新扫码登录。"
    assert gateway.start_calls == 1
    assert gateway.reliable_calls == 1
    stored = scheduler.get_task(task_id)
    assert stored is not None
    assert stored.fail_count == 0
    assert stored.metadata["last_channel_unavailable"] == execution.error


@pytest.mark.asyncio
async def test_configured_channel_without_gateway_marks_delivery_failed(tmp_path):
    executor = TaskExecutor(agent_factory=lambda: _SuccessfulAgent(), gateway=None)
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task(channel_id="feishu:feishu-1", chat_id="chat-1")
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "failed"
    assert execution.error == "任务已完成，但结果通知发送失败，请检查 IM 通道连接状态。"
    stored = scheduler.get_task(task_id)
    assert stored is not None
    assert stored.fail_count == 1


@pytest.mark.asyncio
async def test_queued_delivery_does_not_count_as_immediate_success(tmp_path):
    executor = TaskExecutor(agent_factory=lambda: _SuccessfulAgent(), gateway=_QueuedGateway())
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task(channel_id="qqbot:xiababy", chat_id="chat-1")
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "failed"
    assert execution.error == "任务已完成，但结果通知发送失败，请检查 IM 通道连接状态。"


@pytest.mark.asyncio
async def test_result_delivery_uses_reliable_gateway_path(tmp_path):
    gateway = _ReliableGateway()
    executor = TaskExecutor(agent_factory=lambda: _SuccessfulAgent(), gateway=gateway)
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task(channel_id="qqbot:xiababy", chat_id="chat-1")
    task.metadata["notify_on_start"] = False
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "success"
    assert "daily report" in gateway.sent_text


@pytest.mark.asyncio
async def test_system_task_uses_same_completion_notification_path(tmp_path, monkeypatch):
    gateway = _ReliableGateway()
    executor = TaskExecutor(gateway=gateway)

    async def fake_system_task(task):
        return True, "system task summary"

    monkeypatch.setattr(executor, "_execute_system_task", fake_system_task)
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task(
        channel_id="qqbot:xiababy",
        chat_id="chat-1",
        action="system:daily_memory",
    )
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "success"
    assert "system task summary" in gateway.sent_text


@pytest.mark.asyncio
async def test_completion_notification_falls_back_to_known_im_target(tmp_path):
    gateway = _FallbackGateway(tmp_path)
    executor = TaskExecutor(agent_factory=lambda: _SuccessfulAgent(), gateway=gateway)
    scheduler = await _make_scheduler(tmp_path, executor=executor.execute)
    task = _make_task(channel_id="qqbot:xiababy", chat_id="chat-1")
    task.metadata["notify_on_start"] = False
    task_id = await scheduler.add_task(task)

    execution = await scheduler.trigger_now(task_id)

    assert execution is not None
    assert execution.status == "success"
    assert gateway.calls == [
        ("qqbot:xiababy", "chat-1"),
        ("feishu:bot", "chat-2"),
    ]
