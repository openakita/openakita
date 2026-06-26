#!/usr/bin/env python3
"""Stage built web/docs assets under src/openakita for release wheels.

The Python package can build without Node/VitePress outputs. Release jobs that
need a self-contained wheel build the frontend/docs first, then run this script
so Hatch includes the package-local artifacts declared in pyproject.toml.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SOURCE = ROOT / "apps" / "setup-center" / "dist-web"
DOCS_SOURCE = ROOT / "docs-site" / ".vitepress" / "dist"
WEB_TARGET = ROOT / "src" / "openakita" / "web"
DOCS_TARGET = ROOT / "src" / "openakita" / "docs_dist"


def _has_real_assets(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(item.is_file() and item.name != ".keep" for item in path.rglob("*"))


def _stage_tree(label: str, source: Path, target: Path, command: str) -> int:
    if not _has_real_assets(source):
        raise RuntimeError(f"{label} assets missing at {source}; build with `{command}`")
    if not (source / "index.html").is_file():
        raise RuntimeError(f"{label} assets missing index.html at {source}")

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return sum(1 for item in target.rglob("*") if item.is_file())


def clean_staged_assets(
    *,
    web_target: Path = WEB_TARGET,
    docs_target: Path = DOCS_TARGET,
) -> None:
    for target in (web_target, docs_target):
        if target.exists():
            shutil.rmtree(target)


def stage_package_assets(
    *,
    web_source: Path = WEB_SOURCE,
    docs_source: Path = DOCS_SOURCE,
    web_target: Path = WEB_TARGET,
    docs_target: Path = DOCS_TARGET,
) -> list[tuple[str, int]]:
    return [
        (
            "web frontend",
            _stage_tree(
                "web frontend",
                web_source,
                web_target,
                "cd apps/setup-center && npm ci && npm run build:web",
            ),
        ),
        (
            "user docs",
            _stage_tree(
                "user docs",
                docs_source,
                docs_target,
                "cd docs-site && npm ci && npm run build",
            ),
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove staged package assets and exit",
    )
    args = parser.parse_args()

    if args.clean:
        clean_staged_assets()
        print("Removed staged package assets.")
        return 0

    for label, count in stage_package_assets():
        print(f"Staged {label}: {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
