"""Unit tests for the ``GET /sources`` backend helper.

The route itself is a thin serialisation wrapper over
``finpulse_models.SOURCE_DEFS``. We verify that the mapping hasn't
drifted and that every entry carries the fields the dynamic UI relies on.
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
        "wallstreetcn-quick",
        "cls",
        "cls-telegraph",
        "cls-hot",
        "xueqiu",
        "xueqiu-hotstock",
        "fastbull-news",
        "eastmoney",
        "pbc_omo",
        "yicai",
        "nbd",
        "stcn",
        "nbs",
        "fed_fomc",
        "sec_edgar",
        "rss_generic",
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


def test_iter_sources_for_ui_exposes_fetch_contract() -> None:
    """The frontend no longer ships a static KNOWN_SOURCES fallback.
    It depends on the backend contract to explain source display groups
    and the executable fetcher/probe target for each row.
    """
    from finpulse_models import iter_sources_for_ui  # type: ignore

    items = iter_sources_for_ui()
    by_id = {item["id"]: item for item in items}
    assert len(items) == len(_load_source_defs())
    assert by_id["wallstreetcn"]["group"] == "newsnow"
    assert by_id["wallstreetcn"]["fetcher_id"] == "newsnow"
    assert by_id["wallstreetcn"]["can_probe_individual"] is False
    assert by_id["eastmoney"]["fetcher_id"] == "eastmoney"
    assert by_id["rss_generic"]["group"] == "custom_rss"
    for item in items:
        assert item["probe_target"]
        assert isinstance(item["capabilities"], list)
        assert isinstance(item["ui_order"], int)


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
