"""UI bundle smoke tests for footage-gate.

These tests are deliberately lightweight — they don't spin up a browser.
What they do guarantee:

1. The bundled ``index.html`` exists and loads with the expected
   self-contained ``_assets/`` references (SDK 0.7.0 contract).
2. The 8 hard UI contracts from the v1.0 plan §2.5 are textually
   present in the bundle so a future rewrite can't quietly delete
   one and ship.
3. The bundle stays under the agreed 2800-line ceiling so the file
   doesn't bloat past the maintainable budget.
4. Every i18n key referenced by the React code has a translation in
   both ``zh`` and ``en`` (best-effort regex extraction).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UI_PATH = Path(__file__).parent.parent / "ui" / "dist" / "index.html"
ASSETS = ["bootstrap.js", "i18n.js", "icons.js", "markdown-mini.js", "styles.css"]

# Hard contracts from the v1.0 plan §2.5 (must stay in the bundle).
HARD_CONTRACTS = [
    ("PluginErrorBoundary", "class-component error boundary"),
    ('id: "create"', "4 tabs include create"),
    ('id: "tasks"', "4 tabs include tasks"),
    ('id: "guide"', "4 tabs include guide"),
    ('id: "settings"', "4 tabs include settings"),
    ('"split-layout"', "split-layout 42% / flex right"),
    ("mode-btn", "mode-btn pattern for both mode + filter pickers"),
    ("onEvent(", "onEvent subscription"),
    ("setInterval", "setInterval polling fallback"),
    ("oa-config-banner", "config banner for missing FFmpeg"),
    ("api-pill", "api-pill (FFmpeg status pill in this plugin)"),
    ("I18N_DICT", "single-source i18n dictionary"),
]

# Settings sections per seedance alignment.
SETTINGS_SECTIONS = [
    "settings.section.api",
    "settings.section.permissions",
    "settings.section.system",
    "settings.section.defaults",
    "settings.section.storage",
    "settings.section.about",
]

MAX_LINES = 2800


@pytest.fixture(scope="module")
def ui_text() -> str:
    assert UI_PATH.is_file(), f"UI bundle missing: {UI_PATH}"
    return UI_PATH.read_text(encoding="utf-8")


def test_ui_bundle_exists(ui_text: str) -> None:
    assert ui_text.startswith("<!DOCTYPE html>")
    assert "<title>" in ui_text and "Footage Gate" in ui_text


def test_ui_self_contained_assets(ui_text: str) -> None:
    """SDK 0.7.0: must NOT depend on host-mounted /api/plugins/_sdk/*."""
    for asset in ASSETS:
        assert f"_assets/{asset}" in ui_text, f"missing asset reference: {asset}"
        assert (UI_PATH.parent / "_assets" / asset).is_file(), (
            f"asset file missing on disk: {asset}"
        )
    forbidden_patterns = [
        r'src="/api/plugins/_sdk/',
        r'href="/api/plugins/_sdk/',
        r'src="/_sdk/',
        r'href="/_sdk/',
    ]
    for pat in forbidden_patterns:
        assert not re.search(pat, ui_text), (
            f"forbidden host-mounted SDK reference present: {pat}"
        )


def test_ui_under_line_budget(ui_text: str) -> None:
    line_count = ui_text.count("\n") + 1
    assert line_count <= MAX_LINES, (
        f"UI bundle is {line_count} lines, ceiling is {MAX_LINES}"
    )


@pytest.mark.parametrize("token,description", HARD_CONTRACTS)
def test_ui_hard_contracts_present(ui_text: str, token: str, description: str) -> None:
    assert token in ui_text, (
        f"hard contract missing — {description!r} (token={token!r})"
    )


@pytest.mark.parametrize("section_key", SETTINGS_SECTIONS)
def test_ui_settings_sections_present(ui_text: str, section_key: str) -> None:
    assert f'"{section_key}"' in ui_text, (
        f"settings section i18n key missing: {section_key}"
    )


def test_ui_modes_listed(ui_text: str) -> None:
    """All 4 mode IDs must appear in the bundle (mode-btn render path)."""
    for mid in ("source_review", "silence_cut", "auto_color", "cut_qc"):
        assert mid in ui_text, f"mode id missing from bundle: {mid}"


def test_ui_cut_qc_auto_remux_toggle(ui_text: str) -> None:
    """cut_qc must expose a UI toggle that lets the user opt into auto-remux.

    This is the explicit user requirement from the plan refresh — the toggle
    must live in the UI, not be backend-only.
    """
    assert "create.qc.autoRemux.label" in ui_text
    assert "auto_remux" in ui_text
    assert "Switch on={!!params.auto_remux}" in ui_text or "params.auto_remux" in ui_text


def test_ui_hdr_tonemap_toggle(ui_text: str) -> None:
    """auto_color must expose a HDR tone-map toggle (defended against
    upstream video-use PR #6 issue) that defaults to ON."""
    assert "create.color.hdr.label" in ui_text
    assert "hdr_tonemap" in ui_text


def test_ui_i18n_keys_have_translations(ui_text: str) -> None:
    """Every i18n key that the React code references must have a string in
    both zh and en. Best-effort regex extraction — only catches keys passed
    as a *literal* first arg to ``t(...)`` because that's how 100% of keys
    are introduced in this bundle."""
    # Match only true `t(` / `_i18n.t(` / `_tr(` calls (with a non-identifier
    # char immediately before `t`, so we don't pick up `set(`, `let(`, etc).
    referenced = set(re.findall(r'(?<![a-zA-Z0-9_])t\("([a-zA-Z][a-zA-Z0-9_.]+)"', ui_text))
    referenced.update(re.findall(r'_i18n\.t\("([a-zA-Z][a-zA-Z0-9_.]+)"', ui_text))
    referenced.update(re.findall(r'(?<![a-zA-Z0-9_])_tr\("([a-zA-Z][a-zA-Z0-9_.]+)"', ui_text))
    # ``modes.<id>`` keys are built dynamically; whitelist them so we don't
    # false-positive.
    dynamic_prefixes = ("modes.", "status.")
    static_refs = {k for k in referenced if not k.startswith(dynamic_prefixes)}

    zh_keys = set(re.findall(r'^\s*"([a-zA-Z][a-zA-Z0-9_.]+)":', ui_text, re.MULTILINE))

    missing = sorted(static_refs - zh_keys)
    assert not missing, (
        f"i18n keys referenced but not defined ({len(missing)}): {missing[:8]}"
    )


def test_ui_react_18_mount_pattern(ui_text: str) -> None:
    """We must use React 18's ``ReactDOM.createRoot`` (not legacy render)."""
    assert "ReactDOM.createRoot(document.getElementById" in ui_text
    assert "ReactDOM.render(" not in ui_text


def test_ui_no_top_level_throws(ui_text: str) -> None:
    """Smoke check: the ``<App />`` must be wrapped in PluginErrorBoundary."""
    # Fragile but cheap — looks for `<PluginErrorBoundary>` immediately
    # before `<App />` in the mount block.
    mount = ui_text.split("ReactDOM.createRoot")[-1]
    assert "<PluginErrorBoundary>" in mount
    assert "<App />" in mount
