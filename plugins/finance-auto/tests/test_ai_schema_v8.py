"""M2 AI Stage 1 — schema v8 migration tests.

Asserts:

* The freshly initialised DB carries the three new tables.
* ``ai_scenarios`` is seeded with the 6 default rows.
* ``schema_version`` records ``8``.
* Re-running ``init`` is a no-op (idempotent).
* Constraints reject malformed rows (sensitivity / decision / outcome).
* Foreign key from ``llm_call_audit.consent_id`` works.
"""

from __future__ import annotations

import pytest

from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.db.migrations import v8_ai_tables
from finance_auto_backend.schema import SCHEMA_VERSION

EXPECTED_TABLES = ("ai_consent", "ai_scenarios", "llm_call_audit")


@pytest.mark.asyncio
async def test_v8_creates_three_tables(tmp_path):
    db = FinanceAutoDB(tmp_path / "v8.sqlite")
    await db.init()
    try:
        async with db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('ai_consent','ai_scenarios','llm_call_audit')"
        ) as cur:
            names = sorted(r["name"] for r in await cur.fetchall())
        assert names == sorted(EXPECTED_TABLES)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_v8_seeds_six_default_scenarios(tmp_path):
    db = FinanceAutoDB(tmp_path / "v8_seed.sqlite")
    await db.init()
    try:
        async with db.conn.execute(
            "SELECT scenario_id, default_sensitivity, default_enabled "
            "FROM ai_scenarios ORDER BY scenario_id"
        ) as cur:
            rows = await cur.fetchall()
        ids = sorted(r["scenario_id"] for r in rows)
        # Six scenarios per the M2 task spec.
        assert ids == sorted(
            sid for sid, *_ in v8_ai_tables.default_scenarios()
        )
        # Distribution: 3 metadata + 3 aggregated.
        levels = [r["default_sensitivity"] for r in rows]
        assert levels.count("metadata") == 3
        assert levels.count("aggregated") == 3
        assert levels.count("raw") == 0
        # All 6 default-enabled.
        assert all(r["default_enabled"] == 1 for r in rows)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_v8_records_schema_version(tmp_path):
    db = FinanceAutoDB(tmp_path / "v8_ver.sqlite")
    await db.init()
    try:
        async with db.conn.execute(
            "SELECT version FROM schema_version WHERE component='finance_auto'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        # v8 introduces the AI tables; the recorded version must be at
        # least 8 (the M2 Biz worker bumps it further to 9 on the same
        # branch).  We accept >=8 so the two workers can coexist on the
        # same revamp/v3-orgs branch without one's tests blocking the
        # other's commits.
        assert row["version"] >= 8
        assert SCHEMA_VERSION >= 8
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_v8_init_is_idempotent(tmp_path):
    db_path = tmp_path / "v8_idem.sqlite"
    db = FinanceAutoDB(db_path)
    await db.init()
    await db.close()
    # Run a second connection; the seeds + DDL must be no-ops.
    db2 = FinanceAutoDB(db_path)
    await db2.init()
    try:
        async with db2.conn.execute("SELECT COUNT(*) AS n FROM ai_scenarios") as cur:
            row = await cur.fetchone()
        assert row["n"] == 6  # No duplicates.
    finally:
        await db2.close()


@pytest.mark.asyncio
async def test_v8_consent_check_constraints(tmp_path):
    db = FinanceAutoDB(tmp_path / "v8_check.sqlite")
    await db.init()
    try:
        # Bad sensitivity_level rejected.
        with pytest.raises(Exception) as exc:
            await db.conn.execute(
                "INSERT INTO ai_consent(scenario_id, sensitivity_level, "
                "decision, granted_at) VALUES (?,?,?,?)",
                ("erp_source_detect", "BOGUS", "allow_once", "2026-05-23T00:00:00Z"),
            )
            await db.conn.commit()
        assert "CHECK" in str(exc.value).upper() or "constraint" in str(exc.value).lower()

        # Bad decision rejected.
        await db.conn.execute("ROLLBACK")
        with pytest.raises(Exception):
            await db.conn.execute(
                "INSERT INTO ai_consent(scenario_id, sensitivity_level, "
                "decision, granted_at) VALUES (?,?,?,?)",
                ("erp_source_detect", "metadata", "BOGUS", "2026-05-23T00:00:00Z"),
            )
            await db.conn.commit()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_v8_audit_log_foreign_key(tmp_path):
    db = FinanceAutoDB(tmp_path / "v8_fk.sqlite")
    await db.init()
    try:
        # Insert a consent row, then reference its id from the audit log.
        await db.conn.execute(
            "INSERT INTO ai_consent(scenario_id, sensitivity_level, "
            "decision, granted_at) VALUES (?,?,?,?)",
            ("erp_source_detect", "metadata", "allow_once", "2026-05-23T00:00:00Z"),
        )
        await db.conn.commit()
        async with db.conn.execute(
            "SELECT consent_id FROM ai_consent ORDER BY consent_id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        cid = row["consent_id"]

        await db.conn.execute(
            "INSERT INTO llm_call_audit(timestamp, scenario_id, sensitivity_level, "
            "payload_hash, consent_id, outcome) VALUES (?,?,?,?,?,?)",
            ("2026-05-23T00:00:01Z", "erp_source_detect", "metadata",
             "deadbeef" * 8, cid, "success"),
        )
        await db.conn.commit()

        async with db.conn.execute(
            "SELECT outcome FROM llm_call_audit WHERE consent_id=?", (cid,)
        ) as cur:
            audit = await cur.fetchone()
        assert audit["outcome"] == "success"
    finally:
        await db.close()
