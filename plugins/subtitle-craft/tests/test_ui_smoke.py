"""Phase 5 UI smoke / grep guards.

These tests don't launch a browser — they just enforce structural
invariants on ``ui/dist/index.html`` that we promised in Gate 5:

- File is non-trivial (≥1500 lines) — Phase 5 is a full UI, not a stub.
- No ``handoff`` / ``跨插件`` / ``下一步`` strings outside i18n keys
  (red line C5: v1.0 ships zero cross-plugin dispatch surface).
- All 4 tabs are declared (create / tasks / library / settings).
- All 4 modes are declared (auto_subtitle / translate / repair / burn).
- Tongyi-image 8-item alignment is enforced via class-name presence:
    1. ``oa-hero-title`` in the header
    2. ``oa-config-banner`` for the missing-key banner
    3. ``oa-section-label``-equivalent (.label with the accent bar)
    4. Lazy-mounted tabs (``tabsMounted``)
    5. Bridge SDK (``bridge:api-request``)
    6. Theme + locale follow host (``bridge:theme-change``, ``bridge:locale-change``)
    7. ``oa-preview-area`` right-side preview (NOT a drawer)
    8. Toast + modal (``modal-mask`` + ``showToast``)
- ``character_identify_enabled`` is gated on diarization (red line:
  toggle disabled when diarization is off).
- ``/healthz`` is rendered with all 4 fields.
- SSE event ``task_update`` is wired (red line #21).
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
INDEX_HTML = PLUGIN_DIR / "ui" / "dist" / "index.html"


def _read():
    return INDEX_HTML.read_text(encoding="utf-8")


def test_index_html_exists_and_is_substantial():
    assert INDEX_HTML.is_file()
    text = _read()
    line_count = text.count("\n")
    # Phase 5 spec: ~2500 lines target; we accept ≥1500 as the floor
    # so cosmetic shrinking later doesn't reflexively break this test.
    assert line_count >= 1500, f"UI is too small ({line_count} lines)"


def test_no_handoff_strings_in_ui():
    text = _read().lower()
    # The literal "handoff" must not appear anywhere in the UI bundle —
    # not in i18n keys, not in route paths, not in button labels.
    assert "handoff" not in text, "UI must not reference 'handoff' in v1.0"
    # Chinese cross-plugin strings: 跨插件 / 下一步 (a typical
    # "send to other plugin" CTA). These two phrases appear in the v2
    # roadmap copy; they MUST NOT appear in v1.0 UI.
    text_zh = _read()
    assert "跨插件" not in text_zh
    assert "下一步" not in text_zh


def test_all_four_tabs_present():
    text = _read()
    for tab_id in ('"create"', '"tasks"', '"library"', '"settings"'):
        assert tab_id in text, f"missing tab id: {tab_id}"
    # Tab labels via i18n keys
    for key in ("tabs.create", "tabs.tasks", "tabs.library", "tabs.settings"):
        assert key in text, f"missing i18n tab key: {key}"


def test_all_five_modes_present():
    """v1.1: hook_picker is the 5th mode; UI must list it alongside the four
    v1.0 modes both as a literal and as an i18n key."""
    text = _read()
    for mode in ("auto_subtitle", "translate", "repair", "burn", "hook_picker"):
        assert f'"{mode}"' in text, f"mode literal missing: {mode}"
        assert f"modes.{mode}" in text, f"mode i18n key missing: modes.{mode}"


def test_hook_picker_ui_components_present():
    """Phase 4 UI surfaces for hook_picker mode (v1.1)."""
    text = _read()
    # 1. mode tile NEW badge + gold chip variant
    assert "oa-mode-tile__new-badge" in text
    assert 'data-mode="hook_picker"' in text or "data-mode={m}" in text
    # 2. HookResultPanel + HooksList components live in this single bundle
    assert "function HookResultPanel" in text
    assert "function HooksList" in text
    # 3. Library 4th sub-tab "hooks" + its i18n keys
    assert '"hooks"' in text
    assert "library.tabs.hooks" in text
    assert "library.intro.hooks" in text
    # 4. Source picker + advanced options i18n
    for key in (
        "hook.source.label",
        "hook.source.upload",
        "hook.source.from_task",
        "hook.instruction.label",
        "hook.duration.label",
        "hook.advanced",
        "hook.window.label",
        "hook.model.label",
        "hook.result.title",
        "hook.result.copy_timecode",
    ):
        assert key in text, f"hook_picker i18n key missing: {key}"
    # 5. /library/hooks endpoint is wired in the UI
    assert "/library/hooks" in text


def test_hook_picker_ui_does_not_leak_backend_symbols():
    """Pure-frontend file must not import / reference backend symbols."""
    text = _read()
    # Backend functions live in subtitle_hook_picker.py — they must NOT
    # appear in the UI bundle (would imply broken layering).
    for sym in (
        "select_hook_dialogue",
        "HookSelectionError",
        "_match_dialogue_lines_to_subtitles",
        "subtitle_hook_picker",
    ):
        assert sym not in text, f"backend symbol leaked into UI: {sym}"


def test_tongyi_image_eight_item_alignment():
    text = _read()
    # 1. Hero title
    assert "oa-hero-title" in text
    # 2. Config banner
    assert "oa-config-banner" in text
    # 3. Section-style labels (the .label class with ::before accent bar)
    assert ".label::before" in text
    # 4. Lazy-mount tab pattern
    assert "tabsMounted" in text
    # 5. Bridge SDK API request envelope
    assert "bridge:api-request" in text
    # 6. Host theme + locale follow
    assert "bridge:theme-change" in text
    assert "bridge:locale-change" in text
    # 7. Right-side preview area class (NOT a drawer)
    assert "oa-preview-area" in text
    assert "drawer" not in text.lower(), "Phase 5 must use right-side preview, not a drawer"
    # 8. Modal + toast
    assert "modal-mask" in text
    assert "showToast" in text


def test_char_identify_gated_by_diarization():
    text = _read()
    # The toggle uses Switch with a `disabled={!diarization}` guard
    # AND its row gets the .disabled class when diarization is off.
    assert re.search(r"Switch[^>]*on=\{charIdentify\}[^>]*disabled=\{!diarization\}", text)
    assert "create.charIdentify.requireDiarization" in text


def test_healthz_renders_all_four_fields():
    text = _read()
    # FFmpeg health used to be a plain `ffmpeg_ok` HealthRow tag;
    # since the 系统组件 redesign it is surfaced by the FfmpegInstaller
    # component (which talks to /system/components — the same source the
    # /healthz field is computed from server-side). The UI must therefore
    # mention either `ffmpeg_ok` (legacy renderer) OR the FfmpegInstaller
    # entry point, never neither.
    assert ("ffmpeg_ok" in text) or ("FfmpegInstaller" in text), (
        "FFmpeg health surface missing — expected ffmpeg_ok HealthRow or FfmpegInstaller component"
    )
    for f in (
        "playwright_ok",
        "playwright_browser_ready",
        "dashscope_api_key_present",
    ):
        assert f in text, f"healthz field missing in UI: {f}"


def test_sse_event_wired():
    text = _read()
    assert 'onEvent("task_update"' in text


def test_no_drawer_pattern():
    # Defensive: tongyi-image right-side preview, not a slide-in drawer.
    text = _read().lower()
    assert "drawer" not in text


def test_self_contained_no_host_sdk_reference():
    text = _read()
    # Must not depend on the removed host-mounted /api/plugins/_sdk/* path.
    assert "/_sdk/" not in text
