"""Phase 0 sanity checks — vendored imports, plugin skeleton, and red-line grep guards.

These tests enforce structural invariants that must hold from day 1:

- All 5 vendored helpers under ``subtitle_craft_inline/`` import cleanly.
- ``plugin.json`` declares exactly 4 tools (no ``*_handoff_*`` tools per v1.0
  scope) and the ``subtitle-craft`` id.
- Red-line grep guards (matches Gate 0 in ``docs/subtitle-craft-plan.md``):
  no business code under ``plugins/subtitle-craft/`` may import from
  ``plugins-archive`` / ``_shared`` / ``sdk.contrib`` / ``openakita_plugin_sdk.contrib``,
  nor may it reference ``handoff`` outside docstrings/comments. The
  ``vendor_client.VendorError`` raw kind ``rate_limit`` is internal to that
  module and is mapped to the 9-key ``ERROR_HINTS`` taxonomy at write time —
  the grep here scopes to *imports* and *route paths*, not raw strings.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _iter_python_files(root: Path):
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def test_vendor_client_import():
    from subtitle_craft_inline.vendor_client import BaseVendorClient, VendorError

    assert BaseVendorClient is not None
    assert VendorError is not None


def test_upload_preview_import():
    from subtitle_craft_inline.upload_preview import (
        add_upload_preview_route,
        build_preview_url,
    )

    assert add_upload_preview_route is not None
    assert build_preview_url is not None


def test_storage_stats_import():
    from subtitle_craft_inline.storage_stats import StorageStats, collect_storage_stats

    assert StorageStats is not None
    assert collect_storage_stats is not None


def test_llm_json_parser_import():
    from subtitle_craft_inline.llm_json_parser import (
        parse_llm_json,
        parse_llm_json_array,
        parse_llm_json_object,
    )

    assert parse_llm_json is not None
    assert parse_llm_json_array is not None
    assert parse_llm_json_object is not None


def test_llm_json_parser_basic():
    from subtitle_craft_inline.llm_json_parser import parse_llm_json

    assert parse_llm_json('{"a": 1}') == {"a": 1}
    assert parse_llm_json("```json\n[1, 2, 3]\n```", expect=list) == [1, 2, 3]
    assert parse_llm_json("no json here", fallback={}) == {}


def test_parallel_executor_import():
    from subtitle_craft_inline.parallel_executor import (
        ParallelResult,
        ParallelSummary,
        run_parallel,
        summarize,
    )

    assert ParallelResult is not None
    assert ParallelSummary is not None
    assert run_parallel is not None
    assert summarize is not None


def test_plugin_skeleton_import():
    try:
        from plugin import Plugin

        p = Plugin()
        assert hasattr(p, "on_load")
        assert hasattr(p, "on_unload")
    except ImportError:
        pytest.skip("openakita SDK not available in test env")


def test_plugin_json_shape():
    """v1.1 declares 5 tools — the four v1.0 task tools plus the new
    ``subtitle.hook_pick``.  The ``handoff`` red line still applies."""
    data = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert data["id"] == "subtitle-craft"
    assert data["entry"] == "plugin.py"
    tools = data["provides"]["tools"]
    assert len(tools) == 5, f"v1.1 must declare exactly 5 tools, got {tools}"
    assert set(tools) == {
        "subtitle_craft_create",
        "subtitle_craft_status",
        "subtitle_craft_list",
        "subtitle_craft_cancel",
        "subtitle.hook_pick",
    }
    for t in tools:
        assert "handoff" not in t, f"v1.1 still forbids handoff tools: {t}"


# ── Red-line grep guards (Phase 0 / Gate 0) ───────────────────────────────────


_FORBIDDEN_IMPORT_PATTERNS = [
    re.compile(r"^\s*from\s+plugins[_-]archive\b", re.MULTILINE),
    re.compile(r"^\s*from\s+_shared\b", re.MULTILINE),
    re.compile(r"^\s*from\s+sdk\.contrib\b", re.MULTILINE),
    re.compile(r"^\s*from\s+openakita_plugin_sdk\.contrib\b", re.MULTILINE),
    re.compile(r"^\s*import\s+plugins[_-]archive\b", re.MULTILINE),
    re.compile(r"^\s*import\s+_shared\b", re.MULTILINE),
]


def test_no_forbidden_imports():
    """No business code may import from archive / shared / SDK contrib paths."""
    offenders = []
    for path in _iter_python_files(PLUGIN_DIR):
        text = path.read_text(encoding="utf-8")
        for pat in _FORBIDDEN_IMPORT_PATTERNS:
            for m in pat.finditer(text):
                offenders.append(f"{path.relative_to(PLUGIN_DIR)}: {m.group(0).strip()}")
    assert not offenders, "Forbidden imports detected:\n" + "\n".join(offenders)


def test_no_handoff_module_files():
    """No physical ``*handoff*.py`` source file may exist under the plugin.

    Schema-level reservation (``tasks.origin_*`` columns + ``assets_bus`` table)
    is allowed in Phase 1 SQL DDL inside ``subtitle_task_manager.py``, but no
    dedicated Handoff module / router / tool registration is permitted in v1.0.
    Discussion in docstrings/comments is fine; this guard only prevents
    accidental re-introduction of the v1-era ``subtitle_handoff.py`` file.
    """
    offenders = [
        p.relative_to(PLUGIN_DIR)
        for p in _iter_python_files(PLUGIN_DIR)
        if "handoff" in p.name.lower()
    ]
    assert not offenders, f"Handoff module files must not exist in v1.0: {offenders}"


def test_no_handoff_route_literal():
    """No string literal of the form ``"/handoff/..."`` may exist in plugin code.

    Excludes test files and any file whose path contains ``tests/`` (their
    own grep guards may quote the pattern verbatim).
    """
    forbidden_route = re.compile(r"""["']/handoff/""")
    offenders = []
    for path in _iter_python_files(PLUGIN_DIR):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for m in forbidden_route.finditer(text):
            offenders.append(f"{path.relative_to(PLUGIN_DIR)}: '{m.group(0)}'")
    assert not offenders, "Handoff route literal in v1.0 code:\n" + "\n".join(offenders)


def test_ui_assets_present():
    assets = PLUGIN_DIR / "ui" / "dist" / "_assets"
    expected = {"bootstrap.js", "i18n.js", "icons.js", "markdown-mini.js", "styles.css"}
    actual = {p.name for p in assets.iterdir() if p.is_file()}
    missing = expected - actual
    assert not missing, f"Missing UI assets: {missing}"
    assert (PLUGIN_DIR / "ui" / "dist" / "index.html").is_file()
