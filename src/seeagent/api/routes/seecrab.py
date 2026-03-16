# src/seeagent/api/routes/seecrab.py
"""SeeCrab API routes: SSE streaming chat + session management."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..schemas_seecrab import (
    SeeCrabAnswerRequest,
    SeeCrabChatRequest,
    SeeCrabSessionUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/seecrab")

def _upsert_step_card(cards: list[dict], event: dict) -> None:
    """Upsert a step_card event into the cards list by step_id."""
    step_id = event.get("step_id")
    card = {k: v for k, v in event.items() if k != "type"}
    for i, c in enumerate(cards):
        if c.get("step_id") == step_id:
            cards[i] = card
            return
    cards.append(card)


# ── Busy-lock (per-conversation, same pattern as chat.py) ──

_busy_locks: dict[str, tuple[str, float]] = {}  # conv_id → (client_id, timestamp)
_busy_lock_mutex = asyncio.Lock()
_busy_thread_lock = __import__("threading").Lock()
_LOCK_TTL = 600  # seconds — consistent with chat.py BUSY_TIMEOUT_SECONDS


async def _mark_busy(conv_id: str, client_id: str) -> bool:
    """Try to acquire busy-lock. Returns True if acquired."""
    async with _busy_lock_mutex:
        _expire_stale_locks()
        if conv_id in _busy_locks:
            existing_client, _ = _busy_locks[conv_id]
            if existing_client != client_id:
                return False
        _busy_locks[conv_id] = (client_id, time.time())
        return True


def _clear_busy(conv_id: str) -> None:
    # Use thread-safe lock to support cross-loop calls (same pattern as chat.py)
    with _busy_thread_lock:
        _busy_locks.pop(conv_id, None)


def _expire_stale_locks() -> None:
    now = time.time()
    expired = [k for k, (_, ts) in _busy_locks.items() if now - ts > _LOCK_TTL]
    for k in expired:
        del _busy_locks[k]


async def _get_agent(request: Request, conversation_id: str | None, profile_id: str | None = None):
    """Get per-session agent from pool, or fallback to global agent."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None and conversation_id:
        try:
            return pool.get_or_create(conversation_id, profile_id)
        except Exception:
            pass
    return getattr(request.app.state, "agent", None)


@router.post("/chat")
async def seecrab_chat(body: SeeCrabChatRequest, request: Request):
    """SSE streaming chat via SeeCrabAdapter."""
    # Get agent from pool (per-session isolation) or fallback to global
    agent = await _get_agent(request, body.conversation_id, body.agent_profile_id)
    if agent is None:
        return JSONResponse({"error": "Agent not initialized"}, status_code=503)

    session_manager = getattr(request.app.state, "session_manager", None)
    conversation_id = body.conversation_id or f"seecrab_{uuid.uuid4().hex[:12]}"
    client_id = body.client_id or uuid.uuid4().hex[:8]

    # Busy-lock check
    if not await _mark_busy(conversation_id, client_id):
        return JSONResponse(
            {"error": "Another request is already processing this conversation"},
            status_code=409,
        )

    async def generate():
        from seeagent.api.adapters.seecrab_adapter import SeeCrabAdapter

        # Disconnect watcher
        disconnect_event = asyncio.Event()

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    logger.info(f"[SeeCrab] Client disconnected: {conversation_id}")
                    if hasattr(agent, "cancel_current_task"):
                        agent.cancel_current_task("客户端断开连接", session_id=conversation_id)
                    disconnect_event.set()
                    return
                await asyncio.sleep(2)

        watcher_task = asyncio.create_task(_disconnect_watcher())
        adapter = None

        try:
            # Resolve session
            session = None
            session_messages: list[dict] = []
            user_messages: list[str] = []
            if session_manager and conversation_id:
                try:
                    session = session_manager.get_session(
                        channel="seecrab",
                        chat_id=conversation_id,
                        user_id="seecrab_user",
                        create_if_missing=True,
                    )
                    if session and body.message:
                        session.add_message("user", body.message)
                        session_messages = list(
                            session.context.messages
                        ) if hasattr(session, "context") else []
                        user_messages = [
                            m.get("content", "")
                            for m in session_messages
                            if m.get("role") == "user"
                        ][-5:]
                        session_manager.mark_dirty()
                except Exception as e:
                    logger.warning(f"[SeeCrab] Session error: {e}")

            if not user_messages and body.message:
                user_messages = [body.message]

            brain = getattr(agent, "brain", None)
            adapter = SeeCrabAdapter(brain=brain, user_messages=user_messages)
            reply_id = f"reply_{uuid.uuid4().hex[:12]}"

            raw_stream = agent.chat_with_session_stream(
                message=body.message,
                session_messages=session_messages,
                session_id=conversation_id,
                session=session,
                plan_mode=body.plan_mode,
                endpoint_override=body.endpoint,
                thinking_mode=body.thinking_mode,
                thinking_depth=body.thinking_depth,
                attachments=body.attachments,
            )

            # Dual-loop bridge if needed
            try:
                from seeagent.core.engine_bridge import engine_stream, is_dual_loop
                if is_dual_loop():
                    raw_stream = engine_stream(raw_stream)
            except ImportError:
                pass

            # Emit session_title from first user message
            is_first_message = len(user_messages) <= 1
            if is_first_message and body.message:
                title = body.message[:30] + ("..." if len(body.message) > 30 else "")
                title_event = json.dumps({
                    "type": "session_title",
                    "session_id": conversation_id,
                    "title": title,
                }, ensure_ascii=False)
                yield f"data: {title_event}\n\n"
                # Persist title in metadata (survives to_dict/from_dict)
                if session:
                    session.metadata["title"] = title
                    session_manager.mark_dirty()

            full_reply = ""
            reply_state = {
                "thinking": "",
                "step_cards": [],
                "plan_checklist": None,
                "timer": {"ttft": None, "total": None},
            }

            async for event in adapter.transform(raw_stream, reply_id=reply_id):
                if disconnect_event.is_set():
                    break
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"

                # Collect reply_state for persistence
                etype = event.get("type")
                if etype == "ai_text":
                    full_reply += event.get("content", "")
                elif etype == "thinking":
                    reply_state["thinking"] += event.get("content", "")
                elif etype == "step_card":
                    _upsert_step_card(reply_state["step_cards"], event)
                elif etype == "plan_checklist":
                    reply_state["plan_checklist"] = event.get("steps")
                elif etype == "timer_update":
                    phase = event.get("phase")
                    if phase in reply_state["timer"] and event.get("state") == "done":
                        reply_state["timer"][phase] = event.get("value")

            # Save assistant reply with reply_state to session
            if session and full_reply:
                try:
                    session.add_message(
                        "assistant", full_reply, reply_state=reply_state
                    )
                    if session_manager:
                        session_manager.mark_dirty()
                except Exception:
                    pass

        except Exception as e:
            logger.exception(f"[SeeCrab] Chat error: {e}")
            err = json.dumps(
                {"type": "error", "message": str(e), "code": "internal"},
                ensure_ascii=False,
            )
            yield f"data: {err}\n\n"
            yield 'data: {"type": "done"}\n\n'
        finally:
            # Cleanup: flush aggregator to cancel any pending title tasks
            if adapter is not None:
                try:
                    await adapter.aggregator.flush()
                except Exception:
                    pass
            watcher_task.cancel()
            try:
                await watcher_task
            except (asyncio.CancelledError, Exception):
                pass
            _clear_busy(conversation_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
async def list_sessions(request: Request):
    """List conversation sessions."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"sessions": []})
    try:
        sessions = sm.list_sessions(channel="seecrab")
        sessions.sort(key=lambda s: s.last_active, reverse=True)
        result = []
        for s in sessions:
            messages = s.context.messages if hasattr(s, "context") else []
            last_msg = ""
            if messages:
                # Prefer last assistant reply, fallback to last user message
                for m in reversed(messages):
                    role = m.get("role", "")
                    if role == "assistant":
                        last_msg = m.get("content", "")[:80]
                        break
                    if role == "user" and not last_msg:
                        last_msg = m.get("content", "")[:80]
            result.append({
                "id": s.chat_id,
                "title": s.metadata.get("title", s.chat_id),
                "updated_at": getattr(s, "last_active", datetime.now()).timestamp() * 1000,
                "message_count": len(messages),
                "last_message": last_msg,
            })
        return JSONResponse({"sessions": result})
    except Exception:
        return JSONResponse({"sessions": []})


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get session detail with message history (for SSE reconnect state recovery)."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)
    try:
        session = sm.get_session(
            channel="seecrab",
            chat_id=session_id,
            user_id="seecrab_user",
            create_if_missing=False,
        )
        if session is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        messages = []
        if hasattr(session, "context") and hasattr(session.context, "messages"):
            for m in session.context.messages:
                msg_dict = {
                    "role": m.get("role", ""),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", 0),
                    "metadata": m.get("metadata", {}),
                }
                if m.get("reply_state"):
                    msg_dict["reply_state"] = m["reply_state"]
                messages.append(msg_dict)
        return JSONResponse({
            "session_id": session_id,
            "title": session.metadata.get("title", session_id),
            "messages": messages,
        })
    except Exception as e:
        logger.warning(f"[SeeCrab] Get session error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/sessions")
async def create_session(request: Request):
    """Create a new conversation session."""
    session_id = f"seecrab_{uuid.uuid4().hex[:12]}"
    sm = getattr(request.app.state, "session_manager", None)
    if sm:
        sm.get_session(
            channel="seecrab",
            chat_id=session_id,
            user_id="seecrab_user",
            create_if_missing=True,
        )
        sm.mark_dirty()
    return JSONResponse({"session_id": session_id})


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str, body: SeeCrabSessionUpdateRequest, request: Request,
):
    """Update session metadata (title, etc.)."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)
    session = sm.get_session(
        channel="seecrab",
        chat_id=session_id,
        user_id="seecrab_user",
        create_if_missing=False,
    )
    if session is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if body.title is not None:
        session.set_metadata("title", body.title)
    sm.mark_dirty()
    return JSONResponse({"status": "ok"})


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Delete a conversation session."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)
    session_key = f"seecrab:{session_id}:seecrab_user"
    if sm.close_session(session_key):
        return JSONResponse({"status": "ok"})
    return JSONResponse({"error": "Session not found"}, status_code=404)


@router.post("/answer")
async def answer_ask_user(body: SeeCrabAnswerRequest, request: Request):
    """Submit answer to ask_user event.

    The Agent's ask_user mechanism works through gateway.check_interrupt(),
    which only supports IM channels. For SeeCrab (desktop/web), the answer
    should be sent as a new /api/seecrab/chat message with the same
    conversation_id. This endpoint acknowledges the answer and instructs
    the client accordingly.
    """
    return JSONResponse({
        "status": "ok",
        "conversation_id": body.conversation_id,
        "answer": body.answer,
        "hint": "Please send the answer as a new /api/seecrab/chat message with the same conversation_id",
    })
