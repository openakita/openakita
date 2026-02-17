"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Chat request body."""

    message: str = Field("", description="User message text")
    conversation_id: str | None = Field(None, description="Conversation ID for context")
    plan_mode: bool = Field(False, description="Force Plan mode")
    endpoint: str | None = Field(None, description="Specific endpoint name (null=auto)")
    attachments: list[AttachmentInfo] | None = Field(None, description="Attached files/images")
    thinking_mode: str | None = Field(
        None,
        description="Thinking mode override: 'auto'(system decides), 'on'(force enable), 'off'(force disable). null=use system default.",
    )
    thinking_depth: str | None = Field(
        None,
        description="Thinking depth: 'low', 'medium', 'high'. Only effective when thinking is enabled.",
    )


class AttachmentInfo(BaseModel):
    """Attachment metadata."""

    type: str = Field(..., description="image | file | voice")
    name: str = Field(..., description="Filename")
    url: str | None = Field(None, description="URL or data URI")
    mime_type: str | None = Field(None, description="MIME type")


# Fix forward reference
ChatRequest.model_rebuild()


class ChatAnswerRequest(BaseModel):
    """Answer to an ask_user event."""

    conversation_id: str | None = None
    answer: str = ""


class ChatControlRequest(BaseModel):
    """Request body for chat control operations (cancel/skip/insert)."""

    conversation_id: str | None = Field(None, description="Conversation ID")
    reason: str = Field("", description="Reason for the control action")
    message: str = Field("", description="User message (only for insert)")


class HealthCheckRequest(BaseModel):
    """Health check request."""

    endpoint_name: str | None = None
    channel: str | None = None


class HealthResult(BaseModel):
    """Single endpoint health result."""

    name: str
    status: str  # healthy | degraded | unhealthy | unknown
    latency_ms: float | None = None
    error: str | None = None
    error_category: str | None = None
    consecutive_failures: int = 0
    cooldown_remaining: float = 0
    is_extended_cooldown: bool = False
    last_checked_at: str | None = None


class ModelInfo(BaseModel):
    """Available model/endpoint info."""

    name: str
    provider: str
    model: str
    status: str = "unknown"
    has_api_key: bool = False


class SkillInfoResponse(BaseModel):
    """Skill information for the API."""

    name: str
    description: str
    system: bool = False
    enabled: bool = True
    category: str | None = None
    config: list[dict[str, Any]] | None = None
