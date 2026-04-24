"""UI hard-contract smoke tests for fin-pulse.

These asserts mirror §11 of the plan and make sure the single-page UI
stays aligned with the avatar-studio UI Kit conventions:

* ``_assets/`` bundles the five canonical files
  (``bootstrap.js`` / ``styles.css`` / ``icons.js`` / ``i18n.js`` /
  ``markdown-mini.js``).
* The shipped ``index.html`` declares the five bundle tags verbatim,
  the five canonical ``data-theme`` / ``PluginErrorBoundary`` /
  ``TAB_IDS`` tokens, and none of the forbidden ones (SDK 0.6 paths,
  ``classList.toggle('dark-mode')``, ``ReactDOM.render``).
* The plugin manifest loads cleanly and enumerates exactly the seven
  V1.0 agent tools that ``plugin.py`` registers on ``on_load``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
UI_DIR = PLUGIN_DIR / "ui" / "dist"
ASSETS_DIR = UI_DIR / "_assets"
INDEX_HTML = UI_DIR / "index.html"
MANIFEST = PLUGIN_DIR / "plugin.json"


def test_assets_present() -> None:
    """The five vendored UI Kit assets must live under ui/dist/_assets."""
    expected = {"bootstrap.js", "styles.css", "icons.js", "i18n.js", "markdown-mini.js"}
    actual = {p.name for p in ASSETS_DIR.iterdir() if p.is_file()}
    missing = expected - actual
    assert not missing, f"missing UI Kit assets: {missing}"


def test_sidebar_icon_present() -> None:
    """plugins/fin-pulse/icon.svg is auto-discovered by the host to render
    the sidebar app-launcher icon (see api/routes/plugins.py _ICON_NAMES).
    The file must exist, be non-trivial, and declare an <svg> root.
    Also served from ui/dist/icon.svg so ``ui.icon`` resolves through the
    PluginAppHost loading splash.
    """
    for candidate in (PLUGIN_DIR / "icon.svg", UI_DIR / "icon.svg"):
        assert candidate.exists(), f"icon.svg missing: {candidate}"
        blob = candidate.read_text("utf-8")
        assert "<svg" in blob and "viewBox" in blob, f"{candidate} is not a valid SVG"
        assert len(blob) > 256, f"{candidate} seems too small / empty"
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    assert manifest["ui"]["icon"] == "icon.svg", "plugin.json ui.icon should be icon.svg"


def test_index_html_exists_and_nonempty() -> None:
    assert INDEX_HTML.exists(), "ui/dist/index.html missing"
    html = INDEX_HTML.read_text("utf-8")
    assert len(html) > 1024, "index.html is suspiciously short"


def test_ui_hard_contracts() -> None:
    """Every required token from §11 must appear; every forbidden must not."""
    html = INDEX_HTML.read_text("utf-8")
    required = [
        r'<script\s+src="_assets/bootstrap\.js"',
        r'<link\s+rel="stylesheet"\s+href="_assets/styles\.css"',
        r'<script\s+src="_assets/icons\.js"',
        r'<script\s+src="_assets/i18n\.js"',
        r'<script\s+src="_assets/markdown-mini\.js"',
        r'data-theme="dark"',
        r'@media\s*\(prefers-color-scheme:\s*dark\)',
        r'const\s+TAB_IDS\s*=\s*\[\s*"today"\s*,\s*"digests"\s*,\s*"radar"\s*,\s*"ask"\s*,\s*"settings"\s*\]',
        r'const\s+TAB_ICONS\s*=',
        r'class\s+PluginErrorBoundary\s+extends\s+React\.Component',
        r'ReactDOM\.createRoot\(document\.getElementById\("root"\)\)',
        r'window\.OpenAkitaI18n\.register\(I18N_DICT\)',
        r'"tabs\.today"',
        r'"tabs\.digests"',
        r'"tabs\.radar"',
        r'"tabs\.ask"',
        r'"tabs\.settings"',
        r'oa-config-banner',
        r'oa-hero-title',
        r'oa-section-title',
        r'stack-layout',
        r'seg-group',
        r'seg-btn',
        r'filter-bar',
        r'oa-hint',
        r'BrandMark',
        r'api-pill',
        r'ConfirmHost',
        r'setInterval',
        # P0 — api() must unwrap Bridge {ok,status,body} envelopes.
        r'"body"\s+in\s+res',
        # P0 — oaToast must send {type, body} for the host notify API.
        r'type:\s*level',
        r'body:\s*message',
        # P1 — radar editor is kept compact; hit preview grows.
        r'card--compact',
        # P1 — templated schedule buttons exist instead of window.prompt.
        r'SCHEDULE_TEMPLATES',
        r'ScheduleDialog',
        # P2 — CodeBlock component for NewsNow docker snippet.
        r'fp-codeblock',
        # P2 — Darker muted / dim tokens.
        r'--text-muted:\s*#5E3540',
        r'--text-dim:\s*#7A4E59',
    ]
    for pat in required:
        assert re.search(pat, html), f"hard contract missing: {pat}"

    forbidden = [
        r'/api/plugins/_sdk/',
        r"classList\.toggle\('dark-mode'\)",
        # Raw ReactDOM.render is forbidden; createRoot is required.
        r'ReactDOM\.render\(',
        # The fin-pulse custom event names never fired — we must not
        # register listeners for them any more.
        r'onEvent\("plugin:fin-pulse:',
        # The Settings schedule card should no longer use window.prompt
        # for mode/cron/session — replaced by ScheduleDialog.
        r'window\.prompt\("mode\s*\(daily_brief',
    ]
    for pat in forbidden:
        assert not re.search(pat, html), f"forbidden token present: {pat}"


def test_plugin_manifest_matches_tool_shape() -> None:
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    assert manifest["id"] == "fin-pulse"
    assert manifest["type"] == "python"
    assert manifest["entry"] == "plugin.py"
    # Seven V1.0 agent tools — keep in lockstep with plugin.py::_tool_definitions.
    expected_tools = {
        "fin_pulse_create",
        "fin_pulse_status",
        "fin_pulse_list",
        "fin_pulse_cancel",
        "fin_pulse_settings_get",
        "fin_pulse_settings_set",
        "fin_pulse_search_news",
    }
    assert set(manifest["provides"]["tools"]) == expected_tools
    # Permissions MUST include the critical eleven.
    for perm in (
        "tools.register",
        "routes.register",
        "hooks.basic",
        "data.own",
        "channel.send",
        "brain.access",
        "config.read",
        "config.write",
    ):
        assert perm in manifest["permissions"], f"permission '{perm}' missing"


def test_fallback_modes_mirror_models_module() -> None:
    """plugin.py's _FALLBACK_MODES must stay a superset of the Phase-1b
    canonical modes so the /modes route keeps a meaningful payload even
    if the models import ever regresses.
    """
    plugin_src = (PLUGIN_DIR / "plugin.py").read_text("utf-8")
    for mode in ("daily_brief", "hot_radar", "ask_news"):
        assert f'"{mode}"' in plugin_src, f"plugin.py missing fallback mode: {mode}"


def test_ui_tabs_are_hydrated() -> None:
    """Phase 6 — each tab body must talk to the REST surface instead of
    rendering the Phase-1 placeholder. We assert that the hot-path API
    calls and the NewsNow 3-stage wizard tokens are present.
    """
    html = INDEX_HTML.read_text("utf-8")

    # Today tab talks to /articles + /ingest and uses the new
    # /sources route to hydrate its dropdown (P0 — avoids drift with
    # finpulse_models.SOURCE_DEFS).
    assert 'api("GET", "/articles"' in html
    assert 'api("POST", "/ingest"' in html
    assert 'api("GET", "/sources"' in html

    # Digests tab lists + runs + iframes html blobs.
    assert 'api("GET", "/digests' in html
    assert 'api("POST", "/digest/run"' in html
    assert '/digests/' in html and '/html' in html

    # Radar tab exercises the evaluate + config save paths.
    assert 'api("POST", "/radar/evaluate"' in html
    assert 'radar_rules' in html
    # Phase 6b — AI optimise + template + history + naming-save tokens.
    assert 'api("POST", "/radar/ai-suggest"' in html
    assert 'api("GET", "/radar/library"' in html
    assert 'api("POST", "/radar/library"' in html
    assert 'api("DELETE", "/radar/library/"' in html
    for key in (
        "radar.template",
        "radar.ai",
        "radar.history",
        "radar.save.title",
        "radar.save.placeholder",
    ):
        assert key in html, f"Radar i18n key missing: {key}"

    # Ask tab surfaces the 7 agent tools.
    for tool in (
        "fin_pulse_create",
        "fin_pulse_status",
        "fin_pulse_list",
        "fin_pulse_cancel",
        "fin_pulse_settings_get",
        "fin_pulse_settings_set",
        "fin_pulse_search_news",
    ):
        assert tool in html, f"Ask tab missing tool: {tool}"

    # Settings tab exposes channels / schedules / NewsNow wizard.
    # P1 — channels are fed from the host /scheduler/channels proxy so
    # the dropdown matches SchedulerView. /available-channels is kept
    # only as a fallback.
    assert 'api("GET", "/scheduler/channels"' in html
    assert 'api("GET", "/available-channels"' in html
    assert 'api("GET", "/schedules"' in html
    assert 'api("POST", "/schedules"' in html
    assert 'newsnow.mode' in html and 'newsnow.api_url' in html
    assert 'settings.newsnow.step1' in html
    assert 'settings.newsnow.step2' in html
    assert 'settings.newsnow.step3' in html
    # P1 — schedule templates (morning / noon / evening / radar).
    for tpl in ("morning", "noon", "evening", "radar"):
        assert f'settings.schedules.tpl.{tpl}' in html, f"schedule tpl i18n missing: {tpl}"

    # P2 — in-page schedule row actions: run / toggle / delete talk to
    # our own /schedules/{id}/... routes, not the host SchedulerView.
    assert '/schedules/" + s.id + "/trigger' in html, (
        "expected per-row trigger call"
    )
    assert '/schedules/" + s.id + "/toggle' in html, (
        "expected per-row toggle call"
    )
    assert '"DELETE", "/schedules/" + s.id' in html, (
        "expected per-row delete call"
    )
    # P2 — the redirect-to-host button and read-only banner must have
    # been removed now that the in-page dialog is authoritative.
    assert 'settings.schedules.open_host' not in html, (
        "open_host redirect affordance should be gone"
    )
    assert 'settings.schedules.read_only' not in html, (
        "read-only preview banner should be gone"
    )
    # P2 — IM-channels card exposes an explicit refresh button and no
    # longer carries the stale hint about "main program → SchedulerView".
    assert 'settings.channels.refresh' in html
    assert 'settings.channels.desc' not in html
    # P2 — NewsNow defaults to the public aggregator: the pre-filled
    # upstream URL and the 300-second rate-limit floor ship embedded in
    # the HTML bundle.
    assert 'https://newsnow.busiyi.world/api/s' in html
    assert 'NEWSNOW_PUBLIC_MIN_INTERVAL_S' in html
    assert 'newsnow.min_interval_s' in html

    # P1 — global "no IM channel" banner must only live inside the
    # Settings IM-channels card, not at the App shell level.
    assert "showChannelBanner" not in html, (
        "the legacy App-shell channel banner should have been removed"
    )
