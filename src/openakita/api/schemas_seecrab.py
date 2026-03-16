# src/openakita/api/schemas_seecrab.py
"""Pydantic schemas for SeeCrab API."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .schemas import AttachmentInfo


class SeeCrabChatRequest(BaseModel):
    """SeeCrab chat request body."""

    message: str = Field("", description="User message text")
    conversation_id: str | None = Field(None, description="Conversation ID")
    agent_profile_id: str | None = Field(None, description="Agent profile")
    endpoint: str | None = Field(None, description="LLM endpoint override")
    thinking_mode: str | None = Field(None, description="Thinking mode")
    thinking_depth: str | None = Field(None, description="Thinking depth (low/medium/high)")
    plan_mode: bool = Field(False, description="Enable Plan mode")
    attachments: list[AttachmentInfo] | None = Field(None, description="Attachments")
    client_id: str | None = Field(None, description="Client tab ID for busy-lock")


class SeeCrabSessionUpdateRequest(BaseModel):
    """Update session metadata (title, etc.)."""

    title: str | None = Field(None, description="New session title")


class SeeCrabAnswerRequest(BaseModel):
    """Answer to an ask_user event."""

    conversation_id: str = Field(..., description="Conversation ID")
    answer: str = Field(..., description="User answer text")
    client_id: str | None = Field(None)
