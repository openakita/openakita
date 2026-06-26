from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from openakita.inbox import get_inbox_service

router = APIRouter()


class UpdateEventRequest(BaseModel):
    from_version: str | None = None
    to_version: str | None = None
    version: str | None = None
    platform: str | None = None
    channel: str | None = None
    event_type: str | None = None
    event: str | None = None
    update_plan_id: str | None = None
    detail: dict[str, Any] | None = Field(default=None)


@router.get("/api/inbox/messages")
async def list_messages(include_dismissed: bool = Query(default=False)) -> dict[str, Any]:
    service = get_inbox_service()
    messages = await service.list_messages(include_dismissed=include_dismissed)
    return {"messages": messages, "unread_count": await service.unread_count()}


@router.get("/api/inbox/messages/{message_id}")
async def get_message(message_id: str) -> dict[str, Any]:
    message = await get_inbox_service().get_message(message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inbox message not found")
    return message


@router.post("/api/inbox/messages/{message_id}/read")
async def mark_read(message_id: str) -> dict[str, Any]:
    return await _mark_event(message_id, "read")


@router.post("/api/inbox/messages/{message_id}/dismiss")
async def mark_dismissed(message_id: str) -> dict[str, Any]:
    return await _mark_event(message_id, "dismissed")


@router.post("/api/inbox/messages/{message_id}/clicked")
async def mark_clicked(message_id: str) -> dict[str, Any]:
    return await _mark_event(message_id, "clicked")


@router.post("/api/inbox/refresh")
async def refresh() -> dict[str, Any]:
    return await get_inbox_service().refresh()


@router.get("/api/inbox/unread-count")
async def unread_count() -> dict[str, int]:
    return {"unread_count": await get_inbox_service().unread_count()}


@router.get("/api/inbox/diagnostics")
async def diagnostics() -> dict[str, Any]:
    return await get_inbox_service().diagnostics()


@router.post("/api/inbox/update-event")
async def update_event(payload: UpdateEventRequest) -> dict[str, bool]:
    recorded = await get_inbox_service().record_update_event(payload.model_dump(exclude_none=True))
    return {"recorded": recorded}


async def _mark_event(message_id: str, event: str) -> dict[str, Any]:
    service = get_inbox_service()
    changed = await service.mark_event(message_id, event)
    if not changed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inbox message not found")
    return {"ok": True, "unread_count": await service.unread_count()}
