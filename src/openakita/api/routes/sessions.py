"""
Sessions route: GET /api/sessions, GET /api/sessions/{conversation_id}/history,
DELETE /api/sessions/{conversation_id}, POST /api/sessions/generate-title

提供桌面端 session 恢复能力：前端启动时可从后端加载对话列表和历史消息。
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# 会话/频道/用户 ID 白名单：允许字母、数字、下划线、短横线、点、冒号、@；
# 上限 128 字节。挡住路径穿越/控制字符/SQL 元字符等异常输入。
# 与 schemas.ChatRequest.conversation_id 模式保持一致（UUID/IM chatroom@xxx 都覆盖）。
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.@]{1,128}$")
_DEFAULT_HISTORY_LIMIT = 80
_MAX_HISTORY_LIMIT = 200


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


def _visible_history_messages(session) -> list[tuple[int, dict]]:
    """Return UI-visible session messages with their stable original indexes.

    PR-D1: 当内存里的 messages 比预期少（尤其崩溃后重启场景），按需从
    SQLite turn store 回填，保证 ``GET /api/sessions/{id}/history`` 不会
    在用户视角"突然空了"。
    """
    _maybe_backfill_messages(session)

    truncation_prefixes = ("[用户规则（必须遵守）]", "[历史背景，非当前任务]")
    visible: list[tuple[int, dict]] = []
    for idx, msg in enumerate(session.context.messages):
        content = msg.get("content", "")
        if (
            msg.get("role") == "system"
            and isinstance(content, str)
            and content.startswith(truncation_prefixes)
        ):
            continue
        visible.append((idx, msg))
    return visible


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

    # 合并：用 (role, content, timestamp) 做去重
    existing_keys = {
        (m.get("role"), m.get("content"), m.get("timestamp"))
        for m in (session.context.messages or [])
    }
    appended = 0
    try:
        with getattr(session.context, "_msg_lock", _NULL_LOCK):
            for turn in db_turns:
                if not isinstance(turn, dict):
                    continue
                key = (turn.get("role"), turn.get("content"), turn.get("timestamp"))
                if key in existing_keys:
                    continue
                session.context.messages.append(dict(turn))
                existing_keys.add(key)
                appended += 1
    except Exception as exc:
        logger.debug(f"[Sessions] backfill append failed: {exc}")

    if appended:
        # 时间戳排序（缺失则保持原顺序）
        try:
            session.context.messages.sort(key=lambda m: m.get("timestamp") or "")
        except Exception:
            pass
        logger.info(
            f"[Sessions] backfilled {appended} turns from SQLite for {session.session_key}"
        )

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
    _STRIP_MARKERS = [
        "\n\n<<DELEGATION_TRACE>>",
        "\n\n<<TOOL_TRACE>>",
        "\n\n[子Agent工作总结]",
        "\n\n[执行摘要]",
    ]

    role = msg.get("role", "user")
    content = msg.get("content", "")
    if not isinstance(content, str):
        content = str(content) if content else ""
    if role == "assistant":
        for marker in _STRIP_MARKERS:
            if marker in content:
                content = content[: content.index(marker)]
        if (
            content.startswith("<<TOOL_TRACE>>")
            or content.startswith("<<DELEGATION_TRACE>>")
            or content.startswith("[执行摘要]")
            or content.startswith("[子Agent工作总结]")
        ):
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
    tool_summary = msg.get("tool_summary")
    if tool_summary:
        entry["tool_summary"] = tool_summary
    artifacts = msg.get("artifacts")
    if artifacts:
        entry["artifacts"] = artifacts
    ask_user = msg.get("ask_user")
    if ask_user:
        entry["ask_user"] = ask_user
    usage = msg.get("usage")
    if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):
        entry["usage"] = usage
    return entry


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
    sessions.sort(key=lambda s: s.last_active, reverse=True)

    result = []
    for s in sessions:
        visible_msgs = [m for _, m in _visible_history_messages(s)]
        user_msgs = [m for m in visible_msgs if m.get("role") == "user"]
        first_user = user_msgs[0] if user_msgs else None
        title = ""
        if first_user:
            content = first_user.get("content", "")
            title = content[:30] if isinstance(content, str) else ""
        if getattr(s, "channel", "") == "desktop" and s.get_metadata("source_channel"):
            title = getattr(s, "chat_name", "") or getattr(s, "display_name", "") or title

        last_msg_content = ""
        if visible_msgs:
            last_content = visible_msgs[-1].get("content", "")
            if isinstance(last_content, str):
                last_msg_content = last_content[:100]

        selected_endpoint = s.get_metadata("selected_endpoint") or ""
        endpoint_policy = s.get_metadata("endpoint_policy") or "prefer"
        ui_org_state = s.get_metadata("ui_org_state") or {}
        if not isinstance(ui_org_state, dict):
            ui_org_state = {}

        result.append(
            {
                "id": s.chat_id,
                "title": title or "对话",
                "lastMessage": last_msg_content,
                "timestamp": int(s.last_active.timestamp() * 1000),
                "messageCount": len(visible_msgs),
                "agentProfileId": getattr(s.context, "agent_profile_id", "default"),
                "endpointId": selected_endpoint or None,
                "endpointPolicy": endpoint_policy if selected_endpoint else "prefer",
                "orgMode": bool(ui_org_state.get("orgMode") and ui_org_state.get("orgId")),
                "orgId": ui_org_state.get("orgId") or None,
                "orgNodeId": ui_org_state.get("orgNodeId") or None,
            }
        )

    data_epoch = ""
    wac = getattr(request.app.state, "web_access_config", None)
    if wac:
        data_epoch = wac.data_epoch

    return {"sessions": result, "data_epoch": data_epoch, "ready": True}


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
    session.set_metadata("selected_endpoint", body.endpoint_id or "")
    session.set_metadata("endpoint_policy", body.endpoint_policy if body.endpoint_id else "prefer")
    session.set_metadata(
        "ui_org_state",
        {
            "orgMode": bool(body.org_mode and body.org_id),
            "orgId": body.org_id or "",
            "orgNodeId": body.org_node_id or "",
        },
    )
    session_manager.mark_dirty()
    try:
        session_manager.persist()
    except Exception as exc:
        logger.warning("[Sessions API] Failed to persist UI state: %s", exc)
    return {"ok": True}


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

    visible = _visible_history_messages(session)
    if before is not None:
        visible = [(idx, msg) for idx, msg in visible if idx < before]

    page = visible[-limit:]
    result = [_history_entry(session, conversation_id, idx, msg) for idx, msg in page]
    start_index = page[0][0] if page else None
    end_index = page[-1][0] if page else None

    return {
        "messages": result,
        "total": len(_visible_history_messages(session)),
        "start_index": start_index,
        "end_index": end_index,
        "has_more_before": bool(page and any(idx < page[0][0] for idx, _ in visible)),
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
        session.add_message(msg.role, msg.content)

    session_manager.mark_dirty()
    return {"ok": True, "count": len(body.messages), "replaced": body.replace}


@router.post("/api/sessions/generate-title")
async def generate_title(request: Request, body: GenerateTitleRequest):
    """Use LLM to generate a concise conversation title from the first message."""
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
            await _broadcast_session_event(
                "chat:title_update",
                {
                    "conversation_id": body.conversation_id,
                    "title": title,
                },
            )
        return {"title": title}
    except Exception as e:
        logger.warning(f"[Sessions] Title generation failed: {e}")
        return {"title": body.message[:20] or "新对话"}
