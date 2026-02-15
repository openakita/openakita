"""IM channel viewer API — read-only endpoints for Setup Center."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_gateway(request: Request):
    """Get the MessageGateway from app state (set by server.create_app)."""
    return getattr(request.app.state, "gateway", None)


def _get_session_manager(request: Request):
    """Get the SessionManager from app state (set by server.create_app)."""
    return getattr(request.app.state, "session_manager", None)


@router.get("/api/im/channels")
async def list_channels(request: Request):
    """Return all configured IM channels with online status."""
    channels: list[dict[str, Any]] = []

    gateway = _get_gateway(request)
    if gateway is None:
        return JSONResponse(content={"channels": channels})

    # _adapters is a dict {name: adapter} in MessageGateway
    adapters_dict = getattr(gateway, "_adapters", None) or {}
    adapters_list = getattr(gateway, "adapters", [])
    if isinstance(adapters_dict, dict):
        adapter_items = list(adapters_dict.items())
    else:
        adapter_items = [(getattr(a, "name", f"adapter_{i}"), a) for i, a in enumerate(adapters_list)]

    session_mgr = _get_session_manager(request)

    for adapter_name, adapter in adapter_items:
        name = adapter_name or getattr(adapter, "name", None) or getattr(adapter, "channel_type", "unknown")
        # ChannelAdapter base class has is_running property (backed by _running flag)
        status = "online" if getattr(adapter, "is_running", False) or getattr(adapter, "_running", False) else "offline"
        session_count = 0
        last_active = None
        if session_mgr:
            sessions = getattr(session_mgr, "_sessions", {})
            channel_sessions = [s for s in sessions.values() if getattr(s, "channel", None) == name]
            session_count = len(channel_sessions)
            if channel_sessions:
                times = [getattr(s, "last_active", None) or getattr(s, "updated_at", None) for s in channel_sessions]
                times = [t for t in times if t is not None]
                if times:
                    last_active = str(max(times))
        channels.append({
            "channel": name,
            "name": getattr(adapter, "display_name", name),
            "status": status,
            "sessionCount": session_count,
            "lastActive": last_active,
        })

    return JSONResponse(content={"channels": channels})


@router.get("/api/im/sessions")
async def list_sessions(request: Request, channel: str = Query("")):
    """Return sessions for a given IM channel."""
    result: list[dict[str, Any]] = []

    session_mgr = _get_session_manager(request)
    if session_mgr is None:
        return JSONResponse(content={"sessions": result})

    sessions = getattr(session_mgr, "_sessions", {})
    for sid, sess in sessions.items():
        sess_channel = getattr(sess, "channel", None)
        if channel and sess_channel != channel:
            continue
        msg_count = 0
        last_msg = None
        history = getattr(sess, "history", []) or getattr(sess, "messages", [])
        if history:
            msg_count = len(history)
            last_item = history[-1]
            if isinstance(last_item, dict):
                last_msg = (last_item.get("content") or "")[:100]
            else:
                last_msg = str(getattr(last_item, "content", ""))[:100]

        result.append({
            "sessionId": str(sid),
            "channel": sess_channel,
            "chatId": getattr(sess, "chat_id", None),
            "userId": getattr(sess, "user_id", None),
            "state": getattr(sess, "state", "active"),
            "lastActive": str(getattr(sess, "last_active", None) or getattr(sess, "updated_at", "")),
            "messageCount": msg_count,
            "lastMessage": last_msg,
        })

    return JSONResponse(content={"sessions": result})


@router.get("/api/im/sessions/{session_id}/messages")
async def get_session_messages(
    request: Request,
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Return messages for a specific session."""
    session_mgr = _get_session_manager(request)
    if session_mgr is None:
        return JSONResponse(content={"messages": [], "total": 0, "hasMore": False})

    sessions = getattr(session_mgr, "_sessions", {})
    sess = sessions.get(session_id)
    if sess is None:
        return JSONResponse(content={"messages": [], "total": 0, "hasMore": False})

    history = getattr(sess, "history", []) or getattr(sess, "messages", [])
    total = len(history)
    page = history[offset: offset + limit]

    messages: list[dict[str, Any]] = []
    for item in page:
        if isinstance(item, dict):
            messages.append({
                "role": item.get("role", "user"),
                "content": item.get("content", ""),
                "timestamp": item.get("timestamp", ""),
                "metadata": item.get("metadata"),
            })
        else:
            messages.append({
                "role": getattr(item, "role", "user"),
                "content": str(getattr(item, "content", "")),
                "timestamp": str(getattr(item, "timestamp", "")),
                "metadata": getattr(item, "metadata", None),
            })

    return JSONResponse(content={
        "messages": messages,
        "total": total,
        "hasMore": offset + limit < total,
    })
