"""Pydantic models for the M2 AI sub-system.

Mirrors the SQLite tables introduced in schema v8 plus the small set of
DTOs the API + WebSocket channel exchange.  Kept under
``backend/ai/models.py`` (instead of expanding the top-level
``models.py``) so a future refactor can keep the AI module self-contained.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SensitivityLevel = Literal["metadata", "aggregated", "raw"]
ConsentDecision = Literal["deny", "allow_once", "allow_permanent"]
LLMOutcome = Literal["success", "denied", "error", "timeout"]


# ---------------------------------------------------------------------------
# AI Scenario
# ---------------------------------------------------------------------------


class AIScenario(BaseModel):
    """One row in ``ai_scenarios`` — the registry that drives the
    "AI 设置 → 场景列表" UI page.
    """

    model_config = ConfigDict(from_attributes=True)

    scenario_id: str
    name: str
    description: str | None = None
    default_sensitivity: SensitivityLevel
    default_enabled: bool = True
    prompt_template_path: str | None = None
    is_local_only: bool = False
    require_dialog: bool = True
    sensitivity_override: SensitivityLevel | None = None
    enabled_override: bool | None = None
    created_at: str
    updated_at: str | None = None

    @property
    def effective_sensitivity(self) -> SensitivityLevel:
        return self.sensitivity_override or self.default_sensitivity

    @property
    def effective_enabled(self) -> bool:
        return (
            self.enabled_override if self.enabled_override is not None
            else self.default_enabled
        )


class AIScenarioListResponse(BaseModel):
    scenarios: list[AIScenario]
    total: int


class AIScenarioPatchRequest(BaseModel):
    """PATCH /ai/scenarios/{scenario_id} payload."""

    enabled: bool | None = None
    sensitivity_override: SensitivityLevel | None = None


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


class AIConsent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    consent_id: int
    user_id: str = "local"
    scenario_id: str
    sensitivity_level: SensitivityLevel
    decision: ConsentDecision
    granted_at: str
    revoked_at: str | None = None
    source_dialog_id: str | None = None
    skip_desensitize: bool = False
    created_at: str | None = None


class AIConsentListResponse(BaseModel):
    consents: list[AIConsent]
    total: int
    active_permanent: int


class ConsentRespondRequest(BaseModel):
    """Front-end POST body for ``/ai/consent/respond``.

    The dialog id was issued by the back-end when the WebSocket
    ``ai_consent_request`` event went out; the response carries the
    user's selection so the consent checker (which is awaiting on a
    ``Future``) can resume.
    """

    dialog_id: str
    decision: ConsentDecision
    skip_desensitize: bool = False


# ---------------------------------------------------------------------------
# LLM Call Audit
# ---------------------------------------------------------------------------


class LLMCallAudit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: str
    user_id: str = "local"
    org_id: str | None = None
    scenario_id: str
    sensitivity_level: SensitivityLevel
    model_provider: str | None = None
    model_name: str | None = None
    is_local_endpoint: bool = False
    payload_hash: str
    payload_size_bytes: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    consent_id: int | None = None
    outcome: LLMOutcome
    error_message: str | None = None
    desensitized_payload_path: str | None = None
    duration_ms: int | None = None
    created_at: str | None = None


class LLMCallAuditListResponse(BaseModel):
    items: list[LLMCallAudit]
    total: int
    summary: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# WebSocket payload shapes (kept symmetrical with React types).
# ---------------------------------------------------------------------------


class ConsentRequestEvent(BaseModel):
    """Pushed over WebSocket when the back-end needs the user's decision.

    The React side renders this as an ``AIConsentDialog``; the user's
    selection comes back via the ``POST /ai/consent/respond`` REST
    endpoint (more reliable than two-way WS, mirrors the Tauri-IPC
    fallback choice from v0.2 §4.6).
    """

    event: Literal["ai_consent_request"] = "ai_consent_request"
    dialog_id: str
    scenario_id: str
    sensitivity_level: SensitivityLevel
    preview_payload: str
    model_provider: str = ""
    model_name: str = ""
    is_local_endpoint: bool = False
    estimated_tokens: int = 0
    requested_at: str

    @classmethod
    def now(cls, **kwargs: object) -> "ConsentRequestEvent":
        kwargs.setdefault("requested_at", datetime.utcnow().isoformat() + "Z")
        return cls(**kwargs)  # type: ignore[arg-type]


class ParseIssueAIFilledEvent(BaseModel):
    """Emitted after S2 finishes filling ``parse_issues.ai_suggestion``."""

    event: Literal["parse_issue_ai_filled"] = "parse_issue_ai_filled"
    org_id: str
    issue_ids: list[str]
    consent_id: int | None = None
    scenario_id: str = "account_classify_suggest"
    completed_at: str


__all__ = [
    "AIConsent",
    "AIConsentListResponse",
    "AIScenario",
    "AIScenarioListResponse",
    "AIScenarioPatchRequest",
    "ConsentDecision",
    "ConsentRequestEvent",
    "ConsentRespondRequest",
    "LLMCallAudit",
    "LLMCallAuditListResponse",
    "LLMOutcome",
    "ParseIssueAIFilledEvent",
    "SensitivityLevel",
]
