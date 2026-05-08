# ruff: noqa: N999
"""Prompt templates for Media Strategy's host-Brain calls."""

from __future__ import annotations

import json
from typing import Any

EDITORIAL_SYSTEM_ZH = """你是“融媒智策”的采编策研助手。
工作原则：
1. 所有判断必须回到给定来源，不把未经核验的信息写成定论。
2. 台海、政策、时事类内容必须标注单一来源、转载链、时间滞后等风险。
3. “复刻”只复用选题角度、叙事结构、采访与拍摄方法，不照搬原文标题或文案。
4. 输出要面向媒体工作者，给出可执行的采访、拍摄、制作、分发动作。
"""


def items_block(items: list[dict[str, Any]], *, limit: int = 30) -> str:
    rows = []
    for idx, item in enumerate(items[:limit], start=1):
        rows.append(
            {
                "idx": idx,
                "id": item.get("id"),
                "title": item.get("title"),
                "source_id": item.get("source_id"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "summary": item.get("summary") or item.get("ai_summary"),
                "score": item.get("hot_score"),
                "risk_level": item.get("risk_level"),
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def brief_prompt(items: list[dict[str, Any]], *, session: str) -> str:
    return f"""请基于下面新闻候选生成一份中文融媒简报。

简报时段：{session}

要求：
- 先给 3-5 条“今天值得盯”的判断，每条必须引用来源编号或链接。
- 再按“台海/政策/经济/国际/科技/平台热点”归类。
- 对单一来源或转载链不清的内容标注“待复核”。
- 保留原文链接，方便人工确认。

候选新闻 JSON：
{items_block(items)}
"""


def verify_prompt(items: list[dict[str, Any]], *, topic: str) -> str:
    return f"""请为媒体编辑生成“信源复核清单”。

主题：{topic or "按给定新闻自动归纳"}

要求：
- 列出已有来源、发布时间、链接。
- 判断是否为多源交叉印证，不能下真实性定论。
- 给出需要补查的官方口径、当事方回应、历史背景、数据口径。
- 给出“可报道/需等待/仅作线索”的建议。

候选新闻 JSON：
{items_block(items)}
"""


def replicate_prompt(
    items: list[dict[str, Any]],
    *,
    topic: str,
    target_format: str,
    tone: str,
) -> str:
    return f"""请基于给定来源生成“热点复刻与采编执行计划”。

主题：{topic or "按给定新闻自动归纳"}
目标形态：{target_format}
语气：{tone}

输出结构：
1. 选题判断：为什么值得做，适合哪个受众。
2. 复刻策略：只复用角度、结构、节奏，不照搬原文。
3. 采访计划：采访对象、问题清单、备选问题。
4. 拍摄计划：场景、镜头、B-roll、素材清单。
5. 视频/图文脚本：开场钩子、主体段落、结尾行动。
6. 标题与封面：给 5 个候选，避免夸大和未证实表述。
7. 发布与复盘：发布时间、平台版本、风险提醒。

候选新闻 JSON：
{items_block(items)}
"""
