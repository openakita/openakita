from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.command_service import (
    OrgCommandConflict,
    OrgCommandRequest,
    OrgCommandService,
    OrgCommandSurface,
    OrgOutputScope,
    default_scope_for_surface,
)
from openakita.orgs.models import OrgStatus


def test_default_scope_for_surfaces():
    assert default_scope_for_surface(OrgCommandSurface.ORG_CONSOLE) == OrgOutputScope.CONSOLE_FULL
    assert default_scope_for_surface(OrgCommandSurface.DESKTOP_CHAT) == OrgOutputScope.CHAT_SUMMARY
    assert default_scope_for_surface(OrgCommandSurface.IM, chat_type="private") == OrgOutputScope.IM_SUMMARY
    assert default_scope_for_surface(OrgCommandSurface.IM, chat_type="group") == OrgOutputScope.FINAL_ONLY


def test_submit_rejects_second_running_command(persisted_org):
    persisted_org.status = OrgStatus.ACTIVE
    rt = MagicMock()
    rt.get_org.return_value = persisted_org
    service = OrgCommandService(rt, None)
    service._schedule_run = MagicMock()

    first = service.submit(OrgCommandRequest(org_id=persisted_org.id, content="任务 A"))

    with pytest.raises(OrgCommandConflict) as exc:
        service.submit(OrgCommandRequest(org_id=persisted_org.id, content="任务 B"))

    assert exc.value.command_id == first["command_id"]


@pytest.mark.asyncio
async def test_replace_existing_cancels_previous_command_before_running_new(persisted_org):
    persisted_org.status = OrgStatus.ACTIVE
    rt = MagicMock()
    rt.get_org.return_value = persisted_org
    rt._has_active_delegations.return_value = True
    old_can_finish = asyncio.Event()
    new_started = asyncio.Event()
    new_can_finish = asyncio.Event()

    async def send_command(_org_id, _target_node_id, content, *, command_id):
        if content == "任务 A":
            await old_can_finish.wait()
            return {"result": "old done", "command_id": command_id}
        if content == "任务 B":
            new_started.set()
            await new_can_finish.wait()
            return {"result": "new done", "command_id": command_id}
        return {"result": "done", "command_id": command_id}

    async def cancel_user_command(_org_id, _command_id):
        old_can_finish.set()
        return {"ok": True, "cancelled_roots": ["node_ceo"]}

    rt.send_command = AsyncMock(side_effect=send_command)
    rt.cancel_user_command = AsyncMock(side_effect=cancel_user_command)
    service = OrgCommandService(rt, None)

    first = service.submit(OrgCommandRequest(org_id=persisted_org.id, content="任务 A"))
    second = service.submit(
        OrgCommandRequest(
            org_id=persisted_org.id,
            content="任务 B",
            replace_existing=True,
        )
    )
    await asyncio.wait_for(new_started.wait(), timeout=1)

    rt.cancel_user_command.assert_awaited_with(persisted_org.id, first["command_id"])
    rt.send_command.assert_any_await(
        persisted_org.id,
        None,
        "任务 B",
        command_id=second["command_id"],
    )
    assert service._running_by_root[(persisted_org.id, "node_ceo")] == second["command_id"]
    new_can_finish.set()


def test_get_status_includes_scope_and_surface(persisted_org):
    persisted_org.status = OrgStatus.ACTIVE
    rt = MagicMock()
    rt.get_org.return_value = persisted_org
    rt.get_command_tracker_snapshot.return_value = None
    service = OrgCommandService(rt, None)
    service._schedule_run = MagicMock()

    started = service.submit(
        OrgCommandRequest(
            org_id=persisted_org.id,
            content="任务",
            origin_surface=OrgCommandSurface.DESKTOP_CHAT,
            output_scope=OrgOutputScope.CHAT_SUMMARY,
        )
    )

    status = service.get_status(persisted_org.id, started["command_id"])
    assert status is not None
    assert status["origin_surface"] == "desktop_chat"
    assert status["output_scope"] == "chat_summary"


@pytest.mark.asyncio
async def test_summary_delivery_records_target(persisted_org):
    persisted_org.status = OrgStatus.ACTIVE
    rt = MagicMock()
    rt.get_org.return_value = persisted_org
    service = OrgCommandService(rt, None)
    service._schedule_run = MagicMock()
    started = service.submit(OrgCommandRequest(org_id=persisted_org.id, content="任务"))
    queue = service.subscribe_summary(
        started["command_id"],
        surface="desktop_chat",
        target="conv-1",
    )

    await service.publish_summary(started["command_id"], {"type": "org_progress"})
    await queue.get()

    delivered = service.commands[started["command_id"]]["delivered_to"]
    assert delivered[-1]["surface"] == "desktop_chat"
    assert delivered[-1]["target"] == "conv-1"


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_finished_command(persisted_org):
    persisted_org.status = OrgStatus.ACTIVE
    rt = MagicMock()
    rt.get_org.return_value = persisted_org
    rt.get_command_tracker_snapshot.return_value = None
    service = OrgCommandService(rt, None)
    service._schedule_run = MagicMock()
    started = service.submit(OrgCommandRequest(org_id=persisted_org.id, content="任务"))
    service._update_command_state(started["command_id"], status="done", phase="done")

    result = await service.cancel(persisted_org.id, started["command_id"])
    assert result == {"ok": True, "command_id": started["command_id"], "already_done": True}


def test_purge_removes_old_finished_commands(persisted_org):
    persisted_org.status = OrgStatus.ACTIVE
    rt = MagicMock()
    rt.get_org.return_value = persisted_org
    service = OrgCommandService(rt, None)
    old_id = "oldcmd"
    service.commands[old_id] = {
        "command_id": old_id,
        "org_id": persisted_org.id,
        "root_node_id": "node_ceo",
        "status": "done",
        "phase": "done",
        "result": {},
        "error": None,
        "created_at": time.time() - 7200,
        "updated_at": time.time() - 7200,
    }

    service._purge_old_commands()
    assert old_id not in service.commands
