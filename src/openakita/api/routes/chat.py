"""
Chat route: POST /api/chat (SSE streaming)

流式返回 AI 对话响应，包含思考内容、文本、工具调用、Plan 等事件。
使用完整的 Agent 流水线（persona, memory, traits, context compression）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..schemas import ChatAnswerRequest, ChatRequest

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


async def _build_full_system_prompt(actual_agent, message: str, session_type: str = "desktop") -> str:
    """Build the full system prompt using Agent's complete pipeline.

    This includes: persona, memory retrieval, traits, runtime context, catalogs, etc.
    Same as what IM/CLI use via _chat_with_tools_and_context().
    """
    task_description = message[:200] if message else ""
    try:
        system_prompt = await actual_agent._build_system_prompt_compiled(
            task_description=task_description,
            session_type=session_type,
        )
    except Exception as e:
        logger.warning(f"Failed to build compiled prompt, falling back to static: {e}")
        system_prompt = actual_agent._context.system if hasattr(actual_agent, "_context") else ""
    return system_prompt


async def _do_trait_mining(actual_agent, message: str):
    """Run trait mining on the user message (async, non-blocking)."""
    try:
        if hasattr(actual_agent, "trait_miner") and actual_agent.trait_miner and actual_agent.trait_miner.brain:
            mined_traits = await actual_agent.trait_miner.mine_from_message(message, role="user")
            if mined_traits:
                for trait in mined_traits:
                    from openakita.memory.types import Memory, MemoryPriority, MemoryType
                    mem = Memory(
                        type=MemoryType.PERSONA_TRAIT,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"{trait.dimension}={trait.preference}",
                        source=trait.source,
                        tags=[f"dimension:{trait.dimension}", f"preference:{trait.preference}"],
                        importance_score=trait.confidence,
                    )
                    actual_agent.memory_manager.add_memory(mem)
                logger.debug(f"[Chat API] Mined {len(mined_traits)} traits from user message")
    except Exception as e:
        logger.debug(f"[Chat API] Trait mining failed (non-critical): {e}")


async def _stream_chat(
    request: ChatRequest,
    agent: object,
    session_manager: object | None = None,
) -> AsyncIterator[str]:
    """Generate SSE events from agent processing with full Agent pipeline."""

    _reply_chars = 0
    _reply_preview = ""
    _done_sent = False

    def _sse(event_type: str, data: dict | None = None) -> str:
        nonlocal _reply_chars, _reply_preview, _done_sent
        if event_type == "done":
            if _done_sent:
                return ""  # already sent done, skip duplicate
            _done_sent = True
            preview = _reply_preview[:100].replace("\n", " ")
            logger.info(f"[Chat API] 回复完成: {_reply_chars}字 | \"{preview}{'...' if _reply_chars > 100 else ''}\"")
        payload = {"type": event_type, **(data or {})}
        if event_type == "text_delta" and data and "content" in data:
            chunk = data["content"]
            _reply_chars += len(chunk)
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
        conversation_id = request.conversation_id or ""
        session = None
        session_messages_history = []

        if session_manager and conversation_id:
            try:
                session = session_manager.get_session(
                    channel="desktop",
                    chat_id=conversation_id,
                    user_id="desktop_user",
                    create_if_missing=True,
                )
                if session:
                    # Get history before adding current message
                    session_messages_history = list(session.context.messages) if hasattr(session, "context") else []
                    # Add current user message to session
                    if request.message:
                        session.add_message("user", request.message)
                    session_manager.mark_dirty()
            except Exception as e:
                logger.warning(f"[Chat API] Session management error: {e}")

        # --- Build messages (with multimodal attachments) ---
        messages = []

        # Include history from session (excluding the current message we just added)
        for msg in session_messages_history:
            role = msg.get("role", "user") if isinstance(msg, dict) else getattr(msg, "role", "user")
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        # Context boundary marker (same as chat_with_session)
        if messages:
            messages.append({"role": "user", "content": "[上下文结束，以下是用户的最新消息]"})
            messages.append({"role": "assistant", "content": "好的，我已了解之前的对话上下文。请告诉我你现在的需求。"})

        # Current user message (with attachment support)
        if request.message or request.attachments:
            if request.attachments:
                content_blocks: list[dict] = []
                if request.message:
                    content_blocks.append({"type": "text", "text": request.message})
                for att in request.attachments:
                    if att.type == "image" and att.url:
                        content_blocks.append({"type": "image_url", "image_url": {"url": att.url}})
                    elif att.url:
                        content_blocks.append({
                            "type": "text",
                            "text": f"[附件: {att.name or 'file'} ({att.mime_type or att.type})] URL: {att.url}",
                        })
                if content_blocks:
                    messages.append({"role": "user", "content": content_blocks})
            elif request.message:
                messages.append({"role": "user", "content": request.message})

        # --- Trait mining (async, like IM/CLI) ---
        if request.message:
            await _do_trait_mining(actual_agent, request.message)

        # --- Record turn for memory consolidation ---
        if request.message and hasattr(actual_agent, "memory_manager"):
            actual_agent.memory_manager.record_turn("user", request.message)

        # --- Build full system prompt (persona + memory + traits + runtime) ---
        system_prompt = await _build_full_system_prompt(
            actual_agent, request.message or "", session_type="desktop"
        )

        # --- Endpoint override & plan mode ---
        endpoint_override = request.endpoint
        plan_mode = request.plan_mode

        # --- Context compression (like IM/CLI) ---
        try:
            messages = await actual_agent._compress_context(messages)
        except Exception as e:
            logger.debug(f"[Chat API] Context compression skipped: {e}")

        # --- Set conversation context on agent (for plan/memory alignment) ---
        if conversation_id:
            actual_agent._current_conversation_id = conversation_id

        # --- Stream via reasoning engine ---
        agent_tools = getattr(actual_agent, "_tools", [])
        engine = getattr(actual_agent, "reasoning_engine", None)

        if engine is not None:
            try:
                async for event in engine.reason_stream(
                    messages=messages,
                    tools=agent_tools,
                    system_prompt=system_prompt,
                    plan_mode=plan_mode,
                    endpoint_override=endpoint_override,
                    conversation_id=conversation_id,
                ):
                    yield _sse(event["type"], {k: v for k, v in event.items() if k != "type"})
                    # Inject artifact events for deliver_artifacts results
                    if event.get("type") == "tool_call_end" and event.get("tool") == "deliver_artifacts":
                        try:
                            result_data = json.loads(event.get("result", "{}"))
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
                        except (json.JSONDecodeError, TypeError, KeyError):
                            pass
            except Exception as e:
                logger.error(f"Reasoning engine error: {e}", exc_info=True)
                yield _sse("error", {"message": str(e)[:500]})
        else:
            # Fallback: direct LLM streaming
            llm_client = getattr(brain, "_llm_client", None)
            if llm_client is None:
                yield _sse("error", {"message": "No LLM client available"})
                yield _sse("done")
                return

            if endpoint_override and conversation_id:
                try:
                    llm_client.switch_model(
                        endpoint_name=endpoint_override,
                        hours=0.05,
                        reason="chat fallback endpoint override",
                        conversation_id=conversation_id,
                    )
                except Exception:
                    pass

            yield _sse("thinking_start")
            yield _sse("thinking_end")

            try:
                from openakita.llm.types import Message
                llm_messages = [Message(role="user", content=request.message)]
                # Include attachments in fallback path too
                if request.attachments:
                    att_text = "\n".join(
                        f"[附件: {a.name or 'file'}] {a.url}" for a in request.attachments if a.url
                    )
                    if att_text:
                        llm_messages = [Message(role="user", content=f"{request.message}\n{att_text}")]
                async for chunk in llm_client.chat_stream(messages=llm_messages):
                    if isinstance(chunk, dict):
                        ctype = chunk.get("type", "")
                        if ctype in ("content_block_delta", "text"):
                            text = chunk.get("text", "") or chunk.get("delta", {}).get("text", "")
                            if text:
                                yield _sse("text_delta", {"content": text})
                        elif ctype == "thinking":
                            yield _sse("thinking_delta", {"content": chunk.get("text", "")})
                    elif isinstance(chunk, str):
                        yield _sse("text_delta", {"content": chunk})
            except Exception as e:
                logger.error(f"LLM streaming error: {e}", exc_info=True)
                yield _sse("error", {"message": str(e)[:500]})

        # --- Record assistant response for memory ---
        if _reply_preview and hasattr(actual_agent, "memory_manager"):
            actual_agent.memory_manager.record_turn("assistant", _reply_preview)

        # --- Save assistant response to session ---
        if session and _reply_preview:
            try:
                session.add_message("assistant", _reply_preview)
                if session_manager:
                    session_manager.mark_dirty()
            except Exception:
                pass

        yield _sse("done", {"usage": None})

    except Exception as e:
        logger.error(f"Chat stream error: {e}", exc_info=True)
        yield _sse("error", {"message": str(e)[:500]})
        yield _sse("done")


@router.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    """
    Chat endpoint with SSE streaming.

    Uses the full Agent pipeline (persona, memory, traits, context compression)
    for feature parity with IM/CLI channels.

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
        + (f" | conv={body.conversation_id}" if body.conversation_id else "")
    )

    return StreamingResponse(
        _stream_chat(body, agent, session_manager),
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
