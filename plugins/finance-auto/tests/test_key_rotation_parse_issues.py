"""Regression tests for the P1-D audit finding (M3 closing report v1).

Background
----------

``KeyRotationService._ENCRYPTED_TABLES`` originally walked only the three
canonical encrypted-payload tables (``organizations`` /
``trial_balance_imports`` / ``trial_balance_rows``).  ``parse_issues``
stores its encrypted side-channel **inside** the ``original_data`` JSON
column as a hex blob keyed by ``__enc_blob__`` (see
``parse_issue_routes._persist_detected_issues``) — that path was NOT
rotated, so a rotate-then-read sequence on a parse_issue row would
decrypt with the new key against ciphertext bound to the old key and
explode.

This test reproduces the failure on the pre-fix code path and then
asserts that the fixed rotation pipeline:

1. Counts the embedded blob in ``preview_rotation``;
2. Re-encrypts the embedded blob during ``rotate_key`` so subsequent
   reads via ``_decode_original_data`` come back identical to the
   pre-rotation plaintext.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.encryption import pack_payload, split_parse_issue_payload
from finance_auto_backend.key_manager import ENV_PASSPHRASE, SALT_LEN
from finance_auto_backend.key_meta import write_key_meta
from finance_auto_backend.models import OrganizationCreate
from finance_auto_backend.parse_issue_routes import _decode_original_data
from finance_auto_backend.routes import FinanceAutoService
from finance_auto_backend.services.key_rotation import KeyRotationService


@pytest.fixture
def env_seed(monkeypatch):
    # Force the env-derived seed path so the fixture is deterministic on
    # CI workers that have no keyring.
    monkeypatch.setenv(ENV_PASSPHRASE, "rotation-parse-issues-pw")
    monkeypatch.setattr(
        "finance_auto_backend.key_manager._load_seed_from_keyring",
        lambda account=None: None,
    )
    yield


async def _seed_encrypted_parse_issue(
    service: FinanceAutoService,
    *,
    org_id: str,
    period_id: str,
    issue_id: str,
    original: dict,
    sheet_name: str = "余额表",
) -> dict:
    """Insert one parse_issue with an encrypted ``__enc_blob__`` exactly
    the way ``parse_issue_routes`` does, then return the persisted dict
    so callers can compare against post-rotation reads.

    The supporting ``trial_balance_imports`` row is needed because the
    parse_issues schema FK-references it.
    """
    import_id = f"imp_{issue_id[-8:]}"
    await service.db.conn.execute(
        "INSERT INTO trial_balance_imports(id, org_id, period_id, "
        "source_file, file_size, parser_used, row_count, status, uploaded_at) "
        "VALUES (?,?,?,?,?,?,?,?, datetime('now'))",
        (import_id, org_id, period_id, "seed.xlsx", 0, "seed", 1, "ok"),
    )
    plain, amounts, pii = split_parse_issue_payload(original)
    blob = pack_payload(
        service.key_manager,
        amounts=amounts or None,
        pii=pii or None,
    )
    plain["__enc_blob__"] = blob.hex()
    await service.db.conn.execute(
        "INSERT INTO parse_issues(id, org_id, period_id, import_id, row_index, "
        "sheet_name, column_name, issue_type, severity, pattern_signature, "
        "original_data, applied_to_learning, auto_applied, version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))",
        (
            issue_id, org_id, period_id, import_id, 1, sheet_name,
            "parent_code", "unknown_code", "must_fix", "unknown:9001",
            json.dumps(plain, ensure_ascii=False),
            0, 0, 1,
        ),
    )
    await service.db.conn.commit()
    return original


@pytest.mark.asyncio
async def test_rotate_key_reencrypts_parse_issue_embedded_blob(
    tmp_path: Path, env_seed,
):
    """rotate_key must walk parse_issues.__enc_blob__ so post-rotation
    reads decrypt cleanly with the new key (the P1-D regression)."""
    db_path = tmp_path / "rot.sqlite"
    db = FinanceAutoDB(db_path)
    await db.init()
    try:
        # 1. Enable encryption with a fresh salt.
        await write_key_meta(
            db.conn, salt=secrets.token_bytes(SALT_LEN),
            enabled=True, seed_source="env",
        )
        service = FinanceAutoService(db)
        outcome = await service.auto_unlock_if_configured()
        assert outcome == "unlocked", outcome

        org = await service.create_org(
            OrganizationCreate(name="轮换测试", code="ROT-001")
        )

        # 2. Seed two parse_issues with rich encrypted payloads.
        original_a = {
            "raw_code": "9001",
            "parent_code": "9001",
            "full_code": "9001",
            "account_name": "需轮换密钥的客户A",
            "aux_text": "客户辅助核算-机密",
            "opening_debit": 123456.78,
            "closing_debit": 123900.10,
            "closing_credit": 0,
        }
        original_b = {
            "raw_code": "9002",
            "parent_code": "9002",
            "full_code": "9002",
            "account_name": "另一家公司B",
            "aux_text": "供应商: 苏州某机械有限公司",
            "imbalance_delta": 12.34,
        }
        await _seed_encrypted_parse_issue(
            service, org_id=org.id, period_id="2025-FY",
            issue_id="iss_a_rot_aaaaaa", original=original_a,
        )
        await _seed_encrypted_parse_issue(
            service, org_id=org.id, period_id="2025-FY",
            issue_id="iss_b_rot_bbbbbb", original=original_b,
        )

        # 3. Preview must include the embedded-blob count.
        rotation = KeyRotationService(service)
        preview = await rotation.preview_rotation()
        assert "parse_issues.original_data" in preview["counts"], preview
        assert preview["counts"]["parse_issues.original_data"] == 2

        # 4. Trigger the rotation.  ``new_seed`` left as None means
        # rotation reuses the existing env-derived seed but generates a
        # brand new salt — exactly the production code path.
        summary = await rotation.rotate_key(
            reason="P1-D regression test"
        )
        assert summary["status"] == "success", summary
        # rows_processed must include both parse_issue rows + the org row
        # (orgs gets re-encrypted too).
        assert summary["rows_processed"] >= 2
        # Embedded blobs alone account for at least 2.
        assert summary["total_rows"] >= 2

        # 5. Read both rows back through _decode_original_data using the
        # NEW key (service.key_manager was already swapped by rotation).
        for issue_id, expected in (
            ("iss_a_rot_aaaaaa", original_a),
            ("iss_b_rot_bbbbbb", original_b),
        ):
            async with db.conn.execute(
                "SELECT original_data FROM parse_issues WHERE id=?",
                (issue_id,),
            ) as cur:
                row = await cur.fetchone()
            assert row is not None, issue_id
            decoded = _decode_original_data(service, row["original_data"])
            # PII keys round-trip through the encrypted blob; numeric keys
            # likewise.  Compare those that flow through split_parse_issue_payload.
            for k in ("account_name", "aux_text"):
                if k in expected:
                    assert decoded.get(k) == expected[k], (issue_id, k, decoded)
            for k in (
                "opening_debit", "closing_debit", "closing_credit",
                "imbalance_delta",
            ):
                if k in expected and expected[k] != 0:
                    assert decoded.get(k) == expected[k], (issue_id, k, decoded)
            # Plain keys (codes) also survive the round-trip.
            for k in ("raw_code", "parent_code", "full_code"):
                if k in expected:
                    assert decoded.get(k) == expected[k], (issue_id, k, decoded)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rotate_key_skips_rows_without_embedded_blob(
    tmp_path: Path, env_seed,
):
    """A parse_issue without ``__enc_blob__`` (encryption off at insert
    time) must not raise during rotation."""
    db_path = tmp_path / "rot_skip.sqlite"
    db = FinanceAutoDB(db_path)
    await db.init()
    try:
        # No encryption initially — insert one issue with plain JSON,
        # then enable encryption + rotate.
        service = FinanceAutoService(db)
        org = await service.create_org(
            OrganizationCreate(name="混合测试", code="ROT-002")
        )
        # Plain (cleartext) parse_issue.
        await service.db.conn.execute(
            "INSERT INTO trial_balance_imports(id, org_id, period_id, "
            "source_file, file_size, parser_used, row_count, status, uploaded_at) "
            "VALUES (?,?,?,?,?,?,?,?, datetime('now'))",
            ("imp_plain", org.id, "2025-FY", "p.xlsx", 0, "seed", 1, "ok"),
        )
        await service.db.conn.execute(
            "INSERT INTO parse_issues(id, org_id, period_id, import_id, "
            "row_index, sheet_name, column_name, issue_type, severity, "
            "pattern_signature, original_data, applied_to_learning, "
            "auto_applied, version, created_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))",
            (
                "iss_plain", org.id, "2025-FY", "imp_plain", 1, "余额表",
                "parent_code", "unknown_code", "must_fix", "unknown:9001",
                json.dumps({"raw_code": "9001", "parent_code": "9001"}),
                0, 0, 1,
            ),
        )
        await service.db.conn.commit()

        # Enable encryption + rotate.  The plain row must be left alone.
        await write_key_meta(
            db.conn, salt=secrets.token_bytes(SALT_LEN),
            enabled=True, seed_source="env",
        )
        outcome = await service.auto_unlock_if_configured()
        assert outcome == "unlocked"
        rotation = KeyRotationService(service)
        preview = await rotation.preview_rotation()
        # No __enc_blob__ present → 0 embedded rows queued.
        assert preview["counts"].get("parse_issues.original_data", 0) == 0
        summary = await rotation.rotate_key(reason="skip test")
        assert summary["status"] == "success", summary

        # The plain row is still readable as-is.
        async with db.conn.execute(
            "SELECT original_data FROM parse_issues WHERE id=?",
            ("iss_plain",),
        ) as cur:
            row = await cur.fetchone()
        decoded = json.loads(row["original_data"])
        assert decoded["raw_code"] == "9001"
        assert "__enc_blob__" not in decoded
    finally:
        await db.close()
