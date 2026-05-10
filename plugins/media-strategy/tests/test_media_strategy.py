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
    assert manifest["brand"]["iconify"] == "game-icons:newspaper"
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
    assert "game-icons:newspaper" in html
    assert "#0F766E" in html
    for token in (
        "/radar",
        "/ingest",
        "/sources/sync",
        "/packages/subscribe",
        "/ai/analyze-top",
        "/reports",
        "/storage/stats",
        "/storage/open-folder",
        "/storage/list-dir",
        "/storage/mkdir",
    ):
        assert token in html
    assert "[hidden] { display:none !important; }" in html
    assert 'data-tab="reports"' in html
    for icon in (PLUGIN_DIR / "icon.svg", ui / "icon.svg", ui / "media-strategy-brand.svg"):
        blob = icon.read_text("utf-8")
        assert "<svg" in blob and "Iconify source: game-icons:newspaper" in blob


def test_builtin_source_catalog_is_rich() -> None:
    from media_models import SOURCE_DEFS

    assert len(SOURCE_DEFS) >= 37
    for required in (
        "cctv-domestic",
        "cctv-hk-tw",
        "bbc-zh",
        "zaobao-china",
        "taiwan-info",
        "diplomat-china-power",
        "people-politics",
        "xinhua-politics",
        "thepaper-featured",
        "yicai-news",
        "caixin-latest",
        "kr36",
        "ithome",
        "jiqizhixin",
        "qbitai",
        "rsshub-douyin-hot",
        # Taiwan-strait sources added per the screenshot brief.
        "xinhua-taiwan",
        "people-taiwan",
        "chinanews-taiwan",
        "udn-cross-strait",
        "chinatimes-politics",
        "ettoday-mainland",
        "nownews-politics",
    ):
        assert required in SOURCE_DEFS
    packages = {pkg for source in SOURCE_DEFS.values() for pkg in source["packages"]}
    assert {"policy", "taiwan", "economy", "world", "tech", "platform"}.issubset(packages)


def test_taiwan_package_includes_new_sources() -> None:
    from media_models import SOURCE_DEFS

    taiwan_sources = {sid for sid, meta in SOURCE_DEFS.items() if "taiwan" in meta["packages"]}
    # All Taiwan-strait sources default to enabled per editorial policy.
    for required in (
        "xinhua-taiwan",
        "people-taiwan",
        "chinanews-taiwan",
        "udn-cross-strait",
        "chinatimes-politics",
        "ettoday-mainland",
        "nownews-politics",
        "taiwan-info",
        # HTML-listing sources for outlets without public RSS.
        "taiwancn-jsbg",
        "fjsen-taihai",
        "taihainet-twxw",
    ):
        assert required in taiwan_sources, required
        assert SOURCE_DEFS[required]["default_enabled"] is True, required


def test_html_sources_declare_selectors() -> None:
    from media_models import SOURCE_DEFS

    for sid in ("taiwancn-jsbg", "fjsen-taihai", "taihainet-twxw"):
        meta = SOURCE_DEFS[sid]
        assert meta.get("kind") == "html", sid
        selectors = meta.get("selectors") or {}
        assert "item" in selectors and selectors["item"], sid


def test_default_enabled_strategy_favors_domestic() -> None:
    from media_models import SOURCE_DEFS

    # Domestic sources are enabled by default; overseas Western outlets are not.
    assert SOURCE_DEFS["people-politics"]["default_enabled"] is True
    assert SOURCE_DEFS["yicai-news"]["default_enabled"] is True
    assert SOURCE_DEFS["rsshub-weibo-hot"]["default_enabled"] is True
    assert SOURCE_DEFS["bbc-zh"]["default_enabled"] is False
    assert SOURCE_DEFS["dw-zh"]["default_enabled"] is False
    assert SOURCE_DEFS["reuters-world"]["default_enabled"] is False


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
async def test_package_crud_and_source_editing(tmp_path: Path) -> None:
    from media_task_manager import MediaTaskManager

    tm = MediaTaskManager(tmp_path / "ms.sqlite")
    await tm.init()
    try:
        # Builtin packages are seeded into the dedicated table.
        pkgs = await tm.list_packages()
        assert "policy" in pkgs and pkgs["policy"]["custom"] is False

        # Create a custom package.
        custom = await tm.add_custom_package(
            label_zh="地缘安全",
            description="跨区域地缘冲突追踪",
            keywords=["地缘", "冲突"],
            enabled=True,
        )
        assert custom["custom"] is True
        assert custom["enabled"] is True
        assert custom["label_zh"] == "地缘安全"

        # Builtin packages cannot be deleted.
        with pytest.raises(PermissionError):
            await tm.delete_custom_package("policy")

        # Custom package can be edited.
        edited = await tm.update_package(
            custom["id"], description="新描述", keywords=["a", "b"]
        )
        assert edited["description"] == "新描述"
        assert edited["keywords"] == ["a", "b"]

        # Cloning a builtin produces a new custom package with the same metadata.
        clone = await tm.clone_builtin_package("taiwan", label_zh="我的台海")
        assert clone["custom"] is True
        assert clone["label_zh"] == "我的台海"

        # Source editing covers labels, packages, authority, enabled.
        src = await tm.add_custom_source(
            name="Demo", url="https://example.com/feed.xml", package_ids=[custom["id"]]
        )
        updated = await tm.update_source(
            src["id"], label_zh="新名字", authority=0.83, package_ids=[custom["id"], "policy"]
        )
        assert updated["label_zh"] == "新名字"
        assert abs(updated["authority"] - 0.83) < 1e-6
        assert set(updated["package_ids"]) == {custom["id"], "policy"}

        # Bulk toggle by package operates only on members of that package.
        stats = await tm.bulk_set_sources_enabled_for_package(custom["id"], False)
        assert stats["affected"] == 1
        sources_now = await tm.list_sources()
        for s in sources_now:
            if s["id"] == src["id"]:
                assert s["enabled"] is False

        # Builtin source cannot be deleted.
        with pytest.raises(PermissionError):
            await tm.delete_custom_source("cctv-domestic")

        # Deleting a custom package strips its id from sources but keeps the source.
        await tm.delete_custom_package(custom["id"])
        survivors = await tm.list_sources()
        my_src = next(s for s in survivors if s["id"] == src["id"])
        assert custom["id"] not in (my_src.get("package_ids") or [])
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


@pytest.mark.asyncio
async def test_brief_uses_host_brain_think() -> None:
    from media_ai.analyzer import build_brief

    class FakeBrain:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def think(self, prompt: str, **kwargs: object) -> object:
            self.calls.append({"prompt": prompt, **kwargs})
            return type("Resp", (), {"content": "# AI 简报\n\n已由大模型生成。"})()

    brain = FakeBrain()
    md, source = await build_brief(
        brain,
        [{"title": "政策发布", "url": "https://example.com", "source_id": "demo"}],
        title="融媒智策早报",
        session="morning",
    )

    assert source == "brain"
    assert "AI 简报" in md
    assert brain.calls
    assert brain.calls[0]["enable_thinking"] is False
    assert "融媒智策" in str(brain.calls[0]["system"])


@pytest.mark.asyncio
async def test_brain_content_parses_anthropic_blocks() -> None:
    from media_ai.analyzer import build_verify_pack

    class FakeBrain:
        async def messages_create_async(self, **_: object) -> object:
            return type(
                "Msg",
                (),
                {
                    "content": [
                        {"type": "text", "text": "# 复核清单"},
                        type("Block", (), {"text": "需要补查官方口径。"})(),
                    ]
                },
            )()

    md, source = await build_verify_pack(
        FakeBrain(),
        [{"title": "台海政策新动态", "url": "https://example.com", "source_id": "demo"}],
        topic="台海最新动态复核",
    )

    assert source == "brain"
    assert "# 复核清单" in md
    assert "需要补查官方口径" in md
    assert "{'type': 'text'" not in md


@pytest.mark.asyncio
async def test_topic_analysis_uses_brain_for_top_clusters() -> None:
    from media_ai.analyzer import build_topic_analysis

    class FakeBrain:
        async def think(self, prompt: str, **kwargs: object) -> object:
            assert "热点簇 JSON" in prompt
            assert kwargs["enable_thinking"] is False
            return type("Resp", (), {"content": "# AI 选题分析报告\n\n## Top 3"})()

    md, source = await build_topic_analysis(
        FakeBrain(),
        [
            {
                "title": "国台办：坚决反对外部势力干涉",
                "url": "https://example.com/a",
                "source_ids": ["xinhua-taiwan", "people-taiwan"],
                "weighted_score": 9.2,
                "risk_level": "low",
                "evidence": [],
            }
        ],
    )

    assert source == "brain"
    assert "AI 选题分析报告" in md


def test_topic_signature_normalizes_prefixes() -> None:
    from media_ai.analyzer import topic_signature

    a = topic_signature("国台办：坚决反对外部势力干涉")
    b = topic_signature("【最新】国台办：坚决反对外部势力干涉")
    c = topic_signature("快讯丨国台办：坚决反对外部势力干涉")
    assert a and a == b == c
    # Different topic must not collapse into the same key.
    assert topic_signature("国务院：稳预期、稳增长、稳就业") != a


def test_cluster_topics_cross_source_ranking() -> None:
    """图2 的核心：多家媒体同时报道 + 权威加权 → 高权重选题。"""

    from media_ai.analyzer import cluster_topics

    items = [
        {
            "id": "ms-a-1",
            "source_id": "xinhua-taiwan",
            "title": "国台办：坚决反对外部势力干涉",
            "url": "https://x.example/news/1",
            "hot_score": 7.0,
            "risk_level": "low",
            "published_at": "2026-05-09T01:00:00Z",
        },
        {
            "id": "ms-a-2",
            "source_id": "people-taiwan",
            "title": "【最新】国台办：坚决反对外部势力干涉",
            "url": "https://p.example/news/1",
            "hot_score": 6.6,
            "risk_level": "low",
            "published_at": "2026-05-09T01:30:00Z",
        },
        {
            "id": "ms-a-3",
            "source_id": "chinanews-taiwan",
            "title": "快讯丨国台办：坚决反对外部势力干涉",
            "url": "https://cn.example/news/1",
            "hot_score": 6.4,
            "risk_level": "low",
            "published_at": "2026-05-09T02:00:00Z",
        },
        {
            "id": "ms-a-9",
            "source_id": "rsshub-weibo-hot",
            "title": "某明星新综艺定档",
            "url": "https://w.example/x",
            "hot_score": 7.5,
            "risk_level": "medium",
            "published_at": "2026-05-09T02:10:00Z",
        },
    ]
    clusters = cluster_topics(items)
    assert len(clusters) == 2
    top = clusters[0]
    # Cross-source coverage wins over a single high-score weibo trend.
    assert top["sources_count"] == 3
    assert set(top["source_ids"]) == {"xinhua-taiwan", "people-taiwan", "chinanews-taiwan"}
    assert top["weighted_score"] > clusters[1]["weighted_score"]
    assert top["risk_level"] == "low"
    assert top["url"].startswith("https://x.example/")
    assert {"ms-a-1", "ms-a-2", "ms-a-3"} == set(top["article_ids"])


def test_html_listing_explicit_selectors_extract_titles() -> None:
    from media_fetchers.html import parse_html_listing

    html = """
    <html><body>
      <ul class="list01">
        <li><a href="/news/twxw/2026-05-09_12345.shtml">国台办：坚决反对外部势力干涉</a></li>
        <li><a href="/news/twxw/2026-05-09_12346.shtml">两岸经济文化交流合作论坛在厦举行</a></li>
        <li><a href="javascript:void(0)">点击</a></li>
      </ul>
    </body></html>
    """
    items = parse_html_listing(
        "taihainet-twxw",
        html,
        "https://www.taihainet.com/news/twxw/",
        {"item": ".list01 li a"},
    )
    assert len(items) == 2
    assert items[0].title == "国台办：坚决反对外部势力干涉"
    assert items[0].url.startswith("https://www.taihainet.com/news/twxw/")
    assert all(i.source_id == "taihainet-twxw" for i in items)


def test_html_listing_heuristic_fallback() -> None:
    """When explicit selectors miss, the anchor heuristic should still work."""

    from media_fetchers.html import parse_html_listing

    html = """
    <html><body>
      <header><a href="/">首页</a></header>
      <main>
        <a href="/jsbg/2026/0509/c12345.shtml">国务院台办举行例行新闻发布会</a>
        <a href="/jsbg/2026/0509/c12346.shtml">两岸航空业界举办交流座谈</a>
        <a href="/jsbg/2026/0509/c12347.shtml">大陆惠台措施持续落地见效</a>
        <a href="/jsbg/2026/0509/c12348.shtml">海峡两岸青年文创周开幕</a>
        <a href="/jsbg/2026/0509/c12349.shtml">两岸高校学术交流活动启动</a>
        <a href="/about/">关于本网</a>
        <a href="javascript:void(0)">点击</a>
        <a href="https://www.taiwan.cn/">中国台湾网</a>
      </main>
    </body></html>
    """
    items = parse_html_listing(
        "taiwancn-jsbg",
        html,
        "https://www.taiwan.cn/jsbg/",
        # Selector that intentionally matches nothing → fallback to heuristic.
        {"item": ".does-not-exist"},
    )
    titles = {i.title for i in items}
    assert len(items) >= 5
    assert "国务院台办举行例行新闻发布会" in titles
    # 自指首页和 JS 锚点要被过滤掉
    assert all("javascript" not in i.url for i in items)
    assert all("首页" not in i.title for i in items)


def test_top_topics_tool_is_registered() -> None:
    """图2 输出形式：仅返回标题+原文链接，需要在 manifest 里曝出工具。"""

    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text("utf-8"))
    assert "media_strategy_top_topics" in manifest["provides"]["tools"]
    from media_models import TOOL_NAMES

    assert "media_strategy_top_topics" in TOOL_NAMES
