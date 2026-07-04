"""HTTP endpoints for the M3 raw (🔴) AI scenarios.

Mounted by :func:`register_raw_ai_endpoints` (called from
``routes.build_router`` right after :func:`register_ai_endpoints`).
The four new endpoints all live under the plugin's reserved prefix
``/api/plugins/finance-auto/ai/raw/``:

* ``POST /ai/raw/audit-opinion``    — S6.
* ``POST /ai/raw/nl-query``         — S7.
* ``POST /ai/raw/notes-draft``      — S11.
* ``GET  /ai/raw/scenarios``        — list the three new scenarios.

On registration the helper also runs :func:`ensure_raw_scenarios_seeded`
so the three rows show up in ``ai_scenarios`` without touching the
schema migration (DDL stays at v8; we only INSERT OR IGNORE).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .scenarios import raw_audit_opinion, raw_nl_query, raw_notes_draft

if TYPE_CHECKING:
    from ..routes import FinanceAutoService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB seed for the 3 new scenarios (idempotent INSERT OR IGNORE).
# ---------------------------------------------------------------------------

# Tuple shape mirrors v8_ai_tables._SCENARIOS but stays local so we don't
# have to touch the migration file (per the M3 territory constraint).
# Columns: (scenario_id, name, description, default_sensitivity,
#           default_enabled, prompt_template_path, is_local_only,
#           require_dialog)
_RAW_SCENARIOS: tuple[tuple[str, str, str, str, int, str, int, int], ...] = (
    (
        raw_audit_opinion.SCENARIO_ID,
        "审计意见草稿",
        "用户主动触发。结合校验结果 + 审计模板生成审计意见 markdown 草稿。"
        "🔴 raw 级；默认本地端点，仅在用户在弹窗勾选 skip_desensitize=false "
        "时才允许云端（脱敏后）。带 prompt-injection 检测。",
        "raw", 0,
        "templates/ai_prompts/raw_audit_opinion.md.j2",
        0, 1,
    ),
    (
        raw_nl_query.SCENARIO_ID,
        "自然语言查询",
        "聊天框触发。把中文问题翻译为安全 SELECT 语句（白名单表 + 1000 行上限）。"
        "🔴 raw 级；执行前必经 SQL 守卫。",
        "raw", 0,
        "templates/ai_prompts/raw_nl_query.md.j2",
        0, 1,
    ),
    (
        raw_notes_draft.SCENARIO_ID,
        "报表附注自动撰写",
        "由 finance.notes.draft_requested 事件触发。为 narrative / hybrid 类型附注"
        "生成叙述性 markdown，写回 report_notes.content 并触发 "
        "finance.notes.draft_ready 事件。🔴 raw 级；本地优先。",
        "raw", 0,
        "templates/ai_prompts/raw_notes_draft.md.j2",
        0, 1,
    ),
)


async def ensure_raw_scenarios_seeded(service: FinanceAutoService) -> int:
    """Insert any missing rows into ``ai_scenarios`` (idempotent).

    Returns the number of rows actually inserted (``0`` on re-run).
    """
    if not service.db.is_ready():
        # The host may call register_raw_ai_endpoints before init();
        # we defer the seed in that case — the next API call will
        # re-trigger via the ``@router.get`` handler below.
        return 0
    inserted = 0
    for (
        sid, name, desc, level, enabled, tpl_path, is_local_only, require_dialog,
    ) in _RAW_SCENARIOS:
        async with service.db.conn.execute(
            "SELECT 1 FROM ai_scenarios WHERE scenario_id=?", (sid,)
        ) as cur:
            if await cur.fetchone() is not None:
                continue
        await service.db.conn.execute(
            "INSERT OR IGNORE INTO ai_scenarios("
            "scenario_id, name, description, default_sensitivity, "
            "default_enabled, prompt_template_path, is_local_only, "
            "require_dialog, sensitivity_override, enabled_override, "
            "created_at, updated_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, "
            "datetime('now'), datetime('now'))",
            (sid, name, desc, level, enabled, tpl_path, is_local_only,
             require_dialog),
        )
        inserted += 1
    if inserted:
        await service.db.conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AuditOpinionRequest(BaseModel):
    org_id: str
    validations_json: Any | None = None
    template_text: str = ""
    period_label: str = ""
    auto_decision: str | None = None


class NLQueryRequest(BaseModel):
    org_id: str | None = None
    question: str
    execute_sql: bool = False
    auto_decision: str | None = None


class NotesDraftRequest(BaseModel):
    org_id: str
    note_id: str | int
    auto_decision: str | None = None


# ---------------------------------------------------------------------------
# Endpoint registration
# ---------------------------------------------------------------------------


def register_raw_ai_endpoints(
    router: APIRouter, service: FinanceAutoService
) -> None:
    """Mount the 4 new ``/ai/raw/*`` endpoints + seed the registry."""

    # We can't ``await`` here, so the seed runs lazily inside the GET
    # below.  In practice the routes.build_router caller drives the
    # FinanceAutoDB.init() before the first request lands, so the GET
    # always sees a populated table.

    @router.get(
        "/ai/raw/scenarios",
        summary="列出 M3 新增的 3 个 🔴 raw AI 场景（含 override 状态）",
    )
    async def list_raw_scenarios() -> dict[str, Any]:
        await ensure_raw_scenarios_seeded(service)
        wanted = [s[0] for s in _RAW_SCENARIOS]
        placeholders = ", ".join(["?"] * len(wanted))
        async with service.db.conn.execute(
            f"SELECT * FROM ai_scenarios WHERE scenario_id IN ({placeholders}) "
            "ORDER BY scenario_id",
            tuple(wanted),
        ) as cur:
            rows = await cur.fetchall()
        items = [
            {
                "scenario_id": r["scenario_id"],
                "name": r["name"],
                "description": r["description"],
                "default_sensitivity": r["default_sensitivity"],
                "default_enabled": bool(r["default_enabled"]),
                "is_local_only": bool(r["is_local_only"]),
                "require_dialog": bool(r["require_dialog"]),
                "sensitivity_override": r["sensitivity_override"],
                "enabled_override": (
                    bool(r["enabled_override"])
                    if r["enabled_override"] is not None
                    else None
                ),
                "prompt_template_path": r["prompt_template_path"],
            }
            for r in rows
        ]
        return {"scenarios": items, "total": len(items)}

    @router.post(
        "/ai/raw/audit-opinion",
        summary="S6 审计意见草稿 — 🔴 raw，prompt-injection 守卫",
    )
    async def post_audit_opinion(payload: AuditOpinionRequest) -> dict[str, Any]:
        await ensure_raw_scenarios_seeded(service)
        org_name = ""
        try:
            org = await service.get_org(payload.org_id)
            org_name = getattr(org, "name", "") or ""
        except HTTPException:
            # When the org is missing we still let the scenario run
            # so the audit row + denied/error path is exercisable
            # from the acceptance script without a pre-seeded org.
            pass
        scenario_payload = raw_audit_opinion.build_payload(
            validations_json=payload.validations_json or [],
            template_text=payload.template_text or "",
            period_label=payload.period_label or "",
            org_name=org_name or payload.org_id,
        )
        result = await raw_audit_opinion.run(
            service,
            payload=scenario_payload,
            org_id=payload.org_id,
            auto_decision=payload.auto_decision,
        )
        body = result.to_dict()
        if result.outcome == "success":
            body["markdown"] = result.response_text or ""
        return body

    @router.post(
        "/ai/raw/nl-query",
        summary="S7 自然语言查询 — 翻译为 SELECT 并按需执行",
    )
    async def post_nl_query(payload: NLQueryRequest) -> dict[str, Any]:
        await ensure_raw_scenarios_seeded(service)
        if not (payload.question or "").strip():
            raise HTTPException(status_code=400, detail="question is required")
        scenario_payload = raw_nl_query.build_payload(
            question=payload.question,
            org_id=payload.org_id,
        )
        result = await raw_nl_query.run(
            service,
            payload=scenario_payload,
            org_id=payload.org_id,
            auto_decision=payload.auto_decision,
            execute_sql=bool(payload.execute_sql),
        )
        parsed: dict[str, Any] = (
            result.parsed if isinstance(result.parsed, dict) else {}
        )
        body: dict[str, Any] = {
            "sql": parsed.get("sql") or "",
            "safe": bool(parsed.get("safe")),
            "validation_errors": list(parsed.get("validation_errors") or []),
            "referenced_tables": list(parsed.get("referenced_tables") or []),
            "executed": bool(parsed.get("executed")),
            "scenario_result": result.to_dict(),
        }
        rows = parsed.get("rows")
        if rows is not None:
            body["rows"] = rows
        exec_err = parsed.get("execute_error")
        if exec_err:
            body["execute_error"] = exec_err
        return body

    @router.post(
        "/ai/raw/notes-draft",
        summary="S11 报表附注 — 读取 report_notes 行并生成 markdown",
    )
    async def post_notes_draft(payload: NotesDraftRequest) -> dict[str, Any]:
        await ensure_raw_scenarios_seeded(service)
        # Read the pending note row.  If the table doesn't exist
        # (Sibling A's notes work hasn't merged) we surface a 503 so
        # the acceptance script can short-circuit the assertion.
        try:
            async with service.db.conn.execute(
                "SELECT * FROM report_notes WHERE id=?", (payload.note_id,)
            ) as cur:
                row = await cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "no such table" in msg:
                raise HTTPException(
                    status_code=503,
                    detail="report_notes table not available yet",
                ) from exc
            raise
        if row is None:
            raise HTTPException(status_code=404, detail="note not found")
        row_dict = {k: row[k] for k in row.keys()}  # noqa: SIM118
        scenario_payload = raw_notes_draft.build_payload(
            note_section=str(
                row_dict.get("section")
                or row_dict.get("note_section")
                or ""
            ),
            context_summary=str(row_dict.get("context_summary") or ""),
            reference_template=str(row_dict.get("reference_template") or ""),
            language=str(row_dict.get("language") or "zh"),
            note_id=payload.note_id,
            org_id=payload.org_id,
        )
        result = await raw_notes_draft.run(
            service,
            payload=scenario_payload,
            org_id=payload.org_id,
            auto_decision=payload.auto_decision,
        )
        updated_note: dict[str, Any] | None = None
        if result.outcome == "success":
            try:
                await raw_notes_draft._persist_note(
                    service,
                    note_id=payload.note_id,
                    content=(result.response_text or ""),
                    audit_id=result.audit_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "finance-auto: notes-draft persist failed for %s: %s",
                    payload.note_id, exc,
                )
            from .event_bus import get_event_bus
            await get_event_bus().emit(
                "finance.notes.draft_ready",
                {
                    "note_id": payload.note_id,
                    "org_id": payload.org_id,
                    "kind": raw_notes_draft.FINAL_KIND,
                    "audit_id": result.audit_id,
                    "scenario_id": raw_notes_draft.SCENARIO_ID,
                },
            )
            try:
                async with service.db.conn.execute(
                    "SELECT * FROM report_notes WHERE id=?",
                    (payload.note_id,),
                ) as cur:
                    fresh = await cur.fetchone()
                if fresh is not None:
                    updated_note = {k: fresh[k] for k in fresh.keys()}  # noqa: SIM118
            except Exception:  # noqa: BLE001
                updated_note = None
        return {
            "note": updated_note,
            "scenario_result": result.to_dict(),
        }


__all__ = [
    "ensure_raw_scenarios_seeded",
    "register_raw_ai_endpoints",
]
