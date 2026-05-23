"""HTTP endpoints for the M2 AI sub-system.

Mounted onto the plugin's router via :func:`register_ai_endpoints`
(called by ``routes.build_router``).  Endpoints land under the
plugin's reserved prefix ``/api/plugins/finance-auto/ai/``.

Stage 3 (this file) only ships the consent-respond endpoint that
unblocks the WS dialog channel; Stage 6 layers the full management API
(scenarios + consent listing + audit log) on top.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

from .consent import get_dialog_registry
from .event_bus import get_event_bus
from .models import (
    AIConsent,
    AIConsentListResponse,
    AIScenario,
    AIScenarioListResponse,
    AIScenarioPatchRequest,
    ConsentRespondRequest,
    LLMCallAudit,
    LLMCallAuditListResponse,
)

if TYPE_CHECKING:
    from ..routes import FinanceAutoService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------


def _row_to_scenario(row: Any) -> AIScenario:
    return AIScenario(
        scenario_id=row["scenario_id"],
        name=row["name"],
        description=row["description"],
        default_sensitivity=row["default_sensitivity"],
        default_enabled=bool(row["default_enabled"]),
        prompt_template_path=row["prompt_template_path"],
        is_local_only=bool(row["is_local_only"]),
        require_dialog=bool(row["require_dialog"]),
        sensitivity_override=row["sensitivity_override"],
        enabled_override=(
            bool(row["enabled_override"])
            if row["enabled_override"] is not None
            else None
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_consent(row: Any) -> AIConsent:
    return AIConsent(
        consent_id=row["consent_id"],
        user_id=row["user_id"] or "local",
        scenario_id=row["scenario_id"],
        sensitivity_level=row["sensitivity_level"],
        decision=row["decision"],
        granted_at=row["granted_at"],
        revoked_at=row["revoked_at"],
        source_dialog_id=row["source_dialog_id"],
        skip_desensitize=bool(row["skip_desensitize"]),
        created_at=row["created_at"],
    )


def _row_to_audit(row: Any) -> LLMCallAudit:
    return LLMCallAudit(
        id=row["id"],
        timestamp=row["timestamp"],
        user_id=row["user_id"] or "local",
        org_id=row["org_id"],
        scenario_id=row["scenario_id"],
        sensitivity_level=row["sensitivity_level"],
        model_provider=row["model_provider"],
        model_name=row["model_name"],
        is_local_endpoint=bool(row["is_local_endpoint"]),
        payload_hash=row["payload_hash"],
        payload_size_bytes=row["payload_size_bytes"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        consent_id=row["consent_id"],
        outcome=row["outcome"],
        error_message=row["error_message"],
        desensitized_payload_path=row["desensitized_payload_path"],
        duration_ms=row["duration_ms"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Endpoint registration
# ---------------------------------------------------------------------------


def register_ai_endpoints(router: APIRouter, service: FinanceAutoService) -> None:
    @router.get(
        "/ai/scenarios",
        summary="列出所有 AI 场景及其默认开关 / 当前 override",
    )
    async def list_scenarios() -> AIScenarioListResponse:
        async with service.db.conn.execute(
            "SELECT * FROM ai_scenarios ORDER BY scenario_id"
        ) as cur:
            rows = await cur.fetchall()
        items = [_row_to_scenario(r) for r in rows]
        return AIScenarioListResponse(scenarios=items, total=len(items))

    @router.patch(
        "/ai/scenarios/{scenario_id}",
        summary="修改场景的 enabled / sensitivity_override",
    )
    async def patch_scenario(
        scenario_id: str, payload: AIScenarioPatchRequest
    ) -> AIScenario:
        async with service.db.conn.execute(
            "SELECT * FROM ai_scenarios WHERE scenario_id=?", (scenario_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="scenario not found")

        updates: list[str] = []
        args: list[Any] = []
        if payload.enabled is not None:
            updates.append("enabled_override=?")
            args.append(1 if payload.enabled else 0)
        if payload.sensitivity_override is not None:
            updates.append("sensitivity_override=?")
            args.append(payload.sensitivity_override)
        if updates:
            updates.append("updated_at=datetime('now')")
            args.append(scenario_id)
            await service.db.conn.execute(
                f"UPDATE ai_scenarios SET {', '.join(updates)} WHERE scenario_id=?",
                tuple(args),
            )
            await service.db.conn.commit()

        async with service.db.conn.execute(
            "SELECT * FROM ai_scenarios WHERE scenario_id=?", (scenario_id,)
        ) as cur:
            updated = await cur.fetchone()
        return _row_to_scenario(updated)

    @router.get("/ai/consent", summary="列出当前用户的 AI 授权记录")
    async def list_consent(
        user_id: str = Query(default="local"),
        active_only: bool = Query(default=False),
    ) -> AIConsentListResponse:
        clauses = ["user_id=?"]
        args: list[Any] = [user_id]
        if active_only:
            clauses.append(
                "decision='allow_permanent' AND revoked_at IS NULL"
            )
        sql = (
            f"SELECT * FROM ai_consent WHERE {' AND '.join(clauses)} "
            f"ORDER BY granted_at DESC"
        )
        async with service.db.conn.execute(sql, tuple(args)) as cur:
            rows = await cur.fetchall()
        consents = [_row_to_consent(r) for r in rows]
        active = sum(
            1 for c in consents
            if c.decision == "allow_permanent" and c.revoked_at is None
        )
        return AIConsentListResponse(
            consents=consents, total=len(consents), active_permanent=active
        )

    @router.post(
        "/ai/consent/respond",
        summary="前端 AIConsentDialog 的回执 — 解锁后端 await",
    )
    async def respond_consent(payload: ConsentRespondRequest) -> dict:
        registry = get_dialog_registry()
        ok = await registry.resolve(
            payload.dialog_id,
            {
                "decision": payload.decision,
                "skip_desensitize": payload.skip_desensitize,
            },
        )
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"unknown or already-resolved dialog_id: {payload.dialog_id}",
            )
        return {
            "ok": True,
            "dialog_id": payload.dialog_id,
            "decision": payload.decision,
        }

    @router.delete(
        "/ai/consent/{consent_id}",
        summary="撤销永久授权（写 revoked_at）",
    )
    async def revoke_consent(consent_id: int) -> AIConsent:
        async with service.db.conn.execute(
            "SELECT * FROM ai_consent WHERE consent_id=?", (consent_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="consent not found")
        if row["decision"] != "allow_permanent":
            raise HTTPException(
                status_code=400,
                detail="only allow_permanent grants can be revoked",
            )
        if row["revoked_at"] is not None:
            return _row_to_consent(row)
        await service.db.conn.execute(
            "UPDATE ai_consent SET revoked_at=datetime('now') WHERE consent_id=?",
            (consent_id,),
        )
        await service.db.conn.commit()
        async with service.db.conn.execute(
            "SELECT * FROM ai_consent WHERE consent_id=?", (consent_id,)
        ) as cur:
            updated = await cur.fetchone()
        await get_event_bus().emit(
            "finance.ai.consent.revoked",
            {"consent_id": consent_id, "scenario_id": updated["scenario_id"]},
        )
        return _row_to_consent(updated)

    @router.get(
        "/ai/audit-log",
        summary="LLM 调用历史（可按 org_id / scenario / outcome 过滤）",
    )
    async def list_audit_log(
        org_id: str | None = Query(default=None),
        scenario_id: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> LLMCallAuditListResponse:
        clauses: list[str] = []
        args: list[Any] = []
        if org_id:
            clauses.append("org_id=?")
            args.append(org_id)
        if scenario_id:
            clauses.append("scenario_id=?")
            args.append(scenario_id)
        if outcome:
            clauses.append("outcome=?")
            args.append(outcome)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        async with service.db.conn.execute(
            f"SELECT * FROM llm_call_audit{where} "
            f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            tuple(args + [limit, offset]),
        ) as cur:
            rows = await cur.fetchall()
        async with service.db.conn.execute(
            f"SELECT outcome, COUNT(*) AS n FROM llm_call_audit{where} "
            "GROUP BY outcome",
            tuple(args),
        ) as cur:
            agg = await cur.fetchall()
        summary = {r["outcome"]: int(r["n"]) for r in agg}
        async with service.db.conn.execute(
            f"SELECT COUNT(*) AS n FROM llm_call_audit{where}",
            tuple(args),
        ) as cur:
            total_row = await cur.fetchone()
        items = [_row_to_audit(r) for r in rows]
        return LLMCallAuditListResponse(
            items=items,
            total=int(total_row["n"]) if total_row else 0,
            summary=summary,
        )


__all__ = ["register_ai_endpoints"]
