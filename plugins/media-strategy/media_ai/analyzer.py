# ruff: noqa: N999
"""Rule ranking and host-Brain report helpers for Media Strategy."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from media_models import PACKAGE_DEFS

from media_ai.prompts import (
    EDITORIAL_SYSTEM_ZH,
    brief_prompt,
    replicate_prompt,
    topic_analysis_prompt,
    verify_prompt,
)


def _brain_content(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if text:
                    parts.append(str(text))
                continue
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _keyword_score(text: str, package_ids: list[str]) -> float:
    score = 0.0
    for pid in package_ids:
        meta = PACKAGE_DEFS.get(pid, {})
        for kw in meta.get("keywords", []):
            if kw and kw.lower() in text.lower():
                score += 0.45
    hot_words = ("突发", "最新", "发布", "宣布", "回应", "制裁", "冲突", "演习", "会晤", "调查")
    for word in hot_words:
        if word in text:
            score += 0.3
    return min(score, 3.0)


# Common Chinese newsroom prefixes that should not split otherwise identical
# topics across sources. Strip them before computing the clustering signature.
_TITLE_PREFIX_RE = re.compile(
    r"^\s*[【\[（(]?\s*"
    r"(?:突发|最新|快讯|独家|首发|实时|直播|更新|快报|视频|图集|多图|组图|专访|分析|评论|社论)"
    r"\s*[】\]）)]?\s*[:：丨\|·\-—,，]?\s*"
)
_RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_RANK_TO_RISK: dict[int, str] = {0: "low", 1: "medium", 2: "high"}


def topic_signature(title: str, *, length: int = 24) -> str:
    """Compute a normalized clustering key for cross-source topic grouping.

    Different outlets often phrase the same event with slightly different
    titles ("国台办：xxx" vs "国台办回应xxx vs 突发：国台办xxx"). The signature
    strips common newsroom prefixes and keeps the first ``length`` alnum
    characters so equivalent topics from different sources collapse into a
    single cluster.
    """

    if not title:
        return ""
    cleaned = _TITLE_PREFIX_RE.sub("", title.strip()).strip()
    cleaned = "".join(ch.lower() for ch in cleaned if ch.isalnum())
    return cleaned[:length]


def cluster_topics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group articles by topic signature and rank by coverage + authority.

    Selection logic (实现图2「选题推荐逻辑优化」):
    1. 同一选题被多个来源同时报道 → 视为重要，权重越高
    2. 单条最高 hot_score 仍是排序基底（已含权威权重 + 时新度）
    3. 覆盖度对数加权，避免热搜聚合源把单一事件刷爆排名
    """

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        sig = topic_signature(str(item.get("title") or ""))
        if not sig:
            sig = f"_id_{item.get('id') or id(item)}"
        groups.setdefault(sig, []).append(item)

    out: list[dict[str, Any]] = []
    for sig, members in groups.items():
        sources_seen: list[str] = []
        for m in members:
            sid = str(m.get("source_id") or "")
            if sid and sid not in sources_seen:
                sources_seen.append(sid)

        leader = max(members, key=lambda x: float(x.get("hot_score") or 0))
        max_hot = float(leader.get("hot_score") or 0)
        sum_hot = sum(float(m.get("hot_score") or 0) for m in members)
        avg_hot = sum_hot / max(1, len(members))

        risk_max = 0
        latest_at = ""
        article_ids: list[str] = []
        for m in members:
            risk_max = max(risk_max, _RISK_RANK.get(str(m.get("risk_level") or "medium"), 1))
            published = str(m.get("published_at") or m.get("fetched_at") or "")
            if published > latest_at:
                latest_at = published
            aid = str(m.get("id") or "")
            if aid:
                article_ids.append(aid)

        coverage = max(1, len(sources_seen))
        # 覆盖加权采用对数：1->2 家 +1.6 分；3 家 +3.2；5 家 +4.6；10 家 +5.7。
        coverage_bonus = 1.6 * math.log2(coverage + 1)
        weighted = round(max_hot + coverage_bonus + 0.4 * avg_hot, 2)

        out.append(
            {
                "signature": sig,
                "title": str(leader.get("title") or ""),
                "url": str(leader.get("url") or ""),
                "lead_source_id": str(leader.get("source_id") or ""),
                "source_ids": sources_seen,
                "sources_count": coverage,
                "hot_score_max": round(max_hot, 2),
                "hot_score_avg": round(avg_hot, 2),
                "weighted_score": weighted,
                "coverage_bonus": round(coverage_bonus, 2),
                "article_ids": article_ids,
                "article_count": len(members),
                "risk_level": _RANK_TO_RISK[risk_max],
                "published_at": latest_at or None,
            }
        )

    out.sort(
        key=lambda x: (
            -float(x["weighted_score"]),
            -int(x["sources_count"]),
            -float(x["hot_score_max"]),
        )
    )
    return out


def score_article(item: dict[str, Any], source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assign a deterministic 0-10 hotspot score and risk level."""

    source = source or {}
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    package_ids = list(item.get("package_ids") or source.get("package_ids") or [])
    authority = float(source.get("authority") or 0.5)
    published = _parse_time(item.get("published_at")) or _parse_time(item.get("fetched_at"))
    age_hours = 72.0
    if published is not None:
        age_hours = max(0.0, (datetime.now(UTC) - published).total_seconds() / 3600)
    freshness = max(0.0, 3.0 * math.exp(-age_hours / 36.0))
    base = 2.0 + authority * 2.0 + freshness
    base += _keyword_score(f"{title}\n{summary}", package_ids)
    if len(title) >= 12:
        base += 0.4
    score = round(max(0.0, min(base, 10.0)), 2)
    risk = "low" if authority >= 0.72 and score >= 5 else "medium"
    if authority < 0.55 or not item.get("published_at"):
        risk = "high"
    reason = f"权威权重 {authority:.2f}，新鲜度约 {age_hours:.1f} 小时，命中分类 {', '.join(package_ids) or '未分类'}。"
    return {"hot_score": score, "risk_level": risk, "ai_reason": reason}


def fallback_brief(items: list[dict[str, Any]], *, title: str) -> str:
    lines = [f"# {title}", "", "以下为规则整理结果；主程序大模型不可用时不会生成确定性判断。", ""]
    for idx, item in enumerate(items, start=1):
        lines.append(
            f"{idx}. [{item.get('title')}]({item.get('url')}) "
            f"({item.get('source_id')}, score={item.get('hot_score', 0)})"
        )
        summary = item.get("summary") or item.get("ai_summary") or ""
        if summary:
            lines.append(f"   - 摘要：{summary[:180]}")
        lines.append(f"   - 复核提示：风险等级 {item.get('risk_level', 'medium')}，请打开原文链接确认。")
    return "\n".join(lines).strip()


async def call_brain(
    brain: Any,
    prompt: str,
    *,
    max_tokens: int = 1800,
    temperature: float = 0.2,
) -> str:
    if brain is None:
        raise RuntimeError("brain.access not granted")

    # OpenAkita's host Brain exposes think()/messages_create_async(); older
    # plugin-facing adapters may expose chat(). Try host-native APIs first so
    # reports use the configured main model instead of silently falling back.
    if hasattr(brain, "think"):
        response = await brain.think(
            prompt,
            system=EDITORIAL_SYSTEM_ZH,
            max_tokens=max_tokens,
            enable_thinking=False,
        )
        return _brain_content(response).strip()

    messages = [{"role": "user", "content": prompt}]
    if hasattr(brain, "messages_create_async"):
        response = await brain.messages_create_async(
            messages=messages,
            system=EDITORIAL_SYSTEM_ZH,
            max_tokens=max_tokens,
            use_thinking=False,
        )
        return _brain_content(response).strip()

    response = await brain.chat(
        messages=messages,
        system=EDITORIAL_SYSTEM_ZH,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return _brain_content(response).strip()


async def build_brief(
    brain: Any,
    items: list[dict[str, Any]],
    *,
    title: str,
    session: str,
    temperature: float = 0.2,
) -> tuple[str, str]:
    prompt = brief_prompt(items, session=session)
    try:
        md = await call_brain(brain, prompt, temperature=temperature)
        return md or fallback_brief(items, title=title), "brain"
    except Exception:
        return fallback_brief(items, title=title), "fallback"


async def build_verify_pack(
    brain: Any,
    items: list[dict[str, Any]],
    *,
    topic: str,
    temperature: float = 0.2,
) -> tuple[str, str]:
    try:
        md = await call_brain(brain, verify_prompt(items, topic=topic), temperature=temperature)
        return md or fallback_brief(items, title=f"{topic or '热点'}信源复核"), "brain"
    except Exception:
        return fallback_brief(items, title=f"{topic or '热点'}信源复核"), "fallback"


async def build_topic_analysis(
    brain: Any,
    topics: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
) -> tuple[str, str]:
    try:
        md = await call_brain(
            brain,
            topic_analysis_prompt(topics),
            max_tokens=2800,
            temperature=temperature,
        )
        return md or _fallback_topic_analysis(topics), "brain"
    except Exception:
        return _fallback_topic_analysis(topics), "fallback"


async def build_replicate_plan(
    brain: Any,
    items: list[dict[str, Any]],
    *,
    topic: str,
    target_format: str,
    tone: str,
    revision_instructions: str = "",
    annotations: str = "",
    temperature: float = 0.2,
) -> tuple[str, str]:
    try:
        md = await call_brain(
            brain,
            replicate_prompt(
                items,
                topic=topic,
                target_format=target_format,
                tone=tone,
                revision_instructions=revision_instructions,
                annotations=annotations,
            ),
            max_tokens=2600,
            temperature=temperature,
        )
        return md or _fallback_plan(items, topic=topic, target_format=target_format), "brain"
    except Exception:
        return _fallback_plan(items, topic=topic, target_format=target_format), "fallback"


def _fallback_plan(items: list[dict[str, Any]], *, topic: str, target_format: str) -> str:
    topic_title = topic or (items[0].get("title") if items else "候选热点")
    lines = [
        f"# {topic_title}：热点复刻与采编执行计划",
        "",
        f"目标形态：{target_format}",
        "",
        "## 选题判断",
        "该计划由规则模板生成，需编辑人工确认来源真实性后再进入生产。",
        "",
        "## 来源依据",
    ]
    for item in items:
        lines.append(f"- [{item.get('title')}]({item.get('url')})（{item.get('source_id')}）")
    lines.extend(
        [
            "",
            "## 采访计划",
            "- 采访官方或权威解释口径，确认事件时间线。",
            "- 采访相关领域专家，解释影响边界和背景。",
            "- 准备反方或不同立场问题，避免单一叙事。",
            "",
            "## 拍摄计划",
            "- 开场：用地图、数据截图或标题墙交代事件。",
            "- 主体：主持人口播 + 原文链接截图 + 时间线图卡。",
            "- 结尾：提示观众关注后续官方回应。",
            "",
            "## 标题方向",
            "- 不使用未证实结论，采用“发生了什么 / 为什么值得关注 / 后续看什么”的稳健表达。",
        ]
    )
    return "\n".join(lines)


def _fallback_topic_analysis(topics: list[dict[str, Any]]) -> str:
    lines = [
        "# AI 选题分析报告",
        "",
        "以下为规则整理结果；主程序大模型不可用时不会生成深度判断。",
        "",
    ]
    for idx, topic in enumerate(topics, start=1):
        lines.extend(
            [
                f"## {idx}. {topic.get('title') or '未命名热点'}",
                f"- 原文：{topic.get('url') or '无'}",
                f"- 覆盖源：{', '.join(topic.get('source_ids') or []) or '未知'}",
                f"- 加权分：{topic.get('weighted_score', 0)}",
                f"- 风险等级：{topic.get('risk_level', 'medium')}",
                "- 复核提示：请打开原文链接确认来源、时间和转引链。",
                "",
            ]
        )
    return "\n".join(lines).strip()


def markdown_to_html(md: str) -> str:
    """Tiny markdown renderer for plugin reports."""

    html_lines = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            html_lines.append("")
        elif line.startswith("# "):
            html_lines.append(f"<h1>{_esc(line[2:])}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{_esc(line[3:])}</h2>")
        elif line.startswith("- "):
            html_lines.append(f"<li>{_linkify(_esc(line[2:]))}</li>")
        elif re.match(r"^\d+\. ", line):
            html_lines.append(f"<p>{_linkify(_esc(line))}</p>")
        else:
            html_lines.append(f"<p>{_linkify(_esc(line))}</p>")
    return "\n".join(html_lines)


def _esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _linkify(value: str) -> str:
    return re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2" target="_blank" rel="noreferrer">\1</a>',
        value,
    )
