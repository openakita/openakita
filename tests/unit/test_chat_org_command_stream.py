"""Regression coverage for organization commands sent from desktop chat."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from openakita.api.routes.chat import _stream_org_command_chat
from openakita.api.routes.sessions import _history_entry
from openakita.api.schemas import AttachmentInfo, ChatRequest
from openakita.orgs.command_models import OrgCommandConflict


def _decode_sse(raw: str) -> dict[str, Any]:
    assert raw.startswith("data: ")
    return json.loads(raw.removeprefix("data: ").strip())


class _CommandService:
    def __init__(self, *, submit_error: Exception | None = None) -> None:
        self.request = None
        self.submit_error = submit_error

    async def submit(self, request):
        self.request = request
        if self.submit_error is not None:
            raise self.submit_error
        return {"command_id": "cmd_1", "status": "running", "root_node_id": "root_1"}

    def subscribe_summary(self, command_id: str, *, surface: str, target: str):
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        queue.put_nowait(
            {
                "type": "org_command_done",
                "command_id": command_id,
                "result": {"deliverable": "组织任务完成"},
            }
        )
        return queue

    def unsubscribe_summary(self, command_id: str, queue) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []
        self.last_active = datetime.now()

    def add_message(self, role: str, content: str, **metadata) -> None:
        self.messages.append((role, content, metadata))

    def set_metadata(self, key: str, value: Any) -> None:
        return None


class _SessionManager:
    def __init__(self) -> None:
        self.session = _Session()
        self.dirty = False
        self.persisted = False

    def get_session(self, **kwargs):
        return self.session

    def mark_dirty(self) -> None:
        self.dirty = True

    def persist(self) -> None:
        self.persisted = True


async def _collect(
    service: _CommandService,
    body: ChatRequest,
    *,
    session_manager: _SessionManager | None = None,
) -> list[dict[str, Any]]:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                org_command_service=service,
                session_manager=session_manager,
            )
        )
    )
    return [
        _decode_sse(event)
        async for event in _stream_org_command_chat(
            body,
            request=request,
            conversation_id="conversation_1",
            client_id="",
            busy_generation=0,
        )
    ]


@pytest.mark.asyncio
async def test_org_chat_awaits_submit_and_forwards_input_attachments() -> None:
    service = _CommandService()
    body = ChatRequest(
        message="开始创作",
        org_mode=True,
        org_id="org_1",
        attachments=[AttachmentInfo(type="file", name="brief.txt", local_path="C:\\brief.txt")],
    )

    events = await _collect(service, body)

    assert service.request is not None
    assert service.request.org_id == "org_1"
    assert service.request.content == "开始创作"
    assert service.request.input_attachments == [
        {
            "type": "file",
            "name": "brief.txt",
            "localPath": "C:\\brief.txt",
            "uploadStatus": "uploaded",
        }
    ]
    assert [event["type"] for event in events] == [
        "org_command_started",
        "org_command_done",
        "text_replace",
        "done",
    ]
    assert events[2]["content"] == "组织任务完成"


@pytest.mark.asyncio
async def test_org_chat_surfaces_unexpected_submit_errors() -> None:
    service = _CommandService(submit_error=TypeError("request contract drift"))
    body = ChatRequest(message="开始创作", org_mode=True, org_id="org_1")

    session_manager = _SessionManager()
    events = await _collect(service, body, session_manager=session_manager)

    assert [event["type"] for event in events] == ["error", "done"]
    assert events[0]["message"] == "组织命令提交失败，请重试。"
    assert session_manager.persisted is True
    assert session_manager.session.messages[-1][2]["error_info"]["error_code"] == (
        "org_command_submit_failed"
    )


@pytest.mark.asyncio
async def test_org_chat_exposes_non_runnable_status_for_localization() -> None:
    error = OrgCommandConflict(
        "组织尚未启动。当前状态: dormant",
        command_id="",
        error_code="org_not_runnable",
        org_status="dormant",
    )
    service = _CommandService(submit_error=error)
    body = ChatRequest(message="开始创作", org_mode=True, org_id="org_1")

    session_manager = _SessionManager()
    events = await _collect(service, body, session_manager=session_manager)

    assert events[0]["type"] == "error"
    assert events[0]["error_code"] == "org_not_runnable"
    assert events[0]["org_status"] == "dormant"
    assert session_manager.persisted is True
    role, content, metadata = session_manager.session.messages[-1]
    assert role == "assistant"
    assert content == ""
    assert metadata["error_info"] == {
        "message": "组织尚未启动。当前状态: dormant",
        "raw": "组织尚未启动。当前状态: dormant",
        "error_code": "org_not_runnable",
        "org_status": "dormant",
    }


def test_session_history_serializes_persisted_org_error() -> None:
    session = _Session()
    error_info = {
        "message": "组织尚未启动。当前状态: dormant",
        "raw": "组织尚未启动。当前状态: dormant",
        "error_code": "org_not_runnable",
        "org_status": "dormant",
    }

    entry = _history_entry(
        session,
        "conversation_1",
        1,
        {"role": "assistant", "content": "", "error_info": error_info},
    )

    assert entry["error_info"] == error_info
