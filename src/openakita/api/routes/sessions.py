"""
Sessions route: GET /api/sessions, GET /api/sessions/{conversation_id}/history,
POST /api/sessions/generate-title

提供桌面端 session 恢复能力：前端启动时可从后端加载对话列表和历史消息。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class GenerateTitleRequest(BaseModel):
    message: str = Field(..., description="用户第一条消息")
    reply: str = Field("", description="AI 回复摘要（可选）")


@router.get("/api/sessions")
async def list_sessions(request: Request, channel: str = "desktop"):
    """List sessions for a given channel (default: desktop).

    Returns a list of conversations with metadata, ordered by last_active desc.
    """
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        return {"sessions": []}

    sessions = session_manager.list_sessions(channel=channel)
    sessions.sort(key=lambda s: s.last_active, reverse=True)

    result = []
    for s in sessions:
        msgs = s.context.messages
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        last_user = user_msgs[-1] if user_msgs else None
        title = ""
        if last_user:
            content = last_user.get("content", "")
            title = content[:30] if isinstance(content, str) else ""

        last_msg_content = ""
        if msgs:
            last_content = msgs[-1].get("content", "")
            if isinstance(last_content, str):
                last_msg_content = last_content[:100]

        result.append({
            "id": s.chat_id,
            "title": title or "对话",
            "lastMessage": last_msg_content,
            "timestamp": int(s.last_active.timestamp() * 1000),
            "messageCount": len(msgs),
        })

    return {"sessions": result}


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

    result = []
    for i, msg in enumerate(session.context.messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content) if content else ""
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
        result.append(entry)

    return {"messages": result}


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
        title = response.content.strip().strip('"\'"\u201c\u201d\u2018\u2019\u300c\u300d\u3010\u3011').strip()  # noqa: B005
        if not title or len(title) > 30:
            title = body.message[:20] or "新对话"
        return {"title": title}
    except Exception as e:
        logger.warning(f"[Sessions] Title generation failed: {e}")
        return {"title": body.message[:20] or "新对话"}
