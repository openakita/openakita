"""FastAPI router for finance-auto (M1 W1 + W2).

Five W1 endpoints scoped under the plugin's reserved prefix
``/api/plugins/finance-auto`` (host PluginManager mounts the router with that
prefix automatically when the plugin calls ``api.register_api_routes``):

* ``POST /orgs``                                 — create a 账套
* ``GET  /orgs``                                 — list 账套
* ``POST /orgs/{org_id}/imports``                — upload + parse a balance file
* ``GET  /orgs/{org_id}/imports``                — list imports for an org
* ``GET  /orgs/{org_id}/imports/{import_id}/rows`` — paged read of parsed rows

W2 adds report generation, VAT declaration parsing and audit-template upload
endpoints — registered via separate ``register_*`` factories from the same
build_router function so the W1 surface stays untouched.

The router is created lazily via :func:`build_router` so a single
:class:`FinanceAutoService` instance can back both the host-loaded plugin and
the standalone end-to-end harness (``_e2e_run.py``).

W2 also wires in optional field-level AES-256-GCM encryption (see
``key_manager.py``).  Behaviour:

* If the ``key_meta`` row marks encryption enabled and the seed is reachable
  via OS keyring (or the env-var fallback), every new write goes through
  ``pack_payload`` and the cleartext columns become NULLs / 0s.
* If encryption is disabled (default for fresh DBs and any DB that hasn't
  been migrated yet) the W1 cleartext-column path is used unchanged.
* Reads transparently decrypt the BLOB when present and prefer the decrypted
  values over the cleartext columns.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from .db import FinanceAutoDB
from .encryption import (
    IMPORT_PII_FIELDS,
    ORG_DOCREF_FIELDS,
    ORG_PII_FIELDS,
    pack_payload,
    unpack_payload,
)
from .key_manager import KeyManager
from .key_meta import GLOBAL_COMPONENT, read_key_meta
from .models import (
    Account,
    AccountingPeriod,
    ImportListResponse,
    Organization,
    OrganizationCreate,
    OrgListResponse,
    RowListResponse,
    TrialBalanceImport,
    TrialBalanceRow,
    UploadResponse,
)
from .parse_issue_routes import (
    register_parse_issue_endpoints,
    run_parse_issue_detection_after_import,
)
from .parsers.xls_parser import ParsedRow, ParseResult, parse_trial_balance

logger = logging.getLogger(__name__)

# Soft cap on uploads — 64 MB covers the 36 MB worst-case sample from spike
# while preventing trivial memory blow-ups in M1 W1.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _maybe_unpack(km: KeyManager | None, blob: Any) -> dict[str, dict[str, Any]]:
    """Return a normalised ``{amounts, pii, docrefs}`` dict from a row's
    ``_encrypted_payload`` BLOB (or empty dicts if the BLOB / KeyManager is
    unusable)."""
    if km is None or not km.is_enabled() or not blob:
        return {"amounts": {}, "pii": {}, "docrefs": {}}
    try:
        return unpack_payload(km, bytes(blob))
    except Exception as exc:  # noqa: BLE001 — never break list endpoints
        logger.warning("finance-auto: encrypted payload decrypt failed: %s", exc)
        return {"amounts": {}, "pii": {}, "docrefs": {}}


def _prefer(enc_value: Any, raw_value: Any) -> Any:
    """Return ``enc_value`` if non-empty, else fall back to the cleartext.

    ``''`` and ``None`` count as sentinel values so encrypted reads always win
    over the empty-string fillers we stash in NOT NULL columns.
    """
    if enc_value is not None and enc_value != "":
        return enc_value
    return raw_value


def _has_blob(row) -> bool:
    """``aiosqlite.Row`` doesn't always implement ``__contains__`` cleanly so
    we round-trip through ``.keys()`` once and cache the boolean."""
    try:
        return "_encrypted_payload" in row.keys()  # noqa: SIM118 — Row.keys() returns a list
    except Exception:
        return False


def _row_to_organization(row, km: KeyManager | None = None) -> Organization:
    payload = _maybe_unpack(km, row["_encrypted_payload"]) if _has_blob(row) else {"pii": {}, "docrefs": {}}
    pii = payload.get("pii") or {}
    docrefs = payload.get("docrefs") or {}
    return Organization(
        id=row["id"],
        name=_prefer(pii.get("name"), row["name"]),
        code=row["code"],
        industry=row["industry"],
        standard=row["standard"],
        aux_mode=row["aux_mode"],
        erp_source=_prefer(docrefs.get("erp_source"), row["erp_source"]),
        fiscal_start=row["fiscal_start"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_import(row, km: KeyManager | None = None) -> TrialBalanceImport:
    payload = _maybe_unpack(km, row["_encrypted_payload"]) if _has_blob(row) else {"pii": {}}
    pii = payload.get("pii") or {}
    return TrialBalanceImport(
        id=row["id"],
        org_id=row["org_id"],
        period_id=row["period_id"],
        source_file=_prefer(pii.get("source_file"), row["source_file"]),
        file_size=row["file_size"],
        file_sha256=row["file_sha256"],
        parser_used=row["parser_used"],
        row_count=row["row_count"],
        status=row["status"],
        error_message=row["error_message"],
        uploaded_at=row["uploaded_at"],
        parsed_at=row["parsed_at"],
    )


def _row_to_balance_row(row, km: KeyManager | None = None) -> TrialBalanceRow:
    payload = _maybe_unpack(km, row["_encrypted_payload"]) if _has_blob(row) else {"amounts": {}, "pii": {}}
    amounts = payload.get("amounts") or {}
    pii = payload.get("pii") or {}

    def _amt(k: str) -> float:
        if k in amounts and amounts[k] is not None:
            try:
                return float(amounts[k])
            except (TypeError, ValueError):
                return 0.0
        return float(row[k] or 0.0)

    return TrialBalanceRow(
        id=row["id"],
        import_id=row["import_id"],
        org_id=row["org_id"],
        period_id=row["period_id"],
        row_index=row["row_index"],
        raw_code=row["raw_code"],
        parent_code=row["parent_code"],
        child_code=row["child_code"],
        full_code=row["full_code"],
        account_name=_prefer(pii.get("account_name"), row["account_name"]),
        aux_text=_prefer(pii.get("aux_text"), row["aux_text"]),
        opening_debit=_amt("opening_debit"),
        opening_credit=_amt("opening_credit"),
        period_debit=_amt("period_debit"),
        period_credit=_amt("period_credit"),
        closing_debit=_amt("closing_debit"),
        closing_credit=_amt("closing_credit"),
    )


class FinanceAutoService:
    """Service layer — wraps the DB and exposes the operations the routes
    need.  Kept separate from the router so unit tests can call methods
    directly without going through HTTP.

    The optional :class:`KeyManager` is shared across the lifetime of a
    service instance.  When ``key_manager.is_enabled()`` is true the writes
    go through ``pack_payload`` (cleartext columns are NULL/0); otherwise the
    W1 cleartext path is used.  Reads are unconditional (decrypt on demand).
    """

    def __init__(self, db: FinanceAutoDB, key_manager: KeyManager | None = None):
        self.db = db
        self.key_manager: KeyManager = key_manager or KeyManager()

    # Convenience: True when the manager is unlocked AND we should write
    # encrypted payloads on new inserts.
    def encryption_enabled(self) -> bool:
        return self.key_manager.is_enabled()

    async def auto_unlock_if_configured(self) -> str | None:
        """If ``key_meta.enabled`` is set, derive the master key now.

        Returns one of ``'unlocked'`` / ``'no_meta'`` / ``'meta_disabled'`` /
        ``'seed_unavailable'`` so callers can log the outcome.

        Safe to call repeatedly; idempotent when already unlocked.
        """
        from .key_manager import acquire_seed
        from .key_meta import GLOBAL_COMPONENT, read_key_meta

        meta = await read_key_meta(self.db.conn, GLOBAL_COMPONENT)
        if meta is None:
            return "no_meta"
        if not meta.enabled:
            return "meta_disabled"
        if self.key_manager.is_enabled():
            return "unlocked"
        try:
            seed, _src = acquire_seed(create_if_missing=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "finance-auto: encryption is enabled in key_meta but the seed "
                "could not be loaded (%s); falling back to cleartext columns "
                "for new writes — run finance-auto migrate-encrypt to fix.",
                exc,
            )
            return "seed_unavailable"
        self.key_manager.unlock(seed, meta.salt)
        logger.info("finance-auto: KeyManager unlocked from existing key_meta entry.")
        return "unlocked"

    # ----------------------- orgs -------------------------------------------

    async def create_org(self, payload: OrganizationCreate) -> Organization:
        org = Organization.from_create(payload)
        enc_enabled = self.encryption_enabled()
        blob: bytes | None = None
        if enc_enabled:
            pii = {k: getattr(org, k) for k in ORG_PII_FIELDS if getattr(org, k) is not None}
            docrefs = {
                k: getattr(org, k) for k in ORG_DOCREF_FIELDS if getattr(org, k) is not None
            }
            blob = pack_payload(self.key_manager, pii=pii, docrefs=docrefs)
        try:
            await self.db.conn.execute(
                "INSERT INTO organizations(id, name, code, industry, standard, aux_mode, "
                "erp_source, fiscal_start, created_at, updated_at, _encrypted_payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    org.id,
                    # name is NOT NULL — store '' sentinel when encrypted; the
                    # real value lives in _encrypted_payload.pii.name.
                    "" if enc_enabled else org.name,
                    org.code,
                    org.industry,
                    org.standard,
                    org.aux_mode,
                    None if enc_enabled else org.erp_source,
                    org.fiscal_start,
                    org.created_at,
                    org.updated_at,
                    blob,
                ),
            )
            await self.db.conn.commit()
        except Exception as exc:  # likely UNIQUE constraint on code
            msg = str(exc)
            if "UNIQUE" in msg.upper():
                raise HTTPException(
                    status_code=409,
                    detail=f"organization code already exists: {payload.code}",
                ) from exc
            raise HTTPException(status_code=500, detail=f"create_org failed: {exc}") from exc
        return org

    async def list_orgs(self) -> list[Organization]:
        async with self.db.conn.execute(
            "SELECT * FROM organizations ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_organization(r, self.key_manager) for r in rows]

    async def get_org(self, org_id: str) -> Organization:
        async with self.db.conn.execute(
            "SELECT * FROM organizations WHERE id = ?", (org_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"org not found: {org_id}")
        return _row_to_organization(row, self.key_manager)

    # ----------------------- accounting periods (helper) --------------------

    async def ensure_period(self, *, org_id: str, period_id: str) -> AccountingPeriod:
        async with self.db.conn.execute(
            "SELECT * FROM accounting_periods WHERE org_id=? AND period_id=?",
            (org_id, period_id),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            return AccountingPeriod(
                id=row["id"],
                org_id=row["org_id"],
                period_id=row["period_id"],
                period_kind=row["period_kind"],
                start_date=row["start_date"],
                end_date=row["end_date"],
                is_closed=bool(row["is_closed"]),
                created_at=row["created_at"],
            )
        period = AccountingPeriod.new(org_id=org_id, period_id=period_id)
        await self.db.conn.execute(
            "INSERT INTO accounting_periods(id, org_id, period_id, period_kind, "
            "start_date, end_date, is_closed, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                period.id,
                period.org_id,
                period.period_id,
                period.period_kind,
                period.start_date,
                period.end_date,
                int(period.is_closed),
                period.created_at,
            ),
        )
        await self.db.conn.commit()
        return period

    # ----------------------- imports + rows ---------------------------------

    async def insert_pending_import(
        self,
        *,
        org_id: str,
        period_id: str,
        source_file: str,
        file_size: int,
        file_sha256: str | None,
    ) -> TrialBalanceImport:
        imp = TrialBalanceImport.pending(
            org_id=org_id,
            period_id=period_id,
            source_file=source_file,
            file_size=file_size,
            file_sha256=file_sha256,
        )
        enc_enabled = self.encryption_enabled()
        blob: bytes | None = None
        if enc_enabled:
            pii = {k: getattr(imp, k) for k in IMPORT_PII_FIELDS if getattr(imp, k)}
            blob = pack_payload(self.key_manager, pii=pii)
        await self.db.conn.execute(
            "INSERT INTO trial_balance_imports(id, org_id, period_id, source_file, file_size, "
            "file_sha256, parser_used, row_count, status, error_message, uploaded_at, parsed_at, "
            "_encrypted_payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                imp.id,
                imp.org_id,
                imp.period_id,
                # source_file is NOT NULL.  Empty sentinel when encrypted.
                "" if enc_enabled else imp.source_file,
                imp.file_size,
                imp.file_sha256,
                imp.parser_used,
                imp.row_count,
                imp.status,
                imp.error_message,
                imp.uploaded_at,
                imp.parsed_at,
                blob,
            ),
        )
        await self.db.conn.commit()
        return imp

    async def finalise_import(
        self,
        *,
        import_id: str,
        parser_used: str,
        row_count: int,
        status: str,
        error_message: str | None,
    ) -> None:
        await self.db.conn.execute(
            "UPDATE trial_balance_imports SET parser_used=?, row_count=?, status=?, "
            "error_message=?, parsed_at=? WHERE id=?",
            (parser_used, row_count, status, error_message, _utcnow_iso(), import_id),
        )
        await self.db.conn.commit()

    async def persist_rows(
        self,
        *,
        import_id: str,
        org_id: str,
        period_id: str,
        rows: list[ParsedRow],
    ) -> None:
        if not rows:
            return
        enc_enabled = self.encryption_enabled()

        def _row_params(r: ParsedRow) -> tuple:
            blob: bytes | None = None
            if enc_enabled:
                amounts = {
                    "opening_debit": r.opening_debit,
                    "opening_credit": r.opening_credit,
                    "period_debit": r.period_debit,
                    "period_credit": r.period_credit,
                    "closing_debit": r.closing_debit,
                    "closing_credit": r.closing_credit,
                }
                pii: dict[str, object] = {}
                if r.account_name:
                    pii["account_name"] = r.account_name
                if r.aux_text:
                    pii["aux_text"] = r.aux_text
                blob = pack_payload(
                    self.key_manager, amounts=amounts, pii=pii or None
                )
            return (
                f"row_{import_id}_{r.row_index}",
                import_id,
                org_id,
                period_id,
                r.row_index,
                r.raw_code,
                r.parent_code,
                r.child_code,
                r.full_code,
                None if enc_enabled else r.account_name,
                None if enc_enabled else r.aux_text,
                0.0 if enc_enabled else r.opening_debit,
                0.0 if enc_enabled else r.opening_credit,
                0.0 if enc_enabled else r.period_debit,
                0.0 if enc_enabled else r.period_credit,
                0.0 if enc_enabled else r.closing_debit,
                0.0 if enc_enabled else r.closing_credit,
                blob,
            )

        params = [_row_params(r) for r in rows]
        await self.db.conn.executemany(
            "INSERT INTO trial_balance_rows(id, import_id, org_id, period_id, row_index, "
            "raw_code, parent_code, child_code, full_code, account_name, aux_text, "
            "opening_debit, opening_credit, period_debit, period_credit, "
            "closing_debit, closing_credit, _encrypted_payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            params,
        )
        # Also seed / refresh ``accounts`` rows so account lookups work later.
        # Use UPSERT semantics (REPLACE on conflict against (org_id, full_code)).
        seen: dict[str, ParsedRow] = {}
        for r in rows:
            if r.full_code and r.full_code not in seen:
                seen[r.full_code] = r
        if seen:
            acc_params = []
            for full_code, r in seen.items():
                acc = Account.new(
                    org_id=org_id,
                    parent_code=r.parent_code,
                    child_code=r.child_code,
                    name=(r.account_name or "(未命名)"),
                )
                acc_params.append(
                    (
                        acc.id,
                        acc.org_id,
                        acc.parent_code,
                        acc.child_code,
                        full_code,
                        acc.name,
                        acc.balance_side,
                        acc.category,
                        int(acc.is_active),
                        acc.created_at,
                    )
                )
            await self.db.conn.executemany(
                "INSERT INTO accounts(id, org_id, parent_code, child_code, full_code, "
                "name, balance_side, category, is_active, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(org_id, full_code) DO UPDATE SET name=excluded.name",
                acc_params,
            )
        await self.db.conn.commit()

    async def list_imports(self, org_id: str) -> list[TrialBalanceImport]:
        async with self.db.conn.execute(
            "SELECT * FROM trial_balance_imports WHERE org_id=? "
            "ORDER BY uploaded_at DESC",
            (org_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_import(r, self.key_manager) for r in rows]

    async def get_import(self, *, org_id: str, import_id: str) -> TrialBalanceImport:
        async with self.db.conn.execute(
            "SELECT * FROM trial_balance_imports WHERE org_id=? AND id=?",
            (org_id, import_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="import not found")
        return _row_to_import(row, self.key_manager)

    async def list_all_rows(
        self, *, org_id: str, import_id: str
    ) -> list[TrialBalanceRow]:
        """Return every row of an import.  Used by the report generator
        (Stage 4) to feed the full balance set into the rule engine without
        paginating."""
        await self.get_import(org_id=org_id, import_id=import_id)
        async with self.db.conn.execute(
            "SELECT * FROM trial_balance_rows WHERE import_id=? "
            "ORDER BY row_index ASC",
            (import_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_balance_row(r, self.key_manager) for r in rows]

    async def list_rows(
        self,
        *,
        org_id: str,
        import_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[TrialBalanceRow], int]:
        # First validate ownership (otherwise a wrong org_id silently returns []).
        await self.get_import(org_id=org_id, import_id=import_id)
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM trial_balance_rows WHERE import_id=?", (import_id,)
        ) as cur:
            total_row = await cur.fetchone()
            total = total_row[0] if total_row else 0
        async with self.db.conn.execute(
            "SELECT * FROM trial_balance_rows WHERE import_id=? "
            "ORDER BY row_index ASC LIMIT ? OFFSET ?",
            (import_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_balance_row(r, self.key_manager) for r in rows], total


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_router(service: FinanceAutoService) -> APIRouter:
    router = APIRouter(tags=["finance-auto"])

    @router.get("/health", summary="finance-auto 健康检查")
    async def health() -> dict:
        meta = await read_key_meta(service.db.conn, GLOBAL_COMPONENT) if service.db.is_ready() else None
        return {
            "ok": service.db.is_ready(),
            "schema_path": str(service.db.path),
            "journal_mode": await service.db.journal_mode(),
            "encryption": {
                "enabled": service.encryption_enabled(),
                "meta_enabled": bool(meta and meta.enabled),
                "seed_source": meta.seed_source if meta else None,
            },
        }

    @router.post(
        "/orgs",
        status_code=201,
        summary="创建账套 (Organization)",
    )
    async def create_org(payload: OrganizationCreate) -> Organization:
        return await service.create_org(payload)

    @router.get(
        "/orgs",
        summary="列出账套",
    )
    async def list_orgs() -> OrgListResponse:
        rows = await service.list_orgs()
        return OrgListResponse(organizations=rows, total=len(rows))

    @router.post(
        "/orgs/{org_id}/imports",
        status_code=201,
        summary="上传 + 解析余额表",
    )
    async def upload_import(
        org_id: str,
        file: UploadFile = File(..., description="余额表 .xls / .xlsx"),
        period_id: str = Form(..., description="会计期间 ID，如 2025-FY"),
    ) -> UploadResponse:
        # 1. Validate org exists
        await service.get_org(org_id)

        if not file.filename:
            raise HTTPException(status_code=400, detail="file has no filename")

        # 2. Persist upload to a temp file (the parser needs a Path).
        raw_bytes = await file.read()
        if len(raw_bytes) == 0:
            raise HTTPException(status_code=400, detail="empty upload")
        if len(raw_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"upload too large: {len(raw_bytes)} bytes > "
                    f"{MAX_UPLOAD_BYTES} bytes"
                ),
            )

        sha = hashlib.sha256(raw_bytes).hexdigest()
        suffix = Path(file.filename).suffix.lower() or ".xlsx"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="finauto_upload_")
        try:
            import os as _os

            with _os.fdopen(fd, "wb") as f:
                f.write(raw_bytes)

            # 3. Header row in DB so we can attach status later.
            await service.ensure_period(org_id=org_id, period_id=period_id)
            imp = await service.insert_pending_import(
                org_id=org_id,
                period_id=period_id,
                source_file=file.filename,
                file_size=len(raw_bytes),
                file_sha256=sha,
            )

            # 4. Run the three-tier parser.
            try:
                result: ParseResult = parse_trial_balance(tmp_path)
            except Exception as exc:  # parser totally failed
                await service.finalise_import(
                    import_id=imp.id,
                    parser_used="",
                    row_count=0,
                    status="failed",
                    error_message=str(exc)[:500],
                )
                logger.exception("finance-auto: parse failed for %s", file.filename)
                raise HTTPException(
                    status_code=422,
                    detail=f"parse failed: {type(exc).__name__}: {exc}",
                ) from exc

            # 5. Persist the rows + update the import header.
            await service.persist_rows(
                import_id=imp.id,
                org_id=org_id,
                period_id=period_id,
                rows=result.rows,
            )
            await service.finalise_import(
                import_id=imp.id,
                parser_used=result.parser_used,
                row_count=len(result.rows),
                status="ok",
                error_message=None,
            )

            # 6. (W3 Stage 1) Run the 6-class parse-issue detector and
            # auto-apply any learning samples that match.  Failures here
            # must not break the upload — degrade gracefully to 0 counts.
            issue_summary = {"detected": 0, "must_fix": 0, "auto_applied": 0}
            try:
                issue_summary = await run_parse_issue_detection_after_import(
                    service,
                    org_id=org_id,
                    period_id=period_id,
                    import_id=imp.id,
                    rows=result.rows,
                    sheet_name=result.sheet_name or "余额表",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "finance-auto: parse-issue detection failed for import %s: %s",
                    imp.id, exc,
                )

            return UploadResponse(
                import_id=imp.id,
                row_count=len(result.rows),
                parser_used=result.parser_used,
                status="ok",
                error_message=None,
                parse_issues_detected=issue_summary.get("detected", 0),
                parse_issues_must_fix=issue_summary.get("must_fix", 0),
                parse_issues_auto_applied=issue_summary.get("auto_applied", 0),
            )
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

    @router.get(
        "/orgs/{org_id}/imports",
        summary="列出某账套的导入记录",
    )
    async def list_imports(org_id: str) -> ImportListResponse:
        await service.get_org(org_id)
        imps = await service.list_imports(org_id)
        return ImportListResponse(imports=imps, total=len(imps))

    @router.get(
        "/orgs/{org_id}/imports/{import_id}/rows",
        summary="分页查询解析后的余额表行",
    )
    async def list_import_rows(
        org_id: str,
        import_id: str,
        limit: int = Query(default=20, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> RowListResponse:
        rows, total = await service.list_rows(
            org_id=org_id,
            import_id=import_id,
            limit=limit,
            offset=offset,
        )
        return RowListResponse(rows=rows, total=total, limit=limit, offset=offset)

    # M1 W2 Stage 4 / 5 / 6 + W3 Stage 1 -- attach the optional endpoint
    # families onto the same router.  Kept in separate modules so this file
    # stays small.
    from .audit_routes import register_audit_endpoints
    from .cross_period_routes import register_cross_period_endpoints
    from .industry_routes import register_industry_endpoints
    from .manual_input_routes import register_manual_input_endpoints
    from .report_routes import register_report_endpoints
    from .vat_routes import register_vat_endpoints

    register_report_endpoints(router, service)
    register_vat_endpoints(router, service)
    register_audit_endpoints(router, service)
    register_parse_issue_endpoints(router, service)
    register_cross_period_endpoints(router, service)
    register_manual_input_endpoints(router, service)
    register_industry_endpoints(router, service)

    # M2 AI endpoints (Stage 3+ -- consent dialog channel, scenario admin,
    # consent listing, audit-log).  WebSocket lives at /ws under the same
    # plugin prefix; REST endpoints land under /ai/.  Wired last so the
    # `/health` and W1/W2/W3 surface keep their numerical ordering.
    from .ai.routes import register_ai_endpoints
    from .ai.ws import register_ws_endpoint
    register_ws_endpoint(router)
    register_ai_endpoints(router, service)

    return router


# ---------------------------------------------------------------------------
# Convenience: build router + service in one shot (used by both plugin.py
# and the standalone e2e harness).
# ---------------------------------------------------------------------------


def build_router_and_service(db_path: Path | str) -> tuple[APIRouter, FinanceAutoService, FinanceAutoDB]:
    db = FinanceAutoDB(db_path)
    service = FinanceAutoService(db)
    router = build_router(service)
    return router, service, db


# Re-export JSONResponse so plugin.py can fall back on a structured error.
__all__ = [
    "FinanceAutoService",
    "MAX_UPLOAD_BYTES",
    "build_router",
    "build_router_and_service",
    "JSONResponse",
]
