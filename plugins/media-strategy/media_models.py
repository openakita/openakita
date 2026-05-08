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
    "iconify": "solar:radar-linear",
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
        "default_enabled": False,
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
        "default_enabled": False,
    },
    "platform": {
        "label_zh": "平台热点",
        "label_en": "Platform Trends",
        "description": "适合融媒体选题的热榜、舆论和传播观察源。",
        "keywords": ["热搜", "视频", "社交", "平台", "舆论", "传播"],
        "default_enabled": False,
    },
}

SOURCE_DEFS: Final[dict[str, dict[str, Any]]] = {
    "bbc-zh": {
        "label_zh": "BBC 中文",
        "label_en": "BBC Chinese",
        "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "packages": ["world", "taiwan", "policy"],
        "authority": 0.78,
        "default_enabled": True,
    },
    "dw-zh": {
        "label_zh": "德国之声中文",
        "label_en": "DW Chinese",
        "url": "https://rss.dw.com/rdf/rss-chi-all",
        "packages": ["world", "taiwan", "policy"],
        "authority": 0.72,
        "default_enabled": True,
    },
    "zaobao-china": {
        "label_zh": "联合早报中国即时",
        "label_en": "Zaobao China",
        "url": "https://www.zaobao.com.sg/realtime/china/rss.xml",
        "packages": ["policy", "taiwan", "economy"],
        "authority": 0.76,
        "default_enabled": True,
    },
    "chinadaily-china": {
        "label_zh": "中国日报 China",
        "label_en": "China Daily China",
        "url": "https://www.chinadaily.com.cn/rss/china_rss.xml",
        "packages": ["policy", "economy", "world"],
        "authority": 0.7,
        "default_enabled": True,
    },
    "reuters-world": {
        "label_zh": "Reuters World",
        "label_en": "Reuters World",
        "url": "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best",
        "packages": ["world", "economy"],
        "authority": 0.82,
        "default_enabled": False,
    },
}

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
    "media_strategy_search_news",
    "media_strategy_daily_brief",
    "media_strategy_verify_pack",
    "media_strategy_replicate_plan",
)
