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
import mimetypes
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from .db import FinanceAutoDB
from .encryption import (
    IMPORT_PII_FIELDS,
    ORG_DOCREF_FIELDS,
    ORG_PII_FIELDS,
    DecryptionError,
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


def _maybe_unpack(
    km: KeyManager | None,
    blob: Any,
    *,
    accept_corrupted: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return a normalised ``{amounts, pii, docrefs}`` dict from a row's
    ``_encrypted_payload`` BLOB.

    EX-P2-6 (extended audit §4.2): decryption failures are no longer
    silently swallowed.  Any error inside :func:`unpack_payload` now
    bubbles up as :class:`encryption.DecryptionError` unless the
    caller explicitly opts in to corruption-tolerant reads (typically
    via the route-level ``?accept_corrupted=true`` query string).
    The opt-in branch still logs at WARNING so the audit trail
    records the silent fallback.
    """
    if km is None or not km.is_enabled() or not blob:
        return {"amounts": {}, "pii": {}, "docrefs": {}}
    try:
        return unpack_payload(km, bytes(blob))
    except Exception as exc:  # noqa: BLE001 — re-wrap into DecryptionError
        if accept_corrupted:
            logger.warning(
                "finance-auto: encrypted payload decrypt failed "
                "(accept_corrupted=true): %s",
                exc,
            )
            return {"amounts": {}, "pii": {}, "docrefs": {}}
        logger.error(
            "finance-auto: encrypted payload decrypt failed: %s", exc
        )
        raise DecryptionError(
            f"encrypted payload decrypt failed: {type(exc).__name__}: {exc}"
        ) from exc


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


def _row_to_organization(
    row,
    km: KeyManager | None = None,
    *,
    accept_corrupted: bool = False,
) -> Organization:
    payload = (
        _maybe_unpack(
            km, row["_encrypted_payload"], accept_corrupted=accept_corrupted
        )
        if _has_blob(row)
        else {"pii": {}, "docrefs": {}}
    )
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


def _row_to_import(
    row,
    km: KeyManager | None = None,
    *,
    accept_corrupted: bool = False,
) -> TrialBalanceImport:
    payload = (
        _maybe_unpack(
            km, row["_encrypted_payload"], accept_corrupted=accept_corrupted
        )
        if _has_blob(row)
        else {"pii": {}}
    )
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


def _row_to_balance_row(
    row,
    km: KeyManager | None = None,
    *,
    accept_corrupted: bool = False,
) -> TrialBalanceRow:
    payload = (
        _maybe_unpack(
            km, row["_encrypted_payload"], accept_corrupted=accept_corrupted
        )
        if _has_blob(row)
        else {"amounts": {}, "pii": {}}
    )
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
        # PluginAPI handle set by ``plugin.py`` on load. The host wires its
        # Brain (LLM layer) *after* plugins finish loading, so we resolve the
        # brain lazily (per request) rather than capturing it at load time.
        self.plugin_api: Any | None = None

    def get_host_brain(self) -> Any | None:
        """Resolve the host Brain on demand (needs ``brain.access``).

        Returns ``None`` when the permission is absent or the host has not
        wired a brain yet, in which case the AI scenarios fall back to the
        offline :class:`MockLLMResponder`.
        """
        api = self.plugin_api
        if api is None:
            return None
        try:
            return api.get_brain()
        except Exception:  # noqa: BLE001 — brain.access may be absent
            return None

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

    async def list_orgs(
        self, *, accept_corrupted: bool = False
    ) -> list[Organization]:
        async with self.db.conn.execute(
            "SELECT * FROM organizations ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            _row_to_organization(
                r, self.key_manager, accept_corrupted=accept_corrupted
            )
            for r in rows
        ]

    async def get_org(
        self, org_id: str, *, accept_corrupted: bool = False
    ) -> Organization:
        async with self.db.conn.execute(
            "SELECT * FROM organizations WHERE id = ?", (org_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"org not found: {org_id}")
        return _row_to_organization(
            row, self.key_manager, accept_corrupted=accept_corrupted
        )

    # ----------------------- org delete (EX-P2-10) --------------------------

    # Tables that physically hold data scoped to an org but whose org_id
    # column has no FK CASCADE constraint (so SQLite's PRAGMA foreign_keys
    # cascade does not reach them).  Explicit DELETEs needed.
    _NON_FK_ORG_TABLES: tuple[str, ...] = (
        "learning_samples",
        "llm_call_audit",
        "reclassification_history",
        "backup_history",
    )

    # Tables whose org_id column carries an ON DELETE CASCADE constraint —
    # used by ``_count_org_dependents`` to refuse a non-cascade delete when
    # any of them carry rows.  Order has no semantic meaning.
    _FK_ORG_TABLES: tuple[str, ...] = (
        "accounting_periods",
        "accounts",
        "trial_balance_imports",
        "reports",
        "vat_declarations",
        "parse_issues",
        "cross_period_check_results",
        "manual_inputs",
        "assignments",
        "review_workflows",
        "comments",
        "reclassification_rules",
        "reclassification_runs",
        "note_documents",
        "peer_comparison_results",
    )

    async def _count_org_dependents(self, org_id: str) -> dict[str, int]:
        """Per-table row count of records linked to ``org_id``.

        Returns a dict keyed by table name; only tables with a non-zero
        count are included.  Used by the DELETE /orgs handler to refuse
        ``cascade=false`` deletion when the org still has data.
        """
        counts: dict[str, int] = {}
        for table in self._FK_ORG_TABLES + self._NON_FK_ORG_TABLES:
            # SQL identifiers can't be parameterised; table names are
            # whitelisted via the class-level tuples so injection is not
            # possible.
            async with self.db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE org_id = ?",
                (org_id,),
            ) as cur:
                row = await cur.fetchone()
            n = int(row[0]) if row else 0
            if n > 0:
                counts[table] = n
        # consolidation_groups uses parent_org_id, not org_id; check it
        # separately so the diagnostic stays useful.
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM consolidation_groups WHERE parent_org_id = ?",
            (org_id,),
        ) as cur:
            row = await cur.fetchone()
        n = int(row[0]) if row else 0
        if n > 0:
            counts["consolidation_groups"] = n
        # consolidation_members uses subsidiary_org_id.
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM consolidation_members WHERE subsidiary_org_id = ?",
            (org_id,),
        ) as cur:
            row = await cur.fetchone()
        n = int(row[0]) if row else 0
        if n > 0:
            counts["consolidation_members"] = n
        return counts

    async def _list_org_backup_paths(self, org_id: str) -> list[str]:
        """Return on-disk backup file paths registered against ``org_id``."""
        async with self.db.conn.execute(
            "SELECT backup_path FROM backup_history "
            "WHERE org_id = ? AND backup_path IS NOT NULL",
            (org_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [str(r[0]) for r in rows if r[0]]

    async def delete_org(
        self,
        org_id: str,
        *,
        cascade: bool = False,
        actor_id: str = "local",
    ) -> dict[str, Any]:
        """Delete an org (with optional cascade across all dependent tables).

        Behaviour:

        * ``cascade=False`` (default, safe): if any dependent row exists in
          the FK-cascade tables OR the non-FK ``org_id`` tables, raises
          ``HTTPException(409, {"error": "org_not_empty", "dependents": {...}})``.
        * ``cascade=True``: explicit DELETEs first against the non-FK
          tables (``learning_samples`` / ``llm_call_audit`` /
          ``reclassification_history`` / ``backup_history``), then ``DELETE
          FROM organizations`` which fires SQLite ON DELETE CASCADE for the
          remaining 17 FK-linked tables in a single statement.  Backup
          files referenced by ``backup_history.backup_path`` are unlinked
          best-effort (errors logged, never re-raised).

        Returns a structured summary so the caller can audit-log the
        operation: ``{"deleted": True, "cascade": <bool>, "tables_purged":
        {table: rows}, "backup_files_removed": <int>, "org_id": ...}``.

        EX-P2-10 (v1.0.0-rc1):
        * Caller-side authorisation is handled by
          ``Depends(require_permission("org", "delete"))`` at the route
          layer; the service method trusts ``actor_id``.
        * Uses an explicit ``BEGIN``/``COMMIT``/``ROLLBACK`` envelope so a
          mid-cascade failure doesn't leave the org partially purged.
        """
        # 0. Verify the org exists (404 if not).
        await self.get_org(org_id, accept_corrupted=True)

        # 1. If not cascading, refuse when any dependent row exists.
        if not cascade:
            counts = await self._count_org_dependents(org_id)
            if counts:
                total = sum(counts.values())
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "org_not_empty",
                        "org_id": org_id,
                        "total_dependents": total,
                        "dependents": counts,
                        "hint": (
                            "pass ?cascade=true to delete the org and all "
                            "linked rows; this action is irreversible."
                        ),
                    },
                )

        # 2. Collect on-disk backup file paths *before* the row goes away.
        backup_paths = await self._list_org_backup_paths(org_id)

        # 3. Cascade-delete inside one transaction.  SQLite's foreign-key
        # cascade handles the 17 FK-linked tables; we manually purge the
        # 4 + 2 non-FK ones plus the FK-via-different-column ones.
        tables_purged: dict[str, int] = {}
        conn = self.db.conn
        try:
            await conn.execute("BEGIN")
            for table in self._NON_FK_ORG_TABLES:
                async with conn.execute(
                    f"DELETE FROM {table} WHERE org_id = ?",
                    (org_id,),
                ) as cur:
                    tables_purged[table] = cur.rowcount or 0
            # consolidation_groups + consolidation_members use a different
            # column name; FK CASCADE still fires, but we record the count
            # for the audit trail so the caller knows what disappeared.
            async with conn.execute(
                "SELECT COUNT(*) FROM consolidation_groups WHERE parent_org_id = ?",
                (org_id,),
            ) as cur:
                row = await cur.fetchone()
                tables_purged["consolidation_groups"] = int(row[0]) if row else 0
            async with conn.execute(
                "SELECT COUNT(*) FROM consolidation_members WHERE subsidiary_org_id = ?",
                (org_id,),
            ) as cur:
                row = await cur.fetchone()
                tables_purged["consolidation_members"] = int(row[0]) if row else 0
            # Per-FK-table pre-count so the audit summary is complete.
            for table in self._FK_ORG_TABLES:
                async with conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE org_id = ?",
                    (org_id,),
                ) as cur:
                    row = await cur.fetchone()
                    tables_purged[table] = int(row[0]) if row else 0
            # The final blow: SQLite ON DELETE CASCADE handles the FK fan-out.
            async with conn.execute(
                "DELETE FROM organizations WHERE id = ?",
                (org_id,),
            ) as cur:
                org_rows_deleted = cur.rowcount or 0
            await conn.commit()
        except Exception as exc:
            try:
                await conn.rollback()
            except Exception:  # noqa: BLE001 — best-effort rollback
                pass
            logger.exception(
                "finance-auto: delete_org failed for org_id=%s actor=%s",
                org_id, actor_id,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "delete_org_failed",
                    "message": str(exc),
                },
            ) from exc

        # 4. Unlink on-disk backup artefacts (best-effort).
        removed = 0
        for raw_path in backup_paths:
            try:
                p = Path(raw_path)
                if p.exists():
                    p.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning(
                    "finance-auto: failed to unlink backup file %s for "
                    "deleted org %s: %s",
                    raw_path, org_id, exc,
                )

        logger.info(
            "finance-auto: deleted org %s by actor=%s cascade=%s "
            "purged_rows=%d backup_files_removed=%d",
            org_id, actor_id, cascade,
            sum(v for v in tables_purged.values() if v),
            removed,
        )
        return {
            "deleted": True,
            "org_id": org_id,
            "cascade": cascade,
            "actor_id": actor_id,
            "org_rows_deleted": org_rows_deleted,
            "tables_purged": {k: v for k, v in tables_purged.items() if v},
            "backup_files_removed": removed,
        }

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

    async def list_imports(
        self, org_id: str, *, accept_corrupted: bool = False
    ) -> list[TrialBalanceImport]:
        async with self.db.conn.execute(
            "SELECT * FROM trial_balance_imports WHERE org_id=? "
            "ORDER BY uploaded_at DESC",
            (org_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            _row_to_import(r, self.key_manager, accept_corrupted=accept_corrupted)
            for r in rows
        ]

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
        self,
        *,
        org_id: str,
        import_id: str,
        accept_corrupted: bool = False,
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
        return [
            _row_to_balance_row(
                r, self.key_manager, accept_corrupted=accept_corrupted
            )
            for r in rows
        ]

    async def list_rows(
        self,
        *,
        org_id: str,
        import_id: str,
        limit: int,
        offset: int,
        accept_corrupted: bool = False,
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
        return [
            _row_to_balance_row(
                r, self.key_manager, accept_corrupted=accept_corrupted
            )
            for r in rows
        ], total


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def _build_v1_router(service: FinanceAutoService) -> APIRouter:
    """Build the actual finance-auto API router (the ``v1`` surface).

    This is the single source of truth for every plugin endpoint.  The
    public ``build_router`` wrapper below mounts this under ``/v1`` and
    adds the legacy 308 catch-all so existing clients keep working.

    EX-P2-13 (v1.0.0-rc1): the introduction of the ``/v1/`` namespace is
    a forward-compat preparation for the eventual ``/v2`` schema break.
    No semantic change in this commit — every endpoint still resolves
    via 308 from the legacy paths.
    """
    router = APIRouter(tags=["finance-auto"])
    # EX-P1-2: bind the service onto every incoming request so the
    # RBAC dependency factory (``rbac.require_permission``) can find
    # it via ``request.state.finance_auto_service``.  Pure side-
    # effect on Request, no impact on existing handlers.
    from .rbac import attach_service_for_rbac, require_permission
    attach_service_for_rbac(router, service)

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
    async def list_orgs(
        accept_corrupted: bool = Query(
            default=False,
            description=(
                "EX-P2-6 灾难恢复：明确接受损坏密文（默认 false 时静默"
                "fallback 已禁用，解密失败抛 500/decrypt_failed）"
            ),
        ),
    ) -> OrgListResponse:
        try:
            rows = await service.list_orgs(accept_corrupted=accept_corrupted)
        except DecryptionError as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": "decrypt_failed", "message": str(exc)},
            ) from exc
        return OrgListResponse(organizations=rows, total=len(rows))

    @router.delete(
        "/orgs/{org_id}",
        status_code=200,
        summary="删除账套（admin / partner，?cascade=true 才级联）",
    )
    async def delete_org(
        org_id: str,
        cascade: bool = Query(
            default=False,
            description=(
                "EX-P2-10 v1.0.0-rc1：默认 false，账套若有任何业务数据则 "
                "返回 409 + 各表残留行数；true 时显式级联删 17 FK 表 + 4 个 "
                "无 FK 的 org_id 表 + 备份文件，不可恢复。"
            ),
        ),
        actor_id: str = Depends(require_permission("org", "delete")),
    ) -> dict:
        # The dependency above raises 403/404 before we get here when the
        # caller lacks org.delete.  Returning the structured summary lets
        # the host log the destructive action.
        result = await service.delete_org(
            org_id, cascade=cascade, actor_id=actor_id,
        )
        return result

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
    async def list_imports(
        org_id: str,
        accept_corrupted: bool = Query(default=False),
    ) -> ImportListResponse:
        await service.get_org(org_id)
        try:
            imps = await service.list_imports(
                org_id, accept_corrupted=accept_corrupted
            )
        except DecryptionError as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": "decrypt_failed", "message": str(exc)},
            ) from exc
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
        accept_corrupted: bool = Query(default=False),
    ) -> RowListResponse:
        try:
            rows, total = await service.list_rows(
                org_id=org_id,
                import_id=import_id,
                limit=limit,
                offset=offset,
                accept_corrupted=accept_corrupted,
            )
        except DecryptionError as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": "decrypt_failed", "message": str(exc)},
            ) from exc
        return RowListResponse(rows=rows, total=total, limit=limit, offset=offset)

    # M1 W2 Stage 4 / 5 / 6 + W3 Stage 1 + M2 Biz Stage 2 / 3 / 6 -- attach
    # the optional endpoint families onto the same router.  Kept in separate
    # modules so this file stays small.
    from .audit_routes import register_audit_endpoints
    from .collab_routes import register_collab_endpoints
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
    # M2 Biz endpoints (Stage 2 collaboration / review workflow).  Stage 3
    # reclassification + Stage 6 consolidation endpoints attach themselves
    # once their modules ship (see commits 3 and 6); the try/except blocks
    # let this routes.py file land before those endpoint families exist.
    register_collab_endpoints(router, service)
    try:  # Stage 3 (reclassification).
        from .reclassification_routes import register_reclassification_endpoints
        register_reclassification_endpoints(router, service)
    except ImportError:
        pass
    try:  # Stage 4 (indirect cash-flow engine).
        from .cash_flow_routes import register_cash_flow_endpoints
        register_cash_flow_endpoints(router, service)
    except ImportError:
        pass
    try:  # Stage 6 (consolidation).
        from .consolidation_routes import register_consolidation_endpoints
        register_consolidation_endpoints(router, service)
    except ImportError:
        pass

    # M2 AI endpoints (Stage 3+ -- consent dialog channel, scenario admin,
    # consent listing, audit-log).  WebSocket lives at /ws under the same
    # plugin prefix; REST endpoints land under /ai/.  Wired last so the
    # `/health` and W1/W2/W3 surface keep their numerical ordering.
    from .ai.routes import register_ai_endpoints
    from .ai.ws import register_ws_endpoint
    register_ws_endpoint(router)
    register_ai_endpoints(router, service)

    # M3 raw AI (S6/S7/S11) — 4 new endpoints + lazy seed of the 3
    # new ai_scenarios rows.  Wired after the M2 admin surface so the
    # GET /ai/scenarios route still returns the full registry while
    # the /ai/raw/* family only exposes the new entries.
    from .ai.raw_routes import register_raw_ai_endpoints
    from .ai.scenarios.raw_notes_draft import attach_event_bus_subscriber
    register_raw_ai_endpoints(router, service)
    attach_event_bus_subscriber(service)

    # M3 Biz endpoints (Stage 3 + Stage 5 of Sibling A): report notes
    # auto-generation + peer comparison.  Notes go in first because the
    # generator subscriber attached above relies on the report_notes
    # table introduced in schema v10; the peer module wire-up below uses
    # an ImportError guard so this stage can land before peer_routes.py
    # exists.
    from .notes_routes import register_notes_endpoints
    register_notes_endpoints(router, service)
    try:
        from .peer_routes import register_peer_endpoints
        register_peer_endpoints(router, service)
    except ImportError:
        pass

    # M3 Infra Stage 4 — admin surface for key rotation + encrypted
    # backup/restore + system info.  Wired last so the /admin/* family
    # never shadows the W1/W2/W3/M2/M3 user-facing endpoints.
    from .infra_routes import register_infra_endpoints
    register_infra_endpoints(router, service)

    return router


# ---------------------------------------------------------------------------
# EX-P2-13: legacy ``/api/plugins/finance-auto/<path>`` → 308 redirect to
# ``/api/plugins/finance-auto/v1/<path>`` so existing UI bundles and any
# pinned-version downstream tooling keep working without code changes.
# ---------------------------------------------------------------------------


# Plugin manager mounts the router at this prefix, so the redirect target
# must include it.  Kept as a constant so a host-level rename surfaces in
# one place.
PLUGIN_MOUNT_PREFIX = "/api/plugins/finance-auto"

# Path segments that should NOT be redirected — they are either:
#   * already the v1 surface (``v1/...``)
#   * the WebSocket endpoint, which cannot accept an HTTP 308 (clients
#     must reconnect to the new URL; we keep both ``/ws`` and ``/v1/ws``
#     mounted so existing browser bundles keep streaming until they ship
#     the path bump).
_LEGACY_REDIRECT_EXEMPT_PREFIXES: tuple[str, ...] = ("v1/", "v1", "ws", "ws/")


# Plugin UI bundle lives at ``<plugin_root>/ui/dist`` (this file sits at
# ``<plugin_root>/finance_auto_backend/routes.py``).
_UI_DIST_DIR = Path(__file__).resolve().parents[1] / "ui" / "dist"


def _attach_ui_static(outer: APIRouter) -> None:
    """Serve the plugin UI bundle from inside the plugin router.

    The host mounts ``ui/dist`` at ``/api/plugins/finance-auto/ui`` too, but
    that mount is registered *after* this router (``server.py`` flushes
    pending routers before pending UI mounts). Since the catch-all
    ``/{legacy_path:path}`` below is greedy, a host-level mount registered
    later never gets reached — ``/ui/`` matched the catch-all and 308'd to
    ``/v1/ui/`` (→ 404 ``{"detail":"not_found"}``). Registering explicit UI
    routes here, *before* the catch-all, guarantees ``/ui/...`` resolves to
    the static bundle regardless of host mount ordering.
    """
    if not _UI_DIST_DIR.is_dir():
        return

    root = _UI_DIST_DIR.resolve()

    def _resolve(rel: str) -> Path:
        rel = (rel or "").lstrip("/")
        target = (root / rel).resolve() if rel else (root / "index.html")
        if target != root and root not in target.parents:
            # Path traversal attempt — refuse and fall back to the SPA entry.
            return root / "index.html"
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            target = root / "index.html"
        return target

    @outer.get("/ui", include_in_schema=False)
    @outer.get("/ui/", include_in_schema=False)
    @outer.get("/ui/{ui_path:path}", include_in_schema=False)
    async def serve_ui(ui_path: str = "") -> FileResponse:
        target = _resolve(ui_path)
        media_type, _ = mimetypes.guess_type(str(target))
        headers = {"Cache-Control": "no-cache, no-store, must-revalidate"}
        return FileResponse(
            str(target),
            media_type=media_type or "application/octet-stream",
            headers=headers,
        )


def _attach_legacy_redirects(outer: APIRouter, service: FinanceAutoService) -> None:
    """Mount a catch-all on ``outer`` that 308-redirects every non-v1
    HTTP path to the corresponding ``/v1/`` URL.

    Preserves the query string and the request body (308 — unlike 301/302
    — guarantees the client replays the same method + body to the new
    target, which is what we need for POST/PUT/DELETE).
    """

    @outer.api_route(
        "/{legacy_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
        name="finance_auto_legacy_v1_redirect",
    )
    async def legacy_redirect(legacy_path: str, request: Request) -> RedirectResponse:
        # Exempt: v1/* and ws — see _LEGACY_REDIRECT_EXEMPT_PREFIXES.
        normalised = legacy_path.lstrip("/")
        for skip in _LEGACY_REDIRECT_EXEMPT_PREFIXES:
            if normalised == skip.rstrip("/") or normalised.startswith(skip):
                # Bubble up as 404 so FastAPI's normal "path not found"
                # response surfaces; we never want to redirect onto
                # ourselves and create an infinite loop.
                raise HTTPException(status_code=404, detail="not_found")
        target = f"{PLUGIN_MOUNT_PREFIX}/v1/{normalised}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=308)


def build_router(service: FinanceAutoService) -> APIRouter:
    """Public router factory used by ``plugin.py`` + the e2e harness.

    Layout (post-EX-P2-13):

    * ``/v1/...`` — the real endpoint surface (every route defined by
      ``_build_v1_router``).
    * ``/ws`` + ``/v1/ws`` — duplicated WebSocket registration so the UI
      bundle's existing ``/ws`` URL keeps streaming during the v1.0.x
      transition window.
    * ``/{anything-else}`` — 308 redirect to ``/v1/{anything-else}``,
      preserving query string + method + body.

    The wrapper is intentionally additive: the legacy paths still resolve
    (via 308) so no client breakage; new clients (and the v1.x UI rev)
    should target ``/v1/`` directly to save the round-trip.
    """
    outer = APIRouter(tags=["finance-auto"])
    # The outer router needs the same RBAC service binding so the
    # legacy redirect doesn't blow up before the redirect fires.
    from .rbac import attach_service_for_rbac
    attach_service_for_rbac(outer, service)

    # Real surface — every endpoint lives here.
    v1 = _build_v1_router(service)
    outer.include_router(v1, prefix="/v1")

    # WebSocket dual-mount: ``/ws`` (legacy) + ``/v1/ws`` (current).  The
    # v1 router already carries ``/ws`` (registered by
    # ``register_ws_endpoint`` inside ``_build_v1_router``); include_router
    # remounted it at ``/v1/ws``.  We also expose the legacy ``/ws`` at
    # the outer level so the existing UI bundle keeps streaming until
    # task 5 step 6 ships the URL switch.
    from .ai.ws import register_ws_endpoint
    register_ws_endpoint(outer)

    # Serve the UI bundle *before* the catch-all so ``/ui/...`` is not
    # swallowed by the legacy 308 redirect (see _attach_ui_static docstring).
    _attach_ui_static(outer)

    # Catch-all 308 redirect for legacy HTTP paths.
    _attach_legacy_redirects(outer, service)

    return outer


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
