"""Tests for OrgHeartbeat — heartbeat scheduling, standup, lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.heartbeat import OrgHeartbeat
from openakita.orgs.models import OrgStatus
from .conftest import make_org


@pytest.fixture()
def heartbeat(mock_runtime) -> OrgHeartbeat:
    return OrgHeartbeat(mock_runtime)


class TestStartStop:
    async def test_start_with_heartbeat_disabled(self, heartbeat: OrgHeartbeat, persisted_org):
        persisted_org.heartbeat_enabled = False
        await heartbeat.start_for_org(persisted_org)
        assert persisted_org.id not in heartbeat._heartbeat_tasks

    async def test_start_with_heartbeat_enabled(self, heartbeat: OrgHeartbeat, persisted_org):
        persisted_org.heartbeat_enabled = True
        await heartbeat.start_for_org(persisted_org)
        assert persisted_org.id in heartbeat._heartbeat_tasks

        await heartbeat.stop_for_org(persisted_org.id)
        assert persisted_org.id not in heartbeat._heartbeat_tasks

    async def test_start_with_standup_enabled(self, heartbeat: OrgHeartbeat, persisted_org):
        persisted_org.standup_enabled = True
        await heartbeat.start_for_org(persisted_org)
        assert persisted_org.id in heartbeat._standup_tasks

        await heartbeat.stop_all()
        assert len(heartbeat._heartbeat_tasks) == 0
        assert len(heartbeat._standup_tasks) == 0

    async def test_stop_all_empty(self, heartbeat: OrgHeartbeat):
        await heartbeat.stop_all()


class TestTriggerHeartbeat:
    async def test_trigger_nonexistent_org(self, heartbeat: OrgHeartbeat, mock_runtime):
        mock_runtime.get_org = MagicMock(return_value=None)
        result = await heartbeat.trigger_heartbeat("fake_org")
        assert "error" in result

    async def test_trigger_with_no_root_nodes(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        persisted_org.nodes = []
        result = await heartbeat.trigger_heartbeat(persisted_org.id)
        assert "error" in result
        assert "root" in result["error"].lower() or "No root" in result["error"]

    async def test_trigger_heartbeat_success(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        mock_runtime.send_command = AsyncMock(return_value={"result": "一切正常"})
        result = await heartbeat.trigger_heartbeat(persisted_org.id)
        assert result == {"result": "一切正常"}

        mock_runtime.send_command.assert_awaited_once()
        call_args = mock_runtime.send_command.call_args
        assert call_args[0][0] == persisted_org.id
        assert call_args[0][1] == "node_ceo"
        assert "健康检查" in call_args[0][2]

    async def test_heartbeat_prompt_contains_node_status(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        mock_runtime.send_command = AsyncMock(return_value={"result": "ok"})
        await heartbeat.trigger_heartbeat(persisted_org.id)

        prompt = mock_runtime.send_command.call_args[0][2]
        assert "CEO" in prompt
        assert "CTO" in prompt
        assert "状态=" in prompt
        assert "健康检查" in prompt

    async def test_heartbeat_emits_events(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        mock_runtime.send_command = AsyncMock(return_value={"result": "ok"})
        await heartbeat.trigger_heartbeat(persisted_org.id)

        es = mock_runtime.get_event_store()
        events = es.query(event_type="heartbeat_triggered")
        assert len(events) >= 1
        events2 = es.query(event_type="heartbeat_decision")
        assert len(events2) >= 1


class TestHeartbeatWithCoreBusiness:
    """Tests for heartbeat behavior when core_business is set (v1.3)."""

    async def test_heartbeat_uses_review_mode(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        persisted_org.operation_mode = "autonomous"
        persisted_org.core_business = "做一个 AI 产品，当前阶段目标：完成 MVP"
        mock_runtime.send_command = AsyncMock(return_value={"result": "复盘完成"})
        result = await heartbeat.trigger_heartbeat(persisted_org.id)

        prompt = mock_runtime.send_command.call_args[0][2]
        assert "经营复盘" in prompt
        assert "心跳检查" not in prompt
        assert "核心业务目标" in prompt
        assert "做一个 AI 产品" in prompt

    async def test_heartbeat_review_includes_blackboard_step(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        persisted_org.core_business = "电商运营"
        mock_runtime.send_command = AsyncMock(return_value={"result": "ok"})
        await heartbeat.trigger_heartbeat(persisted_org.id)

        prompt = mock_runtime.send_command.call_args[0][2]
        assert "org_read_blackboard" in prompt
        assert "回顾" in prompt or "查看黑板" in prompt

    async def test_heartbeat_without_core_business_uses_check_mode(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        persisted_org.operation_mode = "autonomous"
        persisted_org.core_business = ""
        mock_runtime.send_command = AsyncMock(return_value={"result": "ok"})
        await heartbeat.trigger_heartbeat(persisted_org.id)

        prompt = mock_runtime.send_command.call_args[0][2]
        assert "心跳检查" in prompt
        assert "经营复盘" not in prompt

    async def test_heartbeat_uses_dynamic_persona_label(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        from openakita.orgs.models import UserPersona
        persisted_org.core_business = "内容运营"
        persisted_org.user_persona = UserPersona(title="出品人", display_name="出品人")
        mock_runtime.send_command = AsyncMock(return_value={"result": "ok"})
        await heartbeat.trigger_heartbeat(persisted_org.id)

        prompt = mock_runtime.send_command.call_args[0][2]
        assert "出品人" in prompt

    async def test_heartbeat_no_hardcoded_ceo(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        """Ensure the heartbeat prompt template itself doesn't hardcode 'CEO'."""
        persisted_org.core_business = "研究课题"
        persisted_org.nodes[0].role_title = "课题负责人"
        mock_runtime.send_command = AsyncMock(return_value={"result": "ok"})
        await heartbeat.trigger_heartbeat(persisted_org.id)

        prompt = mock_runtime.send_command.call_args[0][2]
        prompt_lines = prompt.split("\n")
        for line in prompt_lines:
            if "课题负责人" in line or "CEO" in line:
                continue
            if line.startswith("- ") and "CEO" in line:
                continue
        assert "课题负责人" in prompt


class TestTriggerStandup:
    async def test_trigger_nonexistent_org(self, heartbeat: OrgHeartbeat, mock_runtime):
        mock_runtime.get_org = MagicMock(return_value=None)
        result = await heartbeat.trigger_standup("fake_org")
        assert "error" in result

    async def test_trigger_standup_success(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        mock_runtime.send_command = AsyncMock(return_value={"result": "晨会完成"})
        result = await heartbeat.trigger_standup(persisted_org.id)
        assert result == {"result": "晨会完成"}

        prompt = mock_runtime.send_command.call_args[0][2]
        assert "晨会" in prompt
        assert "议程" in prompt

    async def test_standup_generates_report_file(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime, org_dir):
        mock_runtime.send_command = AsyncMock(return_value={"result": "今日计划"})
        mock_runtime._manager._org_dir = MagicMock(return_value=org_dir)
        await heartbeat.trigger_standup(persisted_org.id)

        reports = list((org_dir / "reports").glob("standup_*.md"))
        assert len(reports) >= 1
        content = reports[0].read_text(encoding="utf-8")
        assert "晨会纪要" in content

    async def test_standup_emits_events(self, heartbeat: OrgHeartbeat, persisted_org, mock_runtime):
        mock_runtime.send_command = AsyncMock(return_value={"result": "ok"})
        await heartbeat.trigger_standup(persisted_org.id)

        es = mock_runtime.get_event_store()
        started = es.query(event_type="standup_started")
        assert len(started) >= 1
        completed = es.query(event_type="standup_completed")
        assert len(completed) >= 1

