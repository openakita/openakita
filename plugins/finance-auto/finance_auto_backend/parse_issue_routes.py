"""HTTP endpoints + service helpers for parse_issues + learning_samples.

Mounted onto the W1 router by :func:`register_parse_issue_endpoints` (called
from ``routes.build_router``).  Four endpoints:

* ``GET    /orgs/{org_id}/parse-issues``
* ``POST   /orgs/{org_id}/parse-issues/{issue_id}/decide``
* ``POST   /orgs/{org_id}/parse-issues/{issue_id}/learn``
* ``GET    /orgs/{org_id}/learning-samples``

A fifth helper, :func:`run_parse_issue_detection_after_import`, is what the
upload route calls so every freshly persisted import automatically gets
issues + auto-applied learning samples.  Keeping it here (instead of in
``routes.py``) means the W1 surface stays unchanged.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .rbac import require_permission
from .encryption import (
    DecryptionError,
    pack_payload,
    split_parse_issue_payload,
    unpack_payload,
)
from .models import (
    IssueType,
    LearningSample,
    LearningSampleListResponse,
    ParseIssue,
    ParseIssueDecisionRequest,
    ParseIssueLearnRequest,
    ParseIssueListResponse,
)
from .validators.parse_issue_detector import (
    DetectedIssue,
    detect_parse_issues,
)

if TYPE_CHECKING:
    from .parsers.xls_parser import ParsedRow
    from .routes import FinanceAutoService

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _persist_detected_issues(
    service: FinanceAutoService,
    *,
    org_id: str,
    period_id: str,
    import_id: str,
    detected: list[DetectedIssue],
) -> list[str]:
    """Insert detected issues, returning the newly created issue ids."""
    if not detected:
        return []
    enc_enabled = service.encryption_enabled()
    now = _utcnow_iso()
    params: list[tuple] = []
    issue_ids: list[str] = []
    for det in detected:
        issue_id = f"iss_{uuid.uuid4().hex[:12]}"
        issue_ids.append(issue_id)
        plain, amounts, pii = split_parse_issue_payload(det.original_data)
        if enc_enabled and (amounts or pii):
            blob = pack_payload(
                service.key_manager,
                amounts=amounts or None,
                pii=pii or None,
            )
            plain["__enc_blob__"] = blob.hex()
        original_json = json.dumps(plain, ensure_ascii=False, default=str)
        params.append((
            issue_id,
            org_id,
            period_id,
            import_id,
            det.row_index,
            det.sheet_name,
            det.column_name,
            det.issue_type,
            det.severity,
            det.pattern_signature,
            original_json,
            None,  # ai_suggestion
            None,  # ai_confidence
            None,  # ai_consent_id
            None,  # user_decision
            "{}",  # user_decision_payload
            None,  # user_decided_at
            "",    # user_decided_by
            0,     # applied_to_learning
            None,  # learning_sample_id
            0,     # auto_applied (set later if a sample matches)
            None,  # auto_applied_source
            1,     # version
            now,
        ))
    await service.db.conn.executemany(
        "INSERT INTO parse_issues(id, org_id, period_id, import_id, row_index, "
        "sheet_name, column_name, issue_type, severity, pattern_signature, "
        "original_data, ai_suggestion, ai_confidence, ai_consent_id, "
        "user_decision, user_decision_payload, user_decided_at, user_decided_by, "
        "applied_to_learning, learning_sample_id, auto_applied, "
        "auto_applied_source, version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params,
    )
    await service.db.conn.commit()
    return issue_ids


def _decode_original_data(
    service: FinanceAutoService,
    raw_json: str,
    *,
    accept_corrupted: bool = False,
) -> dict[str, Any]:
    """Reverse the encryption split.  Reads back any ``__enc_blob__`` hex
    payload and merges the decrypted fields back into the plain dict.

    EX-P2-6: decryption failures now raise :class:`encryption.DecryptionError`
    unless the caller explicitly opts in via ``accept_corrupted=True``
    (typically wired to a ``?accept_corrupted=true`` query string).
    The opt-in branch logs a WARNING so the audit trail keeps a record
    of the silent fallback.
    """
    try:
        plain = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(plain, dict):
        return {}
    blob_hex = plain.pop("__enc_blob__", None)
    if blob_hex and service.encryption_enabled():
        try:
            blob = bytes.fromhex(blob_hex)
            decoded = unpack_payload(service.key_manager, blob)
            for k, v in (decoded.get("amounts") or {}).items():
                plain[k] = v
            for k, v in (decoded.get("pii") or {}).items():
                plain[k] = v
        except Exception as exc:  # noqa: BLE001 — re-wrap into DecryptionError
            if accept_corrupted:
                logger.warning(
                    "finance-auto: parse_issue original_data decrypt failed "
                    "(accept_corrupted=true): %s",
                    exc,
                )
            else:
                logger.error(
                    "finance-auto: parse_issue original_data decrypt failed: %s",
                    exc,
                )
                raise DecryptionError(
                    "parse_issue original_data decrypt failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
    return plain


def _row_to_parse_issue(
    service: FinanceAutoService,
    row: Any,
    *,
    accept_corrupted: bool = False,
) -> ParseIssue:
    return ParseIssue(
        id=row["id"],
        org_id=row["org_id"],
        period_id=row["period_id"],
        import_id=row["import_id"],
        row_index=row["row_index"],
        sheet_name=row["sheet_name"] or "",
        column_name=row["column_name"] or "",
        issue_type=row["issue_type"],
        severity=row["severity"],
        pattern_signature=row["pattern_signature"] or "",
        original_data=_decode_original_data(
            service, row["original_data"], accept_corrupted=accept_corrupted
        ),
        ai_suggestion=(json.loads(row["ai_suggestion"]) if row["ai_suggestion"] else None),
        ai_confidence=row["ai_confidence"],
        ai_consent_id=row["ai_consent_id"],
        user_decision=row["user_decision"],
        user_decision_payload=json.loads(row["user_decision_payload"] or "{}"),
        user_decided_at=row["user_decided_at"],
        user_decided_by=row["user_decided_by"] or "",
        applied_to_learning=bool(row["applied_to_learning"]),
        learning_sample_id=row["learning_sample_id"],
        auto_applied=bool(row["auto_applied"]),
        auto_applied_source=row["auto_applied_source"],
        version=row["version"] or 1,
        created_at=row["created_at"],
    )


def _row_to_learning_sample(row: Any) -> LearningSample:
    return LearningSample(
        id=row["id"],
        org_id=row["org_id"],
        pattern_type=row["pattern_type"],
        pattern_signature=row["pattern_signature"],
        action=json.loads(row["action"] or "{}"),
        confidence=row["confidence"] or 0.0,
        hit_count=row["hit_count"] or 0,
        last_used_at=row["last_used_at"],
        auto_apply=bool(row["auto_apply"]),
        source_decision_id=row["source_decision_id"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Learning-sample auto-apply
# ---------------------------------------------------------------------------


async def _auto_apply_learning(
    service: FinanceAutoService,
    *,
    org_id: str,
    issue_ids: list[str],
) -> int:
    """For every just-created issue, check if a matching learning sample
    exists with ``auto_apply=1``.  If so, copy its action into the issue,
    mark ``auto_applied=1``, and bump the sample's hit count.

    Returns the number of issues auto-applied.  Errors fall back to ``0`` so
    the upload flow is never broken by a malformed sample.
    """
    if not issue_ids:
        return 0
    placeholders = ",".join("?" * len(issue_ids))
    async with service.db.conn.execute(
        f"SELECT id, issue_type, pattern_signature FROM parse_issues "
        f"WHERE id IN ({placeholders})",
        tuple(issue_ids),
    ) as cur:
        new_issues = await cur.fetchall()

    applied = 0
    now = _utcnow_iso()
    for nrow in new_issues:
        async with service.db.conn.execute(
            "SELECT * FROM learning_samples WHERE auto_apply=1 AND pattern_type=? "
            "AND pattern_signature=? AND (org_id=? OR org_id IS NULL) "
            "ORDER BY (org_id IS NULL) ASC, confidence DESC LIMIT 1",
            (nrow["issue_type"], nrow["pattern_signature"], org_id),
        ) as cur:
            sample = await cur.fetchone()
        if sample is None:
            continue
        await service.db.conn.execute(
            "UPDATE parse_issues SET user_decision=?, user_decision_payload=?, "
            "user_decided_at=?, user_decided_by=?, auto_applied=1, "
            "auto_applied_source=?, applied_to_learning=1, learning_sample_id=?, "
            "version=version+1 WHERE id=?",
            (
                "apply_ai",
                sample["action"] or "{}",
                now,
                f"learning_sample:{sample['id']}",
                sample["id"],
                sample["id"],
                nrow["id"],
            ),
        )
        await service.db.conn.execute(
            "UPDATE learning_samples SET hit_count=hit_count+1, last_used_at=? "
            "WHERE id=?",
            (now, sample["id"]),
        )
        applied += 1
    if applied:
        await service.db.conn.commit()
    return applied


# ---------------------------------------------------------------------------
# Public entry point (called from upload route)
# ---------------------------------------------------------------------------


async def run_parse_issue_detection_after_import(
    service: FinanceAutoService,
    *,
    org_id: str,
    period_id: str,
    import_id: str,
    rows: list[ParsedRow],
    sheet_name: str = "余额表",
) -> dict[str, Any]:
    """Run the detector against the just-persisted rows, write the issues and
    auto-apply any learning samples that match.

    Returns a small dict summarising the run so the upload response can
    surface useful counts to the user.
    """
    detected = detect_parse_issues(rows, sheet_name=sheet_name)
    issue_ids = await _persist_detected_issues(
        service,
        org_id=org_id,
        period_id=period_id,
        import_id=import_id,
        detected=detected,
    )
    auto = await _auto_apply_learning(service, org_id=org_id, issue_ids=issue_ids)
    must_fix = sum(1 for d in detected if d.severity == "must_fix")
    return {
        "detected": len(detected),
        "issue_ids": issue_ids,
        "auto_applied": auto,
        "must_fix": must_fix,
    }


# ---------------------------------------------------------------------------
# Endpoint registration
# ---------------------------------------------------------------------------


def register_parse_issue_endpoints(
    router: APIRouter, service: FinanceAutoService
) -> None:
    @router.get(
        "/orgs/{org_id}/parse-issues",
        summary="列出账套的解析异常（默认仅 pending）",
    )
    async def list_parse_issues(
        org_id: str,
        status: str = Query(default="pending", pattern="^(pending|all|decided|auto_applied)$"),
        issue_type: IssueType | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        accept_corrupted: bool = Query(
            default=False,
            description=(
                "EX-P2-6 灾难恢复：明确接受损坏密文；缺省 false 下"
                "decrypt 失败抛 500/decrypt_failed"
            ),
        ),
    ) -> ParseIssueListResponse:
        await service.get_org(org_id)
        clauses = ["org_id=?"]
        args: list[Any] = [org_id]
        if status == "pending":
            clauses.append("user_decision IS NULL")
        elif status == "decided":
            clauses.append("user_decision IS NOT NULL")
        elif status == "auto_applied":
            clauses.append("auto_applied=1")
        if issue_type:
            clauses.append("issue_type=?")
            args.append(issue_type)
        where = " AND ".join(clauses)
        async with service.db.conn.execute(
            f"SELECT * FROM parse_issues WHERE {where} "
            f"ORDER BY severity DESC, created_at DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        try:
            issues = [
                _row_to_parse_issue(
                    service, r, accept_corrupted=accept_corrupted
                )
                for r in rows
            ]
        except DecryptionError as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": "decrypt_failed", "message": str(exc)},
            ) from exc

        async with service.db.conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN user_decision IS NULL THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN user_decision IS NULL AND severity='must_fix' THEN 1 ELSE 0 END) AS must_fix "
            "FROM parse_issues WHERE org_id=?",
            (org_id,),
        ) as cur:
            agg = await cur.fetchone()
        return ParseIssueListResponse(
            issues=issues,
            total=agg["total"] or 0,
            pending=agg["pending"] or 0,
            must_fix_pending=agg["must_fix"] or 0,
        )

    @router.post(
        "/orgs/{org_id}/parse-issues/{issue_id}/decide",
        summary="对一条解析异常作出决策（apply_ai / manual_fix / skip / ignore_as_other）",
    )
    async def decide_parse_issue(
        org_id: str,
        issue_id: str,
        payload: ParseIssueDecisionRequest,
        _user: str = Depends(require_permission("parse_issue", "decide")),
    ) -> ParseIssue:
        await service.get_org(org_id)
        async with service.db.conn.execute(
            "SELECT * FROM parse_issues WHERE org_id=? AND id=?",
            (org_id, issue_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="parse issue not found")

        await service.db.conn.execute(
            "UPDATE parse_issues SET user_decision=?, user_decision_payload=?, "
            "user_decided_at=?, user_decided_by=?, version=version+1 "
            "WHERE org_id=? AND id=? AND version=?",
            (
                payload.decision,
                json.dumps(payload.payload, ensure_ascii=False, default=str),
                _utcnow_iso(),
                payload.decided_by,
                org_id,
                issue_id,
                row["version"],
            ),
        )
        await service.db.conn.commit()
        async with service.db.conn.execute(
            "SELECT * FROM parse_issues WHERE id=?", (issue_id,),
        ) as cur:
            updated = await cur.fetchone()
        return _row_to_parse_issue(service, updated)

    @router.post(
        "/orgs/{org_id}/parse-issues/{issue_id}/learn",
        summary="把决策保存为 learning_sample（可选 auto_apply / 全局共享）",
    )
    async def learn_parse_issue(
        org_id: str,
        issue_id: str,
        payload: ParseIssueLearnRequest,
        _user: str = Depends(require_permission("parse_issue", "learn")),
    ) -> LearningSample:
        await service.get_org(org_id)
        async with service.db.conn.execute(
            "SELECT * FROM parse_issues WHERE org_id=? AND id=?",
            (org_id, issue_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="parse issue not found")
        if not row["user_decision"]:
            raise HTTPException(
                status_code=400,
                detail="issue must be decided before it can be turned into a learning sample",
            )

        target_org: str | None = None if payload.share_globally else org_id
        sample_id = f"lsm_{uuid.uuid4().hex[:12]}"
        now = _utcnow_iso()
        action_payload = {
            "decision": row["user_decision"],
            "payload": json.loads(row["user_decision_payload"] or "{}"),
        }
        try:
            await service.db.conn.execute(
                "INSERT INTO learning_samples(id, org_id, pattern_type, "
                "pattern_signature, action, confidence, hit_count, "
                "last_used_at, auto_apply, source_decision_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sample_id,
                    target_org,
                    row["issue_type"],
                    row["pattern_signature"],
                    json.dumps(action_payload, ensure_ascii=False, default=str),
                    payload.confidence,
                    0,
                    None,
                    int(payload.auto_apply),
                    issue_id,
                    now,
                ),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                # Same signature already learned — update auto_apply / confidence.
                async with service.db.conn.execute(
                    "SELECT id FROM learning_samples WHERE pattern_type=? AND "
                    "pattern_signature=? AND (org_id=? OR (org_id IS NULL AND ?=1))",
                    (
                        row["issue_type"],
                        row["pattern_signature"],
                        target_org,
                        1 if target_org is None else 0,
                    ),
                ) as cur:
                    existing = await cur.fetchone()
                if existing:
                    sample_id = existing["id"]
                    await service.db.conn.execute(
                        "UPDATE learning_samples SET auto_apply=?, confidence=?, "
                        "source_decision_id=? WHERE id=?",
                        (
                            int(payload.auto_apply),
                            payload.confidence,
                            issue_id,
                            sample_id,
                        ),
                    )
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"learning sample upsert failed: {exc}",
                    ) from exc
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"learning sample insert failed: {exc}",
                ) from exc

        await service.db.conn.execute(
            "UPDATE parse_issues SET applied_to_learning=1, learning_sample_id=?, "
            "version=version+1 WHERE id=?",
            (sample_id, issue_id),
        )
        await service.db.conn.commit()
        async with service.db.conn.execute(
            "SELECT * FROM learning_samples WHERE id=?", (sample_id,),
        ) as cur:
            sample = await cur.fetchone()
        return _row_to_learning_sample(sample)

    @router.get(
        "/orgs/{org_id}/learning-samples",
        summary="列出账套可见的学习样本（含全局样本）",
    )
    async def list_learning_samples(
        org_id: str,
        include_global: bool = Query(default=True),
        auto_apply_only: bool = Query(default=False),
    ) -> LearningSampleListResponse:
        await service.get_org(org_id)
        clauses: list[str] = []
        args: list[Any] = []
        if include_global:
            clauses.append("(org_id=? OR org_id IS NULL)")
            args.append(org_id)
        else:
            clauses.append("org_id=?")
            args.append(org_id)
        if auto_apply_only:
            clauses.append("auto_apply=1")
        where = " AND ".join(clauses)
        async with service.db.conn.execute(
            f"SELECT * FROM learning_samples WHERE {where} "
            f"ORDER BY (org_id IS NULL) ASC, created_at DESC",
            tuple(args),
        ) as cur:
            rows = await cur.fetchall()
        samples = [_row_to_learning_sample(r) for r in rows]
        return LearningSampleListResponse(samples=samples, total=len(samples))


__all__ = [
    "register_parse_issue_endpoints",
    "run_parse_issue_detection_after_import",
]
