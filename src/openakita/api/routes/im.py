"""IM channel viewer API for Setup Center."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_gateway(request: Request):
    """Get the MessageGateway from app state (set by server.create_app)."""
    return getattr(request.app.state, "gateway", None)


def _get_session_manager(request: Request):
    """Get the SessionManager from app state (set by server.create_app)."""
    return getattr(request.app.state, "session_manager", None)


def _get_bot_config(request: Request):
    gateway = _get_gateway(request)
    return getattr(gateway, "bot_config", None) if gateway else None


def _get_chat_aliases(request: Request):
    gateway = _get_gateway(request)
    return getattr(gateway, "chat_aliases", None) if gateway else None


def _notify_im_event(event: str, data: dict | None = None) -> None:
    try:
        from openakita.api.routes.websocket import broadcast_event

        asyncio.ensure_future(broadcast_event(event, data))
    except Exception:
        pass


@router.get("/api/im/channels")
async def list_channels(request: Request):
    """Return all configured IM channels with online status."""
    channels: list[dict[str, Any]] = []

    gateway = _get_gateway(request)
    if gateway is None:
        return JSONResponse(content={"channels": channels})

    # Build bot_id -> bot_name lookup from settings
    from openakita.config import settings

    bot_name_map: dict[str, str] = {}
    for b in getattr(settings, "im_bots", []):
        if isinstance(b, dict) and b.get("id") and b.get("name"):
            bot_name_map[b["id"]] = b["name"]

    # _adapters is a dict {name: adapter} in MessageGateway
    adapters_dict = getattr(gateway, "_adapters", None) or {}
    adapters_list = getattr(gateway, "adapters", [])
    if isinstance(adapters_dict, dict):
        adapter_items = list(adapters_dict.items())
    else:
        adapter_items = [
            (getattr(a, "name", f"adapter_{i}"), a) for i, a in enumerate(adapters_list)
        ]

    session_mgr = _get_session_manager(request)

    for adapter_name, adapter in adapter_items:
        name = (
            adapter_name
            or getattr(adapter, "name", None)
            or getattr(adapter, "channel_type", "unknown")
        )
        # ChannelAdapter base class has is_running property (backed by _running flag)
        status = (
            "online"
            if getattr(adapter, "is_running", False) or getattr(adapter, "_running", False)
            else "offline"
        )
        session_count = 0
        last_active = None
        if session_mgr:
            sessions = getattr(session_mgr, "_sessions", {})
            channel_sessions = [s for s in sessions.values() if getattr(s, "channel", None) == name]
            session_count = len(channel_sessions)
            if channel_sessions:
                times = [
                    getattr(s, "last_active", None) or getattr(s, "updated_at", None)
                    for s in channel_sessions
                ]
                times = [t for t in times if t is not None]
                if times:
                    last_active = str(max(times))
        bot_id = getattr(adapter, "bot_id", None) or name
        display_name = (
            getattr(adapter, "display_name", None)
            or bot_name_map.get(bot_id)
            or bot_name_map.get(name)
            or name
        )
        entry: dict[str, Any] = {
            "channel": name,
            "channel_type": getattr(adapter, "channel_type", name.split(":")[0]),
            "name": display_name,
            "status": status,
            "sessionCount": session_count,
            "lastActive": last_active,
        }
        if status == "offline":
            reasons = getattr(gateway, "_failed_adapter_reasons", {})
            if name in reasons:
                entry["error"] = reasons[name]
        channels.append(entry)

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

        # 消息存储在 sess.context.messages（而非 sess.history/sess.messages）
        ctx = getattr(sess, "context", None)
        history = getattr(ctx, "messages", []) if ctx else []
        if history:
            msg_count = len(history)
            last_item = history[-1]
            if isinstance(last_item, dict):
                last_msg = (last_item.get("content") or "")[:100]
            else:
                last_msg = str(getattr(last_item, "content", ""))[:100]

        # SessionState 是 Enum，需要取 .value 才能 JSON 序列化
        state = getattr(sess, "state", "active")
        state_str = state.value if hasattr(state, "value") else str(state)

        _chat_type = getattr(sess, "chat_type", "private") or "private"
        _display_name = getattr(sess, "display_name", "") or ""
        _chat_name = getattr(sess, "chat_name", "") or ""
        _user_id = getattr(sess, "user_id", None)
        _chat_id = getattr(sess, "chat_id", None)
        _sess_id = str(sid)

        bot_config = _get_bot_config(request)
        _bot_enabled = (
            bot_config.is_enabled(sess_channel, _chat_id or "", _user_id or "")
            if bot_config
            else True
        )
        _response_mode = (
            bot_config.get_response_mode(sess_channel, _chat_id or "", "*") if bot_config else None
        )

        alias_store = _get_chat_aliases(request)
        _alias = (
            alias_store.get_alias(sess_channel, _chat_id)
            if alias_store and sess_channel and _chat_id
            else None
        )

        result.append(
            {
                "sessionId": _sess_id,
                "channel": sess_channel,
                "chatId": _chat_id,
                "userId": _user_id,
                "chatType": _chat_type,
                "chatName": _chat_name,
                "displayName": _display_name or _user_id or _chat_id or _sess_id[:12],
                "alias": _alias,
                "state": state_str,
                "lastActive": str(
                    getattr(sess, "last_active", None) or getattr(sess, "updated_at", "")
                ),
                "messageCount": msg_count,
                "lastMessage": last_msg,
                "botEnabled": _bot_enabled,
                "responseMode": _response_mode,
            }
        )

    return JSONResponse(content={"sessions": result})


def _get_storage():
    try:
        from openakita.config import settings
        from openakita.memory.storage import get_shared_storage

        return get_shared_storage(settings.project_root / "data" / "memory" / "openakita.db")
    except Exception:
        return None


def _to_safe_session_id(session_key: str) -> str:
    """Convert session_key (e.g. 'telegram:123:user') to the safe format
    used as SQLite session_id (e.g. 'telegram__123__user').

    Must match the logic in agent.py _prepare_session_context.
    """
    safe = session_key.replace(":", "__")
    return re.sub(r'[/\\+=%?*<>|"\x00-\x1f]', "_", safe)


@router.get("/api/im/sessions/{session_id}/messages")
async def get_session_messages(
    request: Request,
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: str = Query("memory"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    latest: bool = Query(False),
):
    """Return messages for a specific session.

    source=memory (default) or sqlite.
    date_from / date_to: optional ISO date strings (e.g. '2026-03-19') to filter.
    latest: if true, return the last `limit` messages (auto-calculate offset).
    """

    if source == "sqlite":
        storage = _get_storage()
        if storage:
            safe_id = _to_safe_session_id(session_id)
            rows, total = storage.list_turns(
                safe_id,
                limit,
                offset,
                date_from=date_from,
                date_to=date_to,
            )
            messages = [
                {
                    "id": r.get("id"),
                    "role": r.get("role", "user"),
                    "content": r.get("content", ""),
                    "timestamp": r.get("timestamp", ""),
                    "tool_calls": r.get("tool_calls"),
                    "tool_results": r.get("tool_results"),
                }
                for r in rows
            ]
            return JSONResponse(
                content={
                    "messages": messages,
                    "total": total,
                    "hasMore": offset + limit < total,
                    "source": "sqlite",
                }
            )

    session_mgr = _get_session_manager(request)
    if session_mgr is None:
        return JSONResponse(content={"messages": [], "total": 0, "hasMore": False})

    sessions = getattr(session_mgr, "_sessions", {})
    sess = sessions.get(session_id)
    if sess is None:
        return JSONResponse(content={"messages": [], "total": 0, "hasMore": False})

    ctx = getattr(sess, "context", None)
    history = getattr(ctx, "messages", []) if ctx else []

    if date_from or date_to:

        def _in_range(item: Any) -> bool:
            ts = (
                item.get("timestamp", "")
                if isinstance(item, dict)
                else str(getattr(item, "timestamp", ""))
            )
            if not ts:
                return True
            if date_from and ts < date_from:
                return False
            if date_to and ts > date_to + "T23:59:59.999999":
                return False
            return True

        history = [m for m in history if _in_range(m)]

    total = len(history)
    if latest:
        offset = max(0, total - limit)
    page = history[offset : offset + limit]

    def _to_str(content: Any) -> str:
        """Ensure content is a plain string for the API response.

        Anthropic MessageParam may store content as a list of content blocks.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return str(content) if content is not None else ""

    messages: list[dict[str, Any]] = []
    for item in page:
        if isinstance(item, dict):
            messages.append(
                {
                    "role": item.get("role", "user"),
                    "content": _to_str(item.get("content", "")),
                    "timestamp": item.get("timestamp", ""),
                    "metadata": item.get("metadata"),
                    "chain_summary": item.get("chain_summary"),
                }
            )
        else:
            messages.append(
                {
                    "role": getattr(item, "role", "user"),
                    "content": _to_str(getattr(item, "content", "")),
                    "timestamp": str(getattr(item, "timestamp", "")),
                    "metadata": getattr(item, "metadata", None),
                    "chain_summary": getattr(item, "chain_summary", None),
                }
            )

    storage = _get_storage()
    if storage and messages:
        try:
            safe_id = _to_safe_session_id(session_id)
            turns, _ = storage.list_turns(
                safe_id, limit=9999, offset=0, date_from=date_from, date_to=date_to
            )
            content_id_map: dict[tuple[str, str], int] = {}
            for t in turns:
                if t.get("id") is not None:
                    key = (t.get("role", ""), _to_str(t.get("content", ""))[:200])
                    content_id_map.setdefault(key, t["id"])
            matched = 0
            for msg in messages:
                key = (msg.get("role", ""), (msg.get("content") or "")[:200])
                msg["id"] = content_id_map.get(key)
                if msg["id"] is not None:
                    matched += 1
            if messages and matched == 0:
                logger.warning(
                    "ID matching failed for session %s: 0/%d messages matched (sqlite turns=%d)",
                    session_id,
                    len(messages),
                    len(turns),
                )
        except Exception as exc:
            logger.warning("ID matching error for session %s: %s", session_id, exc)

    return JSONResponse(
        content={
            "messages": messages,
            "total": total,
            "offset": offset,
            "hasMore": offset + limit < total,
            "source": "memory",
        }
    )


class DeleteMessagesRequest(BaseModel):
    turn_ids: list[int]


@router.post("/api/im/sessions/{session_id}/messages/delete")
async def delete_session_messages(request: Request, session_id: str, body: DeleteMessagesRequest):
    """Delete specific messages (conversation_turns) by their SQLite IDs."""
    storage = _get_storage()
    if storage is None:
        return JSONResponse(status_code=500, content={"error": "storage not available"})

    # Fetch content of turns before deleting so we can match them in memory
    deleted_keys: set[tuple[str, str]] = set()
    try:
        safe_id = _to_safe_session_id(session_id)
        all_turns, _ = storage.list_turns(safe_id, limit=9999, offset=0)
        turn_id_set = set(body.turn_ids)
        for t in all_turns:
            if t.get("id") in turn_id_set:
                deleted_keys.add((t.get("role", ""), (t.get("content") or "")[:200]))
    except Exception as exc:
        logger.warning("[IM] Failed to build deleted_keys for session %s: %s", session_id, exc)

    deleted = storage.delete_turns(body.turn_ids)
    if deleted:
        logger.info(f"[IM] Deleted {deleted} message(s) from session {session_id}")

    # Remove matching messages from in-memory session context
    session_mgr = _get_session_manager(request)
    if session_mgr and deleted_keys:
        sessions = getattr(session_mgr, "_sessions", {})
        sess = sessions.get(session_id)
        if sess:
            ctx = getattr(sess, "context", None)
            if ctx:
                msgs = getattr(ctx, "messages", [])
                if msgs:

                    def _msg_key(m: Any) -> tuple[str, str]:
                        if isinstance(m, dict):
                            return (m.get("role", ""), (m.get("content") or "")[:200])
                        return (getattr(m, "role", ""), str(getattr(m, "content", ""))[:200])

                    ctx.messages = [m for m in msgs if _msg_key(m) not in deleted_keys]

    return JSONResponse(content={"ok": True, "deleted": deleted})


@router.delete("/api/im/sessions/{session_id}")
async def delete_im_session(request: Request, session_id: str):
    """Close and remove an IM session, including its SQLite conversation_turns."""
    session_mgr = _get_session_manager(request)
    if session_mgr is None:
        return JSONResponse(content={"ok": False, "error": "session_manager not available"})

    removed = session_mgr.close_session(session_id)
    if removed:
        logger.info(f"[IM] Deleted session via API: {session_id}")

    turns_deleted = 0
    try:
        from openakita.config import settings
        from openakita.memory.storage import get_shared_storage

        db_path = settings.project_root / "data" / "memory" / "openakita.db"
        storage = get_shared_storage(db_path)
        turns_deleted = storage.delete_turns_for_session(_to_safe_session_id(session_id))
        if turns_deleted:
            logger.info(f"[IM] Purged {turns_deleted} conversation_turns for session: {session_id}")
    except Exception as e:
        logger.warning(f"[IM] Failed to purge conversation_turns for {session_id}: {e}")

    return JSONResponse(content={"ok": True, "removed": removed, "turnsDeleted": turns_deleted})


# ─── Bot Config (per-chat enable/disable) ────────────────────────────────


class BotConfigRequest(BaseModel):
    channel: str
    chat_id: str
    user_id: str = "*"
    enabled: bool
    response_mode: str | None = None


@router.get("/api/im/bot-config")
async def list_bot_config(request: Request, channel: str = Query("")):
    bot_config = _get_bot_config(request)
    if bot_config is None:
        return JSONResponse(content={"rules": []})
    return JSONResponse(content={"rules": bot_config.list_rules(channel or None)})


@router.post("/api/im/bot-config")
async def set_bot_config(request: Request, body: BotConfigRequest):
    bot_config = _get_bot_config(request)
    if bot_config is None:
        return JSONResponse(status_code=500, content={"error": "bot_config not available"})
    from openakita.channels.bot_config import BotConfigRule

    bot_config.set_rule(
        BotConfigRule(
            channel=body.channel,
            chat_id=body.chat_id,
            user_id=body.user_id,
            enabled=body.enabled,
            response_mode=body.response_mode,
        )
    )
    _notify_im_event(
        "im:bot_config_changed",
        {
            "channel": body.channel,
            "chat_id": body.chat_id,
            "enabled": body.enabled,
            "response_mode": body.response_mode,
        },
    )
    return JSONResponse(content={"ok": True})


@router.delete("/api/im/bot-config")
async def delete_bot_config(
    request: Request,
    channel: str = Query(...),
    chat_id: str = Query(...),
    user_id: str = Query("*"),
):
    bot_config = _get_bot_config(request)
    if bot_config is None:
        return JSONResponse(status_code=500, content={"error": "bot_config not available"})
    removed = bot_config.delete_rule(channel, chat_id, user_id)
    if removed:
        _notify_im_event("im:bot_config_changed", {"channel": channel, "chat_id": chat_id})
    return JSONResponse(content={"ok": True, "removed": removed})


# ─── Chat Aliases (per-chat custom display names) ────────────────────────


class ChatAliasRequest(BaseModel):
    channel: str
    chat_id: str
    alias: str


@router.get("/api/im/chat-aliases")
async def list_chat_aliases(request: Request, channel: str = Query("")):
    store = _get_chat_aliases(request)
    if store is None:
        return JSONResponse(content={"aliases": {}})
    return JSONResponse(content={"aliases": store.list_aliases(channel or None)})


@router.post("/api/im/chat-aliases")
async def set_chat_alias(request: Request, body: ChatAliasRequest):
    store = _get_chat_aliases(request)
    if store is None:
        return JSONResponse(status_code=500, content={"error": "chat_aliases not available"})
    store.set_alias(body.channel, body.chat_id, body.alias)
    _notify_im_event(
        "im:chat_alias_changed",
        {
            "channel": body.channel,
            "chat_id": body.chat_id,
            "alias": body.alias,
        },
    )
    return JSONResponse(content={"ok": True})


@router.delete("/api/im/chat-aliases")
async def delete_chat_alias(
    request: Request,
    channel: str = Query(...),
    chat_id: str = Query(...),
):
    store = _get_chat_aliases(request)
    if store is None:
        return JSONResponse(status_code=500, content={"error": "chat_aliases not available"})
    removed = store.delete_alias(channel, chat_id)
    if removed:
        _notify_im_event(
            "im:chat_alias_changed", {"channel": channel, "chat_id": chat_id, "alias": None}
        )
    return JSONResponse(content={"ok": True, "removed": removed})


# ─── Group Policy Management ─────────────────────────────────────────────

_GROUP_POLICY_PATH = Path("data/sessions/group_policy.json")


def _load_group_policy() -> dict:
    if _GROUP_POLICY_PATH.exists():
        try:
            import json

            return json.loads(_GROUP_POLICY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_group_policy(data: dict) -> None:
    from openakita.utils.atomic_io import atomic_json_write

    _GROUP_POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(_GROUP_POLICY_PATH, data)


@router.get("/api/im/group-policy")
async def get_group_policy(request: Request, channel: str = Query("")):
    """Return the current group response mode + allowlist for a channel."""
    gateway = _get_gateway(request)
    if gateway is None:
        return JSONResponse(content={"mode": "mention_only", "allowlist": [], "groups": []})

    mode = gateway._get_group_response_mode(channel, "", "*").value if channel else "mention_only"
    allowlist = list(gateway._get_group_allowlist(channel)) if channel else []

    groups: list[dict[str, Any]] = []
    session_mgr = _get_session_manager(request)
    if session_mgr and channel:
        sessions = getattr(session_mgr, "_sessions", {})
        seen: set[str] = set()
        for sess in sessions.values():
            if getattr(sess, "channel", None) != channel:
                continue
            if getattr(sess, "chat_type", "private") != "group":
                continue
            cid = getattr(sess, "chat_id", None)
            if not cid or cid in seen:
                continue
            seen.add(cid)
            groups.append(
                {
                    "chatId": cid,
                    "chatName": getattr(sess, "chat_name", "") or "",
                    "allowed": cid in allowlist,
                }
            )

    return JSONResponse(content={"mode": mode, "allowlist": allowlist, "groups": groups})


class GroupPolicyRequest(BaseModel):
    channel: str
    mode: str
    allowlist: list[str] = []


@router.post("/api/im/group-policy")
async def set_group_policy(request: Request, body: GroupPolicyRequest):
    """Update group response mode + allowlist for a channel (runtime + persisted)."""
    gateway = _get_gateway(request)
    if gateway is None:
        return JSONResponse(status_code=500, content={"error": "gateway not available"})

    from openakita.channels.group_response import GroupResponseMode

    try:
        GroupResponseMode(body.mode)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": f"Invalid mode: {body.mode}"})

    adapter = gateway._adapters.get(body.channel)
    if adapter is not None:
        adapter._group_response_mode = body.mode
        adapter._group_allowlist = set(body.allowlist)

    policy_data = _load_group_policy()
    policy_data[body.channel] = {"mode": body.mode, "allowlist": body.allowlist}
    _save_group_policy(policy_data)

    _notify_im_event("im:group_policy_changed", {"channel": body.channel, "mode": body.mode})
    return JSONResponse(content={"ok": True})


@router.get("/api/im/telegram/pairing-code")
async def get_telegram_pairing_code(request: Request):
    """Return the current Telegram pairing code (from running adapter or file)."""
    gateway = _get_gateway(request)
    if gateway:
        adapters_dict = getattr(gateway, "_adapters", None) or {}
        for adapter in adapters_dict.values():
            pm = getattr(adapter, "pairing_manager", None)
            if pm and hasattr(pm, "pairing_code"):
                return JSONResponse(content={"code": pm.pairing_code})

    code_file = Path("data/telegram/pairing/pairing_code.txt")
    if code_file.exists():
        try:
            code = code_file.read_text(encoding="utf-8").strip()
            if code:
                return JSONResponse(content={"code": code})
        except Exception:
            pass

    return JSONResponse(content={"code": None})
