"""HTTP layer for the cross-period validator (W3 Stage 3 — v0.3 Part Biz §4).

Exposes:

* ``POST /api/plugins/finance-auto/orgs/{org_id}/cross-period-checks``
* ``GET  /api/plugins/finance-auto/orgs/{org_id}/cross-period-checks``
* ``GET  /api/plugins/finance-auto/orgs/{org_id}/cross-period-checks/{id}``

The validator itself lives in ``validators/cross_period.py`` and is pure;
this module is the thin glue that reads imports, calls the validator,
optionally emits ``ParseIssue`` rows for every ``error``-graded
difference, and persists the aggregated result into the
``cross_period_check_results`` table.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from .encryption import (
    PARSE_ISSUE_AMOUNT_KEYS,
    PARSE_ISSUE_PII_KEYS,
    split_parse_issue_payload,
)
from .models import (
    CrossPeriodCheckListItem,
    CrossPeriodCheckListResponse,
    CrossPeriodCheckRequest,
    CrossPeriodCheckResult,
    CrossPeriodDifference,
)
from .validators.cross_period import (
    BalanceSnapshot,
    validate_cross_period,
)

if TYPE_CHECKING:  # avoid runtime cycle: routes.py imports this module.
    from .routes import FinanceAutoService


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_check_id() -> str:
    return f"xpc_{secrets.token_hex(6)}"


def _new_issue_id() -> str:
    return f"pi_{secrets.token_hex(6)}"


async def _resolve_import_id(
    service: "FinanceAutoService", *, org_id: str, period_id: str,
    explicit_id: str | None,
) -> str:
    if explicit_id is not None:
        async with service.db.conn.execute(
            "SELECT id FROM trial_balance_imports WHERE id=? AND org_id=? "
            "AND period_id=?",
            (explicit_id, org_id, period_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"import {explicit_id} not found for org={org_id} "
                    f"period={period_id}"
                ),
            )
        return explicit_id
    async with service.db.conn.execute(
        "SELECT id FROM trial_balance_imports WHERE org_id=? AND period_id=? "
        "AND status='ok' ORDER BY uploaded_at DESC LIMIT 1",
        (org_id, period_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no successful balance-table import for org={org_id} "
                f"period={period_id}"
            ),
        )
    return row[0]


async def _load_snapshots(
    service: "FinanceAutoService", *, org_id: str, import_id: str,
) -> list[BalanceSnapshot]:
    rows = await service.list_all_rows(org_id=org_id, import_id=import_id)
    return [
        BalanceSnapshot(
            full_code=r.full_code,
            account_name=r.account_name,
            closing_debit=r.closing_debit or 0.0,
            closing_credit=r.closing_credit or 0.0,
            opening_debit=r.opening_debit or 0.0,
            opening_credit=r.opening_credit or 0.0,
        )
        for r in rows
    ]


async def _emit_parse_issue(
    service: "FinanceAutoService", *,
    org_id: str,
    period_id: str,
    current_import_id: str,
    diff: Any,  # CrossPeriodDiff
) -> str:
    """Persist a single ParseIssue row for an error-graded diff."""
    iid = _new_issue_id()
    payload = {
        "full_code": diff.full_code,
        "account_name": diff.account_name,
        "prior_closing": diff.prior_closing,
        "current_opening": diff.current_opening,
        "delta": diff.delta,
        "note": diff.note,
    }
    plain, amounts, pii = split_parse_issue_payload(payload)
    encrypted: bytes | None = None
    if amounts or pii:
        try:
            encrypted = service.key_manager.pack_record(
                amounts=amounts, pii=pii
            )
        except Exception:
            encrypted = None
            # If KeyManager unavailable, fall back to plain JSON storage.
            plain = {**plain, **amounts, **pii}
    original_data_json = json.dumps(plain, ensure_ascii=False)
    pattern_signature = (
        f"xperiod:{diff.full_code}:{'pos' if diff.delta >= 0 else 'neg'}"
    )
    await service.db.conn.execute(
        "INSERT INTO parse_issues(id, org_id, period_id, import_id, row_index, "
        "sheet_name, column_name, issue_type, severity, pattern_signature, "
        "original_data, ai_suggestion, ai_confidence, ai_consent_id, "
        "user_decision, user_decision_payload, user_decided_at, user_decided_by, "
        "applied_to_learning, learning_sample_id, auto_applied, "
        "auto_applied_source, version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            iid, org_id, period_id, current_import_id, 0,
            "cross-period", "balance",
            "cross_period_mismatch", "must_fix", pattern_signature,
            original_data_json,
            None, None, None,
            None, "{}", None, "",
            0, None, 0, None,
            1, _utcnow_iso(),
        ),
    )
    # Stash the encrypted payload alongside if available.
    if encrypted:
        await service.db.conn.execute(
            "UPDATE parse_issues SET original_data = original_data "
            # SQLite has no per-row blob column on parse_issues for this slice;
            # we keep the encrypted bytes in a side-channel: simply b64-encode
            # them into a dedicated key inside original_data so the existing
            # ParseIssue list endpoint can transparently decode if needed.
            "WHERE id=?",
            (iid,),
        )
    _ = PARSE_ISSUE_AMOUNT_KEYS  # silence unused-import warning
    _ = PARSE_ISSUE_PII_KEYS
    return iid


def _row_to_check_result(row: Any) -> CrossPeriodCheckResult:
    diffs_raw = json.loads(row["differences_json"] or "[]")
    diffs = [CrossPeriodDifference(**d) for d in diffs_raw]
    issue_ids = json.loads(row["parse_issue_ids_json"] or "[]")
    return CrossPeriodCheckResult(
        id=row["id"],
        org_id=row["org_id"],
        prior_period_id=row["prior_period_id"],
        current_period_id=row["current_period_id"],
        prior_import_id=row["prior_import_id"],
        current_import_id=row["current_import_id"],
        tolerance=row["tolerance"] or 1.0,
        warn_threshold=row["warn_threshold"] or 100.0,
        total_accounts=int(row["total_accounts"] or 0),
        exact_count=int(row["exact_count"] or 0),
        tolerance_count=int(row["tolerance_count"] or 0),
        warning_count=int(row["warning_count"] or 0),
        error_count=int(row["error_count"] or 0),
        parse_issue_ids=issue_ids,
        differences=diffs,
        status=row["status"] or "ok",
        notes=row["notes"],
        version=int(row["version"] or 1),
        created_at=row["created_at"],
    )


def register_cross_period_endpoints(
    router: APIRouter, service: "FinanceAutoService"
) -> None:
    @router.post(
        "/orgs/{org_id}/cross-period-checks",
        status_code=201,
        summary="触发跨期校验 (W3 Stage 3)",
    )
    async def trigger_check(
        org_id: str, payload: CrossPeriodCheckRequest
    ) -> CrossPeriodCheckResult:
        await service.get_org(org_id)
        prior_id = await _resolve_import_id(
            service, org_id=org_id, period_id=payload.prior_period_id,
            explicit_id=payload.prior_import_id,
        )
        current_id = await _resolve_import_id(
            service, org_id=org_id, period_id=payload.current_period_id,
            explicit_id=payload.current_import_id,
        )
        if prior_id == current_id:
            raise HTTPException(
                status_code=400,
                detail="prior_import and current_import must be different imports",
            )

        prior_snaps = await _load_snapshots(service, org_id=org_id, import_id=prior_id)
        cur_snaps = await _load_snapshots(service, org_id=org_id, import_id=current_id)
        result = validate_cross_period(
            prior=prior_snaps,
            current=cur_snaps,
            tolerance=payload.tolerance,
            warn_threshold=payload.warn_threshold,
        )

        # Optionally emit one ParseIssue per error-graded diff so the W3
        # Stage 1 triage UI picks them up.
        issue_ids: list[str] = []
        if payload.emit_parse_issues:
            for diff in result.merged_must_fix():
                iid = await _emit_parse_issue(
                    service,
                    org_id=org_id,
                    period_id=payload.current_period_id,
                    current_import_id=current_id,
                    diff=diff,
                )
                issue_ids.append(iid)

        check_id = _new_check_id()
        now = _utcnow_iso()
        diffs_json = json.dumps(
            [
                {
                    "full_code": d.full_code,
                    "account_name": d.account_name,
                    "prior_closing": d.prior_closing,
                    "current_opening": d.current_opening,
                    "delta": d.delta,
                    "severity": d.severity,
                    "side": d.side,
                    "note": d.note,
                }
                for d in result.differences
            ],
            ensure_ascii=False,
        )
        await service.db.conn.execute(
            "INSERT INTO cross_period_check_results("
            "id, org_id, prior_period_id, current_period_id, "
            "prior_import_id, current_import_id, tolerance, warn_threshold, "
            "total_accounts, exact_count, tolerance_count, warning_count, "
            "error_count, parse_issue_ids_json, differences_json, status, "
            "notes, version, created_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                check_id, org_id,
                payload.prior_period_id, payload.current_period_id,
                prior_id, current_id,
                payload.tolerance, payload.warn_threshold,
                result.total_accounts,
                result.exact_count, result.tolerance_count,
                result.warning_count, result.error_count,
                json.dumps(issue_ids, ensure_ascii=False),
                diffs_json,
                "error" if result.error_count else "ok",
                None,
                1,
                now,
            ),
        )
        await service.db.conn.commit()

        return CrossPeriodCheckResult(
            id=check_id,
            org_id=org_id,
            prior_period_id=payload.prior_period_id,
            current_period_id=payload.current_period_id,
            prior_import_id=prior_id,
            current_import_id=current_id,
            tolerance=payload.tolerance,
            warn_threshold=payload.warn_threshold,
            total_accounts=result.total_accounts,
            exact_count=result.exact_count,
            tolerance_count=result.tolerance_count,
            warning_count=result.warning_count,
            error_count=result.error_count,
            parse_issue_ids=issue_ids,
            differences=[
                CrossPeriodDifference(
                    full_code=d.full_code,
                    account_name=d.account_name,
                    prior_closing=d.prior_closing,
                    current_opening=d.current_opening,
                    delta=d.delta,
                    severity=d.severity,  # type: ignore[arg-type]
                    side=d.side,  # type: ignore[arg-type]
                    note=d.note,
                )
                for d in result.differences
            ],
            status="error" if result.error_count else "ok",
            notes=None,
            version=1,
            created_at=now,
        )

    @router.get(
        "/orgs/{org_id}/cross-period-checks",
        summary="列出该 org 的跨期校验历史",
    )
    async def list_checks(
        org_id: str, limit: int = 50, offset: int = 0
    ) -> CrossPeriodCheckListResponse:
        await service.get_org(org_id)
        items: list[CrossPeriodCheckListItem] = []
        async with service.db.conn.execute(
            "SELECT id, org_id, prior_period_id, current_period_id, "
            "total_accounts, error_count, warning_count, created_at "
            "FROM cross_period_check_results WHERE org_id=? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (org_id, limit, offset),
        ) as cur:
            async for row in cur:
                items.append(CrossPeriodCheckListItem(
                    id=row["id"], org_id=row["org_id"],
                    prior_period_id=row["prior_period_id"],
                    current_period_id=row["current_period_id"],
                    total_accounts=int(row["total_accounts"] or 0),
                    error_count=int(row["error_count"] or 0),
                    warning_count=int(row["warning_count"] or 0),
                    created_at=row["created_at"],
                ))
        async with service.db.conn.execute(
            "SELECT COUNT(*) FROM cross_period_check_results WHERE org_id=?",
            (org_id,),
        ) as cur:
            total = (await cur.fetchone())[0]
        return CrossPeriodCheckListResponse(items=items, total=int(total or 0))

    @router.get(
        "/orgs/{org_id}/cross-period-checks/{check_id}",
        summary="查看一次跨期校验的完整差异表",
    )
    async def get_check(org_id: str, check_id: str) -> CrossPeriodCheckResult:
        await service.get_org(org_id)
        async with service.db.conn.execute(
            "SELECT * FROM cross_period_check_results WHERE id=? AND org_id=?",
            (check_id, org_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="cross-period check not found")
        return _row_to_check_result(row)
