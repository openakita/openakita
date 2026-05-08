# ruff: noqa: N999
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))


def test_manifest_matches_tool_contract() -> None:
    from media_models import BRAND, DISPLAY_NAME_ZH, SLOGAN, TOOL_NAMES

    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text("utf-8"))
    assert manifest["id"] == "media-strategy"
    assert manifest["display_name_zh"] == DISPLAY_NAME_ZH == "融媒智策"
    assert manifest["slogan"] == SLOGAN
    assert manifest["brand"]["primary"] == BRAND["primary"] == "#0F766E"
    assert manifest["brand"]["iconify"] == "solar:radar-linear"
    assert set(manifest["provides"]["tools"]) == set(TOOL_NAMES)
    assert manifest["provides"]["skill"] == "SKILL.md"
    for perm in ("tools.register", "routes.register", "data.own", "brain.access", "channel.send"):
        assert perm in manifest["permissions"]


def test_ui_assets_and_iconify_tokens_exist() -> None:
    ui = PLUGIN_DIR / "ui" / "dist"
    html = (ui / "index.html").read_text("utf-8")
    assert len(html) > 1024
    for asset in ("bootstrap.js", "styles.css", "icons.js", "i18n.js", "markdown-mini.js"):
        assert (ui / "_assets" / asset).exists()
    assert "/api/plugins/_sdk/" not in html
    assert "solar:radar-linear" in html
    assert "#0F766E" in html
    for token in ("/radar", "/ingest", "/sources/sync", "/packages/subscribe"):
        assert token in html
    for icon in (PLUGIN_DIR / "icon.svg", ui / "icon.svg", ui / "media-strategy-brand.svg"):
        blob = icon.read_text("utf-8")
        assert "<svg" in blob and "Iconify source: solar:radar-linear" in blob


def test_builtin_source_catalog_is_rich() -> None:
    from media_models import SOURCE_DEFS

    assert len(SOURCE_DEFS) >= 18
    for required in (
        "cctv-domestic",
        "cctv-hk-tw",
        "bbc-zh",
        "zaobao-china",
        "taiwan-info",
        "diplomat-china-power",
    ):
        assert required in SOURCE_DEFS
    packages = {pkg for source in SOURCE_DEFS.values() for pkg in source["packages"]}
    assert {"policy", "taiwan", "economy", "world", "tech", "platform"}.issubset(packages)


def test_feed_parser_stdlib_fallback() -> None:
    from media_fetchers import rss

    body = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>x</title>
      <item><title>台海政策新动态</title><link>https://example.com/a</link>
      <description><![CDATA[<p>摘要</p>]]></description><pubDate>Fri, 08 May 2026 01:00:00 GMT</pubDate></item>
    </channel></rss>"""
    old = rss.FEEDPARSER_AVAILABLE
    rss.FEEDPARSER_AVAILABLE = False
    try:
        items = rss.parse_feed("demo", body)
    finally:
        rss.FEEDPARSER_AVAILABLE = old
    assert len(items) == 1
    assert items[0].title == "台海政策新动态"
    assert items[0].summary == "摘要"


def test_validate_feed_url_rejects_localhost() -> None:
    from media_fetchers.rss import UnsafeFeedUrl, validate_feed_url

    with pytest.raises(UnsafeFeedUrl):
        validate_feed_url("http://localhost:8080/rss")


@pytest.mark.asyncio
async def test_task_manager_seeds_and_upserts_article(tmp_path: Path) -> None:
    from media_task_manager import MediaTaskManager

    tm = MediaTaskManager(tmp_path / "media.sqlite")
    await tm.init()
    try:
        packages = await tm.list_packages()
        assert packages["taiwan"]["enabled"] is True
        sources = await tm.list_sources()
        assert len(sources) >= 18
        toggled = await tm.set_source_enabled("cctv-domestic", False)
        assert toggled["enabled"] is False
        source = await tm.add_custom_source(
            name="Demo",
            url="https://example.com/rss.xml",
            package_ids=["taiwan"],
        )
        assert source["custom"] is True
        article, inserted = await tm.upsert_article(
            {
                "source_id": source["id"],
                "package_ids": ["taiwan"],
                "url": "https://example.com/a",
                "title": "台海政策新动态",
                "summary": "摘要",
                "hot_score": 6.5,
                "risk_level": "medium",
            }
        )
        assert inserted is True
        assert article["id"].startswith("ms-a-")
        article2, inserted2 = await tm.upsert_article(
            {
                "source_id": source["id"],
                "package_ids": ["taiwan"],
                "url": "https://example.com/a",
                "title": "台海政策新动态",
            }
        )
        assert inserted2 is False
        assert article2["duplicate_count"] == 2
    finally:
        await tm.close()


@pytest.mark.asyncio
async def test_brief_falls_back_without_brain() -> None:
    from media_ai.analyzer import build_brief

    md, source = await build_brief(
        None,
        [{"title": "政策发布", "url": "https://example.com", "source_id": "demo", "hot_score": 7}],
        title="融媒智策早报",
        session="morning",
    )
    assert source == "fallback"
    assert "融媒智策早报" in md
    assert "https://example.com" in md
