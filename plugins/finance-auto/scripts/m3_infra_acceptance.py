"""End-to-end acceptance script for the M3 Infra deliverables.

Runs the full ``/admin/*`` surface against an in-process FastAPI app
backed by a fresh SQLite file plus a temp backup directory.  No real
KMS / keyring is required — the script seeds ``key_meta.global`` via
direct SQL and pre-loads ``OPENAKITA_FINANCE_AUTO_PASSPHRASE`` into
``os.environ`` so ``acquire_seed`` finds a deterministic seed.

18 verification checks
----------------------

01. schema_version=11 after build_router_and_service.
02. Routes count delta ≥ baseline + 11 (4 key + 6 backup + 1 sysinfo).
03. ``GET /admin/key-versions`` returns ≥ 0 entries.
04. ``GET /admin/key-rotation-preview`` returns counts dict.
05. Encryption seeded: write a ``key_meta.global`` row with a fresh
    32-byte salt + ``enabled=1`` and unlock the live KeyManager.
06. Insert a sample org + import + trial_balance rows under the
    encrypted KeyManager so there's something to rotate.
07. ``POST /admin/key-rotate`` body ``{reason:'test'}`` → 200; a new
    ``key_versions`` row with ``key_version=2`` exists and the previous
    row is ``retired``.
08. After rotation the sample row still decrypts cleanly.
09. Force a rotation failure: monkey-patch ``KeyManager.encrypt`` to
    raise; rotation must rollback, ``key_meta`` still points at the
    pre-failure salt.
10. ``POST /admin/backups`` with passphrase → file created, sha256
    set, size > 256 bytes.
11. ``GET /admin/backups`` lists the backup.
12. ``POST /admin/backups/{id}/restore`` with wrong passphrase →
    ``{ok:false, verified:false}`` and no DB write.
13. ``POST /admin/backups/{id}/restore`` with correct passphrase +
    ``dry_run=True`` → manifest + ``verified=True``.
14. ``POST /admin/backups/{id}/restore`` with correct passphrase +
    temp target path → restored DB readable + schema_version matches.
15. ``DELETE /admin/backups/{id}`` → status='deleted'.
16. ``GET /admin/system-info`` returns dict with schema_version=11.
17. Tauri wiring static check: ``finance.rs`` contains the 4 expected
    ``#[tauri::command]`` functions; ``main.rs`` mounts each into
    ``invoke_handler!``.
18. Regression: run ``m2_closing_acceptance --skip-regression`` (and
    Sibling A/B acceptance scripts when present) as subprocesses;
    each must exit 0.  Use ``--skip-regression`` on this script to
    skip step 18.

Usage::

    d:\\OpenAkita\\.venv\\Scripts\\python.exe ^
        plugins/finance-auto/scripts/m3_infra_acceptance.py ^
        [--json <path>] [--skip-regression] [--keep]

Exit code 0 iff every step succeeded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Ensure the seed for the global KeyManager is deterministic for this run.
# Doing this BEFORE importing finance_auto_backend means the keyring path
# is short-circuited via the env-var fallback even on a worker box.
os.environ.setdefault(
    "OPENAKITA_FINANCE_AUTO_PASSPHRASE",
    secrets.token_bytes(32).hex(),
)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from finance_auto_backend.encryption import pack_payload  # noqa: E402
from finance_auto_backend.key_manager import (  # noqa: E402
    PBKDF2_ITERATIONS,
    SALT_LEN,
    KeyManager,
    acquire_seed,
)
from finance_auto_backend.key_meta import (  # noqa: E402
    GLOBAL_COMPONENT,
    read_key_meta,
    write_key_meta,
)
from finance_auto_backend.routes import build_router_and_service  # noqa: E402
from finance_auto_backend.schema import SCHEMA_VERSION  # noqa: E402

BASE = "/api/plugins/finance-auto"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _checkpoint(name: str, started: float, ok: bool, **extras) -> dict:
    out = {
        "step": name,
        "ok": ok,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        **extras,
    }
    flag = "OK" if ok else "FAIL"
    print(f"[{flag}] {name}  elapsed={out['elapsed_ms']}ms", flush=True)
    return out


def _trace(msg: str) -> None:
    print(f"... {msg}", flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _enable_encryption(service) -> bytes:
    """Seed key_meta.global + unlock the live KeyManager."""
    salt = secrets.token_bytes(SALT_LEN)
    await write_key_meta(
        service.db.conn,
        salt=salt,
        enabled=True,
        seed_source="env",
        component=GLOBAL_COMPONENT,
        kdf_iterations=PBKDF2_ITERATIONS,
    )
    seed, _src = acquire_seed(create_if_missing=False)
    service.key_manager.lock()
    service.key_manager.unlock(seed, salt)
    return salt


async def _seed_sample_rows(service) -> dict[str, str]:
    """Insert one org + one import + 2 trial_balance rows with encrypted
    payloads so the rotation walk has something to do."""
    from finance_auto_backend.models import OrganizationCreate

    org = await service.create_org(
        OrganizationCreate(name="M3 Infra 验收公司", code="ACC_M3_INFRA")
    )
    await service.ensure_period(org_id=org.id, period_id="2025-FY")
    imp = await service.insert_pending_import(
        org_id=org.id,
        period_id="2025-FY",
        source_file="m3_infra_sample.xlsx",
        file_size=4096,
        file_sha256="a" * 64,
    )
    km = service.key_manager
    for idx, code in enumerate(("1001", "1002"), start=1):
        amounts = {
            "opening_debit": 1000.0 * idx,
            "opening_credit": 0.0,
            "period_debit": 200.0 * idx,
            "period_credit": 50.0 * idx,
            "closing_debit": 1150.0 * idx,
            "closing_credit": 0.0,
        }
        pii = {"account_name": f"测试科目{idx}", "aux_text": f"aux-{code}"}
        blob = pack_payload(km, amounts=amounts, pii=pii)
        await service.db.conn.execute(
            "INSERT INTO trial_balance_rows("
            "id, import_id, org_id, period_id, row_index, raw_code, "
            "parent_code, child_code, full_code, account_name, aux_text, "
            "opening_debit, opening_credit, period_debit, period_credit, "
            "closing_debit, closing_credit, _encrypted_payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"row_{imp.id}_{idx}",
                imp.id,
                org.id,
                "2025-FY",
                idx,
                code,
                code,
                None,
                code,
                None,
                None,
                0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                blob,
            ),
        )
    await service.db.conn.commit()
    return {"org_id": org.id, "import_id": imp.id}


async def _decrypt_first_row(service) -> dict:
    from finance_auto_backend.encryption import unpack_payload

    async with service.db.conn.execute(
        "SELECT id, _encrypted_payload FROM trial_balance_rows "
        "WHERE _encrypted_payload IS NOT NULL ORDER BY row_index ASC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None, "no encrypted row to decrypt"
    return unpack_payload(service.key_manager, bytes(row["_encrypted_payload"]))


async def _read_global_salt(service) -> bytes | None:
    meta = await read_key_meta(service.db.conn, GLOBAL_COMPONENT)
    return meta.salt if meta else None


def _check_tauri_wiring(repo_root: Path) -> dict:
    """Static-check the Rust files committed in Stage 5.

    No ``rustc`` / ``cargo`` is invoked — per the M3 Infra worker brief,
    Rust compilation is out of scope here.  Instead we look for the
    expected ``#[tauri::command]`` declarations in ``finance.rs`` plus
    the ``mod finance;`` + ``invoke_handler!`` entries in ``main.rs``.
    """
    finance_rs = repo_root / "apps/setup-center/src-tauri/src/finance.rs"
    main_rs = repo_root / "apps/setup-center/src-tauri/src/main.rs"
    if not finance_rs.exists():
        return {"ok": False, "error": f"missing {finance_rs}"}
    if not main_rs.exists():
        return {"ok": False, "error": f"missing {main_rs}"}
    finance_text = finance_rs.read_text(encoding="utf-8")
    main_text = main_rs.read_text(encoding="utf-8")
    expected_cmds = [
        "show_finance_consent_dialog",
        "finance_system_info",
        "finance_show_notification",
        "finance_pick_save_path",
    ]
    cmd_attr = re.compile(r"#\[tauri::command\]\s+(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")
    declared = set(cmd_attr.findall(finance_text))
    missing_decl = [c for c in expected_cmds if c not in declared]
    if "mod finance;" not in main_text:
        return {
            "ok": False,
            "error": "main.rs missing 'mod finance;'",
            "declared": sorted(declared),
        }
    missing_wires = [
        c for c in expected_cmds if f"finance::{c}" not in main_text
    ]
    ok = not missing_decl and not missing_wires
    return {
        "ok": ok,
        "declared": sorted(declared),
        "missing_decl": missing_decl,
        "missing_wires": missing_wires,
    }


# Patterns that indicate a sibling acceptance script reached its "all
# green" terminal print.  We stream stdout, kill the subprocess as soon
# as one of these matches, and treat the run as success.  The Windows
# aiosqlite shutdown path is known to hang after the script's logic
# completes (the asyncio loop is closed while the connection worker
# thread is still queueing callbacks), so polling for the success
# marker is the only way to keep this acceptance under a minute.
_SUCCESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^OK\s+steps_ok=\d+/\d+"),                # m2_closing,
                                                          # m3_notes_peer
    re.compile(r"acceptance —\s*SUCCESS\b"),              # m3_raw_ai
    re.compile(r"acceptance — SUCCESS\b"),
    re.compile(r"^M\w+\s+acceptance\s+—\s+SUCCESS\b"),
)
_FAILURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^FAIL\s+steps_ok="),
    re.compile(r"acceptance —\s*FAIL\b"),
    re.compile(r"^FAIL\b"),
)


def _run_subprocess_acceptance(script_path: Path, label: str) -> dict:
    """Stream stdout from a sibling acceptance script and detect success.

    Windows + aiosqlite hangs on the asyncio loop shutdown after the
    script body completes, so a plain ``subprocess.run`` blocks
    indefinitely.  We work around that by reading stdout line by line
    until either a success / failure marker fires, then terminate.
    """
    # ``-u`` forces the child Python's stdout / stderr into unbuffered
    # mode so our line iterator sees the success-marker line *as soon as
    # the child prints it*, instead of waiting for the block buffer to
    # flush on process exit (which never happens on Windows because of
    # the aiosqlite asyncio-loop shutdown deadlock).
    cmd = [sys.executable, "-u", str(script_path), "--skip-regression"]
    started = time.perf_counter()
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(REPO_ROOT),
        env=env,
    )
    lines: list[str] = []
    outcome: str | None = None
    deadline = time.time() + 120  # generous per-script budget
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            stripped = line.rstrip()
            if outcome is None:
                for pat in _SUCCESS_PATTERNS:
                    if pat.search(stripped):
                        outcome = "success"
                        break
            if outcome is None:
                for pat in _FAILURE_PATTERNS:
                    if pat.search(stripped):
                        outcome = "failure"
                        break
            if outcome is not None:
                break
            if time.time() > deadline:
                outcome = "timeout"
                break
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    tail = "".join(lines[-25:])
    if outcome == "success":
        return {
            "label": label,
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "stdout_tail": tail[-600:],
            "detected": "success-marker",
        }
    if outcome == "failure":
        return {
            "label": label,
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "stdout_tail": tail[-600:],
            "detected": "failure-marker",
        }
    if outcome == "timeout":
        return {
            "label": label,
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "error": "no marker within budget",
            "stdout_tail": tail[-600:],
        }
    # Pipe closed without any marker — treat as failure unless exit_code 0.
    rc = proc.poll()
    return {
        "label": label,
        "ok": rc == 0,
        "exit_code": rc,
        "elapsed_ms": elapsed_ms,
        "stdout_tail": tail[-600:],
        "detected": "stream-end",
    }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    work = Path(tempfile.mkdtemp(prefix="m3_infra_"))
    db_path = work / "m3_infra.sqlite"
    backup_dir = work / "backups"
    backup_dir.mkdir()
    # fix-round-3 EX-P1-1: the backup service now sandboxes
    # ``dest_dir`` against an allowed root.  Point the env var at our
    # tmp directory so the acceptance script can write its synthetic
    # backups without escaping the sandbox.
    import os as _os
    _os.environ["OPENAKITA_FINANCE_AUTO_BACKUP_ROOT"] = str(work)

    results: list[dict] = []
    failures: list[str] = []
    routes_baseline = 0
    routes_total = 0

    try:
        _trace("building router + service")
        router, service, db = build_router_and_service(db_path)
        routes_total = len(router.routes)
        # baseline = total minus the 11 admin routes we just added.
        routes_baseline = routes_total - sum(
            1
            for rt in router.routes
            if "/admin/" in getattr(rt, "path", "")
        )

        app = FastAPI()
        app.include_router(router, prefix=BASE)
        _trace("initialising DB schema")
        asyncio.run(db.init())
        client = TestClient(app)

        # 01 — schema_version >= 11 --------------------------------
        # fix-round-3 EX-P1-2 + EX-P2-9 bumped SCHEMA_VERSION to 13.
        # The infra acceptance only requires v11+ to satisfy the M3
        # Infra Stage 1 contract (key versioning + rotation + backup
        # ledger).
        t = time.perf_counter()
        # additive schema bumps (v11 → v13) — newer M3+ migrations are backward-compatible
        assert SCHEMA_VERSION >= 11, SCHEMA_VERSION
        results.append(_checkpoint(
            "01_schema_v11", t, True, schema_version=SCHEMA_VERSION
        ))

        # 02 — route count delta -----------------------------------
        t = time.perf_counter()
        admin_routes = [
            rt for rt in router.routes
            if "/admin/" in getattr(rt, "path", "")
        ]
        delta = len(admin_routes)
        assert delta >= 11, f"expected >=11 admin routes, got {delta}"
        results.append(_checkpoint(
            "02_route_delta", t, True,
            total=routes_total, baseline=routes_baseline,
            admin_delta=delta,
        ))

        # 03 — list key versions (initially empty) -----------------
        t = time.perf_counter()
        r = client.get(f"{BASE}/admin/key-versions")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "versions" in body
        results.append(_checkpoint(
            "03_list_key_versions", t, True, total=body["total"]
        ))

        # 04 — preview rotation ------------------------------------
        t = time.perf_counter()
        r = client.get(f"{BASE}/admin/key-rotation-preview")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "counts" in body, body
        results.append(_checkpoint(
            "04_rotation_preview", t, True,
            encryption_enabled=body.get("encryption_enabled"),
            total_rows=body.get("total_rows"),
        ))

        # 05 — enable encryption -----------------------------------
        t = time.perf_counter()
        initial_salt = asyncio.run(_enable_encryption(service))
        meta = asyncio.run(read_key_meta(service.db.conn, GLOBAL_COMPONENT))
        assert meta is not None and meta.enabled
        results.append(_checkpoint(
            "05_enable_encryption", t, True,
            seed_source=meta.seed_source,
            salt_len=len(meta.salt),
        ))

        # 06 — seed encrypted rows ---------------------------------
        t = time.perf_counter()
        seeded = asyncio.run(_seed_sample_rows(service))
        results.append(_checkpoint(
            "06_seed_encrypted_rows", t, True, **seeded
        ))

        # 07 — rotate key + version bookkeeping --------------------
        t = time.perf_counter()
        r = client.post(
            f"{BASE}/admin/key-rotate",
            json={"reason": "M3 infra acceptance — happy path"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success", body
        assert body["to_version"] == 2, body
        # Verify list_versions now has 2 active/retired rows.
        rl = client.get(f"{BASE}/admin/key-versions")
        rl_body = rl.json()
        versions = rl_body["versions"]
        assert any(
            v["key_version"] == 2 and v["status"] == "active" for v in versions
        ), versions
        assert any(
            v["key_version"] == 1 and v["status"] == "retired" for v in versions
        ), versions
        new_salt = asyncio.run(_read_global_salt(service))
        assert new_salt is not None and new_salt != initial_salt, (
            "key_meta.global.salt did not change after rotation"
        )
        results.append(_checkpoint(
            "07_rotate_v1_to_v2", t, True,
            from_version=body["from_version"],
            to_version=body["to_version"],
            rows_processed=body["rows_processed"],
        ))

        # 08 — decrypt still works under new key -------------------
        t = time.perf_counter()
        payload = asyncio.run(_decrypt_first_row(service))
        assert payload["amounts"], payload
        assert payload["pii"].get("account_name", "").startswith("测试科目"), payload
        results.append(_checkpoint(
            "08_round_trip_after_rotation", t, True,
            amounts_keys=sorted(payload["amounts"].keys()),
        ))

        # 09 — forced failure rollback ----------------------------
        t = time.perf_counter()
        pre_failure_salt = asyncio.run(_read_global_salt(service))
        original_encrypt = KeyManager.encrypt

        # Monkey-patch encrypt to raise on the first call against a
        # *non-canary* plaintext.  Canary encryption (b"canary") fires
        # during rotate_key setup; we let that succeed and then break
        # the row-rewrite walk.
        call_state = {"canary_seen": False}

        def _flaky_encrypt(self, domain, plaintext):
            if plaintext == b"canary" and not call_state["canary_seen"]:
                call_state["canary_seen"] = True
                return original_encrypt(self, domain, plaintext)
            raise RuntimeError("injected failure for rotation rollback test")

        KeyManager.encrypt = _flaky_encrypt
        try:
            r = client.post(
                f"{BASE}/admin/key-rotate",
                json={"reason": "M3 infra acceptance — forced rollback"},
            )
        finally:
            KeyManager.encrypt = original_encrypt
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "failed", body
        post_failure_salt = asyncio.run(_read_global_salt(service))
        assert post_failure_salt == pre_failure_salt, (
            "key_meta.global.salt changed despite rotation failure!"
        )
        # The post-failure key_versions row should be retired (not active).
        rl = client.get(f"{BASE}/admin/key-versions").json()["versions"]
        active_versions = [v["key_version"] for v in rl if v["status"] == "active"]
        assert active_versions == [2], (
            f"expected only v2 active after rollback, got {active_versions}"
        )
        # Re-unlock the live KeyManager with the (unchanged) salt so the
        # rest of the checks still operate on a usable encryption state.
        seed, _src = acquire_seed(create_if_missing=False)
        service.key_manager.lock()
        service.key_manager.unlock(seed, post_failure_salt)
        # Sanity: decryption still works after the rollback.
        payload_after = asyncio.run(_decrypt_first_row(service))
        assert payload_after["pii"].get("account_name", "").startswith(
            "测试科目"
        ), payload_after
        results.append(_checkpoint(
            "09_rotation_rollback", t, True,
            failed=True,
            salt_preserved=True,
        ))

        # 10 — create backup --------------------------------------
        t = time.perf_counter()
        passphrase = "correct horse battery staple"
        r = client.post(
            f"{BASE}/admin/backups",
            json={
                "passphrase": passphrase,
                "dest_dir": str(backup_dir),
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        backup_id = body["id"]
        backup_path = Path(body["backup_path"])
        assert backup_path.exists(), backup_path
        size = backup_path.stat().st_size
        assert size > 256, f"backup file too small: {size}"
        assert body["sha256"] and len(body["sha256"]) == 64, body
        results.append(_checkpoint(
            "10_create_backup", t, True,
            id=backup_id, size_bytes=size, sha256_prefix=body["sha256"][:12],
        ))

        # 11 — list backups ---------------------------------------
        t = time.perf_counter()
        r = client.get(f"{BASE}/admin/backups")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] >= 1, body
        assert any(b["id"] == backup_id for b in body["backups"]), body
        results.append(_checkpoint(
            "11_list_backups", t, True, total=body["total"]
        ))

        # 12 — restore wrong passphrase ---------------------------
        t = time.perf_counter()
        r = client.post(
            f"{BASE}/admin/backups/{backup_id}/restore",
            json={"passphrase": "wrong-passphrase", "dry_run": False},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False, body
        assert body["verified"] is False, body
        assert body["error"] == "wrong passphrase", body
        results.append(_checkpoint(
            "12_restore_wrong_passphrase", t, True
        ))

        # 13 — restore dry-run correct passphrase -----------------
        t = time.perf_counter()
        r = client.post(
            f"{BASE}/admin/backups/{backup_id}/restore",
            json={"passphrase": passphrase, "dry_run": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["verified"] is True, body
        assert body["dry_run"] is True, body
        manifest = body["manifest"]
        # additive schema bumps (v11 → v13) — manifest written by newer schema decrypts fine
        assert manifest["schema_version"] >= 11, manifest
        results.append(_checkpoint(
            "13_restore_dry_run_ok", t, True,
            key_versions_count=body["key_versions_count"],
        ))

        # 14 — restore actually materialise a target DB -----------
        t = time.perf_counter()
        target = work / "m3_infra_restored.sqlite"
        r = client.post(
            f"{BASE}/admin/backups/{backup_id}/restore",
            json={
                "passphrase": passphrase,
                "dry_run": False,
                "target_db_path": str(target),
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["verified"] is True, body
        assert Path(body["restored_db_path"]).exists(), body
        with sqlite3.connect(body["restored_db_path"]) as restored:
            cur = restored.execute(
                "SELECT version FROM schema_version "
                "WHERE component='finance_auto'"
            )
            row = cur.fetchone()
        # additive schema bumps (v11 → v13) — restored DB carries the producer's version
        assert row is not None and int(row[0]) >= 11, row
        results.append(_checkpoint(
            "14_restore_materialise", t, True,
            restored_schema_version=int(row[0]),
            restored_path=body["restored_db_path"],
        ))

        # 15 — delete backup --------------------------------------
        t = time.perf_counter()
        r = client.delete(f"{BASE}/admin/backups/{backup_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["status"] == "deleted", body
        # Detail still resolves (status flipped, file gone).
        det = client.get(f"{BASE}/admin/backups/{backup_id}").json()
        assert det["status"] == "deleted", det
        results.append(_checkpoint(
            "15_delete_backup", t, True, file_removed=body["file_removed"]
        ))

        # 16 — system info ----------------------------------------
        t = time.perf_counter()
        r = client.get(f"{BASE}/admin/system-info")
        assert r.status_code == 200, r.text
        body = r.json()
        # additive schema bumps (v11 → v13) — system-info echoes live DB schema_version
        assert body["schema_version"] >= 11, body
        assert body["encryption_enabled"] is True, body
        assert body["kdf_iterations"] == PBKDF2_ITERATIONS, body
        assert body["key_version"] == 2, body
        assert body["backup_count"] >= 1, body
        assert body["last_rotation_at"], body
        results.append(_checkpoint(
            "16_system_info", t, True,
            key_version=body["key_version"],
            backup_count=body["backup_count"],
        ))

        # 17 — Tauri wiring static check --------------------------
        t = time.perf_counter()
        wiring = _check_tauri_wiring(REPO_ROOT)
        assert wiring["ok"], wiring
        results.append(_checkpoint(
            "17_tauri_wiring_static_check", t, True,
            declared=wiring["declared"],
        ))

        asyncio.run(db.close())

    except AssertionError as exc:
        traceback.print_exc()
        failures.append(f"assertion: {exc}")
        results.append(
            {"step": "uncaught_assertion", "ok": False, "error": str(exc)}
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        failures.append(f"exception: {exc}")
        results.append(
            {"step": "uncaught_exception", "ok": False, "error": str(exc)}
        )
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)

    # 18 — regression sweep ---------------------------------------
    regression: dict[str, Any] = {}
    if args.skip_regression:
        regression = {"skipped": "--skip-regression"}
        results.append(_checkpoint(
            "18_regression_subprocess", time.perf_counter(), True,
            skipped=True,
        ))
    else:
        scripts_dir = PLUGIN_ROOT / "scripts"
        candidates = [
            ("m2_closing", scripts_dir / "m2_closing_acceptance.py"),
            ("m3_notes_peer", scripts_dir / "m3_notes_peer_acceptance.py"),
            ("m3_raw_ai", scripts_dir / "m3_raw_ai_acceptance.py"),
        ]
        regression_all_ok = True
        regression_started = time.perf_counter()
        for label, script in candidates:
            if not script.exists():
                regression[label] = {"skipped": "missing"}
                continue
            res = _run_subprocess_acceptance(script, label)
            regression[label] = res
            if not res.get("ok"):
                regression_all_ok = False
                failures.append(
                    f"regression {label} failed: exit={res.get('exit_code')}"
                )
        results.append(_checkpoint(
            "18_regression_subprocess", regression_started, regression_all_ok,
            details=regression,
        ))

    summary = {
        "db_path": str(db_path),
        "schema_version": SCHEMA_VERSION,
        "routes_total": routes_total,
        "routes_baseline": routes_baseline,
        "admin_delta": routes_total - routes_baseline,
        "checks": results,
        "failures": failures,
        "regression": regression,
        "ok": all(r.get("ok") for r in results) and not failures,
    }

    out_path = (
        Path(args.json) if args.json
        else REPO_ROOT / "_m3_infra_acceptance_result.json"
    )
    out_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + "=" * 60)
    print(
        f"M3 INFRA acceptance — {'SUCCESS' if summary['ok'] else 'FAIL'}",
        flush=True,
    )
    print("=" * 60)
    print(f"checks: {len(results)}  failures: {len(failures)}")
    print(f"result file: {out_path}")
    return 0 if summary["ok"] else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument(
        "--keep", action="store_true",
        help="Keep the temporary work directory after the run.",
    )
    p.add_argument("--json", help="Write result JSON to this path.")
    p.add_argument(
        "--skip-regression", action="store_true",
        help="Skip the cross-script regression sweep (step 18).",
    )
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(run(parse_args()))
