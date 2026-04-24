"""Unit tests for the ``GET /sources`` backend helper.

The route itself is a thin serialisation wrapper over
``finpulse_models.SOURCE_DEFS``. We verify that the mapping hasn't
drifted (so the frontend KNOWN_SOURCES fallback stays in sync) and
that every entry carries the fields the UI relies on.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))


def _load_source_defs() -> dict[str, dict[str, object]]:
    from finpulse_models import SOURCE_DEFS  # type: ignore

    return SOURCE_DEFS


def test_source_defs_contain_expected_canonical_ids() -> None:
    """SOURCE_DEFS is the single source of truth for data-source ids.
    Losing one of these ids would silently break the Today-tab filter
    dropdown, so we assert on the canonical set explicitly.
    """
    defs = _load_source_defs()
    expected = {
        "wallstreetcn",
        "cls",
        "xueqiu",
        "eastmoney",
        "pbc_omo",
        "nbs",
        "fed_fomc",
        "sec_edgar",
        "rss_generic",
        "newsnow",
    }
    assert expected.issubset(set(defs.keys())), (
        f"SOURCE_DEFS drifted — missing: {expected - set(defs.keys())}"
    )


def test_source_defs_shape_is_ui_friendly() -> None:
    """Every entry must expose the three fields the /sources route
    serialises for the UI: ``display_zh``, ``display_en``, and
    ``default_enabled``. Extra keys are fine.
    """
    defs = _load_source_defs()
    for sid, meta in defs.items():
        assert isinstance(sid, str) and sid, f"blank source id encountered"
        assert "display_zh" in meta, f"{sid!r} missing display_zh"
        assert "display_en" in meta, f"{sid!r} missing display_en"
        assert "default_enabled" in meta, f"{sid!r} missing default_enabled"


def test_frontend_known_sources_fallback_matches() -> None:
    """The UI keeps a static KNOWN_SOURCES fallback for the first paint
    before the async /sources response lands. If an id drifts between
    the frontend fallback and the backend dictionary the user would
    see an empty article list again (exactly the P0 bug we just fixed).
    """
    index_html = (PLUGIN_DIR / "ui" / "dist" / "index.html").read_text("utf-8")
    defs = _load_source_defs()
    # The fallback is an array of {id, name} objects; assert the three
    # most-used finance-first ids survive.
    for required_id in ("wallstreetcn", "cls", "pbc_omo", "fed_fomc", "newsnow"):
        assert f'id: "{required_id}"' in index_html, (
            f"KNOWN_SOURCES fallback missing canonical id: {required_id!r}"
        )
        assert required_id in defs, f"SOURCE_DEFS missing id: {required_id!r}"


def test_plugin_exposes_sources_route() -> None:
    """The GET /sources route is registered as part of the
    read-only surface. Assert the route signature is present in
    plugin.py so the host bridge mounts it at
    /api/plugins/fin-pulse/sources.
    """
    plugin_src = (PLUGIN_DIR / "plugin.py").read_text("utf-8")
    assert '@router.get("/sources")' in plugin_src, (
        "GET /sources route missing from plugin.py"
    )
    # Must also register the scheduler-channel proxy (P1).
    assert '@router.get("/scheduler/channels")' in plugin_src, (
        "GET /scheduler/channels proxy missing from plugin.py"
    )
