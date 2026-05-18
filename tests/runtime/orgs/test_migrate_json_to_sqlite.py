"""Tests for `scripts/migrate_orgs_v2_json_to_sqlite.py` (P-RC-3 P3.7)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "migrate_orgs_v2_json_to_sqlite.py"
)
_spec = importlib.util.spec_from_file_location("_migrate_json_sqlite", SCRIPT_PATH)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules["_migrate_json_sqlite"] = _module
_spec.loader.exec_module(_module)

from openakita.runtime.models import OrgV2, new_org_id  # noqa: E402
from openakita.runtime.orgs.sqlite_store import SqliteOrgStore  # noqa: E402
from openakita.runtime.orgs.store import JsonOrgStore  # noqa: E402


def _seed_json(path: Path, n: int = 3) -> list[str]:
    store = JsonOrgStore(path=path)
    ids: list[str] = []
    for i in range(n):
        org = OrgV2(
            id=new_org_id(),
            name=f"org_{i}",
            template_id="content_ops",
            description=None,
            nodes=[],
            edges=[],
        )
        store.create(org)
        ids.append(org.id)
    return ids


def test_fresh_migrate_writes_every_row(tmp_path: Path) -> None:
    json_path = tmp_path / "orgs_v2.json"
    sqlite_path = tmp_path / "orgs_v2.sqlite"
    ids = _seed_json(json_path, n=3)
    report = _module.migrate(
        json_path=json_path, sqlite_path=sqlite_path, apply=True
    )
    assert report.seen == 3
    assert report.imported == 3
    assert report.skipped_existing == 0
    assert report.skipped_malformed == 0
    assert sqlite_path.exists()
    store = SqliteOrgStore(path=sqlite_path)
    try:
        assert {o.id for o in store.list()} == set(ids)
    finally:
        store.close()


def test_idempotent_rerun_skips_existing(tmp_path: Path) -> None:
    json_path = tmp_path / "orgs_v2.json"
    sqlite_path = tmp_path / "orgs_v2.sqlite"
    _seed_json(json_path, n=2)
    _module.migrate(json_path=json_path, sqlite_path=sqlite_path, apply=True)
    # Second run.
    report2 = _module.migrate(
        json_path=json_path, sqlite_path=sqlite_path, apply=True
    )
    assert report2.seen == 2
    assert report2.imported == 0
    assert report2.skipped_existing == 2
    assert report2.skipped_malformed == 0


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    json_path = tmp_path / "orgs_v2.json"
    sqlite_path = tmp_path / "orgs_v2.sqlite"
    _seed_json(json_path, n=2)
    report = _module.migrate(
        json_path=json_path, sqlite_path=sqlite_path, apply=False
    )
    assert report.seen == 2
    assert report.imported == 2  # would-be import
    assert report.skipped_existing == 0
    assert report.skipped_malformed == 0
    assert not sqlite_path.exists(), "dry-run must not touch disk"


def test_malformed_row_is_tolerated(tmp_path: Path, caplog) -> None:
    json_path = tmp_path / "orgs_v2.json"
    sqlite_path = tmp_path / "orgs_v2.sqlite"
    good_ids = _seed_json(json_path, n=1)
    # Smuggle a bad row into the JSON file.
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    raw["orgs"]["bad_dict"] = "not a dict"  # type: ignore[assignment]
    raw["orgs"]["bad_shape"] = {"this": "is missing required OrgV2 keys"}
    json_path.write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    report = _module.migrate(
        json_path=json_path, sqlite_path=sqlite_path, apply=True
    )
    # Only the good row makes it across.
    assert report.imported == 1
    store = SqliteOrgStore(path=sqlite_path)
    try:
        assert {o.id for o in store.list()} == set(good_ids)
    finally:
        store.close()
    # And the malformed rows were both counted.
    assert report.skipped_malformed >= 1


def test_no_json_file_is_clean_noop(tmp_path: Path) -> None:
    json_path = tmp_path / "orgs_v2.json"  # does NOT exist
    sqlite_path = tmp_path / "orgs_v2.sqlite"
    report = _module.migrate(
        json_path=json_path, sqlite_path=sqlite_path, apply=True
    )
    assert report.seen == 0
    assert report.imported == 0
    assert not sqlite_path.exists()
