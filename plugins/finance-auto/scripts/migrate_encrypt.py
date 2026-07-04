"""``finance-auto migrate-encrypt`` — re-encrypt cleartext rows to BLOB form.

Usage::

    d:\\OpenAkita\\.venv\\Scripts\\python.exe ^
        d:\\OpenAkita\\plugins\\finance-auto\\scripts\\migrate_encrypt.py ^
        --db d:\\OpenAkita\\tmp_finance_auto_e2e.sqlite

Behaviour
---------

1. If ``key_meta.global`` is missing, generate a fresh 32-byte salt + 32-byte
   seed (latter persisted in the OS keyring), unlock the KeyManager, and
   write ``key_meta(enabled=1)``.
2. Walk every row of ``organizations`` / ``trial_balance_imports`` /
   ``trial_balance_rows`` whose ``_encrypted_payload`` is NULL, build the
   per-row ``{amounts, pii, docrefs}`` dict from the cleartext columns, and
   write the encrypted blob back.
3. Null out the cleartext columns for migrated rows so the DB on disk holds
   only ciphertext for sensitive fields (queryable plaintext columns —
   ``parent_code`` / ``period_id`` / etc. — are kept untouched).

Idempotent: running it twice is a no-op (already-encrypted rows are skipped).
Use ``--org-id <id>`` to limit the scope to a single 账套 (otherwise all rows
in the DB are migrated).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
import sys
from collections.abc import Iterable
from pathlib import Path

# Ensure the plugin dir is importable when run as a script.
_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from finance_auto_backend.db import FinanceAutoDB  # noqa: E402
from finance_auto_backend.encryption import (  # noqa: E402
    IMPORT_PII_FIELDS,
    ORG_DOCREF_FIELDS,
    ORG_PII_FIELDS,
    pack_payload,
)
from finance_auto_backend.key_manager import (  # noqa: E402
    SALT_LEN,
    KeyManager,
    acquire_seed,
)
from finance_auto_backend.key_meta import (  # noqa: E402
    GLOBAL_COMPONENT,
    read_key_meta,
    write_key_meta,
)

logger = logging.getLogger("finance-auto.migrate-encrypt")


async def _migrate_orgs(
    db: FinanceAutoDB, km: KeyManager, *, org_id: str | None
) -> int:
    """Encrypt cleartext columns into ``_encrypted_payload`` for org rows."""
    sql = "SELECT * FROM organizations WHERE _encrypted_payload IS NULL"
    params: tuple = ()
    if org_id is not None:
        sql += " AND id = ?"
        params = (org_id,)
    async with db.conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    if not rows:
        return 0
    for row in rows:
        pii = {k: row[k] for k in ORG_PII_FIELDS if row[k]}
        docrefs = {k: row[k] for k in ORG_DOCREF_FIELDS if row[k]}
        blob = pack_payload(km, pii=pii, docrefs=docrefs)
        # ``name`` is NOT NULL, so we keep an empty-string sentinel and rely
        # on the read path to overlay the encrypted value.
        await db.conn.execute(
            "UPDATE organizations SET _encrypted_payload=?, name='', "
            "erp_source=NULL WHERE id=?",
            (blob, row["id"]),
        )
    await db.conn.commit()
    return len(rows)


async def _migrate_imports(
    db: FinanceAutoDB, km: KeyManager, *, org_id: str | None
) -> int:
    sql = "SELECT * FROM trial_balance_imports WHERE _encrypted_payload IS NULL"
    params: tuple = ()
    if org_id is not None:
        sql += " AND org_id = ?"
        params = (org_id,)
    async with db.conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    if not rows:
        return 0
    for row in rows:
        pii = {k: row[k] for k in IMPORT_PII_FIELDS if row[k]}
        blob = pack_payload(km, pii=pii)
        # ``source_file`` is NOT NULL, use '' sentinel.
        await db.conn.execute(
            "UPDATE trial_balance_imports SET _encrypted_payload=?, "
            "source_file='' WHERE id=?",
            (blob, row["id"]),
        )
    await db.conn.commit()
    return len(rows)


async def _migrate_rows(
    db: FinanceAutoDB, km: KeyManager, *, org_id: str | None
) -> int:
    sql = "SELECT * FROM trial_balance_rows WHERE _encrypted_payload IS NULL"
    params: tuple = ()
    if org_id is not None:
        sql += " AND org_id = ?"
        params = (org_id,)
    async with db.conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    if not rows:
        return 0
    BATCH = 500
    migrated = 0
    pending: list[tuple] = []
    for row in rows:
        amounts = {
            "opening_debit": row["opening_debit"] or 0.0,
            "opening_credit": row["opening_credit"] or 0.0,
            "period_debit": row["period_debit"] or 0.0,
            "period_credit": row["period_credit"] or 0.0,
            "closing_debit": row["closing_debit"] or 0.0,
            "closing_credit": row["closing_credit"] or 0.0,
        }
        pii: dict[str, object] = {}
        if row["account_name"]:
            pii["account_name"] = row["account_name"]
        if row["aux_text"]:
            pii["aux_text"] = row["aux_text"]
        blob = pack_payload(km, amounts=amounts, pii=pii or None)
        pending.append((blob, row["id"]))
        if len(pending) >= BATCH:
            await db.conn.executemany(
                "UPDATE trial_balance_rows SET _encrypted_payload=?, "
                "account_name=NULL, aux_text=NULL, "
                "opening_debit=0, opening_credit=0, period_debit=0, "
                "period_credit=0, closing_debit=0, closing_credit=0 "
                "WHERE id=?",
                pending,
            )
            migrated += len(pending)
            pending = []
    if pending:
        await db.conn.executemany(
            "UPDATE trial_balance_rows SET _encrypted_payload=?, "
            "account_name=NULL, aux_text=NULL, "
            "opening_debit=0, opening_credit=0, period_debit=0, "
            "period_credit=0, closing_debit=0, closing_credit=0 "
            "WHERE id=?",
            pending,
        )
        migrated += len(pending)
    await db.conn.commit()
    return migrated


async def run(db_path: Path, org_id: str | None) -> dict[str, int | str]:
    db = FinanceAutoDB(db_path)
    await db.init()
    try:
        meta = await read_key_meta(db.conn, GLOBAL_COMPONENT)
        if meta is None or not meta.enabled:
            seed, src = acquire_seed()
            salt = meta.salt if meta and meta.salt else secrets.token_bytes(SALT_LEN)
            km = KeyManager()
            km.unlock(seed, salt)
            await write_key_meta(db.conn, salt=salt, enabled=True, seed_source=src)
            logger.info(
                "finance-auto: key_meta.global persisted (seed_source=%s, salt_hex=%s)",
                src,
                salt.hex()[:16] + "…",
            )
        else:
            seed, _src = acquire_seed(create_if_missing=False)
            km = KeyManager()
            km.unlock(seed, meta.salt)
            logger.info("finance-auto: KeyManager unlocked from existing key_meta.")

        n_org = await _migrate_orgs(db, km, org_id=org_id)
        n_imp = await _migrate_imports(db, km, org_id=org_id)
        n_row = await _migrate_rows(db, km, org_id=org_id)
        return {
            "orgs_migrated": n_org,
            "imports_migrated": n_imp,
            "rows_migrated": n_row,
            "scope": org_id or "*",
        }
    finally:
        await db.close()


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="finance-auto migrate-encrypt")
    parser.add_argument("--db", required=True, help="path to the finance-auto sqlite file")
    parser.add_argument(
        "--org-id",
        default=None,
        help="optional 账套 id; defaults to migrating every row in the DB",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    ns = _parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if ns.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db_path = Path(ns.db).resolve()
    if not db_path.exists():
        print(f"ERR: db not found: {db_path}", file=sys.stderr)
        return 2
    summary = asyncio.run(run(db_path, ns.org_id))
    print("finance-auto migrate-encrypt summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
