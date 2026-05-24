"""Tests for backup/restore sandbox + KDF + overwrite policy.

Covers:

* **EX-P1-1** ``dest_dir`` (create) and ``target_db_path`` (restore)
  path-traversal sandbox enforcement.
* **EX-P1-1** 409 ``target_already_exists`` guard with
  ``overwrite=true`` confirmation flag.
* **EX-P1-3 / EX-P2-2** PBKDF2 default lifted to OWASP-2023's 600k
  while older 200k archives still decrypt via the per-archive
  ``manifest.json["kdf_iterations"]`` field; env-var override is
  honoured for dev / CI scenarios.
"""

from __future__ import annotations

import asyncio
import json
import os
import tarfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi import HTTPException

from finance_auto_backend.db import FinanceAutoDB
from finance_auto_backend.routes import FinanceAutoService
from finance_auto_backend.services import backup_restore as br


PASSPHRASE = "correct-horse-battery-staple"


@pytest.fixture()
def service_and_sandbox(tmp_path: Path):
    """Return a fully-initialised FinanceAutoService whose backup
    sandbox is rooted under tmp_path (no env-var pollution, no
    home-dir writes during the test run)."""
    db_path = tmp_path / "finance_auto.sqlite"
    sandbox = tmp_path / "backup_sandbox"
    db = FinanceAutoDB(db_path)
    asyncio.run(db.init())
    service = FinanceAutoService(db)
    svc = br.BackupRestoreService(service, allowed_root=sandbox)
    yield service, svc, sandbox, db
    asyncio.run(db.close())


# ---------------------------------------------------------------------------
# EX-P1-1 — sandbox + overwrite
# ---------------------------------------------------------------------------


def test_create_backup_inside_sandbox_succeeds(service_and_sandbox) -> None:
    _service, svc, sandbox, _db = service_and_sandbox
    inside = sandbox / "nested"
    out = asyncio.run(
        svc.create_backup(passphrase=PASSPHRASE, dest_dir=inside)
    )
    assert out["status"] == "completed"
    assert Path(out["backup_path"]).exists()
    assert sandbox in Path(out["backup_path"]).resolve().parents


def test_create_backup_path_traversal_rejected(service_and_sandbox) -> None:
    _service, svc, sandbox, _db = service_and_sandbox
    # ``../../etc`` resolves above the sandbox — must 403, no I/O.
    escape = sandbox / ".." / ".." / "etc"
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            svc.create_backup(passphrase=PASSPHRASE, dest_dir=escape)
        )
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert detail["error"] == "path_outside_sandbox"
    assert detail["field"] == "dest_dir"


def test_restore_target_existing_without_overwrite_returns_409(
    service_and_sandbox,
) -> None:
    _service, svc, sandbox, _db = service_and_sandbox
    backup = asyncio.run(svc.create_backup(passphrase=PASSPHRASE))
    # Pre-create a victim file inside the sandbox.
    victim = sandbox / "victim.sqlite"
    victim.write_bytes(b"do-not-clobber")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            svc.restore_backup(
                backup_id=backup["id"],
                passphrase=PASSPHRASE,
                target_db_path=victim,
                overwrite=False,
            )
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "target_already_exists"
    # Victim file UNTOUCHED.
    assert victim.read_bytes() == b"do-not-clobber"


def test_restore_target_existing_with_overwrite_clobbers(
    service_and_sandbox,
) -> None:
    _service, svc, sandbox, _db = service_and_sandbox
    backup = asyncio.run(svc.create_backup(passphrase=PASSPHRASE))
    victim = sandbox / "victim.sqlite"
    victim.write_bytes(b"old-content")
    result = asyncio.run(
        svc.restore_backup(
            backup_id=backup["id"],
            passphrase=PASSPHRASE,
            target_db_path=victim,
            overwrite=True,
        )
    )
    assert result["ok"] is True
    # File was overwritten with the snapshot DB bytes — they start with
    # the standard SQLite magic header.
    assert victim.read_bytes().startswith(b"SQLite format 3")


def test_restore_target_outside_sandbox_rejected(
    service_and_sandbox, tmp_path: Path
) -> None:
    _service, svc, _sandbox, _db = service_and_sandbox
    backup = asyncio.run(svc.create_backup(passphrase=PASSPHRASE))
    # Path outside both sandbox AND the live DB path → 403.
    escape = tmp_path / "elsewhere" / "evil.sqlite"
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            svc.restore_backup(
                backup_id=backup["id"],
                passphrase=PASSPHRASE,
                target_db_path=escape,
                overwrite=False,
            )
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "path_outside_sandbox"


# ---------------------------------------------------------------------------
# EX-P1-3 / EX-P2-2 — PBKDF2 600k + env override + backward compat
# ---------------------------------------------------------------------------


def test_default_kdf_is_owasp_600k(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(br.BACKUP_KDF_ITERATIONS_ENV, raising=False)
    assert br._resolve_kdf_iterations() == 600_000


def test_kdf_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(br.BACKUP_KDF_ITERATIONS_ENV, "150000")
    assert br._resolve_kdf_iterations() == 150_000
    # Below-floor values fall back to the default.
    monkeypatch.setenv(br.BACKUP_KDF_ITERATIONS_ENV, "1234")
    assert br._resolve_kdf_iterations() == 600_000


def test_old_200k_backup_still_decrypts(service_and_sandbox) -> None:
    """Force a 200k archive (simulating an existing pre-RC backup) and
    confirm restore reads the iterations back from manifest.json."""
    _service, svc, sandbox, _db = service_and_sandbox
    # Lower the iterations env var so create_backup uses 200_000.
    with mock.patch.dict(
        os.environ, {br.BACKUP_KDF_ITERATIONS_ENV: "200000"}
    ):
        result = asyncio.run(svc.create_backup(passphrase=PASSPHRASE))
    archive = Path(result["backup_path"])
    # Sanity: manifest records 200000.
    with tarfile.open(archive, "r:gz") as tf:
        f = tf.extractfile("manifest.json")
        assert f is not None
        manifest = json.loads(f.read().decode("utf-8"))
    assert manifest["kdf_iterations"] == 200_000

    # Restore (dry-run) succeeds under the default 600k env, proving
    # the iteration count is read from the manifest, not the constant.
    with mock.patch.dict(
        os.environ, {br.BACKUP_KDF_ITERATIONS_ENV: "600000"}
    ):
        restored = asyncio.run(
            svc.restore_backup(
                backup_id=result["id"],
                passphrase=PASSPHRASE,
                dry_run=True,
            )
        )
    assert restored["ok"] is True
    assert restored["verified"] is True
    assert restored["kdf_iterations"] == 200_000


def test_new_600k_backup_round_trips(
    service_and_sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(br.BACKUP_KDF_ITERATIONS_ENV, raising=False)
    _service, svc, _sandbox, _db = service_and_sandbox
    result = asyncio.run(svc.create_backup(passphrase=PASSPHRASE))
    archive = Path(result["backup_path"])
    with tarfile.open(archive, "r:gz") as tf:
        f = tf.extractfile("manifest.json")
        assert f is not None
        manifest = json.loads(f.read().decode("utf-8"))
    assert manifest["kdf_iterations"] == 600_000
    decrypted = asyncio.run(
        svc.restore_backup(
            backup_id=result["id"],
            passphrase=PASSPHRASE,
            dry_run=True,
        )
    )
    assert decrypted["ok"] is True
    assert decrypted["kdf_iterations"] == 600_000
