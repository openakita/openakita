"""S6 — 审计意见草稿 (🔴 raw).

User-initiated.  Generates an audit-opinion markdown report from a
batch of validation results plus an audit template.  The default
sensitivity is ``raw`` because the source data carries the full
client-side details (account names, period amounts, exception notes);
the router enforces local-only routing unless the user explicitly
opted into cloud via the consent dialog (``skip_desensitize=True``).

Prompt-injection guard
----------------------

Before the LLM call we scan the user-supplied data blob for the
known prompt-injection markers listed in v0.2 §6.2.  If one is
found we flip ``payload['_prompt_injection_detected'] = True`` and
the template emits an unqualified opinion warning at the top.  The
guard never raises — its only purpose is to *flag* the suspicious
input so the audit log + the rendered report make the detection
visible to reviewers.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ..audit import maybe_persist_debug_snapshot, record_llm_call
from ..consent import ConsentDenied, check_consent
from ..desensitizer import desensitize
from ..router import FinanceAIRouter, LLMResponse
from ._base import ScenarioRunResult, render_prompt, resolve_router

if TYPE_CHECKING:
    from ...routes import FinanceAutoService

logger = logging.getLogger(__name__)

SCENARIO_ID = "audit_opinion_draft"
DEFAULT_LEVEL = "raw"
PROMPT_TEMPLATE = (TEMPLATE_DIR / "raw_audit_opinion.md.j2").read_text(
    encoding="utf-8"
)

# Regex covers both English and Chinese phrasings used by the design
# doc.  ``re.IGNORECASE`` only affects the ASCII letters; Chinese
# matches are unaffected.
PROMPT_INJECTION_PATTERN = re.compile(
    r"(ignore\s+(all\s+)?previous"
    r"|disregard\s+(all\s+)?previous"
    r"|忽略(以上|前面)"
    r"|忘记(以上|前面)"
    r"|act\s+as\s+(a\s+)?different"
    r"|new\s+instructions?\s+below)",
    re.IGNORECASE,
)


def build_payload(
    validations_json: Any,
    template_text: str,
    period_label: str,
    org_name: str,
) -> dict[str, Any]:
    """Bundle the four S6 inputs into one payload dict.

    ``validations_json`` may already be a Python list/dict (preferred)
    or a JSON string — both are accepted.  The ``user_data_blob`` key
    is what the injection guard scans; the template only renders the
    structured ``validations_json`` so the blob itself stays out of
    the prompt body.
    """
    if isinstance(validations_json, str):
        try:
            validations = json.loads(validations_json)
        except json.JSONDecodeError:
            validations = []
    else:
        validations = validations_json or []
    blob_parts: list[str] = []
    if isinstance(validations, list):
        for v in validations:
            if isinstance(v, dict):
                blob_parts.append(
                    " ".join(str(x) for x in v.values() if x is not None)
                )
            else:
                blob_parts.append(str(v))
    blob_parts.append(template_text or "")
    return {
        "validations_json": validations,
        "template_text": str(template_text or ""),
        "period_label": str(period_label or ""),
        "org_name": str(org_name or ""),
        "user_data_blob": "\n".join(blob_parts),
    }


def detect_prompt_injection(payload: dict[str, Any]) -> bool:
    """Scan ``payload['user_data_blob']`` (and the validations blob as
    a fallback) for the configured prompt-injection markers.

    Returns True when a marker is found.  The caller is expected to
    mark the payload + render the template's unqualified warning.
    """
    blob = str(payload.get("user_data_blob") or "")
    if not blob:
        try:
            blob = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            blob = str(payload)
    return PROMPT_INJECTION_PATTERN.search(blob) is not None


async def run(
    service: FinanceAutoService,
    *,
    payload: dict,
    org_id: str | None = None,
    router: FinanceAIRouter | None = None,
    auto_decision: str | None = None,
) -> ScenarioRunResult:
    """Execute S6.

    We don't reuse :func:`_base.execute_scenario` directly because we
    need to inject the prompt-injection context flag *between*
    consent and render.  The pipeline still mirrors the base helper
    step-by-step so the audit row contract matches the other
    scenarios (provider / model / hash / outcome / consent_id).
    """
    started = time.perf_counter()
    router = resolve_router(service, router)
    user_id = "local"

    injected = detect_prompt_injection(payload)
    if injected:
        payload = dict(payload)
        payload["_prompt_injection_detected"] = True

    safe_payload = desensitize(payload, DEFAULT_LEVEL)
    try:
        consent = await check_consent(
            service,
            scenario_id=SCENARIO_ID,
            level=DEFAULT_LEVEL,
            payload=payload,
            user_id=user_id,
            auto_decision=auto_decision,  # type: ignore[arg-type]
        )
    except ConsentDenied as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        audit_id = await record_llm_call(
            service,
            scenario_id=SCENARIO_ID,
            sensitivity_level=DEFAULT_LEVEL,
            outcome="denied",
            desensitized_payload=safe_payload,
            org_id=org_id,
            user_id=user_id,
            duration_ms=elapsed,
            error_message=str(exc),
        )
        return ScenarioRunResult(
            scenario_id=SCENARIO_ID,
            outcome="denied",
            audit_id=audit_id,
            duration_ms=elapsed,
            desensitized_payload=safe_payload,
            parsed={"prompt_injection_detected": injected},
            error=str(exc),
        )

    if consent.skip_desensitize:
        safe_payload = payload

    warning = (
        "> ⚠️ Prompt-injection detected — unqualified template returned.\n"
        if injected
        else ""
    )

    context: dict[str, Any] = {
        "validations_json": json.dumps(
            payload.get("validations_json") or [],
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        "template_text": str(payload.get("template_text") or ""),
        "period_label": str(payload.get("period_label") or ""),
        "org_name": str(payload.get("org_name") or ""),
        "prompt_injection_warning": warning,
    }
    prompt_text = render_prompt(PROMPT_TEMPLATE, context)

    response: LLMResponse | None = None
    outcome: str = "success"
    error: str | None = None
    try:
        response = await router.complete(
            scenario_id=SCENARIO_ID,
            level=DEFAULT_LEVEL,
            prompt=prompt_text,
            skip_desensitize=consent.skip_desensitize,
        )
    except Exception as exc:  # noqa: BLE001
        outcome = "error"
        error = f"{type(exc).__name__}: {exc}"

    elapsed = int((time.perf_counter() - started) * 1000)
    snapshot_path = maybe_persist_debug_snapshot(
        scenario_id=SCENARIO_ID,
        desensitized_payload=safe_payload,
        response=response,
    )
    audit_id = await record_llm_call(
        service,
        scenario_id=SCENARIO_ID,
        sensitivity_level=DEFAULT_LEVEL,
        outcome=outcome,  # type: ignore[arg-type]
        desensitized_payload=safe_payload,
        response=response,
        consent_id=consent.consent_id,
        org_id=org_id,
        user_id=user_id,
        duration_ms=elapsed,
        error_message=error,
        desensitized_payload_path=snapshot_path,
    )

    markdown_text = response.text if response else ""
    parsed_meta: dict[str, Any] = {
        "prompt_injection_detected": injected,
        "markdown_chars": len(markdown_text),
    }

    return ScenarioRunResult(
        scenario_id=SCENARIO_ID,
        outcome=outcome,  # type: ignore[arg-type]
        consent_id=consent.consent_id,
        audit_id=audit_id,
        response_text=markdown_text,
        parsed=parsed_meta,
        is_local=(response.is_local if response else False),
        desensitized_payload=safe_payload,
        duration_ms=elapsed,
        error=error,
    )


__all__ = [
    "DEFAULT_LEVEL",
    "PROMPT_INJECTION_PATTERN",
    "PROMPT_TEMPLATE",
    "SCENARIO_ID",
    "build_payload",
    "detect_prompt_injection",
    "run",
]
