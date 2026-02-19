"""
Chat route: POST /api/chat (SSE streaming)

流式返回 AI 对话响应，包含思考内容、文本、工具调用、Plan 等事件。
使用完整的 Agent 流水线（与 IM/CLI 共享 _prepare_session_context / _finalize_session）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..schemas import ChatAnswerRequest, ChatControlRequest, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_agent(agent: object):
    """Resolve the actual Agent instance (supports both Agent and MasterAgent)."""
    from openakita.core.agent import Agent

    if isinstance(agent, Agent):
        return agent
    local = getattr(agent, "_local_agent", None)
    if isinstance(local, Agent):
        return local
    return None


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
        conversation_id = chat_request.conversation_id or ""
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
                    # 先添加用户消息，再获取完整历史（含当前消息）
                    # 这与 IM 路径一致：gateway 先 add_message，再传 session_messages
                    if chat_request.message:
                        session.add_message("user", chat_request.message)
                    session_messages_history = list(session.context.messages) if hasattr(session, "context") else []
                    session_manager.mark_dirty()
            except Exception as e:
                logger.warning(f"[Chat API] Session management error: {e}")

        # --- 委托给 Agent 统一流水线 ---
        async for event in actual_agent.chat_with_session_stream(
            message=chat_request.message or "",
            session_messages=session_messages_history,
            session_id=conversation_id,
            session=session,
            gateway=None,  # Desktop Chat 没有 IM gateway
            plan_mode=chat_request.plan_mode,
            endpoint_override=chat_request.endpoint,
            attachments=chat_request.attachments,
            thinking_mode=chat_request.thinking_mode,
            thinking_depth=chat_request.thinking_depth,
        ):
            # Check if client disconnected
            if await _check_disconnected():
                break

            event_type = event.get("type", "")

            # 捕获 ask_user 问题文本和选项（用于 session 保存）
            if event_type == "ask_user":
                _ask_user_question = event.get("question", "")
                _ask_user_options = event.get("options", [])
                _ask_user_questions = event.get("questions", [])

            # Inject artifact events for deliver_artifacts results
            yield _sse(event_type, {k: v for k, v in event.items() if k != "type"})

            if event_type == "tool_call_end" and event.get("tool") == "deliver_artifacts":
                try:
                    result_str = event.get("result", "{}")
                    # tool_executor may append "\n\n[执行日志]:\n..." to the result,
                    # which breaks JSON parsing — strip it before decoding.
                    _log_marker = "\n\n[执行日志]"
                    if _log_marker in result_str:
                        result_str = result_str[: result_str.index(_log_marker)]
                    result_data = json.loads(result_str)
                    for receipt in result_data.get("receipts", []):
                        if receipt.get("status") == "delivered" and receipt.get("file_url"):
                            yield _sse("artifact", {
                                "artifact_type": receipt.get("type", "file"),
                                "file_url": receipt["file_url"],
                                "path": receipt.get("path", ""),
                                "name": receipt.get("name", ""),
                                "caption": receipt.get("caption", ""),
                                "size": receipt.get("size"),
                            })
                except (json.JSONDecodeError, TypeError, KeyError) as _exc:
                    logger.warning(
                        f"[Chat API] Failed to parse deliver_artifacts result: {_exc} "
                        f"| result_preview={event.get('result', '')[:200]}"
                    )

        # --- Save assistant response to session ---
        assistant_text_to_save = _full_reply
        if not assistant_text_to_save and _ask_user_question:
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
        if session and assistant_text_to_save:
            try:
                session.add_message("assistant", assistant_text_to_save)
                if session_manager:
                    session_manager.mark_dirty()
            except Exception:
                pass

        # Collect usage from the last react trace
        _usage_data: dict | None = None
        try:
            re = getattr(actual_agent, "reasoning_engine", None)
            trace = getattr(re, "_last_react_trace", []) if re else []
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


@router.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    """
    Chat endpoint with SSE streaming.

    Uses the full Agent pipeline (shared with IM/CLI channels)
    via Agent.chat_with_session_stream().

    Returns Server-Sent Events with the following event types:
    - thinking_start / thinking_delta / thinking_end
    - text_delta
    - tool_call_start / tool_call_end
    - plan_created / plan_step_updated
    - ask_user
    - agent_switch
    - error
    - done
    """
    agent = getattr(request.app.state, "agent", None)
    session_manager = getattr(request.app.state, "session_manager", None)

    msg_preview = (body.message or "")[:100]
    att_count = len(body.attachments) if body.attachments else 0
    logger.info(
        f"[Chat API] 收到消息: \"{msg_preview}\""
        + (f" (+{att_count}个附件)" if att_count else "")
        + (f" | endpoint={body.endpoint}" if body.endpoint else "")
        + (" | plan_mode" if body.plan_mode else "")
        + (f" | thinking={body.thinking_mode}" if body.thinking_mode else "")
        + (f" | depth={body.thinking_depth}" if body.thinking_depth else "")
        + (f" | conv={body.conversation_id}" if body.conversation_id else "")
    )

    return StreamingResponse(
        _stream_chat(body, agent, session_manager, http_request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
    """Cancel the current running task globally."""
    agent = getattr(request.app.state, "agent", None)
    actual_agent = _resolve_agent(agent) if agent else None
    if actual_agent is None:
        logger.warning("[Chat API] Cancel failed: Agent not initialized")
        return {"status": "error", "message": "Agent not initialized"}

    reason = body.reason or "用户从聊天界面取消任务"
    _conv_id = body.conversation_id or getattr(actual_agent, "_current_conversation_id", None)
    logger.info(f"[Chat API] Cancel 接收到请求: reason={reason!r}, conv_id={_conv_id!r}")
    actual_agent.cancel_current_task(reason, session_id=_conv_id)
    logger.info(f"[Chat API] Cancel 执行完成: reason={reason!r}")
    return {"status": "ok", "action": "cancel", "reason": reason}


@router.post("/api/chat/skip")
async def chat_skip(request: Request, body: ChatControlRequest):
    """Skip the current running tool/step (does not terminate the task)."""
    agent = getattr(request.app.state, "agent", None)
    actual_agent = _resolve_agent(agent) if agent else None
    if actual_agent is None:
        return {"status": "error", "message": "Agent not initialized"}

    reason = body.reason or "用户从聊天界面跳过当前步骤"
    _conv_id = body.conversation_id or getattr(actual_agent, "_current_conversation_id", None)
    actual_agent.skip_current_step(reason, session_id=_conv_id)
    logger.info(f"[Chat API] Skip requested: reason={reason!r}, conv_id={_conv_id!r}")
    return {"status": "ok", "action": "skip", "reason": reason}


@router.post("/api/chat/insert")
async def chat_insert(request: Request, body: ChatControlRequest):
    """Insert a user message into the running task context.

    Smart routing: if the message is a stop/skip command, automatically
    delegate to cancel/skip instead of blindly inserting.
    """
    agent = getattr(request.app.state, "agent", None)
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
        _conv_id = body.conversation_id or getattr(actual_agent, "_current_conversation_id", None)
        logger.info(f"[Chat API] Insert -> STOP: reason={reason!r}, conv_id={_conv_id!r}")
        actual_agent.cancel_current_task(reason, session_id=_conv_id)
        logger.info(f"[Chat API] Insert -> STOP 执行完成")
        return {"status": "ok", "action": "cancel", "reason": reason}

    if msg_type == "skip":
        reason = f"用户发送跳过指令: {body.message}"
        _skip_conv_id = body.conversation_id or getattr(actual_agent, "_current_conversation_id", None)
        ok = actual_agent.skip_current_step(reason, session_id=_skip_conv_id)
        logger.info(f"[Chat API] Insert -> SKIP: reason={reason!r}, ok={ok}")
        if not ok:
            return {"status": "warning", "action": "skip", "reason": reason, "message": "No active task to skip"}
        return {"status": "ok", "action": "skip", "reason": reason}

    _insert_conv_id = body.conversation_id or getattr(actual_agent, "_current_conversation_id", None)
    ok = await actual_agent.insert_user_message(body.message, session_id=_insert_conv_id)
    logger.info(f"[Chat API] Insert 作为普通消息: ok={ok}, message={body.message[:60]!r}")
    if not ok:
        return {"status": "warning", "action": "insert", "message": "No active task, message dropped"}
    return {"status": "ok", "action": "insert", "message": body.message[:100]}
