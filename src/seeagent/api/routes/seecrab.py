# src/seeagent/api/routes/seecrab.py
"""SeeCrab API routes: SSE streaming chat + session management."""
from __future__ import annotations

from typing import Any

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

_BP_START_COMMANDS = {
    "进入最佳实践",
    "最佳实践模式",
    "开始最佳实践",
}
_BP_NEXT_COMMANDS = {
    "进入下一步",
    "下一步",
    "继续执行",
    "继续",
}

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


def _normalize_bp_command(message: str) -> str:
    punct = " \t\r\n，。！？,.!?：:；;“”\"'`（）()【】[]"
    return "".join(ch for ch in (message or "").strip().lower() if ch not in punct)


def _match_bp_command(message: str) -> str | None:
    normalized = _normalize_bp_command(message)
    if normalized in _BP_START_COMMANDS:
        return "start"
    if normalized in _BP_NEXT_COMMANDS:
        return "next"
    return None


def _has_bp_next_step(snap) -> bool:
    if not snap:
        return False
    total = len(snap.bp_config.subtasks) if snap.bp_config else len(snap.subtask_statuses or {})
    if total <= 0:
        return False
    return int(getattr(snap, "current_subtask_index", 0) or 0) < total


async def _extract_input_from_query(
    brain: Any, user_query: str, input_schema: dict,
) -> dict:
    """用 LLM 从用户原始 query 中提取符合 input_schema 的结构化参数。"""
    if not brain or not user_query or not input_schema:
        return {}

    props = input_schema.get("properties", {})
    if not props:
        return {}

    fields_desc = "\n".join(
        f"- {name}: {info.get('description', '无描述')} (type: {info.get('type', 'string')})"
        for name, info in props.items()
    )
    prompt = (
        "从用户消息中提取以下字段，输出一个 JSON 对象。\n"
        "只提取消息中明确提到或可推断的字段，没有提到的字段不要包含。\n"
        "只输出 JSON，不要其他文字。\n\n"
        f"## 字段定义\n{fields_desc}\n\n"
        f"## 用户消息\n{user_query}"
    )
    try:
        from seeagent.bestpractice.engine import BPEngine

        resp = await brain.think_lightweight(prompt, max_tokens=512)
        text = resp.content if hasattr(resp, "content") else str(resp)
        parsed = BPEngine._parse_output(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        logger.warning(f"[BP] Failed to extract input from query: {e}")
    return {}


async def _stream_bp_start_from_chat(
    request: Request,
    *,
    session_id: str,
    bp_id: str,
    run_mode_str: str,
    input_data: dict,
    session,
    session_manager,
    disconnect_event: asyncio.Event,
):
    from seeagent.api.routes.bestpractice import (
        _bp_clear_busy,
        _bp_mark_busy,
        _bp_renew_busy,
        _collect_reply_state,
        _new_reply_state,
        _persist_bp_to_session,
    )
    from seeagent.bestpractice.facade import (
        get_bp_config_loader,
        get_bp_engine,
        get_bp_state_manager,
    )
    from seeagent.bestpractice.models import RunMode

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        yield {"type": "error", "message": "BP system not initialized", "code": "bp"}
        yield {"type": "done"}
        return
    active = sm.get_active(session_id)
    if active:
        yield {
            "type": "ai_text",
            "content": "当前已有进行中的最佳实践任务，请先使用“进入下一步”继续。",
        }
        yield {"type": "done"}
        return
    loader = get_bp_config_loader()
    bp_config = loader.configs.get(bp_id) if loader and loader.configs else None
    if not bp_config:
        yield {"type": "error", "message": f"BP '{bp_id}' not found", "code": "bp"}
        yield {"type": "done"}
        return
    if not await _bp_mark_busy(session_id, "seecrab_bp_start"):
        yield {"type": "error", "message": "Session is busy", "code": "bp"}
        yield {"type": "done"}
        return

    run_mode = RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL
    instance_id = sm.create_instance(
        bp_config, session_id, initial_input=input_data, run_mode=run_mode,
    )
    reply_state = _new_reply_state()
    full_reply: list[str] = []
    try:
        created_event = {
            "type": "bp_instance_created",
            "instance_id": instance_id,
            "bp_id": bp_id,
            "bp_name": bp_config.name,
            "run_mode": run_mode.value,
            "subtasks": [
                {"id": s.id, "name": s.name}
                for s in bp_config.subtasks
            ],
        }
        yield created_event
        _collect_reply_state(created_event, reply_state, full_reply)
        async for event in engine.advance(instance_id, session):
            if disconnect_event.is_set():
                break
            yield event
            _collect_reply_state(event, reply_state, full_reply)
            if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                _bp_renew_busy(session_id)
        _persist_bp_to_session(
            session,
            instance_id,
            sm,
            reply_state=reply_state,
            full_reply="".join(full_reply),
            session_manager=session_manager,
        )
        sm.clear_pending_offer(session_id)
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e), "code": "bp"}
        yield {"type": "done"}
    finally:
        _bp_clear_busy(session_id)


async def _stream_bp_next_from_chat(
    request: Request,
    *,
    session_id: str,
    instance_id: str,
    session,
    session_manager,
    disconnect_event: asyncio.Event,
):
    from seeagent.api.routes.bestpractice import (
        _bp_clear_busy,
        _bp_mark_busy,
        _bp_renew_busy,
        _collect_reply_state,
        _ensure_bp_restored,
        _new_reply_state,
        _persist_bp_to_session,
    )
    from seeagent.bestpractice.facade import get_bp_engine, get_bp_state_manager

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        yield {"type": "error", "message": "BP system not initialized", "code": "bp"}
        yield {"type": "done"}
        return
    _ensure_bp_restored(request, session_id, sm)
    snap = sm.get(instance_id)
    if not snap:
        yield {"type": "ai_text", "content": "当前没有可继续的最佳实践任务。"}
        yield {"type": "done"}
        return
    if not _has_bp_next_step(snap):
        yield {"type": "ai_text", "content": "当前最佳实践已完成或没有下一步可执行。"}
        yield {"type": "done"}
        return
    if not await _bp_mark_busy(session_id, "seecrab_bp_next"):
        yield {"type": "error", "message": "Session is busy", "code": "bp"}
        yield {"type": "done"}
        return

    reply_state = _new_reply_state()
    full_reply: list[str] = []
    try:
        async for event in engine.advance(instance_id, session):
            if disconnect_event.is_set():
                break
            yield event
            _collect_reply_state(event, reply_state, full_reply)
            if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                _bp_renew_busy(session_id)
        _persist_bp_to_session(
            session,
            instance_id,
            sm,
            reply_state=reply_state,
            full_reply="".join(full_reply),
            session_manager=session_manager,
        )
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e), "code": "bp"}
        yield {"type": "done"}
    finally:
        _bp_clear_busy(session_id)


@router.post("/chat")
async def seecrab_chat(body: SeeCrabChatRequest, request: Request):
    """SSE streaming chat via SeeCrabAdapter."""
    logger.info(f"[BP-DEBUG] /chat received: msg={body.message!r}, conv_id={body.conversation_id}")
    # Get agent from pool (per-session isolation) or fallback to global
    agent = await _get_agent(request, body.conversation_id, body.agent_profile_id)
    if agent is None:
        return JSONResponse({"error": "Agent not initialized"}, status_code=503)

    session_manager = getattr(request.app.state, "session_manager", None)
    conversation_id = body.conversation_id or f"seecrab_{uuid.uuid4().hex[:12]}"
    client_id = body.client_id or uuid.uuid4().hex[:8]

    # Busy-lock check
    if not await _mark_busy(conversation_id, client_id):
        logger.warning(f"[BP-DEBUG] 409 BUSY LOCK for conv_id={conversation_id}, client_id={client_id}")
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

            # 使用 conversation_id(=chat_id)，与前端 activeSessionId 一致，
            # 确保 /api/bp/start 能通过 get_pending_offer(session_id) 找到 pending_offer
            bp_session_id = conversation_id
            bp_cmd = _match_bp_command(body.message or "")
            if bp_cmd:
                from seeagent.bestpractice.facade import get_bp_state_manager

                bp_sm = get_bp_state_manager()
                if bp_cmd == "start":
                    active = bp_sm.get_active(bp_session_id) if bp_sm else None
                    if active:
                        fallback = {
                            "type": "ai_text",
                            "content": "当前已有进行中的最佳实践任务，请先使用“进入下一步”继续。",
                        }
                        yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                        yield 'data: {"type": "done"}\n\n'
                        return
                    pending_offer = bp_sm.get_pending_offer(bp_session_id) if bp_sm else None
                    if pending_offer and pending_offer.get("bp_id"):
                        # 用 LLM 从用户原始 query 中提取 input_data
                        extracted_input = {}
                        user_query = pending_offer.get("user_query", "")
                        first_schema = pending_offer.get("first_input_schema")
                        if user_query and first_schema:
                            brain = getattr(agent, "brain", None)
                            extracted_input = await _extract_input_from_query(
                                brain, user_query, first_schema,
                            )
                            logger.info(
                                f"[BP] Extracted input from query: {extracted_input}"
                            )
                        async for event in _stream_bp_start_from_chat(
                            request,
                            session_id=bp_session_id,
                            bp_id=pending_offer.get("bp_id", ""),
                            run_mode_str=pending_offer.get("default_run_mode", "manual"),
                            input_data=extracted_input,
                            session=session,
                            session_manager=session_manager,
                            disconnect_event=disconnect_event,
                        ):
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        return
                    fallback = {
                        "type": "ai_text",
                        "content": "当前没有可进入的最佳实践，请先触发最佳实践推荐。",
                    }
                    yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                    yield 'data: {"type": "done"}\n\n'
                    return
                if bp_cmd == "next":
                    active = bp_sm.get_active(bp_session_id) if bp_sm else None
                    if active and _has_bp_next_step(active):
                        async for event in _stream_bp_next_from_chat(
                            request,
                            session_id=bp_session_id,
                            instance_id=active.instance_id,
                            session=session,
                            session_manager=session_manager,
                            disconnect_event=disconnect_event,
                        ):
                            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        return
                    fallback = {"type": "ai_text", "content": "当前没有可继续的最佳实践任务。"}
                    yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
                    yield 'data: {"type": "done"}\n\n'
                    return

            try:
                from seeagent.bestpractice.facade import match_bp_from_message
                bp_match = match_bp_from_message(body.message or "", bp_session_id)
                if bp_match:
                    bp_name = bp_match["bp_name"]
                    bp_id = bp_match["bp_id"]
                    subtask_names = " → ".join(
                        s["name"] for s in bp_match.get("subtasks", [])
                    )
                    question = (
                        f"检测到您的需求匹配最佳实践「{bp_name}」，"
                        f"该任务包含 {bp_match['subtask_count']} 个子任务："
                        f"{subtask_names}。是否使用最佳实践流程？"
                    )

                    # Emit session_title for first message
                    is_first_message = len(user_messages) <= 1
                    if is_first_message and body.message:
                        title = body.message[:30] + ("..." if len(body.message) > 30 else "")
                        title_event = json.dumps({
                            "type": "session_title",
                            "session_id": conversation_id,
                            "title": title,
                        }, ensure_ascii=False)
                        yield f"data: {title_event}\n\n"
                        if session:
                            session.metadata["title"] = title
                            session_manager.mark_dirty()

                    ask_event = json.dumps({
                        "type": "bp_offer",
                        "bp_id": bp_id,
                        "bp_name": bp_name,
                        "subtasks": bp_match.get("subtasks", []),
                        "default_run_mode": "manual",
                    }, ensure_ascii=False)
                    yield f"data: {ask_event}\n\n"

                    # Mark this BP as offered so it won't re-trigger in this session
                    from seeagent.bestpractice.facade import get_bp_state_manager
                    bp_sm = get_bp_state_manager()
                    if bp_sm:
                        bp_sm.mark_bp_offered(bp_session_id, bp_id)
                        bp_sm.set_pending_offer(
                            bp_session_id,
                            {
                                "bp_id": bp_id,
                                "bp_name": bp_name,
                                "subtasks": bp_match.get("subtasks", []),
                                "default_run_mode": "manual",
                                "user_query": bp_match.get("user_query", ""),
                                "first_input_schema": bp_match.get("first_input_schema"),
                            },
                        )

                    if session:
                        session.add_message(
                            "assistant", question,
                            reply_state={"bp_offer": {
                                "bp_id": bp_id,
                                "bp_name": bp_name,
                                "subtasks": bp_match.get("subtasks", []),
                            }},
                        )
                        if session_manager:
                            session_manager.mark_dirty()

                    yield 'data: {"type": "done"}\n\n'
                    return  # Skip LLM stream — wait for user choice
            except Exception:
                pass  # Non-critical, don't block chat

            brain = getattr(agent, "brain", None)
            adapter = SeeCrabAdapter(brain=brain, user_messages=user_messages)
            event_bus = asyncio.Queue()
            if session and hasattr(session, "context"):
                session.context._sse_event_bus = event_bus
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
            logger.info(f"[BP-DEBUG] agent.chat_with_session_stream started for msg={body.message!r}")

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
                "agent_thinking": {},
                "agent_summaries": {},
                "plan_checklist": None,
                "timer": {"ttft": None, "total": None},
                "bp_progress": None,
                "bp_subtask_output": None,
            }

            async for event in adapter.transform(raw_stream, reply_id=reply_id, event_bus=event_bus):
                if disconnect_event.is_set():
                    break
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"

                # Collect reply_state for persistence
                etype = event.get("type")
                if etype == "ai_text":
                    aid = event.get("agent_id")
                    if aid and aid != "main":
                        reply_state["agent_summaries"][aid] = (
                            reply_state["agent_summaries"].get(aid, "")
                            + event.get("content", "")
                        )
                    else:
                        full_reply += event.get("content", "")
                elif etype == "thinking":
                    aid = event.get("agent_id")
                    if aid and aid != "main":
                        at = reply_state["agent_thinking"].setdefault(
                            aid, {"content": "", "done": False},
                        )
                        at["content"] += event.get("content", "")
                    else:
                        reply_state["thinking"] += event.get("content", "")
                elif etype == "step_card":
                    _upsert_step_card(reply_state["step_cards"], event)
                elif etype == "plan_checklist":
                    reply_state["plan_checklist"] = event.get("steps")
                elif etype == "timer_update":
                    phase = event.get("phase")
                    if phase in reply_state["timer"] and event.get("state") == "done":
                        reply_state["timer"][phase] = event.get("value")
                elif etype == "bp_progress":
                    reply_state["bp_progress"] = event
                elif etype in ("bp_subtask_output", "bp_subtask_complete"):
                    reply_state["bp_subtask_output"] = event

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
            # Remove stale event_bus reference from session context
            if session and hasattr(session, "context"):
                session.context._sse_event_bus = None
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
