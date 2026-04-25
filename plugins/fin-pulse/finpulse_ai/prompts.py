"""Finance-oriented LLM prompts.

The prompts keep the Horizon 0-10 scoring scale but rewrite the bands
for finance-first operators (central bank → earnings → tech/entertainment).
Bilingual system prompts cover Chinese + English operator personas.

Keep this module string-only — no runtime dependencies — so prompt
review and regression tests are cheap.
"""

from __future__ import annotations

from typing import Final


TAG_EXTRACTION_SYSTEM_ZH: Final[str] = (
    "你是一名资深财经编辑，任务是从用户的兴趣描述中抽取出 5-10 个"
    "可用于新闻聚类的主题标签。每个标签应是可以在金融市场、宏观、"
    "监管、公司事件范围内清晰归类的短语。输出 JSON。"
)

TAG_EXTRACTION_SYSTEM_EN: Final[str] = (
    "You are a senior finance editor. Extract 5-10 thematic tags from the "
    "user's interest blurb that are suitable for clustering news: central "
    "banks, macro indicators, regulation, corporate events. Respond with JSON."
)

TAG_EXTRACTION_USER_TEMPLATE: Final[str] = (
    "兴趣描述 / Interests:\n{interests}\n\n"
    "请返回如下 JSON schema:\n"
    '{{"tags": [{{"tag": "标签中文名", "description": "一句话解释"}}]}}\n'
    "严禁输出 JSON 之外的任何文字、Markdown 标题、代码块。"
)


SCORE_SYSTEM_ZH: Final[str] = (
    "你是一名资深财经分析师，请基于用户的兴趣标签，为一批新闻条目打分并"
    "选出最匹配的标签。评分 0-10，小数 1 位：\n"
    "- 9-10：央行利率决议 / 重大监管突发 / 指数熔断 / 黑天鹅\n"
    "- 7-8：重要经济数据（CPI / PMI / 非农） / 头部公司财报\n"
    "- 5-6：行业研报 / 公司日常公告 / 区域性事件\n"
    "- 3-4：通用热搜科技 / 娱乐 / 普通观点\n"
    "- 0-2：广告 / 水文 / 与金融无关\n"
    "除输出 JSON 之外不得添加其他文字。"
)

SCORE_SYSTEM_EN: Final[str] = (
    "You are a senior finance analyst. Given the user's tags, rate a batch "
    "of news items 0.0-10.0 (one decimal). Bands:\n"
    "9-10: central-bank policy / regulatory surprise / circuit breaker\n"
    "7-8:  prime macro prints (CPI/PMI/NFP) or top-tier earnings\n"
    "5-6:  sector report / routine filings / regional events\n"
    "3-4:  general tech / entertainment / opinion noise\n"
    "0-2:  ads / fluff / unrelated to finance.\n"
    "Respond with JSON ONLY — no prose, no markdown fences."
)

SCORE_USER_TEMPLATE: Final[str] = (
    "Tags / 标签:\n{tags_json}\n\n"
    "Items (每行一条) / Items (one per line):\n{items_block}\n\n"
    "请返回 JSON 数组:\n"
    '[{{"id": 0, "tag_id": 1, "score": 7.5, "reason": "简短双语理由"}}]\n'
    "`id` 必须与输入条目序号一致；`tag_id` 为匹配最高的标签索引。"
)


def build_score_items_block(items: list[dict[str, str]]) -> str:
    """Render a batch of article summaries for the scoring prompt.

    ``items`` is a list of ``{"id": int, "title": str, "summary": str,
    "source_id": str}``. The resulting block is compact so we leave
    headroom inside the token budget for the response.
    """
    lines: list[str] = []
    for it in items:
        src = it.get("source_id") or ""
        tag = f"[{src}]" if src else ""
        title = (it.get("title") or "").strip()
        summary = (it.get("summary") or "").strip().replace("\n", " ")
        if len(summary) > 240:
            summary = summary[:237] + "..."
        lines.append(f"{it['id']} {tag} {title} :: {summary}")
    return "\n".join(lines)


RULES_SUGGEST_SYSTEM_ZH: Final[str] = (
    "你是一名财经雷达规则编辑。你会把用户用自然语言描述的关注点翻译成"
    " fin-pulse 可执行的规则 DSL。DSL 规则如下：\n"
    "- 每一组写在一个空行分隔的段落里；段落内所有词都要满足才算命中\n"
    "- 前缀 `+` 表示该词必须命中（等价）\n"
    "- 前缀 `!` 表示该词一旦出现则整条排除\n"
    "- 前缀 `@别名` 表示同义词替换（放在具体关键词前一行）\n"
    "- `[GLOBAL_FILTER]` 段内所有词作为全局负面词（对所有组生效）\n"
    "- 常用组合：央行 & 政策；公司财报 & 突发；地缘 & 关税；加密 & 合规\n"
    "只返回规则文本本身，不要任何解释、Markdown 标题或代码块。\n"
    "每组不超过 5 个词；控制总规模，避免误伤。"
)

RULES_SUGGEST_SYSTEM_EN: Final[str] = (
    "You are a finance-radar rules editor. Translate the user's plain-language "
    "watchlist into a fin-pulse rules DSL:\n"
    "- Each group is separated by a blank line; all keywords in a group must "
    "co-occur for a hit.\n"
    "- Prefix `+` marks a required keyword.\n"
    "- Prefix `!` excludes the whole item if that word appears.\n"
    "- `@alias` on a line introduces a synonym for the next keyword.\n"
    "- A `[GLOBAL_FILTER]` section applies negative filters to every group.\n"
    "Return ONLY the rules text — no prose, no fences. Keep groups ≤ 5 words."
)

RULES_SUGGEST_USER_TEMPLATE: Final[str] = (
    "自然语言描述 / Description:\n{description}\n\n"
    "现有规则 (可为空，供参考) / Existing rules (may be empty):\n{existing}\n\n"
    "请输出全新的规则文本。"
)


__all__ = [
    "SCORE_SYSTEM_EN",
    "SCORE_SYSTEM_ZH",
    "SCORE_USER_TEMPLATE",
    "TAG_EXTRACTION_SYSTEM_EN",
    "TAG_EXTRACTION_SYSTEM_ZH",
    "TAG_EXTRACTION_USER_TEMPLATE",
    "RULES_SUGGEST_SYSTEM_EN",
    "RULES_SUGGEST_SYSTEM_ZH",
    "RULES_SUGGEST_USER_TEMPLATE",
    "build_score_items_block",
]
