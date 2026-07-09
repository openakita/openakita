#!/usr/bin/env python3
"""Write build-time version metadata into package artifacts.

`scripts/version.py` keeps `src/openakita/_bundled_version.txt` at the clean
release version. Build steps call this script, or import its helpers, when the
artifact needs the immutable `VERSION+git_hash` string.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_VERSION_FILE = ROOT / "src" / "openakita" / "_bundled_version.txt"
TAURI_RESOURCE_VERSION_FILE = (
    ROOT
    / "apps"
    / "setup-center"
    / "src-tauri"
    / "resources"
    / "openakita-server"
    / "_internal"
    / "openakita"
    / "_bundled_version.txt"
)

_HASH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def read_release_version() -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("VERSION is empty")
    if "+" in version:
        raise RuntimeError("VERSION must not contain '+': _bundled_version.txt reserves it")
    return version


def _normalize_hash(raw: str) -> str | None:
    value = raw.strip()
    if not value or value.lower() in {"unknown", "none", "null"}:
        return None
    if _HEX_RE.match(value) and len(value) > 7:
        value = value[:7]
    if not _HASH_RE.match(value):
        return None
    return value


def resolve_git_hash(*, require: bool = False) -> str:
    for env_name in (
        "OPENAKITA_BUILD_GIT_HASH",
        "GITHUB_SHA",
        "CI_COMMIT_SHA",
        "BUILDKITE_COMMIT",
    ):
        value = _normalize_hash(os.environ.get(env_name, ""))
        if value:
            return value

    try:
        value = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--short=7", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        ).strip()
    except Exception as exc:
        if require:
            raise RuntimeError("unable to resolve git hash from env or local git") from exc
        return "dev"

    normalized = _normalize_hash(value)
    if normalized:
        return normalized
    if require:
        raise RuntimeError(f"git returned an invalid short hash: {value!r}")
    return "dev"


def build_version_string(*, require_git: bool = False) -> str:
    return f"{read_release_version()}+{resolve_git_hash(require=require_git)}"


def _resolve_target(path: Path) -> Path:
    if path.is_absolute():
        return path
    return ROOT / path


def _default_targets() -> list[Path]:
    targets = [SOURCE_VERSION_FILE]
    if TAURI_RESOURCE_VERSION_FILE.exists():
        targets.append(TAURI_RESOURCE_VERSION_FILE)
    return targets


def write_build_version(
    target: Path,
    version_string: str,
    *,
    check: bool = False,
    dry_run: bool = False,
    skip_missing: bool = False,
) -> bool:
    target = _resolve_target(target)
    if not target.exists():
        if skip_missing:
            print(f"SKIP: {target.relative_to(ROOT)} does not exist")
            return False
        raise FileNotFoundError(target)

    old = target.read_text(encoding="utf-8").strip()
    rel = target.relative_to(ROOT) if target.is_relative_to(ROOT) else target
    if check:
        if old != version_string:
            raise RuntimeError(f"{rel} is {old!r}, expected {version_string!r}")
        print(f"OK: {rel} already contains {version_string}")
        return False

    if old == version_string:
        print(f"OK: {rel} already contains {version_string}")
        return False

    if dry_run:
        print(f"DRY-RUN: {rel}: {old!r} -> {version_string!r}")
        return True

    target.write_text(version_string, encoding="utf-8", newline="\n")
    print(f"WROTE: {rel}: {old!r} -> {version_string!r}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        action="append",
        type=Path,
        help="version file to update, relative paths are resolved from the repo root",
    )
    parser.add_argument("--check", action="store_true", help="verify without writing")
    parser.add_argument("--dry-run", action="store_true", help="print intended writes")
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="ignore missing target files instead of failing",
    )
    parser.add_argument(
        "--require-git",
        action="store_true",
        help="fail if no real git hash can be resolved",
    )
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="print the resolved build version and exit",
    )
    args = parser.parse_args()

    try:
        version_string = build_version_string(require_git=args.require_git)
        if args.print_only:
            print(version_string)
            return 0

        targets = [_resolve_target(t) for t in args.target] if args.target else _default_targets()
        for target in targets:
            write_build_version(
                target,
                version_string,
                check=args.check,
                dry_run=args.dry_run,
                skip_missing=args.skip_missing,
            )
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
