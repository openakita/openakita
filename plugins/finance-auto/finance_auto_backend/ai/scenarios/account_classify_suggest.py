"""S2 — 未识别科目归类建议 (🟢 metadata).

Reads a batch of unresolved ParseIssue rows (issue_type=unknown_code),
asks the LLM to bucket them, then UPDATE-s ``parse_issues.ai_suggestion
/ ai_confidence / ai_consent_id`` per the v0.2 §9.2 contract.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ..event_bus import get_event_bus
from ..models import ParseIssueAIFilledEvent
from ._base import ScenarioRunResult, execute_scenario

if TYPE_CHECKING:
    from ...routes import FinanceAutoService
    from ..router import FinanceAIRouter

logger = logging.getLogger(__name__)

SCENARIO_ID = "account_classify_suggest"
DEFAULT_LEVEL = "metadata"
PROMPT_TEMPLATE = (TEMPLATE_DIR / "account_classify_suggest.md.j2").read_text(
    encoding="utf-8"
)


def build_payload(unknown_accounts: list[dict[str, str]]) -> dict[str, Any]:
    """Each entry should carry ``account_code`` + ``account_name`` + an
    optional issue_id so we can correlate the suggestion back to the
    originating ParseIssue row.
    """
    return {
        "items": [
            {
                "account_code": a.get("account_code") or "",
                "account_name": a.get("account_name") or "",
                "issue_id": a.get("issue_id") or "",
            }
            for a in unknown_accounts
        ]
    }


async def run(
    service: FinanceAutoService,
    *,
    payload: dict,
    org_id: str | None = None,
    router: FinanceAIRouter | None = None,
    auto_decision: str | None = None,
    apply_to_parse_issues: bool = True,
) -> ScenarioRunResult:
    result = await execute_scenario(
        service,
        scenario_id=SCENARIO_ID,
        level=DEFAULT_LEVEL,
        payload=payload,
        prompt_template=PROMPT_TEMPLATE,
        router=router,
        org_id=org_id,
        auto_decision=auto_decision,
    )
    if (
        result.outcome == "success"
        and apply_to_parse_issues
        and result.parsed is not None
    ):
        await _apply_suggestions(
            service,
            payload=payload,
            parsed=result.parsed,
            consent_id=result.consent_id,
            org_id=org_id,
        )
    return result


async def _apply_suggestions(
    service: FinanceAutoService,
    *,
    payload: dict,
    parsed: Any,
    consent_id: int | None,
    org_id: str | None,
) -> None:
    """Write each AI suggestion back to the matching parse_issues row.

    Matching is done by ``account_code`` (which equals ``full_code`` of
    the unknown row).  When the response contains an ``issue_id`` field
    we prefer that — it's the exact PK and avoids ambiguity for repeat
    codes inside the same import.
    """
    if not isinstance(parsed, list):
        # Some prompts come back wrapped in a dict — try ``.get("items")``.
        if isinstance(parsed, dict):
            parsed = parsed.get("items") or parsed.get("suggestions") or []
        else:
            parsed = []
    suggestion_by_code: dict[str, dict] = {}
    suggestion_by_issue: dict[str, dict] = {}
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("account_code") or "").strip()
        issue_id = str(entry.get("issue_id") or "").strip()
        if issue_id:
            suggestion_by_issue[issue_id] = entry
        if code:
            suggestion_by_code[code] = entry

    inputs = payload.get("items") or []
    affected: list[str] = []
    for item in inputs:
        if not isinstance(item, dict):
            continue
        issue_id = item.get("issue_id") or ""
        code = item.get("account_code") or ""
        suggestion = (
            suggestion_by_issue.get(issue_id) or suggestion_by_code.get(code)
        )
        if suggestion is None or not issue_id:
            continue
        confidence = suggestion.get("confidence")
        try:
            conf = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            conf = None
        await service.db.conn.execute(
            "UPDATE parse_issues SET ai_suggestion=?, ai_confidence=?, "
            "ai_consent_id=?, version=version+1 WHERE id=?",
            (
                json.dumps(suggestion, ensure_ascii=False, default=str),
                conf,
                consent_id,
                issue_id,
            ),
        )
        affected.append(issue_id)
    if affected:
        await service.db.conn.commit()
        ev = ParseIssueAIFilledEvent(
            org_id=org_id or "",
            issue_ids=affected,
            consent_id=consent_id,
            scenario_id=SCENARIO_ID,
            completed_at=__import__("datetime").datetime.utcnow().isoformat() + "Z",
        )
        await get_event_bus().emit(
            "finance.parse.issue.ai_filled", ev.model_dump()
        )


async def fetch_unresolved_unknown_codes(
    service: FinanceAutoService,
    *,
    org_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Convenience: pull pending ``unknown_code`` ParseIssue rows that
    don't yet have an AI suggestion.  Used by the event-driven worker.
    """
    async with service.db.conn.execute(
        "SELECT id, original_data FROM parse_issues WHERE org_id=? AND "
        "issue_type='unknown_code' AND ai_suggestion IS NULL "
        "AND user_decision IS NULL ORDER BY created_at ASC LIMIT ?",
        (org_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            data = json.loads(row["original_data"] or "{}")
        except json.JSONDecodeError:
            data = {}
        out.append(
            {
                "issue_id": row["id"],
                "account_code": data.get("full_code") or data.get("parent_code") or "",
                "account_name": data.get("account_name") or "",
            }
        )
    return out


__all__ = [
    "DEFAULT_LEVEL",
    "PROMPT_TEMPLATE",
    "SCENARIO_ID",
    "build_payload",
    "fetch_unresolved_unknown_codes",
    "run",
]
