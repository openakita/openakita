#!/usr/bin/env python3
"""构建版本化用户文档。

读取 VERSION → 注入 VitePress config → 执行 vitepress build。

用法:
    python scripts/build_docs.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs-site"
VERSION_FILE = ROOT / "VERSION"


def main() -> None:
    if not VERSION_FILE.exists():
        print(f"ERROR: {VERSION_FILE} not found", file=sys.stderr)
        sys.exit(1)

    version = VERSION_FILE.read_text(encoding="utf-8").strip()

    vitepress_dir = DOCS_DIR / ".vitepress"
    vitepress_dir.mkdir(parents=True, exist_ok=True)
    version_json = vitepress_dir / "version.json"
    version_json.write_text(
        json.dumps({"version": version}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote version {version} → {version_json}")

    print(f"Building docs for v{version} ...")
    result = subprocess.run(
        ["npx", "vitepress", "build"],
        cwd=str(DOCS_DIR),
        shell=sys.platform == "win32",
    )
    if result.returncode != 0:
        print("ERROR: vitepress build failed", file=sys.stderr)
        sys.exit(result.returncode)

    dist = vitepress_dir / "dist"
    print(f"Docs built for v{version} → {dist}")


if __name__ == "__main__":
    main()
