"""Production quality checks for plugin UI bundles."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

DEV_ONLY_UI_MARKERS = ("babel-standalone", "@babel/standalone", 'type="text/babel"')


def iter_dev_only_ui_markers(plugins_root: Path) -> Iterator[tuple[Path, str]]:
    """Yield plugin UI dist files that still contain dev-only browser transformers."""
    for dist in plugins_root.glob("*/ui/dist"):
        for path in dist.rglob("*"):
            if path.suffix.lower() not in {".html", ".js", ".jsx", ".ts", ".tsx"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for marker in DEV_ONLY_UI_MARKERS:
                if marker in text:
                    yield path, marker
