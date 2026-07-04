"""Phase 7 migration — move legacy ``data/orgs.db`` to ``data/orgs.legacy.db``
and populate the v2 :mod:`openakita.orgs` JSON store.

The plan calls for a re-entrant script (running twice is a no-op) so
operators can include it in their boot sequence without worrying about
double-application. The script does three things:

1. If ``data/orgs.db`` exists and ``data/orgs.legacy.db`` does *not*,
   rename it to mark the cutover. Otherwise log and skip (no-op).
2. Best-effort read every org row out of the legacy SQLite snapshot
   and translate it to an :class:`OrgV2` payload via the v2 templates
   registry (lookup by ``template_id``). Orgs whose template is
   unknown are skipped with a warning rather than aborting the run.
3. Bootstrap the global registry with the four built-in templates so
   that downstream code (``runtime/orgs/store`` + the canary gateway
   hook) always sees a populated registry.

After this script runs, the only persistent v2 state on disk is
``data/orgs_v2.json``. The legacy file remains under
``data/orgs.legacy.db`` so it can be inspected or restored later.

Usage::

    python scripts/migrate_orgs_to_v2.py            # dry-run / report
    python scripts/migrate_orgs_to_v2.py --apply    # commit changes

Re-entrancy notes
-----------------

* The legacy → legacy.db rename only runs when the source exists.
* The v2 store ``create`` call is idempotent against the org id —
  duplicates from a second run are skipped with a debug log instead
  of raising.
* The bootstrap call is idempotent by construction (``register``
  re-registration of the same id is a no-op).
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# Allow running directly without ``pip install -e .``.
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

logger = logging.getLogger("migrate_orgs_to_v2")


def _resolve_paths() -> tuple[Path, Path, Path]:
    """Return ``(legacy_db, legacy_db_backup, v2_json)`` rooted in
    ``settings.data_dir`` (falls back to ``data/`` when unset)."""
    from openakita.config import settings

    data_dir = Path(getattr(settings, "data_dir", None) or "data")
    return (
        data_dir / "orgs.db",
        data_dir / "orgs.legacy.db",
        data_dir / "orgs_v2.json",
    )


def _rename_legacy_db(legacy: Path, backup: Path, *, apply: bool) -> str:
    if backup.exists():
        return f"skip: {backup} already exists (previous run)"
    if not legacy.exists():
        return f"skip: {legacy} does not exist (nothing to migrate)"
    if not apply:
        return f"would rename: {legacy} → {backup}"
    legacy.rename(backup)
    return f"renamed: {legacy} → {backup}"


def _bootstrap_templates() -> int:
    """Make sure all 4 built-in templates are in the global registry.

    Returns the number of templates *newly* registered (already-present
    templates are not double-counted).
    """
    from openakita.runtime.templates import (
        GLOBAL_REGISTRY,
        collect_builtin_factories,
    )

    factories = collect_builtin_factories()
    new_count = 0
    for factory in factories:
        spec = factory()
        if spec.id in GLOBAL_REGISTRY:
            continue
        GLOBAL_REGISTRY.register(spec)
        new_count += 1
    return new_count


def _migrate_orgs_from_legacy(
    legacy_db: Path,
    *,
    apply: bool,
    manager: object | None = None,
) -> tuple[int, int, int]:
    """Walk the legacy SQLite snapshot and project every org into the v2 store.

    Returns ``(orgs_seen, orgs_imported, orgs_skipped)``. Best-effort:
    schema variations are tolerated, missing template_ids cause a
    skip, malformed rows do not abort the run.

    Sprint 13 H2 (RC-1): ``manager`` is the optional injection point
    for tests to point migration at a tmp-rooted :class:`OrgManager`
    instead of the settings-derived production SSoT. Production
    callers leave it ``None`` and get a fresh manager rooted at
    ``settings.data_dir``.
    """
    if not legacy_db.exists():
        logger.info("no legacy snapshot at %s — fresh-start migration", legacy_db)
        return 0, 0, 0
    try:
        conn = sqlite3.connect(legacy_db)
    except sqlite3.Error as exc:
        logger.warning("could not open legacy snapshot %s (%s) — skipping", legacy_db, exc)
        return 0, 0, 0
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
    except sqlite3.Error as exc:
        logger.warning("could not list legacy tables (%s) — skipping", exc)
        conn.close()
        return 0, 0, 0
    if "orgs" not in tables:
        logger.info("legacy snapshot has no 'orgs' table — nothing to import")
        conn.close()
        return 0, 0, 0

    # Sprint 13 H2 (RC-1): the v2 SSoT is now ``OrgManager`` writing
    # ``data/orgs/<id>/org.json``; the legacy ``JsonOrgStore.create``
    # write path raises. Route imports through the manager so the
    # migration deposits orgs directly into the new SSoT.
    from openakita.runtime.templates import GLOBAL_REGISTRY

    if manager is None:
        from openakita.config import settings

        from openakita.orgs.manager import OrgManager
        from openakita.orgs.store import set_default_org_manager

        manager = OrgManager(Path(getattr(settings, "data_dir", None) or "data"))
        set_default_org_manager(manager)

    seen, imported, skipped = 0, 0, 0
    try:
        rows = conn.execute("SELECT id, name, template_id FROM orgs").fetchall()
    except sqlite3.Error as exc:
        logger.warning("legacy 'orgs' schema unfamiliar (%s) — skipping import", exc)
        rows = []
    conn.close()

    for row in rows:
        seen += 1
        org_id, name, template_id = row[0], row[1], row[2]
        if not template_id or template_id not in GLOBAL_REGISTRY:
            logger.info("skip org %s: unknown template_id=%s", org_id, template_id)
            skipped += 1
            continue
        try:
            org_v2 = GLOBAL_REGISTRY.instantiate(template_id, name=name or org_id)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning("skip org %s: instantiate failed (%s)", org_id, exc)
            skipped += 1
            continue
        if not apply:
            logger.info("would import org %s (template=%s)", org_id, template_id)
            imported += 1
            continue
        # Idempotency: skip when OrgManager already has this id.
        if manager.get(str(org_id)) is not None:
            logger.debug("org %s already in v2 SSoT — idempotent skip", org_id)
            continue
        try:
            # Project the OrgV2 spec into the rich Organization dict
            # the manager expects, preserving the legacy id.
            payload = {
                "id": str(org_id),
                "name": org_v2.name,
                "description": org_v2.description or "",
            }
            manager.create(payload)
            imported += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("import failed for %s: %s", org_id, exc)
            skipped += 1
    return seen, imported, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the migration; default is dry-run.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    legacy_db, legacy_backup, v2_json = _resolve_paths()
    logger.info("legacy db    : %s", legacy_db)
    logger.info("backup target: %s", legacy_backup)
    logger.info("v2 json store: %s", v2_json)

    summary: list[str] = []
    summary.append(_rename_legacy_db(legacy_db, legacy_backup, apply=args.apply))
    new_templates = _bootstrap_templates()
    summary.append(f"templates: bootstrapped {new_templates} new (idempotent)")
    seen, imported, skipped = _migrate_orgs_from_legacy(legacy_backup, apply=args.apply)
    summary.append(f"orgs: seen={seen} imported={imported} skipped={skipped}")

    for line in summary:
        logger.info("→ %s", line)

    if not args.apply:
        logger.warning("dry-run mode — nothing was written. Pass --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
