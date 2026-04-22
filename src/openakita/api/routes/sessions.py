"""
Sessions route: GET /api/sessions, GET /api/sessions/{conversation_id}/history,
DELETE /api/sessions/{conversation_id}, POST /api/sessions/generate-title

Provides desktop session restoration: the frontend can load conversation lists and message history from the backend on startup.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openakita.agents.cli_detector import CliProviderId, discover_all
from openakita.sessions.transcript import (
    parse_claude_stream_json,
    parse_codex_jsonl,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# 会话/频道/用户 ID 白名单：允许字母、数字、下划线、短横线、点、冒号、@；
# 上限 128 字节。挡住路径穿越/控制字符/SQL 元字符等异常输入。
# 与 schemas.ChatRequest.conversation_id 模式保持一致（UUID/IM chatroom@xxx 都覆盖）。
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.@]{1,128}$")


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
    message: str = Field(..., description="User's first message")
    reply: str = Field("", description="AI reply summary (optional)")
    conversation_id: str = Field("", description="Conversation ID (for cross-device title sync)")


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
    # org_* sessions belong to OrgChatPanel, not the main chat UI.
    sessions = [s for s in sessions if not s.chat_id.startswith("org_")]
    sessions.sort(key=lambda s: s.last_active, reverse=True)

    # Backward compatibility: old truncate_history injected synthetic system
    # summaries into sessions. Hide them from the conversation list.
    truncation_prefixes = ("[用户规则（必须遵守）]", "[历史背景，非当前任务]")

    result = []
    for s in sessions:
        msgs = s.context.messages
        visible_msgs = [
            m for m in msgs
            if not (
                m.get("role") == "system"
                and isinstance(m.get("content", ""), str)
                and m.get("content", "").startswith(truncation_prefixes)
            )
        ]
        user_msgs = [m for m in visible_msgs if m.get("role") == "user"]
        first_user = user_msgs[0] if user_msgs else None
        title = ""
        if first_user:
            content = first_user.get("content", "")
            title = content[:30] if isinstance(content, str) else ""

        last_msg_content = ""
        if visible_msgs:
            last_content = visible_msgs[-1].get("content", "")
            if isinstance(last_content, str):
                last_msg_content = last_content[:100]

        result.append(
            {
                "id": s.chat_id,
                "title": title or "Conversation",
                "lastMessage": last_msg_content,
                "timestamp": int(s.last_active.timestamp() * 1000),
                "messageCount": len(visible_msgs),
                "agentProfileId": getattr(s.context, "agent_profile_id", "default"),
            }
        )

    data_epoch = ""
    wac = getattr(request.app.state, "web_access_config", None)
    if wac:
        data_epoch = wac.data_epoch

    return {"sessions": result, "data_epoch": data_epoch, "ready": True}


@router.get("/api/sessions/{conversation_id}/history")
async def get_session_history(
    request: Request,
    conversation_id: str,
    channel: str = "desktop",
    user_id: str = "desktop_user",
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
        return {"messages": []}

    _STRIP_MARKERS = ["\n\n[子Agent工作总结]", "\n\n[执行摘要]"]
    truncation_prefixes = ("[用户规则（必须遵守）]", "[历史背景，非当前任务]")

    result = []
    for i, msg in enumerate(session.context.messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content) if content else ""
        if role == "system" and content.startswith(truncation_prefixes):
            continue
        if role == "assistant":
            for marker in _STRIP_MARKERS:
                if marker in content:
                    content = content[: content.index(marker)]
            if content.startswith("[执行摘要]") or content.startswith("[子Agent工作总结]"):
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
            "id": f"restored-{conversation_id}-{i}",
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
        result.append(entry)

    return {"messages": result}


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

    # Retrieve the session before closing, so we can cancel associated tasks
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

    # Agent level: cooperative cancel (set cancel_event; task exits at next checkpoint)
    try:
        agent = _get_existing_agent(request, conversation_id)
        actual_agent = _resolve_agent(agent) if agent else None
        if actual_agent is not None:
            actual_agent.cancel_current_task("Conversation deleted", session_id=conversation_id)
            logger.info(f"[Sessions] Cancelled agent task: conv={conversation_id}")
    except Exception as e:
        logger.debug(f"[Sessions] Agent cancel skipped: {e}")

    # Orchestrator level: force-cancel asyncio Task (fallback to ensure task stops)
    try:
        orchestrator = getattr(request.app.state, "orchestrator", None)
        if orchestrator is not None:
            if orchestrator.cancel_request(session_id):
                logger.info(f"[Sessions] Cancelled orchestrator tasks: sid={session_id}")
            # Desktop-path tasks do not go through orchestrator.handle_message,
            # so cancel_request may not hit _active_tasks.
            # Purge by conversation_id as well to ensure sub-agent state is cleaned up.
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
        return {"title": body.message[:20] or "New Conversation"}

    from .chat import _resolve_agent

    actual_agent = _resolve_agent(agent)
    if not actual_agent or not actual_agent.brain:
        return {"title": body.message[:20] or "New Conversation"}

    brain = actual_agent.brain
    prompt_parts = [f"User: {body.message[:200]}"]
    if body.reply:
        prompt_parts.append(f"AI: {body.reply[:200]}")
    conversation_text = "\n".join(prompt_parts)

    prompt = (
        "Generate a concise conversation title from the following dialogue.\n"
        "Requirements: 4-10 characters, no punctuation, no quotation marks, output only the title text.\n\n"
        f"{conversation_text}"
    )

    try:
        response = await brain.think_lightweight(
            prompt,
            system="You are a title generation assistant. Output only the title text, nothing else.",
            max_tokens=50,
        )
        title = (
            response.content.strip()
            .strip('"\'"\u201c\u201d\u2018\u2019\u300c\u300d\u3010\u3011')
            .strip()
        )  # noqa: B005
        if not title or len(title) > 30:
            title = body.message[:20] or "New Conversation"
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
        return {"title": body.message[:20] or "New Conversation"}


# ---------------------------------------------------------------------------
# External-CLI historical session browsing (Phase 1 — read-only)
# ---------------------------------------------------------------------------
# Per-provider session-root directories. Phase 2 (plan 10, plan 11) moves these
# constants into each cli_providers/<provider>.py module; this dict then becomes
# a re-export. For Phase 1 we keep the truth here because no cli_providers
# package exists yet.
_PROVIDER_ROOTS: dict[CliProviderId, Path] = {
    CliProviderId.CLAUDE_CODE: Path.home() / ".claude" / "projects",
    CliProviderId.CODEX: Path.home() / ".codex" / "sessions",
    # Other providers don't yet have a known session-history layout; fill in as
    # each adapter lands. The listing endpoint returns [] for unmapped providers.
}

_PARSERS = {
    CliProviderId.CLAUDE_CODE: parse_claude_stream_json,
    CliProviderId.CODEX: parse_codex_jsonl,
}


def _safe_provider(raw: str) -> CliProviderId | None:
    try:
        return CliProviderId(raw)
    except ValueError:
        return None


@router.get("/external-cli/detected")
async def get_external_cli_detected():
    probed = await discover_all()
    return [
        {
            "provider_id": pid.value,
            "installed": d.binary_path is not None,
            "binary_path": d.binary_path,
            "version": d.version,
            "error": d.error,
        }
        for pid, d in probed.items()
    ]


def _claude_cwd_hash(cwd: str) -> str:
    """Claude Code hashes the cwd into the directory name under
    ~/.claude/projects/. The exact algorithm is a simple slug-replace: slashes
    become hyphens, leading slash dropped. Verified by inspecting a live
    ~/.claude/projects/ tree."""
    slug = cwd.lstrip("/").replace("/", "-")
    return slug


@router.get("/external-cli/{provider}")
async def list_external_cli_sessions(provider: str, cwd: str = "", limit: int = 50):
    pid = _safe_provider(provider)
    if pid is None:
        return {"error": f"unknown provider: {provider}", "sessions": []}
    root = _PROVIDER_ROOTS.get(pid)
    if root is None or not root.exists():
        return {"sessions": []}

    # Codex stores sessions flat; cwd filter is advisory only
    scope = root / _claude_cwd_hash(cwd) if pid == CliProviderId.CLAUDE_CODE and cwd else root

    if not scope.exists():
        return {"sessions": []}

    entries = []
    for f in sorted(scope.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = f.stat()
        entries.append({
            "session_id": f.stem,
            "path": str(f),
            "bytes": stat.st_size,
            "mtime": stat.st_mtime,
            "cwd_hash": f.parent.name if pid == CliProviderId.CLAUDE_CODE else None,
        })
        if len(entries) >= limit:
            break
    return {"sessions": entries}
