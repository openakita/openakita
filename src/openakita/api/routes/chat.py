"""
Chat route: POST /api/chat (SSE streaming)

流式返回 AI 对话响应，包含思考内容、文本、工具调用、Plan 等事件。
使用完整的 Agent 流水线（与 IM/CLI 共享 _prepare_session_context / _finalize_session）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from openakita.core.engine_bridge import engine_stream, is_dual_loop, to_engine

from ..schemas import ChatAnswerRequest, ChatControlRequest, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Conversation busy-lock ────────────────────────────────────────────────
BUSY_TIMEOUT_SECONDS = 600  # 10 min auto-release


@dataclass
class BusyInfo:
    client_id: str
    start_time: float = field(default_factory=time.time)


_busy_conversations: dict[str, BusyInfo] = {}
_busy_lock = asyncio.Lock()
_busy_thread_lock = __import__("threading").Lock()


async def _mark_busy(conversation_id: str, client_id: str) -> BusyInfo | None:
    """Mark a conversation as busy. Returns existing BusyInfo if already busy
    from a *different* client, or None on success."""
    async with _busy_lock:
        _expire_stale_locks()
        existing = _busy_conversations.get(conversation_id)
        if existing and existing.client_id != client_id:
            return existing
        _busy_conversations[conversation_id] = BusyInfo(client_id=client_id)
        return None


async def _clear_busy(conversation_id: str) -> None:
    # Use thread-safe lock to support cross-loop calls (engine → API)
    with _busy_thread_lock:
        _busy_conversations.pop(conversation_id, None)


def _expire_stale_locks() -> None:
    """Remove busy entries older than BUSY_TIMEOUT_SECONDS. Must be called under _busy_lock."""
    now = time.time()
    stale = [k for k, v in _busy_conversations.items() if now - v.start_time > BUSY_TIMEOUT_SECONDS]
    for k in stale:
        logger.info("[Chat API] Auto-releasing stale busy lock: conv=%s", k)
        del _busy_conversations[k]


async def _broadcast_chat_event(event: str, data: dict) -> None:
    """Broadcast a chat event via WebSocket to all connected clients."""
    try:
        from .websocket import broadcast_event
        await broadcast_event(event, data)
    except Exception:
        pass


def _resolve_agent(agent: object):
    """Resolve the actual Agent instance."""
    from openakita.core.agent import Agent

    if isinstance(agent, Agent):
        return agent
    return None


def _is_multi_agent_enabled() -> bool:
    from openakita.config import settings
    return settings.multi_agent_enabled


def _resolve_profile(agent_profile_id: str | None):
    """Resolve an AgentProfile by id, falling back to 'default'."""
    from openakita.agents.presets import SYSTEM_PRESETS
    from openakita.agents.profile import AgentProfile, ProfileStore
    from openakita.config import settings

    pid = agent_profile_id or "default"

    for p in SYSTEM_PRESETS:
        if p.id == pid:
            return p

    try:
        store = ProfileStore(settings.data_dir / "agents")
        profile = store.get(pid)
        if profile:
            return profile
    except Exception:
        pass

    for p in SYSTEM_PRESETS:
        if p.id == "default":
            return p

    return AgentProfile(id="default", name="Default Agent")


async def _get_agent_for_session(request: Request, conversation_id: str, agent_profile_id: str | None = None):
    """Get a per-session Agent from pool, or fallback to global agent."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None and conversation_id:
        profile = _resolve_profile(agent_profile_id)
        return await to_engine(pool.get_or_create(conversation_id, profile))
    return getattr(request.app.state, "agent", None)


def _get_existing_agent(request: Request, conversation_id: str | None):
    """Get the existing Agent for a session (no creation). For control ops."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None and conversation_id:
        agent = pool.get_existing(conversation_id)
        if agent is not None:
            return agent
    return getattr(request.app.state, "agent", None)


def _apply_agent_profile(session: object, new_profile_id: str) -> bool:
    """Store agent_profile_id in session context and record the switch.

    Returns True if profile was applied, False if profile_id is invalid.
    """
    from datetime import datetime

    ctx = getattr(session, "context", None)
    if ctx is None:
        return False
    old_profile_id = ctx.agent_profile_id
    if old_profile_id == new_profile_id:
        return True

    # Validate that profile exists
    try:
        from openakita.agents.presets import SYSTEM_PRESETS
        from openakita.agents.profile import ProfileStore
        from openakita.config import settings

        known_ids = {p.id for p in SYSTEM_PRESETS}
        if new_profile_id not in known_ids:
            store = ProfileStore(settings.data_dir / "agents")
            if store.get(new_profile_id) is None:
                logger.warning(f"[Chat API] Unknown agent profile: {new_profile_id!r}")
                return False
    except Exception:
        pass  # graceful fallback — allow switch if validation infra unavailable

    ctx.agent_switch_history.append({
        "from": old_profile_id,
        "to": new_profile_id,
        "at": datetime.now().isoformat(),
    })
    ctx.agent_profile_id = new_profile_id
    logger.info(
        f"[Chat API] Agent profile switched: {old_profile_id!r} -> {new_profile_id!r}"
    )
    return True


async def _stream_chat(
    chat_request: ChatRequest,
    agent: object,
    session_manager: object | None = None,
    http_request: Request | None = None,
) -> AsyncIterator[str]:
    """Generate SSE events via Agent.chat_with_session_stream().

    这是一个瘦 SSE 传输层，核心逻辑全部委托给 Agent 流水线。
    只负责：
    - SSE 格式包装
    - 客户端断开检测
    - artifact 事件注入（deliver_artifacts）
    - ask_user 文本捕获
    - Session 回复保存
    """

    _reply_chars = 0
    _reply_preview = ""
    _full_reply = ""  # 完整回复文本（用于 session 保存）
    _done_sent = False
    _client_disconnected = False
    _ask_user_question = ""
    _ask_user_options: list[dict] = []
    _ask_user_questions: list[dict] = []
    _collected_artifacts: list[dict] = []

    async def _check_disconnected() -> bool:
        nonlocal _client_disconnected
        if _client_disconnected:
            return True
        if http_request is not None:
            try:
                if await http_request.is_disconnected():
                    _client_disconnected = True
                    logger.info("[Chat API] 客户端已断开连接，停止流式输出")
                    return True
            except Exception:
                pass
        return False

    def _sse(event_type: str, data: dict | None = None) -> str:
        nonlocal _reply_chars, _reply_preview, _full_reply, _done_sent
        if event_type == "done":
            if _done_sent:
                return ""
            _done_sent = True
            preview = _reply_preview[:100].replace("\n", " ")
            try:
                logger.info(
                    f"[Chat API] 回复完成: {_reply_chars}字 | "
                    f"\"{preview}{'...' if _reply_chars > 100 else ''}\""
                )
            except (UnicodeEncodeError, OSError):
                pass
        payload = {"type": event_type, **(data or {})}
        if event_type == "text_delta" and data and "content" in data:
            chunk = data["content"]
            _reply_chars += len(chunk)
            _full_reply += chunk
            if len(_reply_preview) < 120:
                _reply_preview += chunk
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    _disconnect_watcher_task: asyncio.Task | None = None

    try:
        actual_agent = _resolve_agent(agent)
        if actual_agent is None:
            yield _sse("error", {"message": "Agent not initialized"})
            yield _sse("done")
            return

        brain = actual_agent.brain
        if brain is None:
            yield _sse("error", {"message": "Agent brain not initialized"})
            yield _sse("done")
            return

        # Ensure agent is initialized
        if not actual_agent._initialized:
            await actual_agent.initialize()

        # --- Session management ---
        import uuid as _uuid
        conversation_id = chat_request.conversation_id or f"api_{_uuid.uuid4().hex[:12]}"
        session = None
        session_messages_history: list[dict] = []

        if session_manager and conversation_id:
            try:
                session = session_manager.get_session(
                    channel="desktop",
                    chat_id=conversation_id,
                    user_id="desktop_user",
                    create_if_missing=True,
                )
                if session:
                    # Apply agent_profile_id if multi-agent mode is enabled
                    if chat_request.agent_profile_id and _is_multi_agent_enabled():
                        _apply_agent_profile(session, chat_request.agent_profile_id)

                    # 先添加用户消息，再获取完整历史（含当前消息）
                    # 这与 IM 路径一致：gateway 先 add_message，再传 session_messages
                    if chat_request.message:
                        session.add_message("user", chat_request.message)
                    session_messages_history = list(session.context.messages) if hasattr(session, "context") else []
                    session_manager.mark_dirty()
            except Exception as e:
                logger.warning(f"[Chat API] Session management error: {e}")

        # --- 后台断连检测：定期检查客户端是否断开，主动触发 cancel ---
        async def _disconnect_watcher():
            nonlocal _client_disconnected
            while True:
                await asyncio.sleep(2.0)
                if _client_disconnected:
                    break
                if http_request is not None:
                    try:
                        if await http_request.is_disconnected():
                            _client_disconnected = True
                            logger.info(
                                "[Chat API] 断连检测器发现客户端断开，触发 cancel_current_task"
                            )
                            try:
                                actual_agent.cancel_current_task(
                                    "客户端断开连接", session_id=conversation_id
                                )
                            except Exception as e:
                                logger.warning(f"[Chat API] 断连触发 cancel 失败: {e}")
                            break
                    except Exception:
                        break

        _disconnect_watcher_task = asyncio.create_task(_disconnect_watcher())

        # --- 委托给 Agent 统一流水线 ---
        # Resolve effective mode (backward compat)
        _effective_mode = chat_request.mode
        if chat_request.plan_mode and _effective_mode == "agent":
            _effective_mode = "plan"

        async for event in actual_agent.chat_with_session_stream(
            message=chat_request.message or "",
            session_messages=session_messages_history,
            session_id=conversation_id,
            session=session,
            gateway=None,
            plan_mode=_effective_mode == "plan",
            mode=_effective_mode,
            endpoint_override=chat_request.endpoint,
            attachments=chat_request.attachments,
            thinking_mode=chat_request.thinking_mode,
            thinking_depth=chat_request.thinking_depth,
        ):
            # Check if client disconnected
            if await _check_disconnected():
                actual_agent.cancel_current_task(
                    "客户端断开连接", session_id=conversation_id
                )
                break

            event_type = event.get("type", "")

            # 拦截 done 事件：不在此处转发，等 usage 收集完毕后统一发送
            if event_type == "done":
                continue

            # 捕获 ask_user 问题文本和选项（用于 session 保存）
            if event_type == "ask_user":
                _ask_user_question = event.get("question", "")
                _ask_user_options = event.get("options", [])
                _ask_user_questions = event.get("questions", [])

            # Inject artifact events for deliver_artifacts results
            yield _sse(event_type, {k: v for k, v in event.items() if k != "type"})

            # deliver_artifacts / send_sticker 都可能返回带 receipts 的 JSON
            _artifact_tools = ("deliver_artifacts", "send_sticker")
            if event_type == "tool_call_end" and event.get("tool") in _artifact_tools:
                try:
                    result_str = event.get("result", "{}")
                    _log_marker = "\n\n[执行日志]"
                    if _log_marker in result_str:
                        result_str = result_str[: result_str.index(_log_marker)]
                    result_data = json.loads(result_str)
                    _receipts = result_data.get("receipts", [])
                    _emitted = 0
                    for receipt in _receipts:
                        if receipt.get("status") == "delivered" and receipt.get("file_url"):
                            art_data = {
                                "artifact_type": receipt.get("type", "file"),
                                "file_url": receipt["file_url"],
                                "path": receipt.get("path", ""),
                                "name": receipt.get("name", ""),
                                "caption": receipt.get("caption", ""),
                                "size": receipt.get("size"),
                            }
                            _collected_artifacts.append(art_data)
                            yield _sse("artifact", art_data)
                            _emitted += 1
                    logger.info(
                        f"[Chat API] Artifact SSE: tool={event.get('tool')}, "
                        f"receipts={len(_receipts)}, emitted={_emitted}"
                    )
                except (json.JSONDecodeError, TypeError, KeyError) as exc:
                    logger.warning(
                        f"[Chat API] Artifact parse failed for {event.get('tool')}: {exc!r}, "
                        f"result preview: {str(event.get('result', ''))[:200]}"
                    )

            # Forward artifact receipts from sub-agents (via orchestrator delegation).
            # delegate_parallel may contain multiple __ARTIFACT_RECEIPTS__ blocks.
            _delegation_tools = ("delegate_to_agent", "delegate_parallel", "spawn_agent")
            if event_type == "tool_call_end" and event.get("tool") in _delegation_tools:
                _art_marker = "__ARTIFACT_RECEIPTS__\n"
                _del_result = event.get("result", "")
                _search_pos = 0
                _del_emitted = 0
                while _art_marker in _del_result[_search_pos:]:
                    try:
                        _idx = _del_result.index(_art_marker, _search_pos) + len(_art_marker)
                        _eol = _del_result.find("\n", _idx)
                        _chunk = _del_result[_idx:] if _eol < 0 else _del_result[_idx:_eol]
                        _search_pos = _idx + len(_chunk)
                        for receipt in json.loads(_chunk):
                            if isinstance(receipt, dict) and receipt.get("file_url"):
                                art_data = {
                                    "artifact_type": receipt.get("type", "file"),
                                    "file_url": receipt["file_url"],
                                    "path": receipt.get("path", ""),
                                    "name": receipt.get("name", ""),
                                    "caption": receipt.get("caption", ""),
                                    "size": receipt.get("size"),
                                }
                                _collected_artifacts.append(art_data)
                                yield _sse("artifact", art_data)
                                _del_emitted += 1
                    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
                        logger.warning(
                            f"[Chat API] Delegation artifact parse failed: {exc!r}, "
                            f"chunk preview: {_del_result[max(0, _search_pos - 50):_search_pos + 100]}"
                        )
                        break
                if _art_marker in _del_result:
                    logger.info(
                        f"[Chat API] Delegation artifact SSE: tool={event.get('tool')}, "
                        f"emitted={_del_emitted}"
                    )

            # Inject ui_preference events for system_config set_ui results
            if event_type == "tool_call_end" and event.get("tool") == "system_config":
                try:
                    result_str = event.get("result", "")
                    if '"ui_preference"' in result_str:
                        _log_marker = "\n\n[执行日志]"
                        if _log_marker in result_str:
                            result_str = result_str[: result_str.index(_log_marker)]
                        result_data = json.loads(result_str)
                        ui_pref = result_data.get("ui_preference")
                        if ui_pref:
                            yield _sse("ui_preference", ui_pref)
                except (json.JSONDecodeError, TypeError, KeyError):
                    pass

        # --- Save assistant response to session ---
        # ask_user 场景：_ask_user_question 已包含 LLM 文本 + 问题（由 reason_stream 拼接），
        # 优先使用它作为保存文本，确保下一轮 LLM 能看到完整的确认问题上下文。
        if _ask_user_question:
            parts = [_ask_user_question]
            if _ask_user_questions:
                for q in _ask_user_questions:
                    q_prompt = q.get("prompt", "")
                    q_opts = q.get("options", [])
                    if q_prompt:
                        parts.append(f"\n{q_prompt}")
                    if q_opts:
                        for o in q_opts:
                            parts.append(f"  - {o.get('id', '')}: {o.get('label', '')}")
            elif _ask_user_options:
                parts.append("\n选项：")
                for o in _ask_user_options:
                    parts.append(f"  - {o.get('id', '')}: {o.get('label', '')}")
            assistant_text_to_save = "\n".join(parts)
        else:
            assistant_text_to_save = _full_reply

        # Collect tool execution summary as structured metadata
        _tool_summary = None
        try:
            _tool_summary = actual_agent.build_tool_trace_summary() or None
            if _tool_summary:
                logger.debug(f"[Chat API] Tool trace summary ({len(_tool_summary)} chars)")
        except Exception:
            pass

        _chain_summary = None
        if session:
            try:
                _chain_summary = session.get_metadata("_last_chain_summary")
                session.set_metadata("_last_chain_summary", None)
            except Exception:
                pass

        if session and assistant_text_to_save:
            try:
                _msg_meta: dict = {}
                if _chain_summary:
                    _msg_meta["chain_summary"] = _chain_summary
                if _tool_summary:
                    _msg_meta["tool_summary"] = _tool_summary
                if _collected_artifacts:
                    _msg_meta["artifacts"] = _collected_artifacts
                if _ask_user_question:
                    _ask_user_data: dict = {"question": _ask_user_question}
                    if _ask_user_options:
                        _ask_user_data["options"] = _ask_user_options
                    if _ask_user_questions:
                        _ask_user_data["questions"] = _ask_user_questions
                    _msg_meta["ask_user"] = _ask_user_data
                session.add_message("assistant", assistant_text_to_save, **_msg_meta)
                if session_manager:
                    session_manager.mark_dirty()
            except Exception as e:
                logger.error(f"[Chat API] Failed to save assistant message to session: {e}", exc_info=True)

        # Ensure sub-agent records are flushed to disk
        if session and hasattr(session, "context") and session_manager:
            if getattr(session.context, "sub_agent_records", None):
                session_manager.mark_dirty()

        # Collect usage — prefer pre-computed summary (survives cleanup),
        # fall back to reading full trace (legacy path)
        _usage_data: dict | None = None
        try:
            _cached = getattr(actual_agent, "_last_usage_summary", None)
            if _cached:
                _usage_data = dict(_cached)
            else:
                re = getattr(actual_agent, "reasoning_engine", None)
                trace = getattr(actual_agent, "_last_finalized_trace", None) or \
                    (getattr(re, "_last_react_trace", []) if re else [])
                if trace:
                    total_in = sum(t.get("tokens", {}).get("input", 0) for t in trace)
                    total_out = sum(t.get("tokens", {}).get("output", 0) for t in trace)
                    _usage_data = {
                        "input_tokens": total_in,
                        "output_tokens": total_out,
                        "total_tokens": total_in + total_out,
                    }
                ctx_mgr = getattr(actual_agent, "context_manager", None) or getattr(re, "_context_manager", None)
                if ctx_mgr and hasattr(ctx_mgr, "get_max_context_tokens"):
                    _max_ctx = ctx_mgr.get_max_context_tokens()
                    _msgs = getattr(re, "_last_working_messages", None) or getattr(
                        getattr(actual_agent, "_context", None), "messages", []
                    )
                    _cur_ctx = ctx_mgr.estimate_messages_tokens(_msgs) if _msgs else 0
                    if _usage_data is None:
                        _usage_data = {}
                    _usage_data["context_tokens"] = _cur_ctx
                    _usage_data["context_limit"] = _max_ctx
        except Exception:
            pass

        yield _sse("done", {"usage": _usage_data})

    except Exception as e:
        logger.error(f"Chat stream error: {e}", exc_info=True)
        yield _sse("error", {"message": str(e)[:500]})
        yield _sse("done")
    finally:
        # ── 清理断连检测任务 ──
        if _disconnect_watcher_task and not _disconnect_watcher_task.done():
            _disconnect_watcher_task.cancel()
            try:
                await _disconnect_watcher_task
            except (asyncio.CancelledError, Exception):
                pass

        # ── Release busy lock & broadcast idle/message_update ──
        _conv_id = chat_request.conversation_id or ""
        _client_id = chat_request.client_id or ""
        if _client_id and _conv_id:
            await _clear_busy(_conv_id)
            try:
                from .websocket import broadcast_event

                await broadcast_event("chat:idle", {"conversation_id": _conv_id})
                if _full_reply:
                    await broadcast_event("chat:message_update", {
                        "conversation_id": _conv_id,
                        "last_message_preview": _full_reply[:100],
                        "timestamp": time.time(),
                    })
            except Exception:
                pass


@router.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    """
    Chat endpoint with SSE streaming.

    Uses the full Agent pipeline (shared with IM/CLI channels)
    via Agent.chat_with_session_stream().

    Each conversation gets its own Agent instance via AgentInstancePool
    to support concurrent streaming without shared-state corruption.

    Returns Server-Sent Events with the following event types:
    - thinking_start / thinking_delta / thinking_end
    - text_delta
    - tool_call_start / tool_call_end
    - plan_created / plan_step_updated
    - ask_user
    - agent_switch
    - agent_handoff
    - error
    - done
    """
    import uuid as _uuid
    conversation_id = body.conversation_id or f"api_{_uuid.uuid4().hex[:12]}"
    client_id = body.client_id or ""

    # ── Busy-lock check ──
    if client_id:
        conflict = await _mark_busy(conversation_id, client_id)
        if conflict is not None:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "conversation_busy",
                    "conversation_id": conversation_id,
                    "busy_client_id": conflict.client_id,
                    "busy_since": conflict.start_time,
                    "message": "该会话正在其他终端进行中，请新建会话或稍后再试",
                },
            )

    try:
        agent = await _get_agent_for_session(request, conversation_id, body.agent_profile_id)
        session_manager = getattr(request.app.state, "session_manager", None)
    except Exception:
        if client_id:
            await _clear_busy(conversation_id)
        raise

    # Resolve effective mode: backward compat plan_mode=true -> mode="plan"
    effective_mode = body.mode
    if body.plan_mode and effective_mode == "agent":
        effective_mode = "plan"

    msg_preview = (body.message or "")[:100]
    att_count = len(body.attachments) if body.attachments else 0
    logger.info(
        f"[Chat API] 收到消息: \"{msg_preview}\""
        + (f" (+{att_count}个附件)" if att_count else "")
        + (f" | endpoint={body.endpoint}" if body.endpoint else "")
        + (f" | mode={effective_mode}" if effective_mode != "agent" else "")
        + (f" | thinking={body.thinking_mode}" if body.thinking_mode else "")
        + (f" | depth={body.thinking_depth}" if body.thinking_depth else "")
        + (f" | conv={conversation_id}")
        + (f" | client={client_id}" if client_id else "")
    )

    # Pass pre-resolved conversation_id so _stream_chat doesn't generate a new one
    body.conversation_id = conversation_id

    # Broadcast busy event
    if client_id:
        await _broadcast_chat_event("chat:busy", {
            "conversation_id": conversation_id,
            "client_id": client_id,
        })

    sse_gen = _stream_chat(body, agent, session_manager, http_request=request)
    if is_dual_loop():
        sse_gen = engine_stream(sse_gen)

    return StreamingResponse(
        sse_gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/chat/busy")
async def chat_busy(
    conversation_id: str = Query("", description="Filter by conversation ID (empty = all)"),
):
    """Return currently busy conversations."""
    async with _busy_lock:
        _expire_stale_locks()
        if conversation_id:
            info = _busy_conversations.get(conversation_id)
            if info:
                return {
                    "busy": True,
                    "conversation_id": conversation_id,
                    "client_id": info.client_id,
                    "since": info.start_time,
                }
            return {"busy": False, "conversation_id": conversation_id}
        return {
            "busy_conversations": [
                {
                    "conversation_id": cid,
                    "client_id": info.client_id,
                    "since": info.start_time,
                }
                for cid, info in _busy_conversations.items()
            ],
        }


@router.post("/api/chat/answer")
async def chat_answer(request: Request, body: ChatAnswerRequest):
    """Handle user answer to an ask_user event."""
    return {
        "status": "ok",
        "conversation_id": body.conversation_id,
        "answer": body.answer,
        "hint": "Please send the answer as a new /api/chat message with the same conversation_id",
    }


@router.post("/api/chat/cancel")
async def chat_cancel(request: Request, body: ChatControlRequest):
    """Cancel the current running task for the specified conversation."""
    conv_id = body.conversation_id
    agent = _get_existing_agent(request, conv_id)
    actual_agent = _resolve_agent(agent) if agent else None
    if actual_agent is None:
        logger.warning("[Chat API] Cancel failed: Agent not initialized")
        return {"status": "error", "message": "Agent not initialized"}

    reason = body.reason or "用户从聊天界面取消任务"
    _conv_id = conv_id or getattr(actual_agent, "_current_conversation_id", None)
    logger.info(f"[Chat API] Cancel 接收到请求: reason={reason!r}, conv_id={_conv_id!r}")
    actual_agent.cancel_current_task(reason, session_id=_conv_id)
    logger.info(f"[Chat API] Cancel 执行完成: reason={reason!r}")
    return {"status": "ok", "action": "cancel", "reason": reason}


@router.post("/api/chat/skip")
async def chat_skip(request: Request, body: ChatControlRequest):
    """Skip the current running tool/step (does not terminate the task)."""
    conv_id = body.conversation_id
    agent = _get_existing_agent(request, conv_id)
    actual_agent = _resolve_agent(agent) if agent else None
    if actual_agent is None:
        return {"status": "error", "message": "Agent not initialized"}

    reason = body.reason or "用户从聊天界面跳过当前步骤"
    _conv_id = conv_id or getattr(actual_agent, "_current_conversation_id", None)
    actual_agent.skip_current_step(reason, session_id=_conv_id)
    logger.info(f"[Chat API] Skip requested: reason={reason!r}, conv_id={_conv_id!r}")
    return {"status": "ok", "action": "skip", "reason": reason}


@router.post("/api/chat/insert")
async def chat_insert(request: Request, body: ChatControlRequest):
    """Insert a user message into the running task context.

    Smart routing: if the message is a stop/skip command, automatically
    delegate to cancel/skip instead of blindly inserting.
    """
    conv_id = body.conversation_id
    agent = _get_existing_agent(request, conv_id)
    actual_agent = _resolve_agent(agent) if agent else None
    if actual_agent is None:
        logger.warning("[Chat API] Insert failed: Agent not initialized")
        return {"status": "error", "message": "Agent not initialized"}

    if not body.message:
        return {"status": "error", "message": "Message is required for insert"}

    logger.info(f"[Chat API] Insert 接收到消息: {body.message[:80]!r}")
    msg_type = actual_agent.classify_interrupt(body.message)
    logger.info(f"[Chat API] Insert 分类结果: msg_type={msg_type!r}, message={body.message[:60]!r}")

    if msg_type == "stop":
        reason = f"用户发送停止指令: {body.message}"
        _conv_id = conv_id or getattr(actual_agent, "_current_conversation_id", None)
        logger.info(f"[Chat API] Insert -> STOP: reason={reason!r}, conv_id={_conv_id!r}")
        actual_agent.cancel_current_task(reason, session_id=_conv_id)
        logger.info("[Chat API] Insert -> STOP 执行完成")
        return {"status": "ok", "action": "cancel", "reason": reason}

    if msg_type == "skip":
        reason = f"用户发送跳过指令: {body.message}"
        _skip_conv_id = conv_id or getattr(actual_agent, "_current_conversation_id", None)
        ok = actual_agent.skip_current_step(reason, session_id=_skip_conv_id)
        logger.info(f"[Chat API] Insert -> SKIP: reason={reason!r}, ok={ok}")
        if not ok:
            return {"status": "warning", "action": "skip", "reason": reason, "message": "No active task to skip"}
        return {"status": "ok", "action": "skip", "reason": reason}

    _insert_conv_id = conv_id or getattr(actual_agent, "_current_conversation_id", None)
    ok = await to_engine(actual_agent.insert_user_message(body.message, session_id=_insert_conv_id))
    logger.info(f"[Chat API] Insert 作为普通消息: ok={ok}, message={body.message[:60]!r}")
    if not ok:
        return {"status": "warning", "action": "insert", "message": "No active task, message dropped"}
    return {"status": "ok", "action": "insert", "message": body.message[:100]}


@router.get("/api/agents/sub-tasks")
async def get_sub_agent_tasks(request: Request, conversation_id: str = ""):
    """Return live sub-agent states for a given conversation (polling endpoint)."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        try:
            from openakita.main import _orchestrator
            orchestrator = _orchestrator
        except (ImportError, AttributeError):
            pass
    if orchestrator is None or not conversation_id:
        return []
    try:
        return orchestrator.get_sub_agent_states(conversation_id)
    except Exception as e:
        logger.warning(f"[Chat API] sub-tasks query error: {e}")
        return []


@router.get("/api/agents/sub-records")
async def get_sub_agent_records(request: Request, conversation_id: str = ""):
    """Return persisted sub-agent work records for a conversation."""
    if not conversation_id:
        return []
    session_manager = getattr(request.app.state, "session_manager", None)
    if session_manager is None:
        return []
    try:
        session = session_manager.get_session(
            "desktop", conversation_id, "desktop_user", create_if_missing=False,
        )
        if session and hasattr(session, "context"):
            return getattr(session.context, "sub_agent_records", [])
    except Exception as e:
        logger.warning(f"[Chat API] sub-records query error: {e}")
    return []
