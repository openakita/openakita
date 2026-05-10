# ruff: noqa: N999
"""Pure metadata for the Media Strategy (融媒智策) plugin.

This module intentionally performs no I/O. The plugin entry, task
manager, RSS fetcher and tests can import it without pulling in the host
runtime or optional dependencies.
"""

from __future__ import annotations

from typing import Any, Final

PLUGIN_ID: Final = "media-strategy"
PLUGIN_VERSION: Final = "0.1.0"
DISPLAY_NAME_ZH: Final = "融媒智策"
DISPLAY_NAME_EN: Final = "Media Strategy"
SLOGAN: Final = "全源融聚・智察热点・策研采编"

BRAND: Final[dict[str, str]] = {
    "primary": "#0F766E",
    "primary_hover": "#0D9488",
    "primary_soft": "#CCFBF1",
    "dark_primary": "#2DD4BF",
    "iconify": "game-icons:newspaper",
}

MODES: Final[dict[str, dict[str, Any]]] = {
    "ingest": {
        "label_zh": "全源融聚",
        "label_en": "Ingest",
        "default_params": {"package_ids": [], "since_hours": 24},
    },
    "hot_radar": {
        "label_zh": "智察热点",
        "label_en": "Hot Radar",
        "default_params": {"category": "", "since_hours": 24, "limit": 20},
    },
    "daily_brief": {
        "label_zh": "融媒简报",
        "label_en": "Daily Brief",
        "default_params": {"session": "morning", "since_hours": 24, "limit": 20},
    },
    "verify_pack": {
        "label_zh": "信源复核",
        "label_en": "Verification Pack",
        "default_params": {"article_ids": [], "topic": ""},
    },
    "replicate_plan": {
        "label_zh": "策研采编",
        "label_en": "Editorial Plan",
        "default_params": {
            "article_ids": [],
            "topic": "",
            "target_format": "short_video",
            "tone": "稳健客观",
        },
    },
}

ERROR_HINTS: Final[dict[str, dict[str, list[str]]]] = {
    "network": {
        "zh": ["请检查网络连接或代理设置", "确认 RSS 源可从当前机器访问"],
        "en": ["Check network/proxy settings", "Verify the feed is reachable"],
    },
    "timeout": {
        "zh": ["源站响应过慢", "可缩短源列表或稍后重试"],
        "en": ["Source timed out", "Reduce sources or retry later"],
    },
    "dependency": {
        "zh": ["插件依赖缺失", "请在健康检查或设置页触发依赖安装"],
        "en": ["Plugin dependency is missing", "Use health/settings to install dependencies"],
    },
    "invalid_source": {
        "zh": ["RSS 地址不合法或存在安全风险", "请换用公开 http/https 订阅地址"],
        "en": ["Feed URL is invalid or unsafe", "Use a public http/https feed URL"],
    },
    "brain_unavailable": {
        "zh": ["主程序大模型不可用", "将仅返回规则分析结果"],
        "en": ["Host Brain is unavailable", "Only deterministic analysis is returned"],
    },
    "not_found": {
        "zh": ["未找到对应任务、文章或报告", "请刷新列表后重试"],
        "en": ["Task, article or report not found", "Refresh and retry"],
    },
    "unknown": {
        "zh": ["请复制 task_id 反馈给维护者", "或截图健康检查和任务详情"],
        "en": ["Report the task_id to maintainers", "Include health and task details"],
    },
}

PACKAGE_DEFS: Final[dict[str, dict[str, Any]]] = {
    "policy": {
        "label_zh": "时政政策",
        "label_en": "Policy",
        "description": "官方政策、宏观治理、公共议题与制度变化。",
        "keywords": ["政策", "国务院", "部委", "法规", "发布会", "治理"],
        "default_enabled": True,
    },
    "taiwan": {
        "label_zh": "台海观察",
        "label_en": "Taiwan Strait",
        "description": "台海相关公开报道、两岸政策与区域安全动态。",
        "keywords": ["台湾", "台海", "两岸", "国台办", "海峡", "赖清德"],
        "default_enabled": True,
    },
    "economy": {
        "label_zh": "经济财经",
        "label_en": "Economy",
        "description": "宏观经济、资本市场、产业政策与国际经贸。",
        "keywords": ["经济", "金融", "贸易", "关税", "汇率", "市场"],
        "default_enabled": True,
    },
    "world": {
        "label_zh": "国际局势",
        "label_en": "World",
        "description": "国际关系、地缘政治、冲突与外交动态。",
        "keywords": ["国际", "外交", "冲突", "安全", "美国", "日本"],
        "default_enabled": True,
    },
    "tech": {
        "label_zh": "科技产业",
        "label_en": "Tech",
        "description": "科技产业、AI、芯片、平台与新质生产力。",
        "keywords": ["科技", "AI", "人工智能", "芯片", "平台", "产业"],
        "default_enabled": True,
    },
    "platform": {
        "label_zh": "平台热点",
        "label_en": "Platform Trends",
        "description": "适合融媒体选题的热榜、舆论和传播观察源。",
        "keywords": ["热搜", "视频", "社交", "平台", "舆论", "传播"],
        "default_enabled": True,
    },
}

SOURCE_DEFS: Final[dict[str, dict[str, Any]]] = {
    "cctv-domestic": {
        "label_zh": "央视国内新闻",
        "label_en": "CCTV Domestic",
        "url": "http://www.cctv.com/program/rss/02/01/index.xml",
        "packages": ["policy"],
        "authority": 0.78,
        "default_enabled": True,
    },
    "cctv-international": {
        "label_zh": "央视国际新闻",
        "label_en": "CCTV International",
        "url": "http://www.cctv.com/program/rss/02/02/index.xml",
        "packages": ["world"],
        "authority": 0.76,
        "default_enabled": True,
    },
    "cctv-finance": {
        "label_zh": "央视财经新闻",
        "label_en": "CCTV Finance",
        "url": "http://www.cctv.com/program/rss/02/04/index.xml",
        "packages": ["economy", "policy"],
        "authority": 0.76,
        "default_enabled": True,
    },
    "cctv-hk-tw": {
        "label_zh": "央视港澳台新闻",
        "label_en": "CCTV HK/Taiwan",
        "url": "http://www.cctv.com/program/rss/02/10/index.xml",
        "packages": ["taiwan", "policy"],
        "authority": 0.76,
        "default_enabled": True,
    },
    "xinhua-taiwan": {
        "label_zh": "新华网 台湾",
        "label_en": "Xinhua Taiwan",
        "url": "http://www.xinhuanet.com/tw/news_tw.xml",
        "packages": ["taiwan", "policy"],
        "authority": 0.76,
        "default_enabled": False,
    },
    "people-taiwan": {
        "label_zh": "人民网 台湾频道",
        "label_en": "People.cn Taiwan",
        "url": "http://www.people.com.cn/rss/tw.xml",
        "packages": ["taiwan", "policy"],
        "authority": 0.74,
        "default_enabled": False,
    },
    "chinanews-taiwan": {
        "label_zh": "中国新闻网 台湾",
        "label_en": "China News Taiwan",
        "url": "https://www.chinanews.com.cn/rss/taiwan.xml",
        "packages": ["taiwan", "policy"],
        "authority": 0.74,
        "default_enabled": True,
    },
    "cctv-xinwenlianbo": {
        "label_zh": "新闻联播",
        "label_en": "Xinwen Lianbo",
        "url": "http://www.cctv.com/program/rss/02/09/index.xml",
        "packages": ["policy", "world"],
        "authority": 0.8,
        "default_enabled": True,
    },
    "people-politics": {
        "label_zh": "人民网 时政",
        "label_en": "People.cn Politics",
        "url": "http://www.people.com.cn/rss/politics.xml",
        "packages": ["policy"],
        "authority": 0.78,
        "default_enabled": True,
    },
    "people-world": {
        "label_zh": "人民网 国际",
        "label_en": "People.cn World",
        "url": "http://www.people.com.cn/rss/world.xml",
        "packages": ["world", "policy"],
        "authority": 0.74,
        "default_enabled": True,
    },
    "xinhua-politics": {
        "label_zh": "新华网 时政",
        "label_en": "Xinhua Politics",
        "url": "http://www.xinhuanet.com/politics/news_politics.xml",
        "packages": ["policy"],
        "authority": 0.78,
        "default_enabled": True,
    },
    "xinhua-world": {
        "label_zh": "新华网 国际",
        "label_en": "Xinhua World",
        "url": "http://www.xinhuanet.com/world/news_world.xml",
        "packages": ["world", "policy"],
        "authority": 0.74,
        "default_enabled": True,
    },
    "thepaper-featured": {
        "label_zh": "澎湃新闻 推荐",
        "label_en": "The Paper Featured",
        "url": "https://rsshub.app/thepaper/featured",
        "packages": ["policy", "world", "economy"],
        "authority": 0.7,
        "default_enabled": False,
    },
    "yicai-news": {
        "label_zh": "第一财经",
        "label_en": "Yicai",
        "url": "https://www.yicai.com/api/ajax/getrss/list/0",
        "packages": ["economy"],
        "authority": 0.72,
        "default_enabled": False,
    },
    "caixin-latest": {
        "label_zh": "财新网 最新",
        "label_en": "Caixin Latest",
        "url": "http://www.caixin.com/rss/latest.xml",
        "packages": ["economy", "policy"],
        "authority": 0.76,
        "default_enabled": True,
    },
    "wallstreetcn-global": {
        "label_zh": "华尔街见闻 全球",
        "label_en": "WallStreetCN Global",
        "url": "https://rsshub.app/wallstreetcn/news/global",
        "packages": ["economy", "world"],
        "authority": 0.7,
        "default_enabled": False,
    },
    "kr36": {
        "label_zh": "36 氪",
        "label_en": "36Kr",
        "url": "https://36kr.com/feed",
        "packages": ["tech", "economy"],
        "authority": 0.66,
        "default_enabled": True,
    },
    "huxiu": {
        "label_zh": "虎嗅",
        "label_en": "Huxiu",
        "url": "https://www.huxiu.com/rss/0.xml",
        "packages": ["tech", "platform"],
        "authority": 0.62,
        "default_enabled": True,
    },
    "ithome": {
        "label_zh": "IT 之家",
        "label_en": "IT Home",
        "url": "https://www.ithome.com/rss/",
        "packages": ["tech"],
        "authority": 0.6,
        "default_enabled": True,
    },
    "jiqizhixin": {
        "label_zh": "机器之心",
        "label_en": "Synced",
        "url": "https://www.jiqizhixin.com/rss",
        "packages": ["tech"],
        "authority": 0.7,
        "default_enabled": True,
    },
    "qbitai": {
        "label_zh": "量子位",
        "label_en": "QbitAI",
        "url": "https://www.qbitai.com/feed",
        "packages": ["tech"],
        "authority": 0.66,
        "default_enabled": True,
    },
    "guancha": {
        "label_zh": "观察者网",
        "label_en": "Guancha",
        "url": "https://rsshub.app/guancha/headline",
        "packages": ["policy", "world"],
        "authority": 0.6,
        "default_enabled": False,
    },
    "huanqiu-opinion": {
        "label_zh": "环球时报 评论",
        "label_en": "Huanqiu Opinion",
        "url": "https://opinion.huanqiu.com/feed/all",
        "packages": ["policy", "world"],
        "authority": 0.62,
        "default_enabled": False,
    },
    "rsshub-douyin-hot": {
        "label_zh": "抖音热门 RSSHub",
        "label_en": "RSSHub Douyin Hot",
        "url": "https://rsshub.app/douyin/hot",
        "packages": ["platform"],
        "authority": 0.42,
        "default_enabled": False,
    },
    "rsshub-bilibili-weekly": {
        "label_zh": "B 站每周必看 RSSHub",
        "label_en": "RSSHub Bilibili Weekly",
        "url": "https://rsshub.app/bilibili/popular/weekly",
        "packages": ["platform", "tech"],
        "authority": 0.46,
        "default_enabled": False,
    },
    "bbc-zh": {
        "label_zh": "BBC 中文",
        "label_en": "BBC Chinese",
        "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "packages": ["world", "taiwan", "policy"],
        "authority": 0.78,
        "default_enabled": False,
    },
    "bbc-world": {
        "label_zh": "BBC World",
        "label_en": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "packages": ["world", "taiwan"],
        "authority": 0.78,
        "default_enabled": False,
    },
    "dw-zh": {
        "label_zh": "德国之声中文",
        "label_en": "DW Chinese",
        "url": "https://rss.dw.com/rdf/rss-chi-all",
        "packages": ["world", "taiwan", "policy"],
        "authority": 0.72,
        "default_enabled": False,
    },
    "rfi-cn": {
        "label_zh": "法广中文",
        "label_en": "RFI Chinese",
        "url": "https://www.rfi.fr/cn/rss",
        "packages": ["world", "taiwan", "policy"],
        "authority": 0.7,
        "default_enabled": False,
    },
    "voa-zh": {
        "label_zh": "美国之音中文",
        "label_en": "VOA Chinese",
        "url": "https://www.voachinese.com/api/z$ote",
        "packages": ["world", "taiwan", "policy"],
        "authority": 0.68,
        "default_enabled": False,
    },
    "zaobao-china": {
        "label_zh": "联合早报中国即时",
        "label_en": "Zaobao China",
        "url": "https://www.zaobao.com.sg/realtime/china/rss.xml",
        "packages": ["policy", "taiwan", "economy"],
        "authority": 0.76,
        "default_enabled": False,
    },
    "zaobao-world": {
        "label_zh": "联合早报国际即时",
        "label_en": "Zaobao World",
        "url": "https://www.zaobao.com.sg/realtime/world/rss.xml",
        "packages": ["world", "economy"],
        "authority": 0.74,
        "default_enabled": False,
    },
    "chinadaily-china": {
        "label_zh": "中国日报 China",
        "label_en": "China Daily China",
        "url": "https://www.chinadaily.com.cn/rss/china_rss.xml",
        "packages": ["policy", "economy", "world"],
        "authority": 0.7,
        "default_enabled": True,
    },
    "chinadaily-world": {
        "label_zh": "中国日报 World",
        "label_en": "China Daily World",
        "url": "https://www.chinadaily.com.cn/rss/world_rss.xml",
        "packages": ["world", "policy"],
        "authority": 0.68,
        "default_enabled": False,
    },
    "chinadaily-business": {
        "label_zh": "中国日报 Business",
        "label_en": "China Daily Business",
        "url": "https://www.chinadaily.com.cn/rss/bizchina_rss.xml",
        "packages": ["economy", "tech"],
        "authority": 0.68,
        "default_enabled": False,
    },
    "taiwan-info": {
        "label_zh": "Taiwan Info",
        "label_en": "Taiwan Info",
        "url": "https://taiwaninfo.nat.gov.tw/rss",
        "packages": ["taiwan", "world"],
        "authority": 0.72,
        "default_enabled": True,
    },
    "udn-cross-strait": {
        "label_zh": "联合新闻网 兩岸",
        "label_en": "UDN Cross-Strait",
        "url": "https://udn.com/rssfeed/news/2/6638?ch=news",
        "packages": ["taiwan", "world"],
        "authority": 0.7,
        "default_enabled": True,
    },
    "chinatimes-politics": {
        "label_zh": "中时新闻网 政治",
        "label_en": "ChinaTimes Politics",
        "url": "https://www.chinatimes.com/politic/rss.xml",
        "packages": ["taiwan", "world"],
        "authority": 0.62,
        "default_enabled": False,
    },
    "ettoday-mainland": {
        "label_zh": "ETToday 中國大陸",
        "label_en": "ETToday Mainland",
        "url": "https://www.ettoday.net/rss/rss-cn.xml",
        "packages": ["taiwan", "world"],
        "authority": 0.66,
        "default_enabled": True,
    },
    "nownews-politics": {
        "label_zh": "今日新闻网 政治",
        "label_en": "NowNews Politics",
        "url": "https://www.nownews.com/cat/politics/feed/",
        "packages": ["taiwan", "world"],
        "authority": 0.6,
        "default_enabled": False,
    },
    # ---------------------------------------------------------------
    # HTML-listing sources for sites without public RSS feeds.
    # Selectors are intentionally conservative so the heuristic anchor
    # scan in media_fetchers/html.py can top up if a layout changes.
    # ---------------------------------------------------------------
    "taiwancn-jsbg": {
        "label_zh": "中国台湾网 即时报道",
        "label_en": "Taiwan.cn Updates",
        "kind": "html",
        "url": "https://www.taiwan.cn/jsbg/",
        "selectors": {
            "item": "ul li a, .news-list li a, .list a",
            "title": "",
            "link": "",
            "link_attr": "href",
        },
        "packages": ["taiwan", "policy"],
        "authority": 0.7,
        "default_enabled": False,
    },
    "fjsen-taihai": {
        "label_zh": "东南网 台海频道",
        "label_en": "Fjsen Taihai",
        "kind": "html",
        "url": "https://taihai.fjsen.com/",
        "selectors": {
            "item": "ul li a, .news_list li a, .list_news li a",
            "title": "",
            "link": "",
            "link_attr": "href",
        },
        "packages": ["taiwan"],
        "authority": 0.62,
        "default_enabled": True,
    },
    "taihainet-twxw": {
        "label_zh": "台海网 台湾新闻",
        "label_en": "Taihainet Taiwan News",
        "kind": "html",
        "url": "https://www.taihainet.com/news/twxw/",
        "selectors": {
            "item": "ul li a, .news-list li a, .list01 li a, .lieall li a",
            "title": "",
            "link": "",
            "link_attr": "href",
        },
        "packages": ["taiwan"],
        "authority": 0.62,
        "default_enabled": False,
    },
    "diplomat-main": {
        "label_zh": "The Diplomat",
        "label_en": "The Diplomat",
        "url": "https://thediplomat.com/feed/",
        "packages": ["world", "taiwan", "policy"],
        "authority": 0.72,
        "default_enabled": False,
    },
    "diplomat-china-power": {
        "label_zh": "The Diplomat China Power",
        "label_en": "The Diplomat China Power",
        "url": "https://thediplomat.com/category/china-power/feed/",
        "packages": ["taiwan", "policy", "world"],
        "authority": 0.73,
        "default_enabled": False,
    },
    "reuters-world": {
        "label_zh": "Reuters World",
        "label_en": "Reuters World",
        "url": "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best",
        "packages": ["world", "economy"],
        "authority": 0.82,
        "default_enabled": False,
    },
    "rsshub-weibo-hot": {
        "label_zh": "微博热搜 RSSHub",
        "label_en": "RSSHub Weibo Hot",
        "url": "https://rsshub.app/weibo/search/hot",
        "packages": ["platform"],
        "authority": 0.42,
        "default_enabled": False,
    },
    "rsshub-zhihu-hot": {
        "label_zh": "知乎热榜 RSSHub",
        "label_en": "RSSHub Zhihu Hot",
        "url": "https://rsshub.app/zhihu/hotlist",
        "packages": ["platform", "tech"],
        "authority": 0.42,
        "default_enabled": False,
    },
}

DEPRECATED_SOURCE_IDS: Final[set[str]] = {
    # Removed from the active catalog because the endpoints currently return
    # 404/403, empty feeds, stale multi-year archives, or unstable third-party data.
    "cctv-domestic",
    "cctv-international",
    "cctv-finance",
    "cctv-hk-tw",
    "cctv-xinwenlianbo",
    "people-politics",
    "people-world",
    "xinhua-politics",
    "xinhua-taiwan",
    "xinhua-world",
    "people-taiwan",
    "chinanews-taiwan",
    "thepaper-featured",
    "yicai-news",
    "caixin-latest",
    "wallstreetcn-global",
    "kr36",
    "huxiu",
    "jiqizhixin",
    "guancha",
    "huanqiu-opinion",
    "rsshub-douyin-hot",
    "rsshub-bilibili-weekly",
    "rsshub-weibo-hot",
    "rsshub-zhihu-hot",
    "voa-zh",
    "zaobao-china",
    "zaobao-world",
    "chinadaily-china",
    "chinadaily-world",
    "chinadaily-business",
    "taiwan-info",
    "udn-cross-strait",
    "chinatimes-politics",
    "ettoday-mainland",
    "nownews-politics",
    "taiwancn-jsbg",
    "taihainet-twxw",
    "dw-zh",
    "reuters-world",
}

AUTHORITY_TOOLTIP_ZH: Final[str] = (
    "权重 (0~1)：决定该信源在热点排序中的加权倍数。"
    "0.8 以上 = 主流官方源（央视/人民/新华），"
    "0.6~0.8 = 行业财经/科技垂直媒体，"
    "0.4 以下 = 社交平台/聚合榜单（信号噪声较大）。"
    "数值仅影响排序与「主流/边缘」标签，不会过滤掉任何文章。"
)

DEFAULT_SETTINGS: Final[dict[str, Any]] = {
    "custom_data_dir": "",
    "output_subdir_mode": "date_category",
    "fetch_timeout_sec": 15,
    "fetch_concurrency": 4,
    "user_agent": "OpenAkita-MediaStrategy/0.1 (+https://github.com/openakita/openakita)",
    "brief_default_limit": 20,
    "radar_default_limit": 30,
    "llm_temperature": 0.2,
}

TOOL_NAMES: Final[tuple[str, ...]] = (
    "media_strategy_subscribe_package",
    "media_strategy_add_feed",
    "media_strategy_list_sources",
    "media_strategy_ingest",
    "media_strategy_hot_radar",
    "media_strategy_top_topics",
    "media_strategy_search_news",
    "media_strategy_ai_analyze_topics",
    "media_strategy_daily_brief",
    "media_strategy_verify_pack",
    "media_strategy_replicate_plan",
)
