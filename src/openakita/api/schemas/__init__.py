"""Typed wire shapes for the OpenAkita HTTP API.

Top-level package init for ``openakita.api.schemas``. Hosts the
v1 wire shapes (ChatRequest / ChatAnswerRequest / ChatControlRequest /
AttachmentInfo / HealthCheckRequest / HealthResult / ModelInfo /
SkillInfoResponse) that originally lived in the sibling ``schemas.py``
module.

The ``schemas/orgs_v2/`` subpackage (P9.7a-2b; D-3 LOCKED) hosts the
v2 REST mint shapes consumed by ``api/routes/orgs_v2_runtime*.py``.

P9.7gamma-3 NIT-A fold-in: the P9.7a-2b commit created this package
to host ``orgs_v2/`` but did not move the legacy ``schemas.py``
contents in. Python's package-shadows-module rule silently broke 19
main-gate collections (every test importing ``ChatRequest`` etc.).
The merge below restores the v1 surface byte-for-byte; the legacy
``schemas.py`` file is deleted in the same commit.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AskUserReplyRequest(BaseModel):
    """Structured reply to a normal ask_user prompt.

    This marks the message as a continuation of an assistant question, not as a
    standalone user intent. It must not create or imply RiskGate authorization.
    """

    message_id: str | None = Field(
        None,
        description="Frontend message id that contained the ask_user prompt, when available.",
        max_length=128,
    )
    answer: str | None = Field(
        None,
        description="Optional explicit answer text. Defaults to ChatRequest.message.",
        max_length=32_768,
    )
    kind: Literal["normal"] = Field(
        "normal",
        description="Only normal ask_user replies use this path; RiskGate uses security-confirm.",
    )


class ChatAttachmentRecord(BaseModel):
    """Attachment shape persisted in session history and rendered by ChatView."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image", "file", "voice", "video", "document"] = Field(
        ..., description="Attachment kind rendered by the chat UI."
    )
    name: str = Field(..., description="Display filename")
    url: str | None = Field(None, description="Renderable or downloadable URL")
    local_path: str | None = Field(
        None,
        alias="localPath",
        description="Server-side local path for uploaded files",
    )
    upload_id: str | None = Field(
        None,
        alias="uploadId",
        description="Upload identifier returned by /api/upload",
    )
    preview_url: str | None = Field(
        None,
        alias="previewUrl",
        description="Image preview URL",
    )
    size: int | None = Field(None, description="Attachment size in bytes")
    mime_type: str | None = Field(None, alias="mimeType", description="MIME type")
    upload_status: Literal["uploading", "uploaded", "failed"] | None = Field(
        None,
        alias="uploadStatus",
        description="Upload state shown by the chat UI",
    )
    upload_error: str | None = Field(
        None,
        alias="uploadError",
        description="Upload error shown by the chat UI",
    )

    def to_history_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class AttachmentInfo(BaseModel):
    """Attachment metadata accepted by chat request endpoints."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image", "file", "voice", "video", "document"] = Field(
        ..., description="image | file | voice | video | document"
    )
    name: str = Field(..., description="Filename")
    url: str | None = Field(None, description="URL or data URI")
    local_path: str | None = Field(None, description="Server-side local path for uploaded files")
    upload_id: str | None = Field(None, description="Upload identifier returned by /api/upload")
    size: int | None = Field(None, description="Attachment size in bytes")
    mime_type: str | None = Field(None, description="MIME type")

    def to_chat_attachment_record(self) -> ChatAttachmentRecord:
        payload: dict[str, Any] = {
            "type": self.type,
            "name": self.name,
            "url": self.url,
            "localPath": self.local_path,
            "uploadId": self.upload_id,
            "previewUrl": self.url if self.type == "image" and self.url else None,
            "size": self.size,
            "mimeType": self.mime_type,
            "uploadStatus": "uploaded",
        }
        return ChatAttachmentRecord.model_validate(
            {key: value for key, value in payload.items() if value is not None}
        )

    def to_chat_attachment_dict(self) -> dict[str, Any]:
        return self.to_chat_attachment_record().to_history_dict()


class ChatRequest(BaseModel):
    """Chat request body."""

    model_config = ConfigDict(extra="forbid")

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
    permission_mode: (
        Literal[
            "plan",
            "default",
            "accept_edits",
            "dont_ask",
            "bypass_permissions",
        ]
        | None
    ) = Field(None, description="Product-level permission mode for this turn")
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
    thinking_depth: Literal["low", "medium", "high", "max", "xhigh"] | None = Field(
        None,
        description="Thinking depth: 'low', 'medium', 'high', 'max'. Only effective when thinking is enabled.",
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
    ask_user_reply: AskUserReplyRequest | None = Field(
        None,
        description=(
            "Structured continuation for a normal ask_user prompt. When set, "
            "the backend treats message as the answer to a previous assistant "
            "question and does not classify it as a new high-risk user request."
        ),
    )
    turn_id: str | None = Field(
        None,
        description=(
            "Per-turn idempotency key (v1.27.14, plan v1.28 S1.6). "
            "Identical turn_id replayed within ~60s returns 409 turn_already_processing "
            "to avoid duplicate streams on flaky networks / SSE reconnects. "
            "Optional; missing means no idempotency short-circuit."
        ),
        max_length=128,
        pattern=r"^[A-Za-z0-9_\-:.@]{0,128}$",
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
