"""Plan-default sanity tests.

These pin down the user-facing default values for the "force fetch
latest news" toggles. They should ship enabled by default for new
installations / fresh plans so daily-brief and radar runs always work
against the latest articles, but must NOT clobber existing user state
that explicitly opted out (verified indirectly via _normalize_*).
"""

from __future__ import annotations

# Reuse the isolated-import helper from test_schedule (which stubs out
# ``openakita.plugins.api`` so plugin.py imports cleanly under any host
# bootstrap state).
from tests.test_schedule import _load_plugin_module

plugin_mod = _load_plugin_module()


# ── Radar plan ──────────────────────────────────────────────────────
#
# ``_normalize_*`` are instance methods on ``Plugin`` but never touch
# ``self`` — we can call them as unbound functions with a sentinel
# instead of standing up a full ``Plugin`` (which would require a
# real ``PluginAPI`` host handle).


_normalize_radar_plan = plugin_mod.Plugin._normalize_radar_plan
_normalize_report_plan = plugin_mod.Plugin._normalize_report_plan


def test_default_radar_plan_force_refresh_enabled() -> None:
    plan = plugin_mod._default_radar_plan()
    assert plan["force_refresh"] is True, (
        "fresh radar plans should pre-pull the latest news so the first "
        "run never returns an empty hit list"
    )


def test_normalize_radar_plan_defaults_force_refresh_when_unset() -> None:
    # Caller passes a plan dict that does NOT carry the field — must
    # default to True so an old persisted plan that pre-dates the field
    # benefits from the new behaviour on first load.
    normalized = _normalize_radar_plan(None, {})
    assert normalized["force_refresh"] is True


def test_normalize_radar_plan_respects_explicit_false() -> None:
    normalized = _normalize_radar_plan(None, {"force_refresh": False})
    assert normalized["force_refresh"] is False, (
        "users who explicitly opted out must keep their setting"
    )


# ── Report (daily brief) plan ───────────────────────────────────────


def test_normalize_report_plan_defaults_pre_ingest_when_unset() -> None:
    normalized = _normalize_report_plan(None, {"id": "morning"})
    assert normalized["preIngest"] is True


def test_normalize_report_plan_respects_explicit_false() -> None:
    normalized = _normalize_report_plan(
        None, {"id": "morning", "preIngest": False}
    )
    assert normalized["preIngest"] is False
