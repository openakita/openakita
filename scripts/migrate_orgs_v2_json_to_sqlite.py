"""Idempotent JSON -> SQLite migration for the v2 OrgV2 store.

P-RC-3 commit P3.7. Reads ``<data_dir>/orgs_v2.json`` (the
JsonOrgStore default) and writes each org into
``<data_dir>/orgs_v2.sqlite`` via SqliteOrgStore. The script is
safe to re-run -- the second invocation reports every org as
``skipped: already present`` and exits 0.

Re-entrancy
-----------
* ``SqliteOrgStore.create`` raises ``ValueError`` when an id is
  already present; the loop catches it and bumps the
  ``skipped_existing`` counter rather than aborting.
* A malformed row in ``orgs_v2.json`` is logged at warning level
  and counted as ``skipped_malformed`` so a single bad row does
  not cripple the migration.
* The ``--apply`` flag gates writes; without it the script is a
  pure dry-run that walks both files and reports the counts.

Switching back to JSON
----------------------
After running this migration, the JSON file remains the source
of truth. To switch back, set ``ORGS_V2_BACKEND=json`` in
``.env`` (or remove the override -- ``json`` is the default).
The SQLite file under ``orgs_v2.sqlite`` becomes a stale copy
that the operator can delete at leisure. See
``docs/revamp/rollback.md`` ``Switching back to JSON`` for the
full SOP.

Usage::

    python scripts/migrate_orgs_v2_json_to_sqlite.py           # dry-run
    python scripts/migrate_orgs_v2_json_to_sqlite.py --apply   # commit
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow running directly without ``pip install -e .``.
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

logger = logging.getLogger("migrate_orgs_v2_json_to_sqlite")


@dataclass(frozen=True)
class MigrationReport:
    seen: int
    imported: int
    skipped_existing: int
    skipped_malformed: int

    def as_dict(self) -> dict[str, int]:
        return {
            "seen": self.seen,
            "imported": self.imported,
            "skipped_existing": self.skipped_existing,
            "skipped_malformed": self.skipped_malformed,
        }


def _resolve_paths(data_dir: Path | None = None) -> tuple[Path, Path]:
    if data_dir is None:
        from openakita.config import settings

        data_dir = Path(getattr(settings, "data_dir", None) or "data")
    return data_dir / "orgs_v2.json", data_dir / "orgs_v2.sqlite"


def _load_json_orgs(json_path: Path) -> tuple[list[tuple[str, dict]], int]:
    """Return ``([(org_id, payload), ...], malformed_count)``."""
    if not json_path.exists():
        return [], 0
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "could not parse %s (%s); migration is a no-op", json_path, exc
        )
        return [], 0
    orgs_raw = raw.get("orgs", {}) if isinstance(raw, dict) else {}
    if not isinstance(orgs_raw, dict):
        logger.warning(
            "%s has no 'orgs' object; nothing to migrate", json_path
        )
        return [], 0
    out: list[tuple[str, dict]] = []
    malformed = 0
    for org_id, payload in orgs_raw.items():
        if not isinstance(payload, dict):
            logger.warning(
                "[migrate] skipping malformed json row id=%s (not a dict)", org_id
            )
            malformed += 1
            continue
        out.append((str(org_id), payload))
    return out, malformed


def migrate(
    *,
    json_path: Path,
    sqlite_path: Path,
    apply: bool,
) -> MigrationReport:
    """Perform (or dry-run) the migration. Returns a structured report."""
    from openakita.runtime.models import OrgV2
    from openakita.runtime.orgs.sqlite_store import SqliteOrgStore

    rows, malformed = _load_json_orgs(json_path)
    seen = len(rows) + malformed
    imported = 0
    skipped_existing = 0
    if not rows:
        logger.info("nothing to migrate (seen=%d malformed=%d)", seen, malformed)
        return MigrationReport(seen, 0, 0, malformed)
    if not apply:
        logger.info(
            "[dry-run] would migrate %d org(s) into %s (skipping %d malformed)",
            len(rows), sqlite_path, malformed,
        )
        return MigrationReport(seen, len(rows), 0, malformed)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteOrgStore(path=sqlite_path)
    try:
        for org_id, payload in rows:
            try:
                org = OrgV2.from_jsonable(payload)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning(
                    "[migrate] dropping malformed payload id=%s (%s)", org_id, exc
                )
                malformed += 1
                continue
            try:
                org.id = org_id  # preserve the original id verbatim
                store.create(org)
                imported += 1
            except ValueError:
                # Idempotent: already present from a prior run.
                logger.debug("[migrate] org id=%s already present, skipping", org_id)
                skipped_existing += 1
    finally:
        store.close()
    return MigrationReport(seen, imported, skipped_existing, malformed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the migration; default is dry-run.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override ``settings.data_dir``; useful for tests.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )
    json_path, sqlite_path = _resolve_paths(args.data_dir)
    logger.info("source json : %s", json_path)
    logger.info("target sqlite: %s", sqlite_path)
    report = migrate(json_path=json_path, sqlite_path=sqlite_path, apply=args.apply)
    for k, v in report.as_dict().items():
        logger.info("  %s = %d", k, v)
    if not args.apply:
        logger.warning("dry-run mode -- nothing was written. Pass --apply to commit.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())