"""Static-analysis acceptance for the M3 UI (Sibling D).

Validates that ``plugins/finance-auto/ui/dist/index.html`` ships the four
new top-level views (NotesEditorView, PeerComparisonView,
KeyManagementView, AdvancedAIView) plus the ``🔴 raw 高级场景`` extension
of AISettingsView.  No browser, no FastAPI -- the dual-track mock
fallback pattern lets the UI ship before the backend siblings A/B/C land.

13 verification gates -- exit 0 iff every gate is green::

    1.  HTML well-formed (html.parser raises no exceptions)
    2.  File size between 1 KB and 600 KB
    3.  4 hash route identifiers present
    4.  4 CN view labels present
    5.  10 endpoint substrings present
    6.  4 fallback notice markers present
    7.  5 quartile assessment strings present
    8.  <svg ... > inside the peer-comparison view block
    9.  Key-rotation confirmation modal text present
    10. raw-scenario consent banner ("🔴 raw") present
    11. Optimistic-lock PATCH body shape (content + version) near notes
    12. NL query <pre> block + validation_errors branch
    13. localStorage mock keys present

Usage::

    python plugins/finance-auto/scripts/m3_ui_acceptance.py [--verbose] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

INDEX = Path(__file__).resolve().parents[1] / "ui" / "dist" / "index.html"
MAX_SIZE = 600 * 1024
MIN_SIZE = 1024

ROUTE_IDS = ["notes-editor", "peer-comparison", "key-management", "advanced-ai"]
LABELS = ["报表附注", "同业对比", "密钥管理", "高级AI"]
ENDPOINTS = [
    "/notes/generate",
    "/notes/documents",
    "/peer-comparison/run",
    "/peer-benchmarks",
    "/ai/raw/audit-opinion",
    "/ai/raw/nl-query",
    "/ai/raw/notes-draft",
    "/admin/key-versions",
    "/admin/key-rotate",
    "/admin/backups",
]
QUARTILES = ["well_below", "below", "median_band", "above", "well_above"]
LS_KEYS = ["finance.notes.mock.v1", "finance.peer.mock.v1", "finance.km.mock.v1"]


class _SilentParser(HTMLParser):
    """HTMLParser that swallows nothing -- exceptions surface to caller."""


def _parse_html(text: str) -> tuple[bool, str]:
    parser = _SilentParser(convert_charrefs=False)
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # noqa: BLE001 -- spec says any feed-time error fails
        return False, f"html.parser raised {type(exc).__name__}: {exc}"
    return True, "ok"


def _slice_view(text: str, marker: str, span: int = 30000) -> str:
    """Return ``span`` bytes after ``marker`` (large enough to cover JSX body)."""

    idx = text.find(marker)
    if idx < 0:
        return ""
    return text[max(0, idx - 200) : idx + span]


def run_checks(html: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    ok, detail = _parse_html(html)
    checks.append({"id": 1, "name": "html_well_formed", "ok": ok, "detail": detail})

    size = len(html.encode("utf-8"))
    checks.append(
        {
            "id": 2,
            "name": "size_within_budget",
            "ok": MIN_SIZE <= size <= MAX_SIZE,
            "detail": f"{size} bytes (limit {MIN_SIZE}..{MAX_SIZE})",
        }
    )

    missing = [r for r in ROUTE_IDS if r not in html]
    checks.append({"id": 3, "name": "route_ids", "ok": not missing, "detail": missing or "all 4 present"})

    missing = [lbl for lbl in LABELS if lbl not in html]
    checks.append({"id": 4, "name": "view_labels", "ok": not missing, "detail": missing or "all 4 present"})

    missing = [ep for ep in ENDPOINTS if ep not in html]
    checks.append({"id": 5, "name": "endpoint_substrings", "ok": not missing, "detail": missing or "all 10 present"})

    fallback_markers = ("M3 Sibling A", "M3 Sibling B", "M3 Sibling C", "后端尚未上线")
    found = [m for m in fallback_markers if m in html]
    checks.append(
        {
            "id": 6,
            "name": "fallback_notices",
            "ok": all(m in found for m in ("M3 Sibling A", "M3 Sibling B", "M3 Sibling C", "后端尚未上线")),
            "detail": found,
        }
    )

    missing = [q for q in QUARTILES if q not in html]
    checks.append({"id": 7, "name": "quartile_strings", "ok": not missing, "detail": missing or "all 5 present"})

    peer_block = _slice_view(html, "function PeerComparisonView")
    has_svg = "<svg" in peer_block
    checks.append(
        {
            "id": 8,
            "name": "peer_svg_chart",
            "ok": has_svg,
            "detail": "<svg> inside peer block" if has_svg else "no <svg> near PeerComparisonView",
        }
    )

    km_block = _slice_view(html, "function KeyManagementView")
    has_modal_text = ("继续？" in km_block) or ("重加密" in km_block)
    checks.append(
        {
            "id": 9,
            "name": "key_rotation_modal_text",
            "ok": has_modal_text,
            "detail": "found '继续？' or '重加密'" if has_modal_text else "missing rotation modal warning",
        }
    )

    has_consent_banner = "🔴 raw" in html
    checks.append(
        {
            "id": 10,
            "name": "raw_consent_banner",
            "ok": has_consent_banner,
            "detail": "🔴 raw substring present" if has_consent_banner else "missing 🔴 raw banner",
        }
    )

    notes_block = _slice_view(html, "saveNote")
    has_lock = ("content:" in notes_block or "content :" in notes_block) and (
        "version:" in notes_block or "version :" in notes_block
    )
    checks.append(
        {
            "id": 11,
            "name": "optimistic_lock_patch_body",
            "ok": has_lock,
            "detail": "content + version near saveNote" if has_lock else "could not find PATCH body shape",
        }
    )

    nl_block = _slice_view(html, "function NlQueryCard")
    has_pre = "<pre" in nl_block
    has_validation_branch = "validation_errors" in nl_block
    checks.append(
        {
            "id": 12,
            "name": "nl_query_render",
            "ok": has_pre and has_validation_branch,
            "detail": f"<pre>={has_pre}, validation_errors={has_validation_branch}",
        }
    )

    missing = [k for k in LS_KEYS if k not in html]
    checks.append({"id": 13, "name": "localstorage_mock_keys", "ok": not missing, "detail": missing or "all 3 present"})

    return checks


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    p = argparse.ArgumentParser(description="M3 UI Sibling D static-analysis acceptance.")
    p.add_argument("--json", dest="json_path", help="Write structured result to JSON file.")
    p.add_argument("--verbose", action="store_true", help="Print every check, not just failures.")
    p.add_argument("--index", default=str(INDEX), help="Path to index.html (default: plugin dist).")
    args = p.parse_args()

    path = Path(args.index)
    if not path.is_file():
        print(f"FAIL: index.html not found at {path}", file=sys.stderr)
        return 2
    html = path.read_text(encoding="utf-8", errors="replace")

    results = run_checks(html)
    passed = sum(1 for r in results if r["ok"])
    total = len(results)

    for r in results:
        if r["ok"] and not args.verbose:
            continue
        flag = "PASS" if r["ok"] else "FAIL"
        print(f"[{flag}] #{r['id']:02d} {r['name']}: {r['detail']}")

    summary = {
        "passed": passed,
        "total": total,
        "all_green": passed == total,
        "index_path": str(path),
        "index_size_bytes": len(html.encode("utf-8")),
        "checks": results,
    }
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"---\nM3 UI acceptance: {passed}/{total} green")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
