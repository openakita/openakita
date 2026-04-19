"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Chat request body."""

    # 32 KB 上限：覆盖正常对话/Markdown 长文，又能挡住意外/恶意大 payload。
    # 超长走 attachments（文件上传），不走 message 文本通道。
    message: str = Field("", description="User message text", max_length=32_768)
    # 允许字母/数字/下划线/连字符/点/冒号/@（覆盖 UUID、IM 群 chatroom@xxx 等）
    conversation_id: str | None = Field(
        None,
        description="Conversation ID for context",
        max_length=128,
        pattern=r"^[A-Za-z0-9_\-:.@]{0,128}$",
    )
    mode: Literal["ask", "plan", "agent"] = Field(
        "agent",
        description="Interaction mode: ask (read-only), plan (plan then execute), agent (full execution)",
    )
    plan_mode: bool = Field(
        False, description="Deprecated: use mode='plan' instead. Kept for backward compatibility."
    )
    endpoint: str | None = Field(None, description="Specific endpoint name (null=auto)")
    attachments: list[AttachmentInfo] | None = Field(None, description="Attached files/images")
    thinking_mode: Literal["auto", "on", "off"] | None = Field(
        None,
        description="Thinking mode override: 'auto'(system decides), 'on'(force enable), 'off'(force disable). null=use system default.",
    )
    thinking_depth: Literal["low", "medium", "high"] | None = Field(
        None,
        description="Thinking depth: 'low', 'medium', 'high'. Only effective when thinking is enabled.",
    )
    agent_profile_id: str | None = Field(
        None,
        description="Agent profile to use for this message.",
    )
    client_id: str | None = Field(
        None,
        description="Unique client/tab identifier for multi-device busy-lock coordination.",
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

    skill_id: str | None = None
    capability_id: str | None = None
    namespace: str | None = None
    origin: str | None = None
    name: str
    description: str
    system: bool = False
    enabled: bool = True
    category: str | None = None
    config: list[dict[str, Any]] | None = None
