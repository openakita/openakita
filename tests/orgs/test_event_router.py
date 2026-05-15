from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openakita.orgs.command_service import (
    OrgCommandRequest,
    OrgCommandService,
    OrgCommandSurface,
    OrgOutputScope,
    set_command_service,
)
from openakita.orgs.event_router import route_org_event, summarize_org_event
from openakita.orgs.models import OrgStatus


def test_summarize_internal_events_without_leaking_message_body():
    assert summarize_org_event(
        "org:node_status",
        {"node_id": "writer", "status": "busy", "current_task": "写一版文案"},
    ) == "writer 开始处理：写一版文案"
    assert summarize_org_event("org:message", {"content": "内部细节"}) is None


@pytest.mark.asyncio
async def test_router_publishes_only_for_external_scopes(persisted_org):
    persisted_org.status = OrgStatus.ACTIVE
    rt = MagicMock()
    rt.get_org.return_value = persisted_org
    service = OrgCommandService(rt, None)
    service._schedule_run = MagicMock()
    set_command_service(service)
    try:
        started = service.submit(
            OrgCommandRequest(
                org_id=persisted_org.id,
                content="任务",
                origin_surface=OrgCommandSurface.DESKTOP_CHAT,
                output_scope=OrgOutputScope.CHAT_SUMMARY,
            )
        )
        queue = service.subscribe_summary(started["command_id"], surface="desktop_chat", target="conv")
        await route_org_event(
            "org:node_status",
            {
                "org_id": persisted_org.id,
                "command_id": started["command_id"],
                "node_id": "node_ceo",
                "status": "busy",
                "current_task": "处理任务",
            },
        )
        item = await queue.get()
        assert item["type"] == "org_progress"
        assert item["summary"] == "node_ceo 开始处理：处理任务"

        await route_org_event(
            "org:message",
            {
                "org_id": persisted_org.id,
                "command_id": started["command_id"],
                "content": "内部节点对话",
            },
        )
        assert queue.empty()
    finally:
        set_command_service(None)


@pytest.mark.asyncio
async def test_final_only_suppresses_progress(persisted_org):
    persisted_org.status = OrgStatus.ACTIVE
    rt = MagicMock()
    rt.get_org.return_value = persisted_org
    service = OrgCommandService(rt, None)
    service._schedule_run = MagicMock()
    set_command_service(service)
    try:
        started = service.submit(
            OrgCommandRequest(
                org_id=persisted_org.id,
                content="任务",
                origin_surface=OrgCommandSurface.IM,
                output_scope=OrgOutputScope.FINAL_ONLY,
            )
        )
        queue = service.subscribe_summary(started["command_id"], surface="im", target="chat")
        await route_org_event(
            "org:node_status",
            {
                "org_id": persisted_org.id,
                "command_id": started["command_id"],
                "node_id": "node_ceo",
                "status": "busy",
            },
        )
        assert queue.empty()
    finally:
        set_command_service(None)
