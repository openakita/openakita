#!/usr/bin/env python
"""F-4 §A-4: migrate non-ASCII template ids to slugified ASCII ids.

Usage:
    .venv/Scripts/python.exe scripts/migrate_non_ascii_template_ids.py
    .venv/Scripts/python.exe scripts/migrate_non_ascii_template_ids.py --apply
    .venv/Scripts/python.exe scripts/migrate_non_ascii_template_ids.py --templates-dir data/org_templates

Default is **dry-run**: the script prints what would change but does
NOT modify any file under ``data/org_templates/``. Use ``--apply``
to actually rename files and update ``_aliases.json``.

What it does in --apply mode (atomic within a single template):
  1. Scan ``<templates_dir>/*.json`` for files whose stem contains
     non-ASCII characters.
  2. For each such file, compute the new ASCII slug via
     ``openakita.orgs._slug.slugify_template_id`` using the
     ORIGINAL file stem as input (so the slug is deterministic and
     reversible via the alias map).
  3. Rename ``<old_stem>.json`` -> ``<new_slug>.json``. If a file
     with the new slug already exists, skip (caller can resolve
     manually).
  4. Append (or update) ``<templates_dir>/_aliases.json`` with the
     ``{"<old_stem>": "<new_slug>"}`` mapping so legacy URLs keep
     working via the F-4 §A-3 alias resolver.

What it never does:
  * Touch files outside ``--templates-dir`` (default
    ``data/org_templates``).
  * Modify the .gitignore'd user data tree without ``--apply``.
  * Overwrite an existing target file.
  * Mutate the JSON content of the template (only the FILE NAME
    changes; the JSON ``name`` field stays as the human-readable
    label that ``list_templates`` now serves via ``display_name``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make ``src/`` importable when run from a checkout without `pip install -e .`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from openakita.orgs._slug import slugify_template_id  # noqa: E402


def scan_for_non_ascii_ids(templates_dir: Path) -> list[dict[str, Any]]:
    """Return a list of plan entries, one per non-ASCII-id template file.

    Each entry::

        {"old_path": Path, "old_stem": str, "new_slug": str,
         "new_path": Path, "collision": bool}
    """
    plan: list[dict[str, Any]] = []
    if not templates_dir.is_dir():
        return plan
    for p in sorted(templates_dir.glob("*.json")):
        # Skip the alias map file itself; never rename it.
        if p.name == "_aliases.json":
            continue
        # Skip archived deprecated files (extension is .json.deprecated).
        if p.suffix != ".json":  # defensive (glob already filtered)
            continue
        stem = p.stem
        if stem.isascii():
            continue
        new_slug = slugify_template_id(stem)
        new_path = templates_dir / f"{new_slug}.json"
        plan.append(
            {
                "old_path": p,
                "old_stem": stem,
                "new_slug": new_slug,
                "new_path": new_path,
                "collision": new_path.exists() and new_path != p,
            }
        )
    return plan


def print_plan(plan: list[dict[str, Any]], templates_dir: Path) -> None:
    """Human-readable dry-run summary."""
    if not plan:
        print(f"[ok] No non-ASCII template ids found in {templates_dir}.")
        return
    print(f"[plan] {len(plan)} non-ASCII template id(s) under {templates_dir}:")
    print()
    print(f"  {'BEFORE':<48}  ->  {'AFTER':<48}  status")
    print(f"  {'-' * 48}      {'-' * 48}  -------")
    for entry in plan:
        before = entry["old_path"].name
        after = entry["new_path"].name
        status = "COLLISION (skip)" if entry["collision"] else "rename + alias"
        print(f"  {before:<48}  ->  {after:<48}  {status}")
    print()
    print("Run again with --apply to perform these renames and update")
    print(f"  {templates_dir / '_aliases.json'}")
    print("with the legacy-id -> new-id mapping(s).")


def apply_plan(plan: list[dict[str, Any]], templates_dir: Path) -> int:
    """Perform the renames and update _aliases.json. Returns count migrated."""
    actionable = [e for e in plan if not e["collision"]]
    skipped = [e for e in plan if e["collision"]]

    # Load existing alias map (if any).
    alias_path = templates_dir / "_aliases.json"
    aliases: dict[str, str] = {}
    if alias_path.is_file():
        try:
            existing = json.loads(alias_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                aliases = {str(k): str(v) for k, v in existing.items()}
        except Exception as exc:
            print(f"[warn] existing _aliases.json unreadable ({exc}); starting fresh")

    migrated = 0
    for entry in actionable:
        old_path: Path = entry["old_path"]
        new_path: Path = entry["new_path"]
        old_stem: str = entry["old_stem"]
        new_slug: str = entry["new_slug"]
        try:
            old_path.rename(new_path)
        except OSError as exc:
            print(f"[error] rename {old_path.name} -> {new_path.name} failed: {exc}")
            continue
        aliases[old_stem] = new_slug
        migrated += 1
        print(f"[done] {old_path.name} -> {new_path.name} (alias added)")

    if migrated:
        alias_path.write_text(
            json.dumps(aliases, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"[done] wrote {alias_path} with {len(aliases)} alias entrie(s)")

    for entry in skipped:
        print(
            f"[skip] {entry['old_path'].name}: target "
            f"{entry['new_path'].name} already exists -- resolve manually"
        )
    return migrated


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="migrate_non_ascii_template_ids.py",
        description=(
            "F-4 §A-4: migrate user-saved org template files whose stem is "
            "non-ASCII (e.g. CJK) to URL-safe ASCII slugs, and append a "
            "legacy-id -> new-id mapping to _aliases.json so old client URLs "
            "keep working via the §A-3 alias resolver. Defaults to dry-run."
        ),
    )
    p.add_argument(
        "--templates-dir",
        default="data/org_templates",
        help="Directory containing the *.json template files (default: data/org_templates).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the rename + alias write. Without this flag "
        "the script only prints the plan (dry-run).",
    )
    args = p.parse_args(argv)

    templates_dir = Path(args.templates_dir).resolve()
    plan = scan_for_non_ascii_ids(templates_dir)

    if args.apply:
        if not plan:
            print(f"[ok] No non-ASCII template ids found in {templates_dir}; nothing to do.")
            return 0
        migrated = apply_plan(plan, templates_dir)
        print()
        print(f"[summary] migrated {migrated} file(s) in {templates_dir}")
        return 0
    else:
        print_plan(plan, templates_dir)
        return 0


if __name__ == "__main__":
    sys.exit(main())
