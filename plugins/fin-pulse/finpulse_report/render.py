"""Daily-brief renderer — Markdown + HTML outputs for the ``digests`` table.

The HTML template uses a compact card-grid aesthetic (source pill + rank
+ ago + score) but keeps a single self-contained document
so the digest can be copy-saved as a PNG via ``html2canvas`` from the
Digests tab. Visual tokens mirror ``avatar-studio`` 's CSS variables
so the rendered card looks native when iframed inside the plugin UI.

The renderer itself is pure — it takes a list of article dicts
(``FinpulseTaskManager.list_articles`` rows) and produces blobs. I/O
lives in :mod:`finpulse_pipeline` (Phase 4a entry point).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence


# ── Data shapes ───────────────────────────────────────────────────────


@dataclass
class DigestStats:
    total_scanned: int = 0
    total_selected: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    score_bands: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_scanned": self.total_scanned,
            "total_selected": self.total_selected,
            "by_source": dict(self.by_source),
            "score_bands": dict(self.score_bands),
        }


@dataclass
class DigestContext:
    session: str  # morning | noon | evening
    lang: str = "zh"  # zh | en
    top_k: int = 20
    generated_at: str = ""
    title: str | None = None


# ── Selection ─────────────────────────────────────────────────────────


_SESSION_LABELS_ZH = {
    "morning": "财经早报",
    "noon": "财经午报",
    "evening": "财经晚报",
}

_SESSION_LABELS_EN = {
    "morning": "Morning Brief",
    "noon": "Midday Brief",
    "evening": "Evening Brief",
}


def session_label(session: str, *, lang: str = "zh") -> str:
    table = _SESSION_LABELS_ZH if lang == "zh" else _SESSION_LABELS_EN
    return table.get(session, session)


def _band(score: float | None) -> str:
    if score is None:
        return "unscored"
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "important"
    if score >= 5.0:
        return "routine"
    if score >= 3.0:
        return "low"
    return "noise"


def select_articles(
    articles: Sequence[dict[str, Any]], *, top_k: int = 20
) -> tuple[list[dict[str, Any]], DigestStats]:
    """Pick the top ``top_k`` articles for the digest.

    Primary key is ``ai_score`` descending (unscored items fall to the
    tail). Ties break by ``fetched_at`` desc so newer news wins within
    the same band.
    """
    stats = DigestStats()
    stats.total_scanned = len(articles)
    ranked = sorted(
        articles,
        key=lambda a: (
            a.get("ai_score") is not None,
            float(a.get("ai_score") or 0.0),
            a.get("fetched_at") or "",
        ),
        reverse=True,
    )
    selected = ranked[: max(1, min(int(top_k), 60))]
    stats.total_selected = len(selected)
    for a in selected:
        sid = a.get("source_id") or "unknown"
        stats.by_source[sid] = stats.by_source.get(sid, 0) + 1
        stats.score_bands[_band(a.get("ai_score"))] = (
            stats.score_bands.get(_band(a.get("ai_score")), 0) + 1
        )
    return selected, stats


# ── Renderers ──────────────────────────────────────────────────────────


def render_markdown(
    ctx: DigestContext, articles: Sequence[dict[str, Any]], *, stats: DigestStats
) -> str:
    """Plain-markdown digest used by the IM dispatch (the splitter
    chunks this by newline).
    """
    label = session_label(ctx.session, lang=ctx.lang)
    title = ctx.title or f"{label} · {_fmt_date(ctx.generated_at)}"
    lines: list[str] = [f"# {title}", ""]
    if not articles:
        lines.append(
            "_本时段暂无命中资讯，可稍后重试 Ingest。_"
            if ctx.lang == "zh"
            else "_No articles matched this session — retry ingest later._"
        )
        return "\n".join(lines)
    for idx, art in enumerate(articles, start=1):
        score = art.get("ai_score")
        score_text = (
            f" [{float(score):.1f}]" if isinstance(score, (int, float)) else ""
        )
        src = art.get("source_id") or "source"
        when = _fmt_time(art.get("published_at") or art.get("fetched_at"))
        title_line = art.get("title") or ""
        url = art.get("url") or ""
        lines.append(f"{idx}. [{src}]{score_text} {title_line}")
        if url:
            lines.append(f"   {url}")
        if when:
            lines.append(f"   {when}")
    lines.append("")
    lines.append(_footer(ctx.lang, stats))
    return "\n".join(lines)


def render_html(
    ctx: DigestContext, articles: Sequence[dict[str, Any]], *, stats: DigestStats
) -> str:
    """Self-contained HTML blob — safe to iframe or copy-to-PNG.

    Uses the same CSS variables as ``avatar-studio`` so the card reads
    as native inside the plugin UI. Fully inline — zero external CDN
    fetches — so rendering works offline.
    """
    zh = ctx.lang == "zh"
    label = session_label(ctx.session, lang=ctx.lang)
    title_raw = ctx.title or f"{label} · {_fmt_date(ctx.generated_at)}"
    title = html.escape(title_raw)
    key_points = _render_key_points(articles, lang=ctx.lang)
    sections = _render_digest_sections(articles, lang=ctx.lang)
    cards_html = _render_article_cards(articles, lang=ctx.lang)
    stats_html = _render_stats_block(stats, lang=ctx.lang)
    generated = html.escape(_fmt_time(ctx.generated_at) or ctx.generated_at or "")
    empty_html = ""
    if not articles:
        empty_html = (
            '<section class="empty-panel"><h2>暂无可展示资讯</h2>'
            '<p>当前时间窗口没有命中内容。建议先在「资讯」页抓取最新来源，'
            '或放宽时间窗口后重新生成。</p></section>'
            if zh
            else '<section class="empty-panel"><h2>No articles selected</h2>'
            '<p>Fetch fresh sources or widen the time window, then generate again.</p></section>'
        )
    return _HTML_TEMPLATE.format(
        title=title,
        label=html.escape(label),
        generated=generated,
        top_k=ctx.top_k,
        scanned=stats.total_scanned,
        selected=stats.total_selected,
        key_points=key_points,
        sections=sections,
        cards=cards_html,
        stats=stats_html,
        empty=empty_html,
    )


def _render_stats_block(stats: DigestStats, *, lang: str = "zh") -> str:
    zh = lang == "zh"
    total_label = "候选 / 选中" if zh else "Scanned / Selected"
    sources_label = "数据源" if zh else "Sources"
    bands_label = "评分分布" if zh else "Score bands"
    sources = "".join(
        f'<span class="pill">{html.escape(k)}: {v}</span>'
        for k, v in stats.by_source.items()
    )
    bands = "".join(
        f'<span class="pill pill-{html.escape(k)}">{html.escape(k)}: {v}</span>'
        for k, v in stats.score_bands.items()
    )
    return (
        '<section class="stats">'
        f'<div><strong>{total_label}:</strong> '
        f"{stats.total_scanned} / {stats.total_selected}</div>"
        f'<div><strong>{sources_label}:</strong> {sources or "—"}</div>'
        f'<div><strong>{bands_label}:</strong> {bands or "—"}</div>'
        "</section>"
    )


def _article_type(article: dict[str, Any]) -> str:
    source = str(article.get("source_id") or "").lower()
    raw = article.get("raw") if isinstance(article.get("raw"), dict) else {}
    content_type = str(raw.get("content_type") or raw.get("type") or "").lower()
    text = f"{article.get('title') or ''} {article.get('summary') or ''}"
    if content_type in {"policy", "data", "filing"} or source in {"nbs", "pbc_omo", "fed_fomc", "sec_edgar"}:
        return "policy"
    if "快讯" in text or content_type == "flash":
        return "flash"
    if raw.get("rank") or raw.get("hot") or "hot" in source or "xueqiu" in source:
        return "rank"
    return "market"


def _section_label(kind: str, *, lang: str = "zh") -> str:
    if lang != "zh":
        return {"policy": "Policy / Macro", "flash": "Market Flash", "rank": "Rankings / Heat", "market": "Companies / Sectors"}.get(kind, kind)
    return {"policy": "政策 / 宏观", "flash": "市场快讯", "rank": "榜单热度", "market": "公司 / 行业"}.get(kind, kind)


def _metric_pills(article: dict[str, Any]) -> str:
    raw = article.get("raw") if isinstance(article.get("raw"), dict) else {}
    vals: list[str] = []
    rank = raw.get("rank") or raw.get("order") or raw.get("index")
    if rank not in (None, ""):
        vals.append(f"No.{rank}")
    heat = raw.get("hot") or raw.get("heat") or raw.get("hot_value") or raw.get("score")
    if heat not in (None, ""):
        vals.append(f"热度 {heat}")
    return "".join(f'<span class="metric">{html.escape(str(v))}</span>' for v in vals)


def _score_html(score: Any) -> str:
    if not isinstance(score, (int, float)):
        return ""
    band = _band(float(score))
    return f'<span class="score score-{band}">AI {float(score):.1f}</span>'


def _render_key_points(articles: Sequence[dict[str, Any]], *, lang: str = "zh") -> str:
    if not articles:
        return ""
    title = "AI 要点摘要" if lang == "zh" else "Key Brief"
    points = "".join(f"<li>{html.escape(str(a.get('title') or ''))}</li>" for a in articles[:8])
    return f'<section class="brief"><h2>{title}</h2><ol>{points}</ol></section>'


def _render_digest_sections(articles: Sequence[dict[str, Any]], *, lang: str = "zh") -> str:
    if not articles:
        return ""
    groups = {"policy": 0, "flash": 0, "market": 0, "rank": 0}
    for article in articles:
        kind = _article_type(article)
        groups[kind] = groups.get(kind, 0) + 1
    cells = "".join(f'<div><strong>{html.escape(_section_label(k, lang=lang))}</strong><span>{v}</span></div>' for k, v in groups.items() if v)
    heading = "市场脉络" if lang == "zh" else "Market Context"
    return f'<section class="context"><h2>{heading}</h2><div class="context-grid">{cells}</div></section>'


def _render_article_cards(articles: Sequence[dict[str, Any]], *, lang: str = "zh") -> str:
    if not articles:
        return ""
    heading = "重点新闻" if lang == "zh" else "Top Stories"
    cards: list[str] = []
    for idx, art in enumerate(articles, start=1):
        src = html.escape(art.get("source_id") or "source")
        ttl = html.escape(art.get("title") or "")
        url = html.escape(art.get("url") or "")
        summary = html.escape(str(art.get("summary") or "")[:220])
        when = html.escape(_fmt_time(art.get("published_at") or art.get("fetched_at")) or "")
        cards.append(f"""
<article class="card">
  <header>
    <span class="rank">#{idx}</span>
    <span class="source">{src}</span>
    {_score_html(art.get("ai_score"))}
    {_metric_pills(art)}
    <span class="when">{when}</span>
  </header>
  <a class="title" href="{url}" target="_blank" rel="noopener">{ttl}</a>
  {f'<p>{summary}</p>' if summary else ''}
</article>""")
    return f'<section class="stories"><h2>{heading}</h2>{"".join(cards)}</section>'


# ── Entry point ───────────────────────────────────────────────────────


def build_daily_brief(
    articles: Sequence[dict[str, Any]],
    *,
    session: str,
    top_k: int = 20,
    lang: str = "zh",
    generated_at: str | None = None,
    title: str | None = None,
) -> tuple[str, str, DigestStats]:
    """Return ``(markdown, html, stats)`` for a daily-brief digest."""
    ctx = DigestContext(
        session=session,
        lang=lang,
        top_k=top_k,
        generated_at=generated_at or _utcnow_iso(),
        title=title,
    )
    selected, stats = select_articles(articles, top_k=top_k)
    md = render_markdown(ctx, selected, stats=stats)
    hb = render_html(ctx, selected, stats=stats)
    return md, hb, stats


# ── Helpers ───────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return _utcnow_iso()[:10]
    return iso[:10]


def _fmt_time(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return iso.replace("T", " ").replace("Z", "").strip()[:16]
    except Exception:  # noqa: BLE001
        return iso


def _footer(lang: str, stats: DigestStats) -> str:
    zh = lang == "zh"
    if zh:
        return (
            f"— 共 {stats.total_selected} 条精选（{stats.total_scanned} 候选），"
            "由 fin-pulse 财经脉动生成"
        )
    return (
        f"— {stats.total_selected} selected ({stats.total_scanned} scanned) "
        "by fin-pulse"
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg: #f7f2f3; --panel: #ffffff; --soft: #fff7f7;
  --text: #241116; --muted: #7A4E59; --primary: #D32F2F;
  --accent: #f59e0b; --border: #f0d7dc; --radius: 18px;
}}
body {{
  margin: 0; padding: 22px;
  font-family: -apple-system,Segoe UI,Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  background: var(--bg); color: var(--text);
}}
.wrap {{ max-width: 1120px; margin: 0 auto; background: var(--panel); border-radius: 24px; overflow: hidden; box-shadow: 0 18px 50px rgba(70,20,30,.10); }}
.hero {{ padding: 30px 34px; color: #fff; background: radial-gradient(circle at 12% 18%, rgba(255,255,255,.22), transparent 24%), linear-gradient(135deg,#7A1020,#D32F2F 58%,#f59e0b); }}
h1 {{ margin: 0; font-size: 28px; letter-spacing: -.02em; }}
.hero p {{ margin: 8px 0 0; opacity: .88; }}
.hero-metrics {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
.hero-metrics span {{ border: 1px solid rgba(255,255,255,.25); background: rgba(255,255,255,.14); border-radius: 999px; padding: 6px 11px; font-size: 12px; }}
section {{ padding: 22px 30px; border-bottom: 1px solid #f3e3e6; }}
section h2 {{ margin: 0 0 12px; font-size: 17px; color: #7A1020; }}
.brief ol {{ margin: 0; padding-left: 22px; }}
.brief li {{ margin: 7px 0; line-height: 1.55; }}
.context-grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(160px,1fr)); gap: 10px; }}
.context-grid div {{ border: 1px solid var(--border); background: var(--soft); border-radius: 14px; padding: 12px; }}
.context-grid strong {{ display: block; font-size: 13px; color: var(--muted); }}
.context-grid span {{ display: block; margin-top: 6px; font-size: 24px; font-weight: 800; color: var(--primary); }}
.stats {{ display: flex; flex-wrap: wrap; gap: 12px; background: var(--soft); font-size: 13px; color: var(--muted); }}
.stats strong {{ color: var(--text); font-weight: 600; }}
.pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; background: rgba(211,47,47,0.08); color: var(--primary); font-size: 12px; margin-right: 4px; }}
.pill-critical {{ background: rgba(220,38,38,0.12); color: #dc2626; }}
.pill-important {{ background: rgba(234,88,12,0.12); color: #ea580c; }}
.pill-routine {{ background: rgba(14,165,233,0.12); color: #0284c7; }}
.pill-low,.pill-noise {{ background: rgba(100,116,139,0.12); color: var(--muted); }}
.card {{ background: #fff; border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; margin-bottom: 12px; }}
.card header {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-bottom: 4px; }}
.rank {{ color: var(--primary); font-weight: 700; }}
.source {{ background: rgba(211,47,47,0.10); color: var(--primary); padding: 1px 7px; border-radius: 999px; font-weight: 700; }}
.score,.metric {{ padding: 1px 7px; border-radius: 999px; font-weight: 700; }}
.score-critical {{ background: #dc2626; color: white; }}
.score-important {{ background: #ea580c; color: white; }}
.score-routine {{ background: #0284c7; color: white; }}
.score-low,.score-noise {{ background: rgba(100,116,139,0.12); color: var(--muted); }}
.metric {{ background: #fff4df; color: #b45309; }}
.when {{ margin-left: auto; font-size: 11px; }}
.title {{ display: block; color: var(--text); text-decoration: none; font-weight: 700; font-size: 16px; line-height: 1.5; }}
.card p {{ margin: 7px 0 0; color: #60424a; font-size: 13px; line-height: 1.55; }}
.title:hover {{ color: var(--primary); text-decoration: underline; }}
.empty-panel {{ text-align: center; padding: 46px 30px; color: var(--muted); }}
.footer {{ padding: 16px 30px; text-align: center; color: #b28a93; font-size: 12px; }}
</style>
</head>
<body>
<main class="wrap">
<header class="hero">
  <h1>{title}</h1>
  <p>{label} · {generated}</p>
  <div class="hero-metrics"><span>精选 {selected} 条</span><span>候选 {scanned} 条</span><span>Top {top_k}</span></div>
</header>
{empty}
{key_points}
{sections}
{stats}
{cards}
<div class="footer">Generated by Fin Pulse · 保留原文链接与生成参数用于复盘</div>
</main>
</body>
</html>"""


__all__ = [
    "DigestContext",
    "DigestStats",
    "build_daily_brief",
    "render_html",
    "render_markdown",
    "select_articles",
    "session_label",
]
