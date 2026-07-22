#!/usr/bin/env python3
"""Place a Cargo-built desktop executable where `tauri bundle` expects it."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI_DIR = ROOT / "apps" / "setup-center" / "src-tauri"


def prepare_binary(target: str = "") -> Path:
    config = json.loads((TAURI_DIR / "tauri.conf.json").read_text(encoding="utf-8"))
    destination_name = config["mainBinaryName"]
    suffix = ".exe" if sys.platform == "win32" else ""
    release_dir = TAURI_DIR / "target"
    if target:
        release_dir /= target
    release_dir /= "release"

    destination = release_dir / f"{destination_name}{suffix}"
    source = release_dir / f"openakita-setup-center{suffix}"
    if source.is_file():
        shutil.copy2(source, destination)
        print(f"Prepared Tauri binary: {source.name} -> {destination.name}")
    elif destination.is_file():
        print(f"Tauri binary already prepared: {destination}")
    else:
        raise FileNotFoundError(f"Cargo binary missing: expected {source} or {destination}")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="", help="Optional Rust target triple")
    args = parser.parse_args()
    prepare_binary(args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
