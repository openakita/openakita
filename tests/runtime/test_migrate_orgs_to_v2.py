"""Tests for ``scripts/migrate_orgs_to_v2.py``.

The migration script is invoked as part of Phase 7 cutover. Tests
must cover:

* Re-entrancy: running twice is a no-op.
* Best-effort tolerance: malformed / missing legacy snapshot must
  not abort.
* Template bootstrap is idempotent.
"""

from __future__ import annotations

import importlib.util
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

from openakita.orgs import reset_default_store, set_default_org_manager
from openakita.orgs.manager import OrgManager
from openakita.runtime.templates import GLOBAL_REGISTRY

# Load the script as a module so we can call its helpers directly
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "migrate_orgs_to_v2.py"
spec = importlib.util.spec_from_file_location("_migrate_orgs", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
_migrate = importlib.util.module_from_spec(spec)
sys.modules["_migrate_orgs"] = _migrate
spec.loader.exec_module(_migrate)


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Root all migration paths under tmp.

    ``settings.data_dir`` is a derived property without a setter, so
    we override ``_resolve_paths`` on the script module itself — that
    is the single seam every helper consults for paths. Sprint 13 H2
    (RC-1) also wires a tmp-rooted :class:`OrgManager` into the
    process-wide default-manager registry so migrations write into
    the test's tmp tree rather than the real ``data/orgs/``.
    """
    paths = (
        tmp_path / "orgs.db",
        tmp_path / "orgs.legacy.db",
        tmp_path / "orgs_v2.json",
    )
    monkeypatch.setattr(_migrate, "_resolve_paths", lambda: paths)
    manager = OrgManager(tmp_path)
    reset_default_store(path=tmp_path / "orgs_v2.json", manager=manager)
    set_default_org_manager(manager)
    yield tmp_path
    set_default_org_manager(None)


def test_rename_skip_when_no_legacy_db(isolated_data_dir: Path) -> None:
    legacy = isolated_data_dir / "orgs.db"
    backup = isolated_data_dir / "orgs.legacy.db"
    result = _migrate._rename_legacy_db(legacy, backup, apply=True)
    assert "skip" in result
    assert not backup.exists()


def test_rename_skip_when_backup_already_exists(isolated_data_dir: Path) -> None:
    legacy = isolated_data_dir / "orgs.db"
    backup = isolated_data_dir / "orgs.legacy.db"
    legacy.write_bytes(b"old")
    backup.write_bytes(b"already done")
    result = _migrate._rename_legacy_db(legacy, backup, apply=True)
    assert "already exists" in result
    assert legacy.exists(), "must not touch legacy when backup is present"
    assert backup.read_bytes() == b"already done"


def test_rename_dry_run_then_apply(isolated_data_dir: Path) -> None:
    legacy = isolated_data_dir / "orgs.db"
    backup = isolated_data_dir / "orgs.legacy.db"
    legacy.write_bytes(b"hello")
    dry = _migrate._rename_legacy_db(legacy, backup, apply=False)
    assert "would rename" in dry
    assert legacy.exists() and not backup.exists()
    applied = _migrate._rename_legacy_db(legacy, backup, apply=True)
    assert "renamed" in applied
    assert backup.exists() and not legacy.exists()
    # Second apply is now a skip (re-entrant)
    again = _migrate._rename_legacy_db(legacy, backup, apply=True)
    assert "skip" in again


def test_bootstrap_templates_is_idempotent(isolated_data_dir: Path) -> None:
    # Drain registry first
    GLOBAL_REGISTRY.clear()
    n1 = _migrate._bootstrap_templates()
    assert n1 >= 4
    n2 = _migrate._bootstrap_templates()
    assert n2 == 0


def test_migrate_skips_when_no_legacy_db(isolated_data_dir: Path) -> None:
    legacy_backup = isolated_data_dir / "orgs.legacy.db"
    seen, imported, skipped = _migrate._migrate_orgs_from_legacy(
        legacy_backup, apply=True
    )
    assert (seen, imported, skipped) == (0, 0, 0)


def test_migrate_skips_when_no_orgs_table(
    isolated_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    legacy_backup = isolated_data_dir / "orgs.legacy.db"
    conn = sqlite3.connect(legacy_backup)
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    with caplog.at_level(logging.INFO):
        seen, imported, skipped = _migrate._migrate_orgs_from_legacy(
            legacy_backup, apply=True
        )
    assert (seen, imported, skipped) == (0, 0, 0)
    assert "no 'orgs' table" in caplog.text


def test_migrate_imports_known_template_and_is_reentrant(
    isolated_data_dir: Path,
) -> None:
    GLOBAL_REGISTRY.clear()
    _migrate._bootstrap_templates()
    legacy_backup = isolated_data_dir / "orgs.legacy.db"
    conn = sqlite3.connect(legacy_backup)
    conn.execute(
        "CREATE TABLE orgs (id TEXT PRIMARY KEY, name TEXT, template_id TEXT)"
    )
    conn.execute(
        "INSERT INTO orgs (id, name, template_id) VALUES (?, ?, ?)",
        ("org_legacy_1", "Acme", "content_ops"),
    )
    conn.execute(
        "INSERT INTO orgs (id, name, template_id) VALUES (?, ?, ?)",
        ("org_legacy_2", "Bad", "no_such_template"),
    )
    conn.commit()
    conn.close()

    # Sprint 13 H2 (RC-1): point the migration at the tmp-rooted
    # manager so this test cannot accidentally write into the
    # real ``data/orgs/``.
    test_manager = OrgManager(isolated_data_dir)
    seen, imported, skipped = _migrate._migrate_orgs_from_legacy(
        legacy_backup, apply=True, manager=test_manager
    )
    assert seen == 2
    assert imported == 1
    assert skipped == 1

    # Re-running is idempotent
    seen2, imported2, skipped2 = _migrate._migrate_orgs_from_legacy(
        legacy_backup, apply=True, manager=test_manager
    )
    assert seen2 == 2
    # imported2 stays 0 since the v2 store already has org_legacy_1;
    # skipped2 includes the unknown-template_id row from the second pass.
    assert imported2 == 0
    assert skipped2 >= 1


def test_main_dry_run_is_safe(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = isolated_data_dir / "orgs.db"
    legacy.write_bytes(b"placeholder")
    monkeypatch.setattr(sys, "argv", ["migrate_orgs_to_v2.py"])
    rc = _migrate.main()
    assert rc == 0
    # Dry-run must not touch the legacy file
    assert legacy.exists()
