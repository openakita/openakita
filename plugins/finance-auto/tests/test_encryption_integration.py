"""Integration tests for the FinanceAutoService + encryption path.

Two scenarios covered:

1. ``test_service_writes_and_reads_encrypted_rows`` — when the KeyManager is
   unlocked via ``key_meta``, new rows go in as ciphertext and can be read
   back transparently through the public service API.
2. ``test_migrate_encrypt_idempotent`` — running the migration on a DB with
   pre-existing cleartext rows nulls out the cleartext columns and fills the
   ``_encrypted_payload`` BLOB, and re-running the migration is a no-op.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.key_manager import (
    ENV_PASSPHRASE,
    SALT_LEN,
)
from finance_auto_backend.key_meta import (
    GLOBAL_COMPONENT,
    read_key_meta,
    write_key_meta,
)
from finance_auto_backend.models import OrganizationCreate
from finance_auto_backend.parsers.xls_parser import ParsedRow
from finance_auto_backend.routes import FinanceAutoService


def _make_rows(import_id: str, n: int = 5) -> list[ParsedRow]:
    out = []
    for i in range(n):
        out.append(
            ParsedRow(
                row_index=i,
                raw_code=f"100{i+1}",
                parent_code=f"100{i+1}".zfill(4),
                child_code=None,
                full_code=f"100{i+1}".zfill(4),
                account_name=f"测试科目{i}",
                aux_text=f"客户{i}" if i % 2 else None,
                opening_debit=100.0 * (i + 1),
                opening_credit=0.0,
                period_debit=10.0 * (i + 1),
                period_credit=0.0,
                closing_debit=110.0 * (i + 1),
                closing_credit=0.0,
            )
        )
    return out


@pytest.fixture
def env_seed(monkeypatch):
    monkeypatch.setenv(ENV_PASSPHRASE, "integration-test-passphrase")
    monkeypatch.setattr(
        "finance_auto_backend.key_manager._load_seed_from_keyring",
        lambda account=None: None,
    )
    yield


@pytest.mark.asyncio
async def test_service_writes_and_reads_encrypted_rows(tmp_path: Path, env_seed):
    """End-to-end: enable encryption → create org → upload rows → read back."""
    db_path = tmp_path / "enc.sqlite"
    db = FinanceAutoDB(db_path)
    await db.init()
    try:
        # Enable encryption with a fresh salt + env-derived seed.
        salt = secrets.token_bytes(SALT_LEN)
        await write_key_meta(db.conn, salt=salt, enabled=True, seed_source="env")
        service = FinanceAutoService(db)
        outcome = await service.auto_unlock_if_configured()
        assert outcome == "unlocked", outcome
        assert service.encryption_enabled()

        org = await service.create_org(
            OrganizationCreate(name="加密测试公司", code="ENC-001")
        )
        await service.ensure_period(org_id=org.id, period_id="2025-FY")
        imp = await service.insert_pending_import(
            org_id=org.id,
            period_id="2025-FY",
            source_file="balance.xlsx",
            file_size=1234,
            file_sha256="abc",
        )
        rows = _make_rows(imp.id, 5)
        await service.persist_rows(
            import_id=imp.id, org_id=org.id, period_id="2025-FY", rows=rows
        )

        # ---- raw DB inspection: cleartext sensitive columns must be ''/0/NULL. ----
        # ``name`` is NOT NULL so we use an empty string sentinel; the real
        # value lives in _encrypted_payload.
        async with db.conn.execute(
            "SELECT name, _encrypted_payload FROM organizations WHERE id=?",
            (org.id,),
        ) as cur:
            row = await cur.fetchone()
        assert row["name"] == ""
        assert row["_encrypted_payload"] is not None and len(row["_encrypted_payload"]) > 32

        async with db.conn.execute(
            "SELECT account_name, opening_debit, _encrypted_payload "
            "FROM trial_balance_rows WHERE import_id=? ORDER BY row_index",
            (imp.id,),
        ) as cur:
            raw_rows = await cur.fetchall()
        for r in raw_rows:
            # account_name is nullable so the encrypted path uses NULL.
            assert r["account_name"] is None
            assert r["opening_debit"] == 0  # cleartext zeroed
            assert r["_encrypted_payload"] is not None

        # ---- public API reads must transparently decrypt. ----
        listed = await service.list_orgs()
        assert listed[0].name == "加密测试公司"

        loaded_imp = await service.get_import(org_id=org.id, import_id=imp.id)
        assert loaded_imp.source_file == "balance.xlsx"

        decoded_rows, total = await service.list_rows(
            org_id=org.id, import_id=imp.id, limit=10, offset=0
        )
        assert total == 5
        assert decoded_rows[0].account_name == "测试科目0"
        assert decoded_rows[0].opening_debit == 100.0
        assert decoded_rows[1].aux_text == "客户1"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_migrate_encrypt_idempotent(tmp_path: Path, env_seed):
    """A DB built in W1 cleartext mode survives re-encryption + re-runs."""
    db_path = tmp_path / "mig.sqlite"
    db = FinanceAutoDB(db_path)
    await db.init()
    try:
        # Phase 1 — write rows in cleartext mode (encryption stays off).
        service = FinanceAutoService(db)
        assert not service.encryption_enabled()
        org = await service.create_org(
            OrganizationCreate(name="迁移测试公司", code="MIG-001")
        )
        await service.ensure_period(org_id=org.id, period_id="2025-FY")
        imp = await service.insert_pending_import(
            org_id=org.id, period_id="2025-FY",
            source_file="b.xlsx", file_size=10, file_sha256=None,
        )
        await service.persist_rows(
            import_id=imp.id, org_id=org.id, period_id="2025-FY",
            rows=_make_rows(imp.id, 3),
        )
    finally:
        await db.close()

    # Phase 2 — run the migration script.
    from scripts.migrate_encrypt import run as migrate  # noqa: PLC0415

    summary = await migrate(db_path, org_id=None)
    assert summary["orgs_migrated"] == 1
    assert summary["imports_migrated"] == 1
    assert summary["rows_migrated"] == 3

    # Phase 3 — re-running is a no-op (everything already encrypted).
    summary2 = await migrate(db_path, org_id=None)
    assert summary2 == {
        "orgs_migrated": 0,
        "imports_migrated": 0,
        "rows_migrated": 0,
        "scope": "*",
    }

    # Phase 4 — read back through the service to confirm decryption works.
    db2 = FinanceAutoDB(db_path)
    await db2.init()
    try:
        service2 = FinanceAutoService(db2)
        outcome = await service2.auto_unlock_if_configured()
        assert outcome == "unlocked", outcome
        rows, total = await service2.list_rows(
            org_id=org.id, import_id=imp.id, limit=10, offset=0
        )
        assert total == 3
        assert rows[0].account_name == "测试科目0"
        meta = await read_key_meta(db2.conn, GLOBAL_COMPONENT)
        assert meta is not None and meta.enabled and meta.seed_source == "env"
    finally:
        await db2.close()


# Also test the migrate_encrypt path lives at the right module path.
def test_migrate_encrypt_module_importable():
    from scripts import migrate_encrypt as m  # noqa: PLC0415

    assert hasattr(m, "main")
    assert hasattr(m, "run")
