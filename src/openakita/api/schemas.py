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
    endpoint_policy: Literal["prefer", "require"] = Field(
        "prefer",
        description=(
            "Endpoint selection policy: prefer allows failover, require only uses the selected endpoint."
        ),
    )
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
    org_mode: bool | None = Field(
        None,
        description="Whether this conversation is currently bound to an organization.",
    )
    org_id: str | None = Field(
        None,
        description="Selected organization ID for this conversation.",
        max_length=128,
    )
    org_node_id: str | None = Field(
        None,
        description="Selected organization node ID for this conversation.",
        max_length=128,
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
    local_path: str | None = Field(None, description="Server-side local path for uploaded files")
    upload_id: str | None = Field(None, description="Upload identifier returned by /api/upload")
    size: int | None = Field(None, description="Attachment size in bytes")
    mime_type: str | None = Field(None, description="MIME type")


# Fix forward reference
ChatRequest.model_rebuild()


class ChatAnswerRequest(BaseModel):
    """Answer to an ask_user event."""

    conversation_id: str | None = None
    answer: str = ""
    # Fix-11: 当用户在 ask_user 弹窗里勾选了"本次会话内同类操作不再询问"，
    # 前端把该字段置为 True；后端会把这条授权写进 session metadata，
    # 以便后续相同 operation_kind 的请求短路 risk gate。
    remember_for_session: bool = Field(
        False,
        description="If true, persist the user's confirmation as an in-session trust grant "
        "for the same operation kind (Fix-11).",
    )


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
