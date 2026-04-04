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

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from openakita.core.engine_bridge import engine_stream, is_dual_loop, to_engine

from ..schemas import ChatAnswerRequest, ChatControlRequest, ChatRequest
from .conversation_lifecycle import get_lifecycle_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/commands")
async def list_commands():
    """Return available slash commands for the Desktop UI."""
    from ...commands.registry import CommandScope, get_commands

    return [
        {
            "name": c.name,
            "label": c.label,
            "description": c.description,
            "argsHint": c.args_hint,
        }
        for c in get_commands()
        if CommandScope.DESKTOP in c.scope
    ]


@router.post("/api/chat/clear")
async def clear_chat(request: Request):
    """Clear session context for a conversation."""
    body = await request.json()
    conversation_id = body.get("conversation_id", "")
    if not conversation_id:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "missing conversation_id"},
        )

    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "session manager not available"},
        )

    cleared = session_manager.clear_history(
        channel="desktop",
        chat_id=conversation_id,
        user_id="desktop_user",
    )
    if cleared:
        _cleanup_chat_runtime_state(request, conversation_id)
        return {"ok": True}

    # Fallback: search by Session.id (handles wrapped IDs from API clients)
    session = session_manager.get_session_by_id(conversation_id)
    if session:
        session.context.clear_messages()
        session_manager.mark_dirty()
        _cleanup_chat_runtime_state(request, conversation_id)
        return {"ok": True}

    return JSONResponse(
        status_code=404,
        content={"ok": False, "error": "session not found"},
    )


def _cleanup_chat_runtime_state(request: Request, conversation_id: str) -> None:
    """Clear runtime state that should not survive /api/chat/clear."""
    try:
        from ...core.policy import get_policy_engine

        get_policy_engine().cleanup_session(conversation_id)
    except Exception:
        pass

    try:
        from ...tools.handlers.plan import clear_session_todo_state

        clear_session_todo_state(conversation_id)
    except Exception:
        pass

    # Clear pending tool confirmations in the ToolExecutor
    try:
        brain = getattr(request.app.state, "brain", None)
        if brain and hasattr(brain, "_tool_executor"):
            brain._tool_executor.clear_confirm_cache()
    except Exception:
        pass

    try:
        orchestrators = []

        app_orchestrator = getattr(request.app.state, "orchestrator", None)
        if app_orchestrator is not None:
            orchestrators.append(app_orchestrator)

        try:
            from openakita.main import _orchestrator as global_orchestrator

            if global_orchestrator is not None and global_orchestrator not in orchestrators:
                orchestrators.append(global_orchestrator)
        except Exception:
            pass

        for orchestrator in orchestrators:
            if hasattr(orchestrator, "purge_session_states"):
                orchestrator.purge_session_states(conversation_id)
    except Exception:
        pass


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
    from openakita.agents.profile import AgentProfile, get_profile_store

    pid = agent_profile_id or "default"

    for p in SYSTEM_PRESETS:
        if p.id == pid:
            return p

    try:
        store = get_profile_store()
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
        from openakita.agents.profile import get_profile_store

        known_ids = {p.id for p in SYSTEM_PRESETS}
        if new_profile_id not in known_ids:
            store = get_profile_store()
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


def _schedule_background_save(
    agent_task: asyncio.Task,
    agent_done: asyncio.Event,
    agent_queue: asyncio.Queue,
    sse_fn,
    session,
    session_manager,
    conversation_id: str,
    full_reply_snapshot: str,
    collected_artifacts: list,
    save_done: bool,
) -> None:
    """Register a background callback so that when a long-running agent task
    finally completes after the SSE stream has closed, the result is still
    saved to the session.  The user will see it when they refresh the page."""

    async def _bg_drain_and_save():
        try:
            await agent_done.wait()
        except Exception:
            return

        bg_reply = full_reply_snapshot
        bg_artifacts = list(collected_artifacts)
        try:
            while not agent_queue.empty():
                ev = agent_queue.get_nowait()
                if ev is None or ev.get("type") == "__agent_error__":
                    break
                et = ev.get("type", "")
                if et == "text_delta" and "content" in ev:
                    bg_reply += ev["content"]
        except Exception:
            pass

        if session and bg_reply and not save_done:
            try:
                meta: dict = {}
                if bg_artifacts:
                    meta["artifacts"] = bg_artifacts
                session.add_message("assistant", bg_reply, **meta)
                if session_manager:
                    session_manager.mark_dirty()
                logger.info(
                    "[Chat API] Background save: %d chars (conv=%s)",
                    len(bg_reply), conversation_id,
                )
            except Exception as e:
                logger.warning("[Chat API] Background save failed: %s", e)

        if conversation_id:
            try:
                await get_lifecycle_manager().finish(conversation_id)
            except Exception:
                pass

    asyncio.create_task(_bg_drain_and_save())
    logger.info(
        "[Chat API] Scheduled background save for long-running task (conv=%s)",
        conversation_id,
    )


async def _stream_chat(
    chat_request: ChatRequest,
    agent: object,
    session_manager: object | None = None,
    http_request: Request | None = None,
    busy_generation: int = 0,
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
    _chain_reply = ""  # chain_text 累积（仅在无 text_delta 时 fallback 使用）
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
        nonlocal _reply_chars, _reply_preview, _full_reply, _chain_reply, _done_sent
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
        from ...events import normalize_stream_event

        payload = normalize_stream_event({"type": event_type, **(data or {})})
        if event_type == "text_delta" and data and "content" in data:
            chunk = data["content"]
            _reply_chars += len(chunk)
            _full_reply += chunk
            if len(_reply_preview) < 120:
                _reply_preview += chunk
        elif event_type == "text_replace" and data and "content" in data:
            _full_reply = data["content"]
            _reply_chars = len(_full_reply)
            _reply_preview = _full_reply[:120]
        elif event_type == "chain_text" and data and "content" in data:
            chunk = data["content"]
            _reply_chars += len(chunk)
            _chain_reply += chunk
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    _disconnect_watcher_task: asyncio.Task | None = None
    _agent_task: asyncio.Task | None = None
    _agent_done = asyncio.Event()
    _agent_queue: asyncio.Queue = asyncio.Queue()
    _save_done = False

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

        # ── Background agent task: decoupled from SSE lifecycle ──
        async def _agent_runner():
            try:
                async for ev in actual_agent.chat_with_session_stream(
                    message=chat_request.message or "",
                    session_messages=session_messages_history,
                    session_id=conversation_id,
                    session=session,
                    gateway=None,
                    plan_mode=chat_request.plan_mode,
                    mode=chat_request.mode,
                    endpoint_override=chat_request.endpoint,
                    attachments=chat_request.attachments,
                    thinking_mode=chat_request.thinking_mode,
                    thinking_depth=chat_request.thinking_depth,
                ):
                    await _agent_queue.put(ev)
            except Exception as exc:
                await _agent_queue.put({"type": "__agent_error__", "__exc_msg__": str(exc)[:500]})
            finally:
                await _agent_queue.put(None)
                _agent_done.set()

        _agent_task = asyncio.create_task(_agent_runner())

        # --- 后台断连检测：宽限期机制 ---
        # 长任务（如 multi-agent 委派）可能运行 10-20 分钟。客户端断连后不立即
        # 取消任务，而是给予较长的宽限期（DISCONNECT_GRACE_SECONDS）。任务完成后
        # 通过 _schedule_background_save 保存结果到 session，用户刷新即可看到。
        DISCONNECT_GRACE_SECONDS = 900  # 15 分钟

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
                                "[Chat API] 客户端断开，进入宽限期（%ds）",
                                DISCONNECT_GRACE_SECONDS,
                            )
                            try:
                                await asyncio.wait_for(
                                    _agent_done.wait(),
                                    timeout=DISCONNECT_GRACE_SECONDS,
                                )
                                logger.info("[Chat API] Agent task 在宽限期内完成")
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "[Chat API] 宽限期超时（%ds），取消任务",
                                    DISCONNECT_GRACE_SECONDS,
                                )
                                try:
                                    actual_agent.cancel_current_task(
                                        "客户端断开连接（宽限期后）",
                                        session_id=conversation_id,
                                    )
                                except Exception as e:
                                    logger.warning(f"[Chat API] 断连 cancel 失败: {e}")
                            break
                    except Exception:
                        break

        _disconnect_watcher_task = asyncio.create_task(_disconnect_watcher())

        # --- 主 SSE 事件循环：从 queue 读取事件并转发 ---
        # 每 SSE_KEEPALIVE_INTERVAL 秒无真实事件时发送 keepalive，
        # 防止前端 fetch 连接因长时间无数据而超时断开（LLM 重试等场景）。
        SSE_KEEPALIVE_INTERVAL = 15.0
        _agent_errored = False
        while True:
            try:
                event = await asyncio.wait_for(
                    _agent_queue.get(), timeout=SSE_KEEPALIVE_INTERVAL
                )
            except TimeoutError:
                if not _client_disconnected and not await _check_disconnected():
                    yield _sse("heartbeat", {"ts": time.time()})
                continue
            if event is None:
                break

            event_type = event.get("type", "")

            if event_type == "__agent_error__":
                _agent_errored = True
                if not _client_disconnected:
                    _err_msg = event.get("__exc_msg__") or "Unknown error"
                    yield _sse("error", {"message": _err_msg})
                    yield _sse("done")
                break

            # 拦截 done 事件：不在此处转发，等 usage 收集完毕后统一发送
            if event_type == "done":
                continue

            # 捕获 ask_user 问题文本和选项（用于 session 保存）
            if event_type == "ask_user":
                _ask_user_question = event.get("question", "")
                _ask_user_options = event.get("options", [])
                _ask_user_questions = event.get("questions", [])

            # Always call _sse to accumulate _full_reply regardless of connection
            event_data = {k: v for k, v in event.items() if k != "type"}
            sse_line = _sse(event_type, event_data)

            # Client disconnected — text is accumulated by _sse above, skip SSE output
            _is_connected = not _client_disconnected
            if _is_connected and not await _check_disconnected():
                yield sse_line
            else:
                continue

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
        _save_done = True
        # ask_user 场景：_ask_user_question 已包含 LLM 文本 + 问题（由 reason_stream 拼接），
        # 优先使用它作为保存文本，确保下一轮 LLM 能看到完整的确认问题上下文。
        if _ask_user_question or _ask_user_questions:
            parts = []
            if _ask_user_question:
                parts.append(_ask_user_question)
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
            ask_text = "\n".join(parts)
            assistant_text_to_save = ask_text if ask_text.strip() else (_full_reply or _chain_reply)
        else:
            assistant_text_to_save = _full_reply or _chain_reply

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

        if not assistant_text_to_save:
            _task = (
                actual_agent.agent_state.current_task
                if hasattr(actual_agent, "agent_state") and actual_agent.agent_state
                else None
            )
            if _task and _task.cancelled:
                assistant_text_to_save = "[任务已取消]"

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

        if not _client_disconnected and not _agent_errored:
            yield _sse("done", {"usage": _usage_data})

    except Exception as e:
        logger.error(f"Chat stream error: {e}", exc_info=True)
        if not _client_disconnected:
            err_msg = str(e)[:500] or f"{type(e).__name__}: unknown error"
            yield _sse("error", {"message": err_msg})
            yield _sse("done")
    finally:
        # ── Wait for agent task to finish (deferred save if SSE gen was interrupted) ──
        _bg_save_scheduled = False
        if _agent_task is not None and not _agent_done.is_set():
            try:
                await asyncio.wait_for(_agent_done.wait(), timeout=65.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                if _agent_task and not _agent_task.done():
                    # 长任务仍在运行 — 不立即取消，注册后台保存回调。
                    # 任务完成时回调会 drain queue 并保存 session。
                    _bg_save_scheduled = True
                    _schedule_background_save(
                        _agent_task, _agent_done, _agent_queue, _sse,
                        session, session_manager, conversation_id,
                        _full_reply, _collected_artifacts, _save_done,
                    )

        # Drain remaining queue events to accumulate _full_reply for deferred save
        if not _save_done and not _bg_save_scheduled:
            try:
                while not _agent_queue.empty():
                    ev = _agent_queue.get_nowait()
                    if ev is None or ev.get("type") == "__agent_error__":
                        break
                    et = ev.get("type", "")
                    if et != "done":
                        _sse(et, {k: v for k, v in ev.items() if k != "type"})
            except Exception:
                pass
            # Deferred session save
            _deferred_text = _full_reply or _chain_reply
            if session and _deferred_text:
                try:
                    _deferred_meta: dict = {}
                    if _collected_artifacts:
                        _deferred_meta["artifacts"] = _collected_artifacts
                    session.add_message("assistant", _deferred_text, **_deferred_meta)
                    if session_manager:
                        session_manager.mark_dirty()
                    logger.info(
                        f"[Chat API] Deferred save: {len(_deferred_text)} chars "
                        f"(client_disconnected={_client_disconnected})"
                    )
                except Exception as e:
                    logger.warning(f"[Chat API] Deferred save failed: {e}")

        # ── 清理断连检测任务 ──
        if _disconnect_watcher_task and not _disconnect_watcher_task.done():
            _disconnect_watcher_task.cancel()
            try:
                await _disconnect_watcher_task
            except (asyncio.CancelledError, Exception):
                pass

        # ── 清理 agent task ──
        if _agent_task and not _agent_task.done() and not _bg_save_scheduled:
            _agent_task.cancel()
            try:
                await _agent_task
            except (asyncio.CancelledError, Exception):
                pass

        # ── Release busy lock (via lifecycle manager) & broadcast message update ──
        _conv_id = chat_request.conversation_id or ""
        if _conv_id:
            await get_lifecycle_manager().finish(_conv_id, generation=busy_generation)
            if _full_reply:
                await _broadcast_chat_event("chat:message_update", {
                    "conversation_id": _conv_id,
                    "client_id": getattr(chat_request, "client_id", "") or "",
                    "last_message_preview": _full_reply[:100],
                    "timestamp": time.time(),
                })


@router.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    """
    Chat endpoint with SSE streaming.

    Uses the full Agent pipeline (shared with IM/CLI channels)
    via Agent.chat_with_session_stream().

    Each conversation gets its own Agent instance via AgentInstancePool
    to support concurrent streaming without shared-state corruption.

    Returns Server-Sent Events with the following event types
    (canonical definitions in openakita.events.StreamEventType):
    - heartbeat / iteration_start
    - thinking_start / thinking_delta / thinking_end / chain_text
    - text_delta
    - tool_call_start / tool_call_end
    - context_compressed
    - security_confirm / ask_user
    - todo_created / todo_step_updated / todo_completed / todo_cancelled
    - agent_handoff / user_insert
    - artifact / ui_preference
    - error
    - done (with optional usage payload)
    """
    import uuid as _uuid

    pool = getattr(request.app.state, "agent_pool", None)
    if not body.conversation_id:
        if pool is not None:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "missing_conversation_id",
                    "message": "conversation_id is required in pool mode to avoid agent instance leaks",
                },
            )
        body.conversation_id = f"api_{_uuid.uuid4().hex[:12]}"

    conversation_id = body.conversation_id
    client_id = body.client_id or ""

    # ── Busy-lock check (via lifecycle manager) ──
    lifecycle = get_lifecycle_manager()
    busy_gen = 0
    if client_id:
        conflict, busy_gen = await lifecycle.start(conversation_id, client_id)
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
            await lifecycle.finish(conversation_id, generation=busy_gen)
        raise

    # Resolve effective mode: backward compat plan_mode=true -> mode="plan"
    effective_mode = body.mode
    if body.plan_mode and effective_mode == "agent":
        effective_mode = "plan"

    msg_preview = (body.message or "")[:100]
    att_count = len(body.attachments) if body.attachments else 0

    # Detect likely client-side encoding corruption: if the message is mostly
    # '?' characters mixed with sparse ASCII, the client probably encoded
    # Chinese/CJK text as ASCII with errors="replace" before sending.
    _msg = body.message or ""
    if len(_msg) > 2:
        _q = _msg.count("?")
        _non_ascii = sum(1 for c in _msg if ord(c) > 127)
        if _q > len(_msg) * 0.4 and _non_ascii == 0:
            logger.warning(
                "[Chat API] 疑似编码损坏: 消息含 %d/%d 个问号且无非ASCII字符, "
                "客户端可能在发送前将中文编码为ASCII(errors=replace)。"
                "请确认客户端使用 UTF-8 编码 JSON body | conv=%s",
                _q, len(_msg), conversation_id,
            )

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

    sse_gen = _stream_chat(body, agent, session_manager, http_request=request, busy_generation=busy_gen)
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
    return await get_lifecycle_manager().get_busy_status(conversation_id)


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

    # Immediately release busy-lock so the UI reflects the cancellation.
    # _stream_chat's finally block will also call finish() with a generation
    # guard, which will be a safe no-op since the lock is already released.
    if _conv_id:
        await get_lifecycle_manager().finish(_conv_id)

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
        if _conv_id:
            await get_lifecycle_manager().finish(_conv_id)
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
    orchestrator = None
    try:
        from openakita.main import _orchestrator
        orchestrator = _orchestrator
    except (ImportError, AttributeError):
        pass
    if orchestrator is None:
        orchestrator = getattr(request.app.state, "orchestrator", None)
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


@router.post("/api/plan/dismiss")
async def dismiss_plan_approval(request: Request):
    """用户关闭审批面板时清除后端 pending 状态"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON body"}
    conversation_id = body.get("conversation_id", "")
    if not conversation_id:
        return {"ok": False, "error": "missing conversation_id"}

    agent = _get_existing_agent(request, conversation_id)
    if agent is None:
        return {"ok": True}

    pending_map = getattr(agent, "_plan_exit_pending", None)
    if isinstance(pending_map, dict):
        pending_map.pop(conversation_id, None)
    return {"ok": True}
