"""
Test data factories for creating pre-configured test objects.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from openakita.channels.types import (
    MediaFile,
    MessageContent,
    MessageType,
    UnifiedMessage,
)
from openakita.sessions.session import Session, SessionConfig, SessionContext, SessionState


def create_test_session(
    chat_id: str = "test-chat-1",
    channel: str = "cli",
    user_id: str = "test-user-1",
    bot_instance_id: str = "",
    messages: list[dict] | None = None,
    state: SessionState = SessionState.ACTIVE,
) -> Session:
    """Create a Session pre-loaded with optional messages."""
    ctx = SessionContext()
    if messages:
        ctx.messages = list(messages)
    return Session(
        id=f"session-{uuid.uuid4().hex[:8]}",
        channel=channel,
        chat_id=chat_id,
        user_id=user_id,
        bot_instance_id=bot_instance_id,
        state=state,
        context=ctx,
        config=SessionConfig(),
    )


def create_channel_message(
    channel: str = "telegram",
    chat_id: str = "chat-123",
    user_id: str = "user-456",
    bot_instance_id: str = "",
    text: str = "Hello",
    message_type: MessageType = MessageType.TEXT,
    images: list[MediaFile] | None = None,
    voices: list[MediaFile] | None = None,
) -> UnifiedMessage:
    """Create a UnifiedMessage for gateway/adapter testing."""
    content = MessageContent(
        text=text,
        images=images or [],
        voices=voices or [],
    )
    return UnifiedMessage(
        id=f"msg-{uuid.uuid4().hex[:8]}",
        channel=channel,
        channel_message_id=f"ch-{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        channel_user_id=user_id,
        chat_id=chat_id,
        bot_instance_id=bot_instance_id,
        message_type=message_type,
        content=content,
        timestamp=datetime.now(),
    )


def create_mock_agent(
    mock_brain: Any = None,
    mock_llm_client: Any = None,
) -> MagicMock:
    """Create a lightweight mock Agent for integration tests."""
    agent = MagicMock()
    agent.brain = mock_brain or MagicMock()
    agent.llm_client = mock_llm_client or MagicMock()
    agent.settings = MagicMock()
    agent.settings.max_iterations = 100
    agent.settings.project_root = "."
    agent.memory_manager = MagicMock()
    agent.state = MagicMock()
    agent.state.has_active_task = False
    agent.state.is_task_cancelled = False
    agent.initialized = True
    agent.chat_with_session = AsyncMock(return_value="Mock response")
    agent.chat_with_session_stream = AsyncMock()
    return agent


def create_mock_gateway() -> MagicMock:
    """Create a mock MessageGateway for testing."""
    gw = MagicMock()
    gw.send = AsyncMock(return_value="sent-msg-id")
    gw.send_to_session = AsyncMock(return_value="sent-msg-id")
    gw.check_interrupt = AsyncMock(return_value=None)
    gw.has_pending_interrupt = MagicMock(return_value=False)
    gw.get_adapter = MagicMock(return_value=None)
    return gw


def create_tool_definition(
    name: str = "test_tool",
    description: str = "A test tool",
    category: str = "test",
    input_schema: dict | None = None,
) -> dict:
    """Create a tool definition dict for catalog/executor testing."""
    return {
        "name": name,
        "category": category,
        "description": description,
        "detail": f"Detailed description for {name}",
        "input_schema": input_schema or {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Input query"}},
            "required": ["query"],
        },
    }


def create_endpoint_config(
    name: str = "test-endpoint",
    provider: str = "openai",
    model: str = "gpt-4",
    priority: int = 1,
    capabilities: list[str] | None = None,
) -> dict:
    """Create an LLM endpoint config dict for LLMClient testing."""
    return {
        "name": name,
        "provider": provider,
        "api_type": "openai",
        "base_url": "https://api.test.com/v1",
        "api_key": "sk-test-key",
        "model": model,
        "priority": priority,
        "max_tokens": 4096,
        "context_window": 128000,
        "timeout": 60,
        "capabilities": capabilities or ["tools"],
    }


def build_conversation(turns: list[tuple[str, str]]) -> list[dict]:
    """
    Build a conversation message list from (role, content) tuples.
    Example: build_conversation([("user", "Hi"), ("assistant", "Hello!")])
    """
    return [{"role": role, "content": content} for role, content in turns]

