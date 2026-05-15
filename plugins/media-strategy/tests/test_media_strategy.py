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
        "/external/open-url",
        "/storage/stats",
        "/storage/open-folder",
        "/storage/list-dir",
        "/storage/mkdir",
        "customTargetFormat",
        "planFeedback",
        "reportModalAnnotate",
        "annotationList",
        "按批注重新处理",
    ):
        assert token in html
    assert "[hidden] { display:none !important; }" in html
    assert 'data-tab="reports"' in html
    for icon in (PLUGIN_DIR / "icon.svg", ui / "icon.svg", ui / "media-strategy-brand.svg"):
        blob = icon.read_text("utf-8")
        assert "<svg" in blob and "Iconify source: game-icons:newspaper" in blob


def test_brief_workbench_presets_match_session_cards() -> None:
    html = (PLUGIN_DIR / "ui" / "dist" / "index.html").read_text("utf-8")

    assert '<option value="12" selected>12 小时</option>' in html
    assert "noon:{label:'每日午报', time:'12:30', since_hours:6, limit:15}" in html
    assert "scope:'preset'" in html
    assert "data-brief-role=\"scheduleStatus\"" in html
    assert "saveCustomSchedule" in html


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
    from media_models import DEPRECATED_SOURCE_IDS, SOURCE_DEFS

    taiwan_sources = {sid for sid, meta in SOURCE_DEFS.items() if "taiwan" in meta["packages"]}
    for required in (
        "xinhua-taiwan",
        "people-taiwan",
        "chinanews-taiwan",
        "huanqiu-taiwan",
        "crntt-rss",
        "udn-cross-strait",
        "chinatimes-politics",
        "chinatimes-cross-strait",
        "ettoday-mainland",
        "nownews-politics",
        "taiwan-info",
        # HTML-listing sources for outlets without public RSS.
        "taiwancn-jsbg",
        "taiwancn-top-news",
        "fjsen-taihai",
        "taihainet-twxw",
        "taihainet-home",
    ):
        assert required in taiwan_sources, required
    assert SOURCE_DEFS["fjsen-taihai"]["default_enabled"] is True
    assert SOURCE_DEFS["xinhua-taiwan"]["default_enabled"] is True
    assert SOURCE_DEFS["xinhua-taiwan"]["kind"] == "html"
    for stale_or_broken in ("nownews-politics",):
        assert stale_or_broken in DEPRECATED_SOURCE_IDS
    assert "people-taiwan" not in DEPRECATED_SOURCE_IDS
    assert "udn-cross-strait" not in DEPRECATED_SOURCE_IDS
    assert "chinatimes-politics" not in DEPRECATED_SOURCE_IDS
    assert "taihainet-twxw" not in DEPRECATED_SOURCE_IDS
    assert SOURCE_DEFS["people-taiwan"]["kind"] == "html"
    assert SOURCE_DEFS["taihainet-twxw"]["url"] == "https://tw.taihainet.com/"
    assert SOURCE_DEFS["udn-cross-strait"]["url"] == "https://udn.com/news/rssfeed/6638"
    assert SOURCE_DEFS["huanqiu-taiwan"]["selectors"]["parser"] == "huanqiu_csr"


def test_html_sources_declare_selectors() -> None:
    from media_models import SOURCE_DEFS

    for sid in (
        "taiwancn-jsbg",
        "taiwancn-top-news",
        "huanqiu-taiwan",
        "chinatimes-politics",
        "fjsen-taihai",
        "taihainet-twxw",
        "taihainet-home",
    ):
        meta = SOURCE_DEFS[sid]
        assert meta.get("kind") == "html", sid
        selectors = meta.get("selectors") or {}
        assert selectors.get("item") or selectors.get("parser"), sid


def test_default_enabled_strategy_favors_domestic() -> None:
    from media_models import DEPRECATED_SOURCE_IDS, RESTORED_SOURCE_IDS, SOURCE_DEFS

    # Default-enabled sources must be currently fetchable and timestamp-safe.
    enabled_ids = {
        sid
        for sid, meta in SOURCE_DEFS.items()
        if meta.get("default_enabled") and sid not in DEPRECATED_SOURCE_IDS
    }
    assert len(enabled_ids) >= 20
    assert SOURCE_DEFS["cctv-domestic"]["default_enabled"] is True
    assert SOURCE_DEFS["people-politics"]["default_enabled"] is True
    assert SOURCE_DEFS["people-world"]["default_enabled"] is True
    assert SOURCE_DEFS["caixin-latest"]["default_enabled"] is True
    assert SOURCE_DEFS["ithome"]["default_enabled"] is True
    assert SOURCE_DEFS["qbitai"]["default_enabled"] is True
    assert SOURCE_DEFS["fjsen-taihai"]["default_enabled"] is True
    for expanded_source in (
        "bbc-zh",
        "bbc-world",
        "rfi-cn",
        "zaobao-china",
        "zaobao-world",
        "diplomat-main",
        "diplomat-china-power",
        "idaily-today",
        "sspai",
        "ifanr",
        "solidot",
        "geekpark",
        "appinn",
        "meituan-tech",
        "xinhua-politics",
        "xinhua-taiwan",
        "xinhua-world",
        "cctv-xinwenlianbo",
        "dw-zh",
        "taihainet-twxw",
        "thepaper-featured",
        "kr36",
        "huxiu",
        "rsshub-douyin-hot",
        "rsshub-bilibili-weekly",
        "rsshub-weibo-hot",
        "rsshub-zhihu-hot",
        "newsnow-baidu-hot",
        "newsnow-toutiao-hot",
    ):
        assert SOURCE_DEFS[expanded_source]["default_enabled"] is True
        assert expanded_source in RESTORED_SOURCE_IDS
    assert RESTORED_SOURCE_IDS.isdisjoint(DEPRECATED_SOURCE_IDS)
    assert "yicai-news" in DEPRECATED_SOURCE_IDS
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


def test_feed_parser_infers_date_from_url_when_feed_date_missing() -> None:
    from media_fetchers import rss

    body = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>x</title>
      <item><title>国台办：两岸文博交流</title>
      <link>https://www.xinhuanet.com/tw/2013-10/16/c_125546161.htm</link>
      <description>摘要</description></item>
    </channel></rss>"""
    old = rss.FEEDPARSER_AVAILABLE
    rss.FEEDPARSER_AVAILABLE = False
    try:
        items = rss.parse_feed("demo", body)
    finally:
        rss.FEEDPARSER_AVAILABLE = old
    assert len(items) == 1
    assert items[0].published_at == "2013-10-16T00:00:00Z"


def test_feed_parser_skips_placeholder_dates() -> None:
    from media_fetchers import rss

    body = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>x</title>
      <item><title>旧接口空壳</title><link>https://example.com/a</link>
      <pubDate>Thu, 01 Jan 1970 08:00:00 +0800</pubDate></item>
    </channel></rss>"""
    old = rss.FEEDPARSER_AVAILABLE
    rss.FEEDPARSER_AVAILABLE = False
    try:
        items = rss.parse_feed("demo", body)
    finally:
        rss.FEEDPARSER_AVAILABLE = old
    assert items == []


def test_html_parser_handles_huanqiu_csr_blob() -> None:
    from media_fetchers.html import parse_huanqiu_channel

    html = (
        "4RXo3VkFDgyarticle有评论称岛内民众期盼和平统一的呼声越来越高，"
        "国台办：统一是不可阻挡的历史大势taiwan.huanqiu.com1778640892140"
    )
    items = parse_huanqiu_channel("huanqiu-taiwan", html, "https://taiwan.huanqiu.com/")
    assert len(items) == 1
    assert items[0].url == "https://taiwan.huanqiu.com/article/4RXo3VkFDgy"
    assert items[0].raw["parser"] == "huanqiu_csr"


def test_html_parser_handles_chinatimes_listing() -> None:
    from media_fetchers.html import parse_chinatimes_listing

    html = """
    <html><body><a href="/realtimenews/20260513000957-260409">
    川普今晚抵北京 全球「北京时间」启动 台海议题成焦点
    </a></body></html>
    """
    items = parse_chinatimes_listing(
        "chinatimes-cross-strait", html, "https://www.chinatimes.com/chinese/"
    )
    assert len(items) == 1
    assert items[0].published_at == "2026-05-13T00:00:00Z"


def test_single_article_parser_reads_metadata() -> None:
    from media_fetchers.html import parse_single_article

    html = """
    <html><head>
      <meta property="og:title" content="台海最新动态：两岸交流持续升温">
      <meta property="article:published_time" content="2026-05-13T10:30:00+08:00">
      <meta name="description" content="这是一条用于手动补充到雷达的新闻摘要。">
    </head><body><article><p>正文第一段，补充新闻背景。</p></article></body></html>
    """
    item = parse_single_article("manual-url", html, "https://example.com/news/20260513/a.html")
    assert item.title == "台海最新动态：两岸交流持续升温"
    assert item.published_at == "2026-05-13T02:30:00Z"
    assert item.raw["parser"] == "single_article"


def test_newsnow_parser_and_rate_limit() -> None:
    from media_fetchers.newsnow import _parse_envelope, newsnow_rate_limit_remaining

    payload = {
        "status": "cache",
        "updatedTime": 1778404729489,
        "items": [
            {
                "title": "平台热点样例",
                "url": "https://example.com/hot",
                "extra": {"hover": "热榜摘要"},
            }
        ],
    }
    items = _parse_envelope(payload, source_id="rsshub-weibo-hot", platform_id="weibo")

    assert len(items) == 1
    assert items[0].published_at == "2026-05-10T09:18:49Z"
    assert items[0].summary == "热榜摘要"
    assert items[0].raw["parser"] == "newsnow"
    assert (
        newsnow_rate_limit_remaining(
            {
                "newsnow.mode": "public",
                "newsnow.min_interval_s": 300,
                "newsnow.last_fetch_ts": "1000",
            },
            now_ts=1100,
        )
        == 200
    )
    assert (
        newsnow_rate_limit_remaining(
            {
                "newsnow.mode": "self_host",
                "newsnow.min_interval_s": 300,
                "newsnow.last_fetch_ts": "1000",
            },
            now_ts=1100,
        )
        == 0
    )


def test_feed_parser_skips_items_without_reliable_time() -> None:
    from media_fetchers import rss

    body = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>x</title>
      <item><title>没有发布时间的新闻</title>
      <link>https://example.com/news/latest.html</link>
      <description>摘要</description></item>
    </channel></rss>"""
    old = rss.FEEDPARSER_AVAILABLE
    rss.FEEDPARSER_AVAILABLE = False
    try:
        items = rss.parse_feed("demo", body)
    finally:
        rss.FEEDPARSER_AVAILABLE = old
    assert items == []


def test_replicate_prompt_accepts_user_revision_context() -> None:
    from media_ai.prompts import replicate_prompt

    prompt = replicate_prompt(
        [{"title": "台海政策新动态", "source_id": "demo", "url": "https://example.com"}],
        topic="台海最新动态",
        target_format="三分钟口播 + 图卡拆条",
        tone="本地融媒体口吻",
        revision_instructions="标题太硬，采访计划要更可执行。",
        annotations="保留第二部分，重写拍摄计划。",
        current_draft="## 选题判断\n原有判断保留。\n\n## 拍摄计划\n这里需要调整。",
    )

    assert "三分钟口播 + 图卡拆条" in prompt
    assert "标题太硬" in prompt
    assert "保留第二部分" in prompt
    assert "当前已有采编计划草稿" in prompt
    assert "优先只改对应位置" in prompt
    assert "不要因为局部批注而重写整篇" in prompt


def test_replicate_report_uses_dedicated_theme() -> None:
    from media_pipeline import _styled_report_html

    html = _styled_report_html(
        title="贸易策研采编计划",
        kind="replicate_plan",
        markdown="# 贸易策研采编计划\n\n## 选题判断\n\n内容",
        meta={"source": "brain"},
    )

    assert "采编执行" in html
    assert "迭代计划" in html
    assert "晨间速览" not in html
    assert ">晨<" not in html


def test_report_markdown_renderer_handles_llm_report_format() -> None:
    from media_ai.analyzer import markdown_to_html

    html = markdown_to_html(
        "\n".join(
            [
                "---",
                "### 📋 已有信源档案",
                "| 来源媒体 | 发布时间 (UTC) | 链接状态 |",
                "|---|---|---|",
                "| 联合早报 | 2026-05-09 14:08 | [查看链接](https://example.com) |",
                "### ⚖️ 交叉印证与真实性判断",
                "**多源情况**：**强交叉**。整合了多方信源。",
                "1. **极高敏感性**：必须标注“据外电报道”。",
                "2. **动态变化**：停火协议极其脆弱。",
            ]
        )
    )

    assert "<hr>" in html
    assert "<h3>📋 已有信源档案</h3>" in html
    assert "<table>" in html
    assert "<strong>多源情况</strong>" in html
    assert "<ol>" in html and "<li><strong>极高敏感性</strong>" in html
    assert "**" not in html
    assert "|---|" not in html


def test_report_markdown_renderer_drops_alignment_rows() -> None:
    from media_ai.analyzer import markdown_to_html

    html = markdown_to_html(
        "\n".join(
            [
                "| 序号 | 核心事件 | 来源媒体 |",
                "| :--- | :--- | :--- |",
                "| - | - | - |",
                "| :— | —: | :–: |",
                "| 01 | 测试事件 | 联合早报 |",
            ]
        )
    )

    assert "<tbody><tr><td>01</td><td>测试事件</td><td>联合早报</td></tr></tbody>" in html
    assert ":---" not in html
    assert "<td>-</td>" not in html


@pytest.mark.asyncio
async def test_report_push_renders_pdf_before_im_file_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import plugin as media_plugin

    async def fake_render(html: str, out_path: Path) -> None:
        assert "<html" in html
        out_path.write_bytes(b"%PDF-1.4 stub\n")

    class StubAdapter:
        def __init__(self) -> None:
            self.file_calls: list[dict[str, str]] = []
            self.text_calls: list[tuple[str, str]] = []

        def has_capability(self, name: str) -> bool:
            return name == "send_file"

        async def send_file(self, chat_id: str, file_path: str, caption: str = "") -> str:
            self.file_calls.append({"chat_id": chat_id, "file_path": file_path, "caption": caption})
            return "file-1"

        async def send_text(self, chat_id: str, text: str) -> str:
            self.text_calls.append((chat_id, text))
            return "text-1"

    class StubGateway:
        def __init__(self, adapter: StubAdapter) -> None:
            self.adapter = adapter

        def get_adapter(self, channel: str) -> StubAdapter | None:
            return self.adapter if channel == "wechat" else None

    class StubAPI:
        def __init__(self, adapter: StubAdapter) -> None:
            self._host = {"gateway": StubGateway(adapter)}
            self.logs: list[tuple[str, str]] = []

        def log(self, message: str, level: str = "info") -> None:
            self.logs.append((level, message))

    monkeypatch.setattr(media_plugin, "_render_report_html_to_pdf", fake_render)
    adapter = StubAdapter()
    p = media_plugin.Plugin()
    p._api = StubAPI(adapter)
    p._data_dir = tmp_path

    result = await p._push_report_to_channel(
        {
            "id": "r1",
            "title": "融媒智策晚报",
            "kind": "daily_brief",
            "markdown": "# 融媒智策晚报\n\n- 台海与 AI 重点动态",
            "html": "<html><body><h1>融媒智策晚报</h1></body></html>",
            "meta": {},
        },
        channel="wechat",
        chat_id="chat-1",
    )

    assert result["ok"] is True
    assert result["mode"] == "file"
    assert result["format"] == "pdf"
    assert adapter.file_calls[0]["file_path"].endswith(".pdf")
    assert adapter.file_calls[0]["caption"] == ""
    assert Path(adapter.file_calls[0]["file_path"]).read_bytes().startswith(b"%PDF")
    assert "已发送 PDF 报表附件" in adapter.text_calls[0][1]


def test_validate_feed_url_rejects_localhost() -> None:
    from media_fetchers.rss import UnsafeFeedUrl, validate_feed_url

    with pytest.raises(UnsafeFeedUrl):
        validate_feed_url("http://localhost:8080/rss")


@pytest.mark.asyncio
async def test_task_manager_seeds_and_upserts_article(tmp_path: Path) -> None:
    from media_models import DEPRECATED_SOURCE_IDS, RESTORED_SOURCE_IDS
    from media_task_manager import MediaTaskManager

    tm = MediaTaskManager(tmp_path / "media.sqlite")
    await tm.init()
    try:
        packages = await tm.list_packages()
        assert packages["taiwan"]["enabled"] is True
        sources = await tm.list_sources()
        source_ids = {source["id"] for source in sources}
        assert {"ithome", "qbitai", "fjsen-taihai"}.issubset(source_ids)
        assert {"xinhua-taiwan", "people-taiwan"}.issubset(source_ids)
        assert source_ids.isdisjoint(DEPRECATED_SOURCE_IDS)
        all_source_ids = {
            source["id"] for source in await tm.list_sources(include_deprecated=True)
        }
        assert "yicai-news" in all_source_ids - source_ids
        enabled_source_ids = {source["id"] for source in await tm.list_sources(enabled_only=True)}
        assert enabled_source_ids.isdisjoint(DEPRECATED_SOURCE_IDS)
        assert RESTORED_SOURCE_IDS.issubset(enabled_source_ids)
        await tm.set_source_enabled("people-politics", False)
        await tm.sync_builtin_sources()
        enabled_after_sync = {source["id"] for source in await tm.list_sources(enabled_only=True)}
        assert "people-politics" in enabled_after_sync
        toggled = await tm.set_source_enabled("ithome", False)
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
        html_source = await tm.add_custom_source(
            name="Demo HTML",
            url="https://example.com/news/",
            package_ids=["taiwan"],
            kind="html",
            selectors={"parser": "chinatimes_listing"},
        )
        assert html_source["kind"] == "html"
        assert html_source["selectors"]["parser"] == "chinatimes_listing"

        await tm.upsert_article(
            {
                "source_id": source["id"],
                "package_ids": ["taiwan"],
                "url": "https://example.com/old",
                "title": "十年前旧闻",
                "published_at": "2013-10-16T00:00:00Z",
                "fetched_at": "2026-05-10T00:00:00Z",
                "hot_score": 9.9,
            }
        )
        recent = await tm.recent_articles(since_hours=24, package_id="taiwan", limit=20)
        assert all(item["title"] != "十年前旧闻" for item in recent)
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
async def test_disabled_package_does_not_show_historical_radar_items(tmp_path: Path) -> None:
    from media_pipeline import MediaPipeline
    from media_task_manager import MediaTaskManager

    class DummyApi:
        def get_brain(self) -> None:
            return None

    tm = MediaTaskManager(tmp_path / "ms.sqlite")
    await tm.init()
    try:
        await tm.set_package_enabled("taiwan", False)
        await tm.upsert_article(
            {
                "source_id": "demo",
                "package_ids": ["taiwan"],
                "url": "https://example.com/taiwan",
                "title": "台海历史数据",
                "summary": "应被停用套餐过滤",
                "published_at": "2026-05-10T00:00:00Z",
                "fetched_at": "2026-05-10T00:01:00Z",
                "hot_score": 9.0,
            }
        )
        pipeline = MediaPipeline(tm, DummyApi(), output_dir=tmp_path)
        radar = await pipeline.hot_radar(
            {"package_id": "taiwan", "since_hours": 24, "limit": 20}
        )
        assert radar["items"] == []
        assert radar["stats"]["package_disabled"] is True
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
