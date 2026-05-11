"""Tests for OrgRuntime — lifecycle, agent creation, task execution (mocked)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.runtime import OrgRuntime
from openakita.orgs.models import MsgType, NodeStatus, OrgMessage, OrgProject, OrgStatus, ProjectTask, ProjectStatus, TaskStatus
from openakita.orgs.project_store import ProjectStore
from .conftest import make_node, make_org


@pytest.fixture()
def runtime(org_manager: OrgManager) -> OrgRuntime:
    return OrgRuntime(org_manager)


class TestLifecycle:
    async def test_start_and_shutdown(self, runtime: OrgRuntime):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        assert runtime._started is True
        await runtime.shutdown()
        assert runtime._started is False

    async def test_start_org(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org(name="运行测试").to_dict())
            result = await runtime.start_org(org.id)
            assert result is not None

            org_manager.invalidate_cache(org.id)
            loaded = org_manager.get(org.id)
            assert loaded.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING)
        finally:
            await runtime.shutdown()

    async def test_stop_org(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org(name="停止测试").to_dict())
            await runtime.start_org(org.id)
            await runtime.stop_org(org.id)

            org_manager.invalidate_cache(org.id)
            loaded = org_manager.get(org.id)
            assert loaded.status == OrgStatus.DORMANT
        finally:
            await runtime.shutdown()


class TestGetAccessors:
    async def test_get_org(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org(name="测试").to_dict())
            await runtime.start_org(org.id)
            got = runtime.get_org(org.id)
            assert got is not None
            assert got.name == "测试"
        finally:
            await runtime.shutdown()

    async def test_get_blackboard(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org().to_dict())
            await runtime.start_org(org.id)
            bb = runtime.get_blackboard(org.id)
            assert bb is not None
        finally:
            await runtime.shutdown()

    async def test_get_event_store(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org().to_dict())
            await runtime.start_org(org.id)
            es = runtime.get_event_store(org.id)
            assert es is not None
        finally:
            await runtime.shutdown()

    async def test_get_messenger(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org().to_dict())
            await runtime.start_org(org.id)
            messenger = runtime.get_messenger(org.id)
            assert messenger is not None
        finally:
            await runtime.shutdown()

    async def test_get_inbox(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org().to_dict())
            await runtime.start_org(org.id)
            inbox = runtime.get_inbox(org.id)
            assert inbox is not None
        finally:
            await runtime.shutdown()

    def test_get_scaler(self, runtime: OrgRuntime):
        scaler = runtime.get_scaler()
        assert scaler is not None

    def test_get_heartbeat(self, runtime: OrgRuntime):
        hb = runtime.get_heartbeat()
        assert hb is not None

    def test_get_scheduler(self, runtime: OrgRuntime):
        sched = runtime.get_scheduler()
        assert sched is not None

    def test_get_notifier(self, runtime: OrgRuntime):
        notifier = runtime.get_notifier()
        assert notifier is not None

    def test_get_reporter(self, runtime: OrgRuntime):
        reporter = runtime.get_reporter()
        assert reporter is not None

    async def test_get_policies(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org().to_dict())
            await runtime.start_org(org.id)
            policies = runtime.get_policies(org.id)
            assert policies is not None
        finally:
            await runtime.shutdown()


class TestSendCommand:
    async def test_send_command_to_root(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org(name="命令测试").to_dict())
            await runtime.start_org(org.id)

            mock_agent = AsyncMock()
            mock_agent.chat = AsyncMock(return_value="收到命令")
            mock_agent.brain = MagicMock()
            mock_agent.brain.drain_usage_accumulator = MagicMock(
                return_value={"calls": 0, "tokens_in": 0, "tokens_out": 0}
            )

            with patch.object(runtime, "_get_or_create_agent", new_callable=AsyncMock, return_value=mock_agent):
                with patch.object(runtime, "_broadcast_ws", new_callable=AsyncMock):
                    result = await runtime.send_command(org.id, None, "做个计划")

            assert result is not None
        finally:
            await runtime.shutdown()


class TestAutoKickoff:
    """Tests for auto-kickoff when core_business is set (v1.3)."""

    async def test_start_org_with_core_business_triggers_kickoff(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org_data = make_org(name="自动启动测试").to_dict()
            org_data["core_business"] = "做一个电商平台"
            org_data["operation_mode"] = "autonomous"
            org = org_manager.create(org_data)

            with patch.object(runtime, "_auto_kickoff", new_callable=AsyncMock) as mock_kickoff:
                await runtime.start_org(org.id)

                await asyncio.sleep(0.1)
                mock_kickoff.assert_called_once()
                call_org = mock_kickoff.call_args[0][0]
                assert call_org.core_business == "做一个电商平台"
        finally:
            await runtime.shutdown()

    async def test_start_org_without_core_business_no_kickoff(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org_data = make_org(name="无业务测试").to_dict()
            org_data["core_business"] = ""
            org_data["operation_mode"] = "autonomous"
            org = org_manager.create(org_data)

            with patch.object(runtime, "_auto_kickoff", new_callable=AsyncMock) as mock_kickoff:
                await runtime.start_org(org.id)
                await asyncio.sleep(0.1)
                mock_kickoff.assert_not_called()
        finally:
            await runtime.shutdown()

    async def test_auto_kickoff_prompt_uses_dynamic_role_title(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org_data = make_org(name="主编团队").to_dict()
            org_data["core_business"] = "内容运营"
            org_data["operation_mode"] = "autonomous"
            org_data["nodes"][0]["role_title"] = "主编"
            org = org_manager.create(org_data)

            captured_prompt = None

            async def capture_activate(org_obj, node, prompt, *args, **kwargs):
                nonlocal captured_prompt
                captured_prompt = prompt
                return {"result": "ok"}

            with patch.object(runtime, "_activate_and_run", side_effect=capture_activate):
                await runtime.start_org(org.id)
                await asyncio.sleep(0.2)

            assert captured_prompt is not None
            assert "主编" in captured_prompt
            assert "经营任务书" in captured_prompt
            assert "内容运营" in captured_prompt
        finally:
            await runtime.shutdown()

    async def test_auto_kickoff_prompt_uses_persona_label(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            from openakita.orgs.models import UserPersona
            org_data = make_org(name="投资项目").to_dict()
            org_data["core_business"] = "AI 研究"
            org_data["operation_mode"] = "autonomous"
            org_data["user_persona"] = {"title": "投资人", "display_name": "张总", "description": ""}
            org = org_manager.create(org_data)

            captured_prompt = None

            async def capture_activate(org_obj, node, prompt, *args, **kwargs):
                nonlocal captured_prompt
                captured_prompt = prompt
                return {"result": "ok"}

            with patch.object(runtime, "_activate_and_run", side_effect=capture_activate):
                await runtime.start_org(org.id)
                await asyncio.sleep(0.2)

            assert captured_prompt is not None
            assert "张总" in captured_prompt
            assert "张总委托你全权负责" in captured_prompt
        finally:
            await runtime.shutdown()


class TestResetOrg:
    async def test_reset_org_status(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org(name="重置测试").to_dict())
            await runtime.start_org(org.id)
            await runtime.reset_org(org.id)

            org_manager.invalidate_cache(org.id)
            loaded = org_manager.get(org.id)
            assert loaded.status == OrgStatus.DORMANT
            for node in loaded.nodes:
                assert node.status == NodeStatus.IDLE
        finally:
            await runtime.shutdown()

    async def test_reset_org_clears_blackboard_disk(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        """Blackboard files on disk must be removed after reset."""
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org(name="黑板清理测试").to_dict())
            await runtime.start_org(org.id)

            bb = runtime.get_blackboard(org.id)
            assert bb is not None
            bb.write_org("测试黑板内容", source_node="node_ceo", importance=0.9)

            bb_file = bb._memory_dir / "blackboard.jsonl"
            assert bb_file.exists(), "blackboard.jsonl should exist after write"

            await runtime.reset_org(org.id)

            assert not bb_file.exists(), "blackboard.jsonl should be removed after reset"

        finally:
            await runtime.shutdown()

    async def test_reset_org_clears_event_store(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        """Event store files on disk must be removed after reset."""
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org(name="事件清理测试").to_dict())
            await runtime.start_org(org.id)

            es = runtime.get_event_store(org.id)
            es.emit("test_event", "system", {"data": "value"})

            await runtime.reset_org(org.id)

            assert runtime.get_blackboard(org.id) is None
            assert org.id not in runtime._active_orgs
        finally:
            await runtime.shutdown()


class TestNodeConcurrencyLimit:
    def test_node_max_concurrent_prefers_node_configuration(self, runtime: OrgRuntime):
        node = make_node(max_concurrent_tasks=99)

        assert runtime._node_max_concurrent(node) == 99

    def test_node_max_concurrent_falls_back_to_runtime_default(self, runtime: OrgRuntime):
        node = make_node(max_concurrent_tasks=0)

        assert runtime._node_max_concurrent(node) == runtime.max_concurrent_per_node

    async def test_drain_node_pending_uses_node_concurrency_limit(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org_data = make_org().to_dict()
            org_data["nodes"][0]["max_concurrent_tasks"] = 4
            org = org_manager.create(org_data)
            await runtime.start_org(org.id)
            node = runtime.get_org(org.id).get_node("node_ceo")
            messenger = runtime.get_messenger(org.id)
            mailbox = messenger.get_mailbox(node.id)
            for index in range(4):
                await mailbox.put(
                    OrgMessage(
                        org_id=org.id,
                        from_node="user",
                        to_node=node.id,
                        msg_type=MsgType.TASK_ASSIGN,
                        content=f"任务 {index}",
                    )
                )

            with patch.object(runtime, "_activate_and_run", new_callable=AsyncMock) as run_mock:
                dispatched = await runtime._drain_node_pending(runtime.get_org(org.id), node)

            assert dispatched == 4
            assert run_mock.await_count == 4
        finally:
            await runtime.shutdown()

    async def test_drain_node_pending_dispatches_99_configured_slots(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org_data = make_org().to_dict()
            org_data["nodes"][0]["max_concurrent_tasks"] = 99
            org = org_manager.create(org_data)
            await runtime.start_org(org.id)
            node = runtime.get_org(org.id).get_node("node_ceo")
            messenger = runtime.get_messenger(org.id)
            mailbox = messenger.get_mailbox(node.id)
            for index in range(99):
                await mailbox.put(
                    OrgMessage(
                        org_id=org.id,
                        from_node="user",
                        to_node=node.id,
                        msg_type=MsgType.TASK_ASSIGN,
                        content=f"并发任务 {index}",
                    )
                )

            with patch.object(runtime, "_activate_and_run", new_callable=AsyncMock) as run_mock:
                dispatched = await runtime._drain_node_pending(runtime.get_org(org.id), node)

            assert dispatched == 99
            assert run_mock.await_count == 99
        finally:
            await runtime.shutdown()

    async def test_drain_node_pending_subtracts_active_tasks(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org_data = make_org().to_dict()
            org_data["nodes"][0]["max_concurrent_tasks"] = 4
            org = org_manager.create(org_data)
            await runtime.start_org(org.id)
            node = runtime.get_org(org.id).get_node("node_ceo")
            messenger = runtime.get_messenger(org.id)
            mailbox = messenger.get_mailbox(node.id)
            runtime._running_tasks[org.id] = {
                f"{node.id}:active-{index}": asyncio.create_task(asyncio.sleep(60))
                for index in range(2)
            }
            for index in range(4):
                await mailbox.put(
                    OrgMessage(
                        org_id=org.id,
                        from_node="user",
                        to_node=node.id,
                        msg_type=MsgType.TASK_ASSIGN,
                        content=f"待处理任务 {index}",
                    )
                )

            with patch.object(runtime, "_activate_and_run", new_callable=AsyncMock) as run_mock:
                dispatched = await runtime._drain_node_pending(runtime.get_org(org.id), node)

            assert dispatched == 2
            assert run_mock.await_count == 2
            for task in runtime._running_tasks[org.id].values():
                task.cancel()
        finally:
            await runtime.shutdown()

    async def test_on_node_message_uses_node_concurrency_limit(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org_data = make_org().to_dict()
            org_data["nodes"][0]["max_concurrent_tasks"] = 4
            org = org_manager.create(org_data)
            await runtime.start_org(org.id)
            live_org = runtime.get_org(org.id)
            node = live_org.get_node("node_ceo")
            runtime._running_tasks[org.id] = {
                f"{node.id}:active-{index}": asyncio.create_task(asyncio.sleep(60))
                for index in range(3)
            }
            msg = OrgMessage(
                org_id=org.id,
                from_node="user",
                to_node=node.id,
                msg_type=MsgType.TASK_ASSIGN,
                content="第四个任务",
            )

            with patch.object(runtime, "_activate_and_run", new_callable=AsyncMock) as run_mock:
                await runtime._on_node_message(org.id, node.id, msg)

            assert run_mock.await_count == 1
            for task in runtime._running_tasks[org.id].values():
                task.cancel()
        finally:
            await runtime.shutdown()

    async def test_on_node_message_blocks_at_node_concurrency_limit(
        self, runtime: OrgRuntime, org_manager: OrgManager,
    ):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org_data = make_org().to_dict()
            org_data["nodes"][0]["max_concurrent_tasks"] = 4
            org = org_manager.create(org_data)
            await runtime.start_org(org.id)
            live_org = runtime.get_org(org.id)
            node = live_org.get_node("node_ceo")
            runtime._running_tasks[org.id] = {
                f"{node.id}:active-{index}": asyncio.create_task(asyncio.sleep(60))
                for index in range(4)
            }
            msg = OrgMessage(
                org_id=org.id,
                from_node="user",
                to_node=node.id,
                msg_type=MsgType.TASK_ASSIGN,
                content="第五个任务",
            )

            with patch.object(runtime, "_activate_and_run", new_callable=AsyncMock) as run_mock:
                await runtime._on_node_message(org.id, node.id, msg)

            assert run_mock.await_count == 0
            for task in runtime._running_tasks[org.id].values():
                task.cancel()
        finally:
            await runtime.shutdown()


class TestStateTransitions:
    async def test_pause_and_resume(self, runtime: OrgRuntime, org_manager: OrgManager):
        with patch("openakita.orgs.templates.ensure_builtin_templates"):
            await runtime.start()
        try:
            org = org_manager.create(make_org().to_dict())
            await runtime.start_org(org.id)
            await runtime.pause_org(org.id)

            org_manager.invalidate_cache(org.id)
            loaded = org_manager.get(org.id)
            assert loaded.status == OrgStatus.PAUSED

            await runtime.resume_org(org.id)
            org_manager.invalidate_cache(org.id)
            loaded = org_manager.get(org.id)
            assert loaded.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING)
        finally:
            await runtime.shutdown()


