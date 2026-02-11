"""
Chat route: POST /api/chat (SSE streaming)

流式返回 AI 对话响应，包含思考内容、文本、工具调用、Plan 等事件。
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


async def _stream_chat(
    request: ChatRequest,
    agent: object,
) -> AsyncIterator[str]:
    """Generate SSE events from agent processing."""

    def _sse(event_type: str, data: dict | None = None) -> str:
        payload = {"type": event_type, **(data or {})}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    try:
        # Resolve the actual Agent instance (supports both Agent and MasterAgent)
        from openakita.core.agent import Agent

        actual_agent: Agent | None = None
        if isinstance(agent, Agent):
            actual_agent = agent
        else:
            # MasterAgent: use its internal _local_agent
            local = getattr(agent, "_local_agent", None)
            if isinstance(local, Agent):
                actual_agent = local

        if actual_agent is None:
            yield _sse("error", {"message": "Agent not initialized"})
            yield _sse("done")
            return

        brain = actual_agent.brain
        if brain is None:
            yield _sse("error", {"message": "Agent brain not initialized"})
            yield _sse("done")
            return

        # Build messages（支持多模态附件）
        messages = []
        if request.message or request.attachments:
            # 如果有附件，构建多模态 content 块
            if request.attachments:
                content_blocks: list[dict] = []
                if request.message:
                    content_blocks.append({"type": "text", "text": request.message})
                for att in request.attachments:
                    if att.type == "image" and att.url:
                        # data:image/... 或 http(s):// URL
                        if att.url.startswith("data:"):
                            # base64 data URI → 内联图片
                            content_blocks.append({
                                "type": "image_url",
                                "image_url": {"url": att.url},
                            })
                        else:
                            content_blocks.append({
                                "type": "image_url",
                                "image_url": {"url": att.url},
                            })
                    elif att.url:
                        # 非图片文件（语音/文档等），作为文本引用
                        content_blocks.append({
                            "type": "text",
                            "text": f"[附件: {att.name or 'file'} ({att.mime_type or att.type})] URL: {att.url}",
                        })
                if content_blocks:
                    messages.append({"role": "user", "content": content_blocks})
            elif request.message:
                messages.append({"role": "user", "content": request.message})

        # Check if we should use a specific endpoint
        endpoint_override = request.endpoint

        # Plan mode: inject into system prompt or use tool
        plan_mode = request.plan_mode

        # Build system prompt & tools from the Agent (same as Agent._converse)
        system_prompt = actual_agent._context.system if hasattr(actual_agent, "_context") else ""
        agent_tools = getattr(actual_agent, "_tools", [])

        # Use the reasoning engine if available for full ReAct loop
        engine = getattr(actual_agent, "reasoning_engine", None)

        if engine is not None:
            # Use streaming reasoning engine with proper tools and system_prompt
            try:
                async for event in engine.reason_stream(
                    messages=messages,
                    tools=agent_tools,
                    system_prompt=system_prompt,
                    plan_mode=plan_mode,
                    endpoint_override=endpoint_override,
                    conversation_id=request.conversation_id,
                ):
                    yield _sse(event["type"], {k: v for k, v in event.items() if k != "type"})
            except Exception as e:
                logger.error(f"Reasoning engine error: {e}", exc_info=True)
                yield _sse("error", {"message": str(e)[:500]})
        else:
            # Fallback: direct LLM streaming (when reasoning_engine is not available)
            llm_client = getattr(brain, "_llm_client", None)
            if llm_client is None:
                yield _sse("error", {"message": "No LLM client available"})
                yield _sse("done")
                return

            # 如果指定了端点，通过 per-conversation override 切换（避免污染全局状态）
            if endpoint_override and request.conversation_id:
                try:
                    llm_client.switch_model(
                        endpoint_name=endpoint_override,
                        hours=0.05,
                        reason="chat fallback endpoint override",
                        conversation_id=request.conversation_id,
                    )
                except Exception:
                    pass

            yield _sse("thinking_start")
            yield _sse("thinking_end")

            try:
                from openakita.llm.types import Message

                llm_messages = [Message(role="user", content=request.message)]
                async for chunk in llm_client.chat_stream(
                    messages=llm_messages,
                ):
                    if isinstance(chunk, dict):
                        ctype = chunk.get("type", "")
                        if ctype == "content_block_delta" or ctype == "text":
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

        yield _sse("done", {"usage": None})

    except Exception as e:
        logger.error(f"Chat stream error: {e}", exc_info=True)
        yield _sse("error", {"message": str(e)[:500]})
        yield _sse("done")


@router.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    """
    Chat endpoint with SSE streaming.

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

    return StreamingResponse(
        _stream_chat(body, agent),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/chat/answer")
async def chat_answer(request: Request, body: ChatAnswerRequest):
    """Handle user answer to an ask_user event.

    Web Chat 模式下，ask_user 会中断 SSE 流。用户回复后前端调用此端点
    确认收到回复，然后通过正常的 /api/chat 接口发送回复消息（带同一个
    conversation_id），reasoning engine 会继续处理。
    """
    return {
        "status": "ok",
        "conversation_id": body.conversation_id,
        "answer": body.answer,
        "hint": "Please send the answer as a new /api/chat message with the same conversation_id",
    }
