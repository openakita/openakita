"""
Sessions route: GET /api/sessions, GET /api/sessions/{conversation_id}/history,
POST /api/sessions, DELETE /api/sessions/{conversation_id},
PATCH /api/sessions/{conversation_id}/title, POST /api/sessions/generate-title

提供桌面端 session 恢复能力：前端启动时可从后端加载对话列表和历史消息。
"""

from __future__ import annotations

import copy
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from openakita.api.schemas import ChatAttachmentRecord

logger = logging.getLogger(__name__)

router = APIRouter()


# 会话/频道/用户 ID 白名单：允许字母、数字、下划线、短横线、点、冒号、@；
# 上限 128 字节。挡住路径穿越/控制字符/SQL 元字符等异常输入。
# 与 schemas.ChatRequest.conversation_id 模式保持一致（UUID/IM chatroom@xxx 都覆盖）。
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.@]{1,128}$")
_DEFAULT_HISTORY_LIMIT = 80
_MAX_HISTORY_LIMIT = 200
_META_CONVERSATION_TITLE = "conversation_title"
_META_TITLE_MANUALLY_SET = "title_manually_set"
_META_TITLE_GENERATED = "title_generated"
_META_PINNED = "pinned"


def _validate_id(value: str, field: str) -> None:
    """对会话/频道/用户 ID 进行白名单校验，不通过即 422。"""
    if not isinstance(value, str) or not _ID_PATTERN.match(value):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field}: must match {_ID_PATTERN.pattern}",
        )


async def _broadcast_session_event(event: str, data: dict) -> None:
    """Broadcast a session lifecycle event via WebSocket."""
    try:
        from .websocket import broadcast_event

        await broadcast_event(event, data)
    except Exception:
        pass


class GenerateTitleRequest(BaseModel):
    message: str = Field(..., description="用户第一条消息")
    reply: str = Field("", description="AI 回复摘要（可选）")
    conversation_id: str = Field("", description="会话 ID（用于跨设备标题同步）")


class SessionUiStateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    endpoint_id: str | None = Field(None, alias="endpointId", max_length=200)
    endpoint_policy: Literal["prefer", "require"] = Field("prefer", alias="endpointPolicy")
    org_mode: bool = Field(False, alias="orgMode")
    org_id: str | None = Field(None, alias="orgId", max_length=128)
    org_node_id: str | None = Field(None, alias="orgNodeId", max_length=128)


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: str = Field(..., alias="conversationId", min_length=1, max_length=128)
    title: str = Field("新对话", max_length=120, description="Initial conversation title")
    title_manually_set: bool = Field(False, alias="titleManuallySet")
    title_generated: bool = Field(False, alias="titleGenerated")
    pinned: bool = Field(False, description="Whether the conversation is pinned")
    agent_profile_id: str | None = Field(None, alias="agentProfileId", max_length=128)
    endpoint_id: str | None = Field(None, alias="endpointId", max_length=200)
    endpoint_policy: Literal["prefer", "require"] = Field("prefer", alias="endpointPolicy")
    org_mode: bool = Field(False, alias="orgMode")
    org_id: str | None = Field(None, alias="orgId", max_length=128)
    org_node_id: str | None = Field(None, alias="orgNodeId", max_length=128)
    working_directory: str | None = Field(None, alias="workingDirectory", max_length=4096)


class SessionTitleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=120, description="会话标题")
    title_manually_set: bool = Field(True, alias="titleManuallySet")
    title_generated: bool = Field(False, alias="titleGenerated")


class SessionPinRequest(BaseModel):
    pinned: bool = Field(..., description="Whether the conversation is pinned")


def _normalize_title(title: str, *, fallback: str | None = None) -> str:
    normalized = " ".join(str(title or "").split()).strip()
    if normalized:
        return normalized
    return fallback or ""


def _apply_session_ui_state(
    session,
    *,
    endpoint_id: str | None,
    endpoint_policy: Literal["prefer", "require"],
    org_mode: bool,
    org_id: str | None,
    org_node_id: str | None,
) -> None:
    session.set_metadata("selected_endpoint", endpoint_id or "")
    session.set_metadata("endpoint_policy", endpoint_policy if endpoint_id else "prefer")
    session.set_metadata(
        "ui_org_state",
        {
            "orgMode": bool(org_mode and org_id),
            "orgId": org_id or "",
            "orgNodeId": org_node_id or "",
        },
    )


def _visible_history_messages(session) -> list[tuple[int, dict]]:
    """Return UI-visible session messages with their stable original indexes.

    PR-D1: 当内存里的 messages 比预期少（尤其崩溃后重启场景），按需从
    SQLite turn store 回填，保证 ``GET /api/sessions/{id}/history`` 不会
    在用户视角"突然空了"。
    """
    _maybe_backfill_messages(session)

    truncation_prefixes = ("[用户规则（必须遵守）]", "[历史背景，非当前任务]")
    visible: list[tuple[int, dict]] = []
    deduped_messages: list[dict] = []
    try:
        from ...sessions.session import is_duplicate_message
    except Exception:
        is_duplicate_message = None

    for idx, msg in enumerate(session.context.messages):
        content = msg.get("content", "")
        if (
            msg.get("role") == "system"
            and isinstance(content, str)
            and content.startswith(truncation_prefixes)
        ):
            continue
        if (
            is_duplicate_message is not None
            and not msg.get("marker_type")
            and is_duplicate_message(deduped_messages, msg)
        ):
            continue
        visible.append((idx, msg))
        deduped_messages.append(msg)
    return visible


def _last_activity_ms(session, visible_msgs: list[dict]) -> int:
    """Conversation 在列表里的"最后活动时间"（毫秒）。

    以**最后一条真实消息**的时间戳为准，回退到 ``last_active`` 再回退到
    ``created_at``。这样既能修正 issue #628（``last_active`` 曾被纯读取
    访问刷成"刚活跃"），也能在不做数据迁移的前提下，让历史里已被污染的
    ``last_active`` 在展示层自愈。
    """
    from datetime import datetime

    for msg in reversed(visible_msgs):
        ts = msg.get("timestamp")
        if not ts:
            continue
        try:
            return int(datetime.fromisoformat(ts).timestamp() * 1000)
        except (ValueError, TypeError):
            continue

    base = getattr(session, "last_active", None) or getattr(session, "created_at", None)
    try:
        return int(base.timestamp() * 1000)
    except Exception:
        return 0


def _session_list_item(session, visible_msgs: list[dict] | None = None, last_ms: int | None = None) -> dict:
    """Serialize one session for conversation-list sync surfaces."""
    if visible_msgs is None:
        visible_msgs = [m for _, m in _visible_history_messages(session)]
    if last_ms is None:
        last_ms = _last_activity_ms(session, visible_msgs)

    user_msgs = [m for m in visible_msgs if m.get("role") == "user"]
    first_user = user_msgs[0] if user_msgs else None
    fallback_title = ""
    if first_user:
        content = first_user.get("content", "")
        fallback_title = content[:30] if isinstance(content, str) else ""
    if getattr(session, "channel", "") == "desktop" and session.get_metadata("source_channel"):
        fallback_title = (
            getattr(session, "chat_name", "")
            or getattr(session, "display_name", "")
            or fallback_title
        )

    stored_title = session.get_metadata(_META_CONVERSATION_TITLE)
    stored_title = stored_title.strip() if isinstance(stored_title, str) else ""
    title_manually_set = bool(stored_title and session.get_metadata(_META_TITLE_MANUALLY_SET))
    title_generated = bool(stored_title and session.get_metadata(_META_TITLE_GENERATED))
    title = stored_title or fallback_title

    last_msg_content = ""
    if visible_msgs:
        last_content = visible_msgs[-1].get("content", "")
        if isinstance(last_content, str):
            last_msg_content = last_content[:100]

    selected_endpoint = session.get_metadata("selected_endpoint") or ""
    endpoint_policy = session.get_metadata("endpoint_policy") or "prefer"
    ui_org_state = session.get_metadata("ui_org_state") or {}
    if not isinstance(ui_org_state, dict):
        ui_org_state = {}

    from ...core.working_directory import session_working_directory

    return {
        "id": session.chat_id,
        "title": title or "对话",
        "titleGenerated": title_generated,
        "titleManuallySet": title_manually_set,
        "pinned": bool(session.get_metadata(_META_PINNED)),
        "lastMessage": last_msg_content,
        "timestamp": last_ms,
        "messageCount": len(visible_msgs),
        "agentProfileId": getattr(session.context, "agent_profile_id", "default"),
        "endpointId": selected_endpoint or None,
        "endpointPolicy": endpoint_policy if selected_endpoint else "prefer",
        "orgMode": bool(ui_org_state.get("orgMode") and ui_org_state.get("orgId")),
        "orgId": ui_org_state.get("orgId") or None,
        "orgNodeId": ui_org_state.get("orgNodeId") or None,
        "workingDirectory": str(session_working_directory(session)),
    }


_BACKFILL_DONE_FLAG = "_history_backfilled"


def _maybe_backfill_messages(session) -> None:
    """Hydrate ``session.context.messages`` from SQLite store if needed.

    一次会话只回填一次（由 ``_history_backfilled`` 元数据标记控制）。
    """
    try:
        from ...core.feature_flags import is_enabled as _ff_enabled

        if not _ff_enabled("history_db_merge_v1"):
            return
    except Exception:
        return

    if not session or not getattr(session, "context", None):
        return

    try:
        already = session.get_metadata(_BACKFILL_DONE_FLAG)
    except Exception:
        already = None
    if already:
        return

    # 仅在 messages 较少时才尝试回填，避免热路径反复扫 SQLite
    try:
        msg_count = len(session.context.messages or [])
    except Exception:
        msg_count = 0

    # 元数据里可能存了 message_count，崩溃前的真实数。
    expected = 0
    try:
        meta_count = session.get_metadata("message_count") or 0
        expected = int(meta_count)
    except Exception:
        expected = 0

    if msg_count >= expected and msg_count > 1:
        # 内存里至少有两条且达到记账值，无需回填
        try:
            session.set_metadata(_BACKFILL_DONE_FLAG, True)
        except Exception:
            pass
        return

    # 找到 SessionManager（通过 session 对象的弱关系定位）
    manager = getattr(session, "_manager", None)
    loader = getattr(manager, "_turn_loader", None) if manager else None
    if loader is None:
        # 没有 store 接入，直接打标避免重复尝试
        try:
            session.set_metadata(_BACKFILL_DONE_FLAG, True)
        except Exception:
            pass
        return

    try:
        import re as _re

        safe_id = (session.session_key or "").replace(":", "__")
        safe_id = _re.sub(r'[/\\+=%?*<>|"\x00-\x1f]', "_", safe_id)
        db_turns = loader(safe_id) or []
    except Exception as exc:
        logger.debug(f"[Sessions] backfill loader failed: {exc}")
        db_turns = []

    if not db_turns:
        try:
            session.set_metadata(_BACKFILL_DONE_FLAG, True)
        except Exception:
            pass
        return

    try:
        from ...sessions.session import is_duplicate_message
    except Exception:
        is_duplicate_message = None

    appended = 0
    try:
        with getattr(session.context, "_msg_lock", _NULL_LOCK):
            for turn in db_turns:
                if not isinstance(turn, dict):
                    continue
                if is_duplicate_message is not None and is_duplicate_message(
                    session.context.messages,
                    turn,
                ):
                    continue
                session.context.messages.append(dict(turn))
                appended += 1
    except Exception as exc:
        logger.debug(f"[Sessions] backfill append failed: {exc}")

    if appended:
        # 时间戳排序（缺失则保持原顺序）
        try:
            session.context.messages.sort(key=lambda m: m.get("timestamp") or "")
        except Exception:
            pass
        logger.info(f"[Sessions] backfilled {appended} turns from SQLite for {session.session_key}")

    try:
        session.set_metadata(_BACKFILL_DONE_FLAG, True)
    except Exception:
        pass


class _NullLock:
    def __enter__(self):  # noqa: D401
        return self

    def __exit__(self, *exc) -> None:  # noqa: D401
        return None


_NULL_LOCK = _NullLock()


def _history_entry(session, conversation_id: str, original_idx: int, msg: dict) -> dict:
    """Serialize a session message for the chat UI."""
    # 内部 trace marker 集中于 ``response_handler``，避免和 agent.py
    # 维护两份列表。lazy import 避开 routes → core 的循环依赖。
    from openakita.core.response_handler import (
        INTERNAL_TRACE_MARKERS,
        INTERNAL_TRACE_SECTION_PREFIXES,
    )

    role = msg.get("role", "user")
    content = msg.get("content", "")
    if not isinstance(content, str):
        content = str(content) if content else ""
    if role == "assistant":
        for marker in INTERNAL_TRACE_SECTION_PREFIXES:
            if marker in content:
                content = content[: content.index(marker)]
        if any(content.startswith(m) for m in INTERNAL_TRACE_MARKERS):
            content = ""

    ts = msg.get("timestamp", "")
    epoch_ms = 0
    if ts:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(ts)
            epoch_ms = int(dt.timestamp() * 1000)
        except Exception:
            pass

    entry: dict = {
        "id": f"restored-{conversation_id}-{original_idx}",
        "index": original_idx,
        "role": role,
        "content": content,
        "timestamp": epoch_ms or int(session.last_active.timestamp() * 1000),
    }
    chain_summary = msg.get("chain_summary")
    if chain_summary:
        entry["chain_summary"] = chain_summary
    # Causally-ordered reasoning timeline (preferred over chain_summary on the
    # client). Lets the reasoning chain re-display faithfully after reload /
    # multi-window switch instead of the lossy summary rebuild.
    chain_timeline = msg.get("chain_timeline")
    if chain_timeline:
        entry["chain_timeline"] = chain_timeline
    tool_summary = msg.get("tool_summary")
    if tool_summary:
        entry["tool_summary"] = tool_summary
    artifacts = msg.get("artifacts")
    if artifacts:
        entry["artifacts"] = artifacts
    sources = msg.get("sources")
    if sources:
        entry["sources"] = sources
    mcp_calls = msg.get("mcp_calls")
    if mcp_calls:
        entry["mcp_calls"] = mcp_calls
    attachments = msg.get("attachments")
    if attachments:
        entry["attachments"] = attachments
    org_timeline = msg.get("org_timeline")
    if org_timeline:
        entry["org_timeline"] = org_timeline
    ask_user = msg.get("ask_user")
    if ask_user:
        entry["ask_user"] = ask_user
    error_info = msg.get("error_info")
    if isinstance(error_info, dict):
        entry["error_info"] = error_info
    usage = msg.get("usage")
    if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):
        entry["usage"] = usage

    # Progress event journal + ordered parts projection — lets rich cards
    # (plan, answered ask_user, attachments) re-display losslessly after reload
    # / multi-window switch. ``parts`` is derived, never stored, so it cannot
    # bloat sessions.json. See openakita.api.message_parts.
    from openakita.api.message_parts import (
        build_message_parts,
        normalize_chat_todo,
        normalize_progress_events,
        project_progress_events_to_todo,
    )

    progress_events = normalize_progress_events(msg.get("progress_events"))
    if progress_events:
        entry["progress_events"] = progress_events
    todo_norm = project_progress_events_to_todo(progress_events) or (
        normalize_chat_todo(msg.get("todo")) if msg.get("todo") else None
    )
    if todo_norm and todo_norm.get("steps"):
        entry["todo"] = todo_norm
    parts = build_message_parts(
        {**msg, "content": content},
        todo=todo_norm,
        progress_events=progress_events,
    )
    if parts:
        entry["parts"] = parts
    return entry


_TODO_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_TODO_CLOSING_EVENT_STATUS = {
    "todo_completed": "completed",
    "todo_cancelled": "cancelled",
}
_TODO_NON_TERMINAL_STEP_STATUSES = {"pending", "in_progress"}


def _todo_id(todo: dict | None) -> str:
    if not isinstance(todo, dict):
        return ""
    return str(todo.get("id") or "").strip()


def _event_plan_id(event: dict) -> str:
    return str(event.get("planId") or event.get("plan_id") or "").strip()


def _remove_open_todo(open_order: list[str], plan_id: str) -> None:
    try:
        open_order.remove(plan_id)
    except ValueError:
        pass


def _remember_open_todo(open_order: list[str], plan_id: str) -> None:
    if not plan_id:
        return
    _remove_open_todo(open_order, plan_id)
    open_order.append(plan_id)


def _latest_open_todo_id(open_order: list[str], todos_by_id: dict[str, dict]) -> str:
    while open_order:
        plan_id = open_order[-1]
        todo = todos_by_id.get(plan_id)
        if isinstance(todo, dict) and todo.get("status") not in _TODO_TERMINAL_STATUSES:
            return plan_id
        open_order.pop()
    return ""


def _finalize_todo(todo: dict, status: str) -> dict:
    out = copy.deepcopy(todo)
    out["status"] = status
    step_status = "cancelled" if status == "cancelled" else "completed"
    for step in out.get("steps") or []:
        if isinstance(step, dict) and step.get("status") in _TODO_NON_TERMINAL_STEP_STATUSES:
            step["status"] = step_status
    return out


def _apply_todo_event(todo: dict, event: dict) -> dict:
    out = copy.deepcopy(todo)
    event_type = event.get("type")
    if event_type == "todo_step_updated":
        step_id = event.get("stepId")
        step_idx = event.get("stepIdx")
        for idx, step in enumerate(out.get("steps") or []):
            if not isinstance(step, dict):
                continue
            if (step_id and step.get("id") == step_id) or (
                isinstance(step_idx, int) and idx == step_idx
            ):
                if event.get("status"):
                    step["status"] = event["status"]
                if "result" in event:
                    step["result"] = event.get("result")
                break
    elif event_type in _TODO_CLOSING_EVENT_STATUS:
        out = _finalize_todo(out, _TODO_CLOSING_EVENT_STATUS[event_type])
    return out


def _project_history_todo_final_states(visible: list[tuple[int, dict]]) -> dict[str, dict]:
    """Infer final todo snapshots across assistant messages in one history.

    Old sessions can contain an in-progress todo snapshot on one assistant
    message and a later completion-only journal on another. The per-message
    projection cannot connect those two records, so the history endpoint does
    this cross-message pass before hydrating the UI.
    """
    from openakita.api.message_parts import (
        normalize_chat_todo,
        normalize_progress_events,
        project_progress_events_to_todo,
        serialize_plan_to_chat_todo,
    )

    todos_by_id: dict[str, dict] = {}
    finalized_by_id: dict[str, dict] = {}
    open_order: list[str] = []

    for _idx, msg in visible:
        progress_events = normalize_progress_events(msg.get("progress_events"))
        projected = project_progress_events_to_todo(progress_events)
        msg_todo = projected or (normalize_chat_todo(msg.get("todo")) if msg.get("todo") else None)

        message_plan_id = _todo_id(msg_todo)
        if message_plan_id:
            todos_by_id[message_plan_id] = copy.deepcopy(msg_todo)
            if msg_todo.get("status") in _TODO_TERMINAL_STATUSES:
                finalized_by_id[message_plan_id] = copy.deepcopy(msg_todo)
                _remove_open_todo(open_order, message_plan_id)
            else:
                _remember_open_todo(open_order, message_plan_id)

        fallback_plan_id = message_plan_id or _latest_open_todo_id(open_order, todos_by_id)
        terminal_targets: set[str] = set()

        for event in progress_events:
            event_type = event.get("type")
            if event_type == "todo_created":
                created = serialize_plan_to_chat_todo(event.get("plan"))
                created_id = _todo_id(created)
                if created_id:
                    todos_by_id[created_id] = copy.deepcopy(created)
                    _remember_open_todo(open_order, created_id)
                    fallback_plan_id = created_id
                continue

            target_id = _event_plan_id(event) or fallback_plan_id
            if not target_id:
                target_id = _latest_open_todo_id(open_order, todos_by_id)
            if not target_id or target_id not in todos_by_id:
                continue

            todos_by_id[target_id] = _apply_todo_event(todos_by_id[target_id], event)
            if event_type in _TODO_CLOSING_EVENT_STATUS:
                terminal_targets.add(target_id)

        for target_id in terminal_targets:
            finalized = todos_by_id.get(target_id)
            if finalized:
                finalized_by_id[target_id] = copy.deepcopy(finalized)
            _remove_open_todo(open_order, target_id)

    return finalized_by_id


def _patch_entry_todo(entry: dict, todo: dict) -> None:
    entry["todo"] = copy.deepcopy(todo)
    parts = entry.get("parts")
    if not isinstance(parts, list):
        return
    patched_parts: list[dict] = []
    target_id = _todo_id(todo)
    for part in parts:
        if (
            isinstance(part, dict)
            and part.get("kind") == "plan"
            and _todo_id(part.get("todo")) == target_id
        ):
            next_part = dict(part)
            next_part["todo"] = copy.deepcopy(todo)
            patched_parts.append(next_part)
        else:
            patched_parts.append(part)
    entry["parts"] = patched_parts


def _reconcile_history_todo_lifecycle(
    entries: list[dict],
    visible: list[tuple[int, dict]],
) -> list[dict]:
    finalized_by_id = _project_history_todo_final_states(visible)
    if not finalized_by_id:
        return entries

    for entry in entries:
        todo = entry.get("todo")
        plan_id = _todo_id(todo)
        finalized = finalized_by_id.get(plan_id)
        if not (plan_id and finalized):
            continue
        if isinstance(todo, dict) and todo.get("status") != finalized.get("status"):
            _patch_entry_todo(entry, finalized)
    return entries


@router.get("/api/sessions")
async def list_sessions(request: Request, channel: str = "desktop"):
    """List sessions for a given channel (default: desktop).

    Returns a list of conversations with metadata, ordered by last_active desc.
    """
    _validate_id(channel, "channel")
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager or not getattr(session_manager, "_sessions_loaded", False):
        wac = getattr(request.app.state, "web_access_config", None)
        return {"sessions": [], "data_epoch": wac.data_epoch if wac else "", "ready": False}

    sessions = session_manager.list_sessions(channel=channel)
    # org_* sessions belong to OrgChatPanel (指挥台), not the main chat UI.
    sessions = [s for s in sessions if not s.chat_id.startswith("org_")]

    # 先算出每个会话的可见消息与"最后活动时间"，再按真实活动时间排序。
    # 不能直接用 s.last_active 排序：它会被纯读取访问污染（issue #628）。
    prepared = []
    for s in sessions:
        visible_msgs = [m for _, m in _visible_history_messages(s)]
        prepared.append((s, visible_msgs, _last_activity_ms(s, visible_msgs)))
    prepared.sort(key=lambda item: item[2], reverse=True)

    result = []
    for s, visible_msgs, last_ms in prepared:
        result.append(_session_list_item(s, visible_msgs, last_ms))

    data_epoch = ""
    wac = getattr(request.app.state, "web_access_config", None)
    if wac:
        data_epoch = wac.data_epoch

    return {"sessions": result, "data_epoch": data_epoch, "ready": True}


@router.post("/api/sessions")
async def create_session(
    request: Request,
    body: SessionCreateRequest,
    channel: str = "desktop",
    user_id: str = "desktop_user",
):
    """Create or update the list metadata for a first-class conversation.

    This endpoint intentionally owns the "empty draft conversation" case so
    title/pin/UI-state updates do not need to create missing sessions as a side
    effect.
    """
    conversation_id = body.conversation_id.strip()
    _validate_id(conversation_id, "conversation_id")
    _validate_id(channel, "channel")
    _validate_id(user_id, "user_id")
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager unavailable")

    existing = session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=False,
    )
    requested_working_directory = None
    from ...core.working_directory import working_directory_feature_enabled

    if body.working_directory and working_directory_feature_enabled():
        from ..working_directories import authorize_working_directory

        requested_working_directory = str(
            authorize_working_directory(request, body.working_directory)
        )
    if existing is not None and requested_working_directory:
        from ...core.working_directory import session_working_directory

        if session_working_directory(existing) != Path(requested_working_directory):
            raise HTTPException(status_code=409, detail="working_directory_locked")
    session = existing or session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=True,
        working_directory=requested_working_directory,
    )
    if session is None:
        raise HTTPException(status_code=500, detail="failed to create session")

    agent_profile_id = (body.agent_profile_id or "").strip()
    if agent_profile_id:
        session.context.agent_profile_id = agent_profile_id

    stored_title = session.get_metadata(_META_CONVERSATION_TITLE)
    title = _normalize_title(body.title, fallback="新对话")
    if not isinstance(stored_title, str) or not stored_title.strip():
        manually_set = bool(body.title_manually_set)
        session.set_metadata(_META_CONVERSATION_TITLE, title)
        session.set_metadata(_META_TITLE_MANUALLY_SET, manually_set)
        session.set_metadata(_META_TITLE_GENERATED, False if manually_set else bool(body.title_generated))

    if existing is None or body.pinned:
        session.set_metadata(_META_PINNED, bool(body.pinned))

    _apply_session_ui_state(
        session,
        endpoint_id=body.endpoint_id,
        endpoint_policy=body.endpoint_policy,
        org_mode=body.org_mode,
        org_id=body.org_id,
        org_node_id=body.org_node_id,
    )
    session_manager.mark_dirty()
    try:
        session_manager.persist()
    except Exception as exc:
        logger.warning("[Sessions API] Failed to persist created session: %s", exc)

    summary = _session_list_item(session)
    logger.info(
        "[Sessions API] session upserted conversation_id=%s title=%r message_count=%s pinned=%s",
        conversation_id,
        summary["title"],
        summary["messageCount"],
        summary["pinned"],
    )
    await _broadcast_session_event(
        "chat:session_update",
        {
            "conversation_id": conversation_id,
            **summary,
        },
    )
    return {"ok": True, "created": existing is None, **summary}


@router.post("/api/sessions/{conversation_id}/ui-state")
async def update_session_ui_state(
    request: Request,
    conversation_id: str,
    body: SessionUiStateRequest,
    channel: str = "desktop",
    user_id: str = "desktop_user",
):
    """Persist per-conversation UI selections such as model endpoint and org mode."""
    _validate_id(conversation_id, "conversation_id")
    _validate_id(channel, "channel")
    _validate_id(user_id, "user_id")
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager unavailable")

    session = session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=False,
    )
    if session is None:
        return {"ok": False, "reason": "session_not_found"}
    _apply_session_ui_state(
        session,
        endpoint_id=body.endpoint_id,
        endpoint_policy=body.endpoint_policy,
        org_mode=body.org_mode,
        org_id=body.org_id,
        org_node_id=body.org_node_id,
    )
    session_manager.mark_dirty()
    try:
        session_manager.persist()
    except Exception as exc:
        logger.warning("[Sessions API] Failed to persist UI state: %s", exc)
    return {"ok": True}


@router.patch("/api/sessions/{conversation_id}/title")
async def update_session_title(
    request: Request,
    conversation_id: str,
    body: SessionTitleRequest,
    channel: str = "desktop",
    user_id: str = "desktop_user",
):
    """Persist a user-facing conversation title in session metadata."""
    _validate_id(conversation_id, "conversation_id")
    _validate_id(channel, "channel")
    _validate_id(user_id, "user_id")
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager unavailable")

    title = _normalize_title(body.title)
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")

    session = session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=False,
    )
    if session is None:
        return {"ok": False, "reason": "session_not_found"}

    manually_set = bool(body.title_manually_set)
    generated = False if manually_set else bool(body.title_generated)
    session.set_metadata(_META_CONVERSATION_TITLE, title)
    session.set_metadata(_META_TITLE_MANUALLY_SET, manually_set)
    session.set_metadata(_META_TITLE_GENERATED, generated)
    session_manager.mark_dirty()
    try:
        session_manager.persist()
    except Exception as exc:
        logger.warning("[Sessions API] Failed to persist title: %s", exc)

    summary = _session_list_item(session)
    logger.info(
        "[Sessions API] title updated conversation_id=%s title=%r manual=%s generated=%s pinned=%s",
        conversation_id,
        summary["title"],
        summary["titleManuallySet"],
        summary["titleGenerated"],
        summary["pinned"],
    )
    await _broadcast_session_event(
        "chat:title_update",
        {
            "conversation_id": conversation_id,
            **summary,
        },
    )
    return {"ok": True, **summary}


@router.patch("/api/sessions/{conversation_id}/pin")
async def update_session_pin(
    request: Request,
    conversation_id: str,
    body: SessionPinRequest,
    channel: str = "desktop",
    user_id: str = "desktop_user",
):
    """Persist conversation pinning so all clients show the same list grouping."""
    _validate_id(conversation_id, "conversation_id")
    _validate_id(channel, "channel")
    _validate_id(user_id, "user_id")
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager unavailable")

    session = session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=False,
    )
    if session is None:
        return {"ok": False, "reason": "session_not_found"}

    pinned = bool(body.pinned)
    session.set_metadata(_META_PINNED, pinned)
    session_manager.mark_dirty()
    try:
        session_manager.persist()
    except Exception as exc:
        logger.warning("[Sessions API] Failed to persist pin state: %s", exc)

    summary = _session_list_item(session)
    logger.info(
        "[Sessions API] pin updated conversation_id=%s pinned=%s title=%r manual=%s",
        conversation_id,
        summary["pinned"],
        summary["title"],
        summary["titleManuallySet"],
    )
    await _broadcast_session_event(
        "chat:pin_update",
        {
            "conversation_id": conversation_id,
            **summary,
        },
    )
    return {"ok": True, **summary}


@router.get("/api/sessions/{conversation_id}/history")
async def get_session_history(
    request: Request,
    conversation_id: str,
    channel: str = "desktop",
    user_id: str = "desktop_user",
    limit: int = Query(
        _DEFAULT_HISTORY_LIMIT,
        ge=1,
        le=_MAX_HISTORY_LIMIT,
        description="Maximum number of visible messages to return.",
    ),
    before: int | None = Query(
        None,
        ge=0,
        description="Return messages whose stable history index is lower than this value.",
    ),
):
    """Get message history for a specific session.

    Returns messages in a format compatible with the frontend ChatMessage type.
    """
    _validate_id(conversation_id, "conversation_id")
    _validate_id(channel, "channel")
    _validate_id(user_id, "user_id")

    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        return {"messages": []}

    session = session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=False,
    )
    if not session:
        return {
            "messages": [],
            "total": 0,
            "start_index": None,
            "end_index": None,
            "has_more_before": False,
        }

    all_visible = _visible_history_messages(session)
    visible = all_visible
    if before is not None:
        visible = [(idx, msg) for idx, msg in visible if idx < before]

    page = visible[-limit:]
    result = [_history_entry(session, conversation_id, idx, msg) for idx, msg in page]
    result = _reconcile_history_todo_lifecycle(result, all_visible)
    start_index = page[0][0] if page else None
    end_index = page[-1][0] if page else None

    # A plan that is still executing has not been finalized into history yet,
    # so a passive re-hydration (window switch / reload) would otherwise lose
    # the live plan card (#615). Surface the in-flight plan snapshot so the
    # frontend can re-attach it to the latest assistant message.
    active_todo = None
    try:
        from ...tools.handlers.plan import get_todo_handler_for_session, has_active_todo
        from ..message_parts import serialize_plan_to_chat_todo

        if has_active_todo(conversation_id):
            _h = get_todo_handler_for_session(conversation_id)
            _p = _h.get_plan_for(conversation_id) if _h else None
            if isinstance(_p, dict) and _p.get("status") == "in_progress":
                active_todo = serialize_plan_to_chat_todo(_p)
    except Exception:
        active_todo = None

    from ...core.working_directory import session_working_directory

    return {
        "messages": result,
        "total": len(all_visible),
        "start_index": start_index,
        "end_index": end_index,
        "has_more_before": bool(page and any(idx < page[0][0] for idx, _ in visible)),
        "active_todo": active_todo,
        "workingDirectory": str(session_working_directory(session)),
    }


@router.get("/api/sessions/{conversation_id}/files/search")
async def search_session_files(
    request: Request,
    conversation_id: str,
    q: str = Query("", max_length=256),
    limit: int = Query(50, ge=1, le=100),
    channel: str = "desktop",
    user_id: str = "desktop_user",
):
    """Search regular files inside a conversation's immutable directory."""
    _validate_id(conversation_id, "conversation_id")
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager unavailable")
    session = session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=False,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    from ...core.working_directory import WorkingDirectoryError, session_working_directory
    from ...tools.file import DEFAULT_IGNORE_DIRS, GREP_EXTRA_BLOCKED_DIR_NAMES

    try:
        root = session_working_directory(session, require_available=True)
    except WorkingDirectoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    needle = q.strip().lower()
    results: list[dict] = []
    dirs_seen = 0
    files_seen = 0
    ignored = DEFAULT_IGNORE_DIRS | GREP_EXTRA_BLOCKED_DIR_NAMES
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs_seen += 1
        if dirs_seen > 3000 or files_seen > 10000:
            break
        dirs[:] = [d for d in dirs if d not in ignored and not (Path(current) / d).is_symlink()]
        for filename in files:
            files_seen += 1
            if files_seen > 10000:
                break
            candidate = Path(current) / filename
            try:
                resolved = candidate.resolve(strict=True)
                relative = resolved.relative_to(root).as_posix()
                if not resolved.is_file() or (needle and needle not in relative.lower()):
                    continue
                stat = resolved.stat()
            except (OSError, RuntimeError, ValueError):
                continue
            mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            results.append(
                {
                    "name": filename,
                    "relativePath": relative,
                    "mimeType": mime_type,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    return {
        "workingDirectory": str(root),
        "files": results,
        "truncated": len(results) >= limit or dirs_seen > 3000 or files_seen > 10000,
    }


def _resolve_file_tree_parent(root: Path, parent: str) -> tuple[Path, str]:
    """Resolve a client-supplied relative directory without following links."""
    raw = parent
    if len(raw) > 4096 or any(ord(ch) < 32 for ch in raw):
        raise HTTPException(status_code=422, detail="Invalid parent path")
    relative = Path(raw) if raw else Path()
    if (
        relative.is_absolute()
        or relative.anchor
        or relative.drive
        or any(part == ".." for part in relative.parts)
    ):
        raise HTTPException(status_code=403, detail="Parent must stay within working directory")

    current = root
    for part in relative.parts:
        if part in ("", "."):
            continue
        current = current / part
        try:
            if current.is_symlink():
                raise HTTPException(status_code=403, detail="Symbolic links are not allowed")
        except OSError as exc:
            raise HTTPException(status_code=409, detail="Working directory is unavailable") from exc

    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Directory not found") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Parent must stay within working directory") from exc
    if not resolved.is_dir():
        raise HTTPException(status_code=422, detail="Parent is not a directory")
    normalized = resolved.relative_to(root).as_posix()
    return resolved, "" if normalized == "." else normalized


def _file_tree_directory_has_children(directory: Path, ignored: set[str]) -> bool:
    try:
        for child in directory.iterdir():
            if child.name in ignored or child.is_symlink():
                continue
            if child.is_dir() or child.is_file():
                return True
    except OSError:
        return False
    return False


@router.get("/api/sessions/{conversation_id}/files/tree")
async def list_session_file_tree(
    request: Request,
    conversation_id: str,
    parent: str = Query("", max_length=4096),
    limit: int = Query(500, ge=1, le=500),
    channel: str = "desktop",
    user_id: str = "desktop_user",
):
    """List one directory level inside a conversation's immutable directory."""
    _validate_id(conversation_id, "conversation_id")
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager unavailable")
    session = session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=False,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    from ...core.working_directory import WorkingDirectoryError, session_working_directory
    from ...tools.file import DEFAULT_IGNORE_DIRS, GREP_EXTRA_BLOCKED_DIR_NAMES

    try:
        root = session_working_directory(session, require_available=True)
    except WorkingDirectoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    directory, normalized_parent = _resolve_file_tree_parent(root, parent)
    ignored = DEFAULT_IGNORE_DIRS | GREP_EXTRA_BLOCKED_DIR_NAMES
    visible: list[Path] = []
    try:
        for child in directory.iterdir():
            if child.name in ignored or child.is_symlink():
                continue
            if child.is_dir() or child.is_file():
                visible.append(child)
    except OSError as exc:
        raise HTTPException(status_code=409, detail="Working directory is unavailable") from exc

    visible.sort(key=lambda item: (not item.is_dir(), item.name.lower(), item.name))
    entries: list[dict] = []
    for child in visible[:limit]:
        relative_path = child.relative_to(root).as_posix()
        if child.is_dir():
            entries.append(
                {
                    "name": child.name,
                    "relativePath": relative_path,
                    "kind": "directory",
                    "hasChildren": _file_tree_directory_has_children(child, ignored),
                }
            )
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "relativePath": relative_path,
                "kind": "file",
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "mimeType": mimetypes.guess_type(child.name)[0] or "application/octet-stream",
            }
        )
    return {
        "workingDirectory": str(root),
        "parent": normalized_parent,
        "entries": entries,
        "truncated": len(visible) > limit,
    }


@router.get("/api/working-directories")
async def browse_working_directories(
    request: Request,
    parent: str | None = Query(None, max_length=4096),
):
    """List administrator-approved roots or one level of their subdirectories."""
    from ..working_directories import authorize_working_directory, configured_working_roots

    if parent:
        directory = authorize_working_directory(request, parent)
        roots = configured_working_roots()
    else:
        roots = configured_working_roots()
        return {
            "directory": None,
            "parent": None,
            "directories": [
                {"name": root.name or str(root), "path": str(root)}
                for root in roots
                if root.is_dir()
            ],
        }

    children: list[dict[str, str]] = []
    try:
        for child in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            try:
                if child.is_symlink() or not child.is_dir():
                    continue
                resolved = child.resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                continue
            children.append({"name": child.name, "path": str(resolved)})
            if len(children) >= 500:
                break
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot browse directory: {exc}") from exc

    parent_path: str | None = None
    for root in roots:
        if directory == root:
            break
        try:
            candidate = directory.parent.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            break
        from ...core.working_directory import is_within

        if is_within(candidate, root):
            parent_path = str(candidate)
            break
    return {
        "directory": str(directory),
        "parent": parent_path,
        "directories": children,
        "truncated": len(children) >= 500,
    }


@router.delete("/api/sessions/{conversation_id}")
async def delete_session(
    request: Request,
    conversation_id: str,
    channel: str = "desktop",
    user_id: str = "desktop_user",
):
    """Delete a session by chat_id.

    Cancels any running tasks, closes the session and removes it from
    the session manager. Conversation history in memory DB is preserved
    for potential recovery.
    """
    _validate_id(conversation_id, "conversation_id")
    _validate_id(channel, "channel")
    _validate_id(user_id, "user_id")

    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        return {"ok": False, "error": "session_manager not available"}

    # 关闭前先通过公开 API 获取 session，用于取消关联任务
    session = session_manager.get_session(
        channel, conversation_id, user_id, create_if_missing=False
    )
    if session is not None:
        _cancel_tasks_for_session(request, conversation_id, session.id)

    # Release busy-lock unconditionally — the conversation is being deleted,
    # so any in-progress state is no longer relevant.
    from .conversation_lifecycle import get_lifecycle_manager

    await get_lifecycle_manager().finish(conversation_id)

    session_key = f"{channel}:{conversation_id}:{user_id}"
    removed = session_manager.close_session(session_key)
    if removed:
        logger.info(f"[Sessions] Deleted session via API: {session_key}")
        try:
            from ...core.session_caches import clear_session_caches
            from .chat import _get_existing_agent, _resolve_agent

            agent = _get_existing_agent(request, conversation_id)
            actual_agent = _resolve_agent(agent) if agent else None
            clear_session_caches(actual_agent)
        except Exception as exc:
            logger.debug(f"[Sessions] clear_session_caches skipped: {exc}")
        await _broadcast_session_event(
            "chat:conversation_deleted",
            {
                "conversation_id": conversation_id,
            },
        )
    else:
        logger.debug(f"[Sessions] Session not found for deletion: {session_key}")

    return {"ok": True, "removed": removed}


def _cancel_tasks_for_session(request: Request, conversation_id: str, session_id: str) -> None:
    """Best-effort cancel of running tasks before session deletion.

    Two levels of cancellation:
    - Agent: cooperative cancel via cancel_event (task exits at next checkpoint)
    - Orchestrator: forceful asyncio.Task.cancel (ensures task stops)
    """
    from .chat import _get_existing_agent, _resolve_agent

    # Agent 级：协作式取消（设置 cancel_event，任务在下一个检查点退出）
    try:
        agent = _get_existing_agent(request, conversation_id)
        actual_agent = _resolve_agent(agent) if agent else None
        if actual_agent is not None:
            actual_agent.cancel_current_task("对话已删除", session_id=conversation_id)
            logger.info(f"[Sessions] Cancelled agent task: conv={conversation_id}")
    except Exception as e:
        logger.debug(f"[Sessions] Agent cancel skipped: {e}")

    # Orchestrator 级：强制取消 asyncio Task（兜底，确保任务停止）
    try:
        orchestrator = getattr(request.app.state, "orchestrator", None)
        if orchestrator is not None:
            if orchestrator.cancel_request(session_id):
                logger.info(f"[Sessions] Cancelled orchestrator tasks: sid={session_id}")
            # Desktop 路径的任务不经过 orchestrator.handle_message，
            # 所以 cancel_request 可能不命中 _active_tasks。
            # 用 conversation_id 再做一次 purge 确保子 Agent 状态被清理。
            if conversation_id != session_id:
                orchestrator.purge_session_states(conversation_id)
    except Exception as e:
        logger.debug(f"[Sessions] Orchestrator cancel skipped: {e}")


class AppendMessageRequest(BaseModel):
    role: str = Field(..., description="user | assistant | system")
    content: str = Field(..., description="Message content")
    attachments: list[ChatAttachmentRecord] | None = Field(None, description="Message attachments")


class AppendBatchRequest(BaseModel):
    messages: list[AppendMessageRequest] = Field(..., description="Messages to append")
    replace: bool = Field(False, description="If true, replace all existing messages")


@router.post("/api/sessions/{conversation_id}/messages")
async def append_session_messages(
    request: Request,
    conversation_id: str,
    body: AppendBatchRequest,
    channel: str = "desktop",
    user_id: str = "desktop_user",
):
    """Append messages to a session (create if missing).

    Used by OrgChatPanel and other embedded chat UIs to persist messages
    through the same session backend as the main ChatView.
    """
    _validate_id(conversation_id, "conversation_id")
    _validate_id(channel, "channel")
    _validate_id(user_id, "user_id")

    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        return {"ok": False, "error": "session_manager not available"}

    session = session_manager.get_session(
        channel=channel,
        chat_id=conversation_id,
        user_id=user_id,
        create_if_missing=True,
    )
    if not session:
        return {"ok": False, "error": "failed to create session"}

    if body.replace:
        session.context.clear_messages()

    for msg in body.messages:
        meta: dict = {}
        if msg.attachments:
            meta["attachments"] = [att.to_history_dict() for att in msg.attachments]
        session.add_message(msg.role, msg.content, **meta)

    session_manager.mark_dirty()
    return {"ok": True, "count": len(body.messages), "replaced": body.replace}


@router.post("/api/sessions/generate-title")
async def generate_title(request: Request, body: GenerateTitleRequest):
    """Use LLM to generate a concise conversation title from the first message."""
    session = None
    session_manager = getattr(request.app.state, "session_manager", None)
    if body.conversation_id:
        _validate_id(body.conversation_id, "conversation_id")
        if session_manager:
            session = session_manager.get_session(
                channel="desktop",
                chat_id=body.conversation_id,
                user_id="desktop_user",
                create_if_missing=False,
            )
            stored_title = session.get_metadata(_META_CONVERSATION_TITLE) if session else ""
            if (
                isinstance(stored_title, str)
                and stored_title.strip()
                and session
                and session.get_metadata(_META_TITLE_MANUALLY_SET)
            ):
                logger.info(
                    "[Sessions API] generated title skipped conversation_id=%s reason=manual_title title=%r",
                    body.conversation_id,
                    stored_title.strip(),
                )
                return {
                    "title": stored_title.strip(),
                    "titleGenerated": False,
                    "titleManuallySet": True,
                }

    agent = getattr(request.app.state, "agent", None)
    if not agent:
        return {"title": body.message[:20] or "新对话"}

    from .chat import _resolve_agent

    actual_agent = _resolve_agent(agent)
    if not actual_agent or not actual_agent.brain:
        return {"title": body.message[:20] or "新对话"}

    brain = actual_agent.brain
    prompt_parts = [f"用户: {body.message[:200]}"]
    if body.reply:
        prompt_parts.append(f"AI: {body.reply[:200]}")
    conversation_text = "\n".join(prompt_parts)

    prompt = (
        "请根据以下对话内容生成一个简洁的会话标题。\n"
        "要求：4-10个字，不加标点符号，不加引号，直接输出标题文字。\n\n"
        f"{conversation_text}"
    )

    try:
        response = await brain.think_lightweight(
            prompt,
            system="你是标题生成助手。只输出标题文字，不要任何额外内容。",
            max_tokens=50,
        )
        from openakita.core.response_handler import strip_thinking_tags

        title = (
            strip_thinking_tags(response.content or "")
            .strip()
            .strip('"\'"\u201c\u201d\u2018\u2019\u300c\u300d\u3010\u3011')
            .strip()
        )  # noqa: B005
        if not title or len(title) > 30:
            title = body.message[:20] or "新对话"
        if body.conversation_id:
            stored_title = session.get_metadata(_META_CONVERSATION_TITLE) if session else ""
            if (
                isinstance(stored_title, str)
                and stored_title.strip()
                and session
                and session.get_metadata(_META_TITLE_MANUALLY_SET)
            ):
                logger.info(
                    "[Sessions API] generated title skipped conversation_id=%s reason=manual_title title=%r",
                    body.conversation_id,
                    stored_title.strip(),
                )
                return {
                    "title": stored_title.strip(),
                    "titleGenerated": False,
                    "titleManuallySet": True,
                }
            if session:
                session.set_metadata(_META_CONVERSATION_TITLE, title)
                session.set_metadata(_META_TITLE_MANUALLY_SET, False)
                session.set_metadata(_META_TITLE_GENERATED, True)
                if session_manager:
                    session_manager.mark_dirty()
                    try:
                        session_manager.persist()
                    except Exception as exc:
                        logger.warning("[Sessions API] Failed to persist generated title: %s", exc)
                summary = _session_list_item(session)
                event_payload = {"conversation_id": body.conversation_id, **summary}
                logger.info(
                    "[Sessions API] generated title updated conversation_id=%s title=%r",
                    body.conversation_id,
                    title,
                )
                await _broadcast_session_event(
                    "chat:title_update",
                    event_payload,
                )
            else:
                logger.info(
                    "[Sessions API] generated title skipped conversation_id=%s reason=session_not_found",
                    body.conversation_id,
                )
        return {"title": title, "titleGenerated": True, "titleManuallySet": False}
    except Exception as e:
        logger.warning(f"[Sessions] Title generation failed: {e}")
        return {"title": body.message[:20] or "新对话"}


# C17 Phase B.4: 让第二端打开 UI 时主动拉取本 session 还在等待的 confirm，
# 不依赖错过的 ``confirm_initiated`` 广播。前端拿到列表后渲染 readonly
# badge，等收到 ``confirm_revoked`` 再清掉。
@router.get("/api/sessions/{conversation_id}/active_confirms")
async def active_confirms(conversation_id: str):
    _validate_id(conversation_id, "conversation_id")
    try:
        from openakita.core.ui_confirm_bus import get_ui_confirm_bus

        bus = get_ui_confirm_bus()
        return {"confirms": bus.active_confirms_for_session(conversation_id)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Sessions] active_confirms(%s) failed: %s", conversation_id, exc)
        return {"confirms": [], "error": str(exc)[:200]}
