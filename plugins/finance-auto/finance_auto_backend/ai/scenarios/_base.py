"""Shared scaffolding for the M2 AI scenarios.

Every scenario follows the same five-step pipeline:

1. Build the prompt from a payload + template (sync).
2. Call ``check_consent`` (async, may emit WS dialog).
3. (When granted) optionally desensitize the payload further.
4. Route to the LLM via ``FinanceAIRouter.complete``.
5. Parse the response + write the audit row.

This module captures (1) + (5) so each scenario only has to declare
its prompt template, payload-shape extraction, and response parser.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from string import Template
from typing import TYPE_CHECKING, Any

from ..audit import maybe_persist_debug_snapshot, record_llm_call
from ..consent import ConsentDenied, check_consent
from ..desensitizer import SensitivityLevel, desensitize
from ..models import LLMOutcome
from ..router import FinanceAIRouter, HostBrainResponder, LLMResponse, MockLLMResponder

if TYPE_CHECKING:
    from ...routes import FinanceAutoService

logger = logging.getLogger(__name__)


def resolve_router(
    service: FinanceAutoService, router: FinanceAIRouter | None
) -> FinanceAIRouter:
    """Pick the router every scenario should use.

    Resolution order:

    1. An explicit ``router`` (tests inject a controlled responder here).
    2. A router backed by the host Brain when the plugin was granted
       ``brain.access`` — ``plugin.py`` stores the brain on the service
       as ``host_brain``. This routes completions through OpenAkita's
       own configured LLM provider.
    3. :class:`MockLLMResponder` — the offline default used by the
       acceptance suite and any deployment without ``brain.access``.
    """
    if router is not None:
        return router
    getter = getattr(service, "get_host_brain", None)
    brain = getter() if callable(getter) else None
    if brain is not None:
        return FinanceAIRouter(responder=HostBrainResponder(brain))
    return FinanceAIRouter(responder=MockLLMResponder())


@dataclass
class ScenarioRunResult:
    """Common envelope returned by every ``run(...)`` coroutine."""

    scenario_id: str
    outcome: LLMOutcome
    consent_id: int | None = None
    audit_id: int | None = None
    response_text: str = ""
    parsed: Any = None
    is_local: bool = False
    desensitized_payload: Any = None
    duration_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "outcome": self.outcome,
            "consent_id": self.consent_id,
            "audit_id": self.audit_id,
            "response_text": self.response_text,
            "parsed": self.parsed,
            "is_local": self.is_local,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def render_prompt(template: str, context: dict[str, Any]) -> str:
    """Lightweight ``string.Template``-style render.

    Avoids pulling in Jinja2 because the templates are tiny and never
    contain control flow.  Missing keys fall back to ``""`` so a partially
    populated payload doesn't blow up the call (the response parser is
    expected to spot the gap downstream).
    """

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    try:
        return Template(template).safe_substitute(_SafeDict(context))
    except Exception as exc:  # noqa: BLE001 — never break the call
        logger.warning("finance-auto: prompt render failed: %s", exc)
        return template


_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


def parse_json_response(text: str) -> Any:
    """Extract a JSON value from an LLM response.

    Tolerates the model wrapping the JSON in a fenced ```json``` block,
    a leading commentary, or a trailing footer.  Returns the first
    parseable structure or ``None`` on failure (callers decide whether
    that counts as a soft error).
    """
    if not text:
        return None
    candidates: list[str] = []
    for match in _JSON_BLOCK_PATTERN.findall(text):
        candidates.append(match)
    # Greedy: also try the whole string if no fenced block.
    candidates.append(text)
    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    # Last-ditch — find the first {...} or [...] substring.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


async def execute_scenario(
    service: FinanceAutoService,
    *,
    scenario_id: str,
    level: SensitivityLevel,
    payload: Any,
    prompt_template: str,
    parser: callable | None = None,
    router: FinanceAIRouter | None = None,
    org_id: str | None = None,
    user_id: str = "local",
    auto_decision: str | None = None,
    timeout: float = 30.0,
) -> ScenarioRunResult:
    """End-to-end execution of one scenario call.

    The router defaults to a fresh ``FinanceAIRouter`` with a
    ``MockLLMResponder``; production code passes in a router that wraps
    the host LLMClient.  ``parser`` is the response → structured-result
    callback; if omitted we use :func:`parse_json_response`.
    """
    started = time.perf_counter()
    router = resolve_router(service, router)
    parser = parser or parse_json_response

    safe_payload = desensitize(payload, level)
    try:
        consent = await check_consent(
            service,
            scenario_id=scenario_id,
            level=level,
            payload=payload,
            user_id=user_id,
            timeout=timeout,
            auto_decision=auto_decision,  # type: ignore[arg-type]
        )
    except ConsentDenied as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        audit_id = await record_llm_call(
            service,
            scenario_id=scenario_id,
            sensitivity_level=level,
            outcome="denied",
            desensitized_payload=safe_payload,
            org_id=org_id,
            user_id=user_id,
            duration_ms=elapsed,
            error_message=str(exc),
        )
        return ScenarioRunResult(
            scenario_id=scenario_id,
            outcome="denied",
            audit_id=audit_id,
            duration_ms=elapsed,
            desensitized_payload=safe_payload,
            error=str(exc),
        )

    if consent.skip_desensitize:
        # User explicitly authorised raw payload (only valid on local).
        safe_payload = payload

    prompt_text = render_prompt(
        prompt_template, _build_prompt_context(payload, safe_payload, scenario_id)
    )

    response: LLMResponse | None = None
    parsed: Any = None
    error: str | None = None
    outcome: LLMOutcome = "success"
    try:
        response = await router.complete(
            scenario_id=scenario_id,
            level=level,
            prompt=prompt_text,
            skip_desensitize=consent.skip_desensitize,
        )
        parsed = parser(response.text)
    except Exception as exc:  # noqa: BLE001
        outcome = "error"
        error = f"{type(exc).__name__}: {exc}"

    elapsed = int((time.perf_counter() - started) * 1000)
    snapshot_path = maybe_persist_debug_snapshot(
        scenario_id=scenario_id,
        desensitized_payload=safe_payload,
        response=response,
    )
    audit_id = await record_llm_call(
        service,
        scenario_id=scenario_id,
        sensitivity_level=level,
        outcome=outcome,
        desensitized_payload=safe_payload,
        response=response,
        consent_id=consent.consent_id,
        org_id=org_id,
        user_id=user_id,
        duration_ms=elapsed,
        error_message=error,
        desensitized_payload_path=snapshot_path,
    )

    return ScenarioRunResult(
        scenario_id=scenario_id,
        outcome=outcome,
        consent_id=consent.consent_id,
        audit_id=audit_id,
        response_text=(response.text if response else ""),
        parsed=parsed,
        is_local=(response.is_local if response else False),
        desensitized_payload=safe_payload,
        duration_ms=elapsed,
        error=error,
    )


def _build_prompt_context(
    raw_payload: Any, safe_payload: Any, scenario_id: str
) -> dict[str, Any]:
    """Default Jinja-style context: serialised forms of the payload.

    Scenario-specific runners pre-build a richer context (account
    names, period labels, etc.) — they pass it directly into
    ``render_prompt`` and bypass this default.
    """
    return {
        "scenario_id": scenario_id,
        "safe_payload_json": json.dumps(safe_payload, ensure_ascii=False, indent=2),
        "raw_payload_json": json.dumps(raw_payload, ensure_ascii=False, indent=2, default=str),
    }


__all__ = [
    "ScenarioRunResult",
    "execute_scenario",
    "parse_json_response",
    "render_prompt",
    "resolve_router",
]
