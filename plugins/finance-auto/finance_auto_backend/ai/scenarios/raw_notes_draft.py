"""S11 — 报表附注自动撰写 (🔴 raw).

Subscribed to the ``finance.notes.draft_requested`` event.  When a
pending narrative / hybrid note row exists in ``report_notes`` we
build a payload, drive the scenario, and persist the LLM-generated
markdown back to ``report_notes.content`` (flipping ``kind`` from
the pending sentinel to ``narrative`` once filled).  On success we
emit ``finance.notes.draft_ready`` so downstream listeners (the
report viewer, UI badges, etc.) can refresh.

The subscriber must be opt-in: :func:`attach_event_bus_subscriber`
is invoked by ``register_raw_ai_endpoints`` when the plugin's
router is built so callers always get a consistent wire-up.  Sibling
A may not have shipped the ``report_notes`` table yet — in that
case every SELECT comes back empty and the subscriber gracefully
no-ops.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ..event_bus import InMemoryEventBus, get_event_bus
from ._base import ScenarioRunResult, execute_scenario

if TYPE_CHECKING:
    from ...routes import FinanceAutoService
    from ..router import FinanceAIRouter

logger = logging.getLogger(__name__)

SCENARIO_ID = "notes_draft"
DEFAULT_LEVEL = "raw"
PROMPT_TEMPLATE = (TEMPLATE_DIR / "raw_notes_draft.md.j2").read_text(encoding="utf-8")
PENDING_KIND = "narrative_pending_ai"
FINAL_KIND = "narrative"


def build_payload(
    *,
    note_section: str,
    context_summary: str,
    reference_template: str = "",
    language: str = "zh",
    note_id: str | int | None = None,
    org_id: str | None = None,
) -> dict[str, Any]:
    return {
        "note_section": str(note_section or "").strip(),
        "context_summary": str(context_summary or ""),
        "reference_template": str(reference_template or ""),
        "language": str(language or "zh").strip() or "zh",
        "note_id": note_id,
        "org_id": str(org_id or ""),
    }


def _markdown_parser(text: str) -> dict[str, Any]:
    """Parser keeps the raw markdown plus a few stats — there's no
    structured JSON to extract from a narrative note."""
    return {
        "markdown": text or "",
        "chars": len(text or ""),
        "paragraph_count": sum(1 for line in (text or "").splitlines() if line.strip()),
    }


async def run(
    service: FinanceAutoService,
    *,
    payload: dict,
    org_id: str | None = None,
    router: FinanceAIRouter | None = None,
    auto_decision: str | None = None,
) -> ScenarioRunResult:
    """Generate the note draft.  Mirrors the other raw scenarios so
    the REST endpoint can introspect ``ScenarioRunResult`` uniformly.
    """
    context: dict[str, Any] = {
        "note_section": payload.get("note_section") or "",
        "context_summary": payload.get("context_summary") or "",
        "reference_template": payload.get("reference_template") or "",
        "language": payload.get("language") or "zh",
    }
    # Pre-render the template via execute_scenario's default context
    # plus our explicit keys — string.Template.safe_substitute drops
    # any unused key, so passing both is safe.
    composite_payload = {**payload, **context}
    return await execute_scenario(
        service,
        scenario_id=SCENARIO_ID,
        level=DEFAULT_LEVEL,
        payload=composite_payload,
        prompt_template=_render_template(context),
        parser=_markdown_parser,
        router=router,
        org_id=org_id or (payload.get("org_id") or None),
        auto_decision=auto_decision,
    )


def _render_template(context: dict[str, Any]) -> str:
    """Pre-render the prompt with our scenario-specific context so
    ``_base.render_prompt`` doesn't overwrite our ``${note_section}``
    / ``${language}`` keys with the default ``safe_payload_json``.
    """
    from string import Template

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            # Re-insert the placeholder so a later render pass (the
            # one inside execute_scenario) can still replace it.
            return "${" + key + "}"

    try:
        return Template(PROMPT_TEMPLATE).safe_substitute(_SafeDict(context))
    except Exception as exc:  # noqa: BLE001
        logger.warning("finance-auto: notes_draft template render failed: %s", exc)
        return PROMPT_TEMPLATE


# ---------------------------------------------------------------------------
# Event-bus subscriber
# ---------------------------------------------------------------------------


_SUBSCRIBER_KEY = "_finance_notes_draft_subscriber_attached"


def attach_event_bus_subscriber(
    service: FinanceAutoService,
    bus: InMemoryEventBus | None = None,
) -> None:
    """Idempotent: registers the ``finance.notes.draft_requested``
    listener once per (service, bus) pair.

    The subscriber dispatches the actual work to
    :func:`_handle_draft_requested` inside a fresh ``asyncio.Task`` so
    the event publisher (which may be a sync caller) never blocks on
    an LLM round-trip.
    """
    target_bus = bus or get_event_bus()
    flag_attr = _SUBSCRIBER_KEY
    if getattr(service, flag_attr, False):
        return
    setattr(service, flag_attr, True)

    def _on_request(payload: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_handle_draft_requested(service, payload))
            return
        loop.create_task(_handle_draft_requested(service, payload))

    target_bus.subscribe("finance.notes.draft_requested", _on_request)


async def _handle_draft_requested(
    service: FinanceAutoService,
    event_payload: dict[str, Any],
) -> None:
    """Resolve the pending ``report_notes`` row, run the scenario, and
    write the generated content back.
    """
    note_id = event_payload.get("note_id")
    org_id = event_payload.get("org_id")
    if note_id is None:
        return
    try:
        row = await _fetch_note_row(service, note_id)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "finance-auto: notes_draft skipped — report_notes unavailable (%s)",
            exc,
        )
        return
    if row is None:
        return
    payload = build_payload(
        note_section=str(row.get("section") or row.get("note_section") or ""),
        context_summary=str(row.get("context_summary") or ""),
        reference_template=str(row.get("reference_template") or ""),
        language=str(row.get("language") or "zh"),
        note_id=note_id,
        org_id=org_id,
    )
    auto_decision = event_payload.get("auto_decision")
    result = await run(
        service,
        payload=payload,
        org_id=org_id,
        auto_decision=auto_decision,
    )
    if result.outcome != "success":
        return
    try:
        await _persist_note(
            service,
            note_id=note_id,
            content=(result.response_text or ""),
            audit_id=result.audit_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("finance-auto: notes_draft persist failed: %s", exc)
        return
    await get_event_bus().emit(
        "finance.notes.draft_ready",
        {
            "note_id": note_id,
            "org_id": org_id,
            "kind": FINAL_KIND,
            "audit_id": result.audit_id,
            "scenario_id": SCENARIO_ID,
        },
    )


async def _fetch_note_row(
    service: FinanceAutoService,
    note_id: str | int,
) -> dict[str, Any] | None:
    """Read one pending ``report_notes`` row as a plain dict.

    Returns ``None`` when the table is missing (Sibling A's notes work
    hasn't merged yet) or the row doesn't exist.
    """
    try:
        async with service.db.conn.execute(
            "SELECT * FROM report_notes WHERE id=?", (note_id,)
        ) as cur:
            row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "no such table" in msg:
            return None
        raise
    if row is None:
        return None
    try:
        return {k: row[k] for k in row.keys()}  # noqa: SIM118
    except Exception:  # noqa: BLE001
        return dict(row)


async def _persist_note(
    service: FinanceAutoService,
    *,
    note_id: str | int,
    content: str,
    audit_id: int | None,
) -> None:
    """Write the generated markdown back, flipping ``kind`` to
    :data:`FINAL_KIND` and remembering which audit row produced it.

    Uses dynamic column probing so we can degrade gracefully if
    Sibling A's notes schema doesn't include ``ai_audit_id`` /
    ``updated_at`` yet.
    """
    async with service.db.conn.execute(
        "PRAGMA table_info(report_notes)"
    ) as cur:
        cols = {r[1] for r in await cur.fetchall()}
    updates = ["content = ?", "kind = ?"]
    args: list[Any] = [content, FINAL_KIND]
    if "ai_audit_id" in cols:
        updates.append("ai_audit_id = ?")
        args.append(audit_id)
    if "updated_at" in cols:
        updates.append("updated_at = datetime('now')")
    args.append(note_id)
    sql = (
        "UPDATE report_notes SET " + ", ".join(updates) + " WHERE id = ?"
    )
    await service.db.conn.execute(sql, tuple(args))
    await service.db.conn.commit()


__all__ = [
    "DEFAULT_LEVEL",
    "FINAL_KIND",
    "PENDING_KIND",
    "PROMPT_TEMPLATE",
    "SCENARIO_ID",
    "attach_event_bus_subscriber",
    "build_payload",
    "run",
]
