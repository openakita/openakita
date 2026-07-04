"""Best-effort Markdown -> PDF rendering for root-node final deliverables.

UI feedback (图5/图6): the 主编 final report should be delivered not just as a
raw ``.md`` but also rendered to a clean PDF for presentation. We reuse the
approach proven by the ``fin-pulse`` plugin (``finpulse_dispatch.py``): convert
the markdown to a small styled HTML document and let Playwright's bundled
Chromium print it to PDF via ``page.pdf()``.

Everything here is **best-effort and fail-silent**: if Playwright / Chromium is
unavailable, or rendering raises, we return ``None`` and the caller falls back
to the markdown file. Rendering can be disabled outright with
``OPENAKITA_ORGS_V2_RENDER_PDF=0``.

The markdown->HTML conversion is a tiny self-contained renderer (headings,
bold/italic/code, ordered/unordered lists, blockquotes, paragraphs) so we carry
no new hard dependency on a markdown package.
"""

from __future__ import annotations

import html as _html
import logging
import os
import re

__all__ = ["pdf_rendering_enabled", "markdown_to_html", "render_markdown_to_pdf"]

_LOGGER = logging.getLogger(__name__)
_DISABLE_VALUES = {"0", "false", "no", "off"}
_ENV_VAR = "OPENAKITA_ORGS_V2_RENDER_PDF"


def pdf_rendering_enabled() -> bool:
    raw = os.environ.get(_ENV_VAR)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLE_VALUES


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    """Escape then apply inline markdown (code/bold/italic/link)."""
    out = _html.escape(text)
    out = _INLINE_CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    out = _LINK.sub(r'<a href="\2">\1</a>', out)
    return out


def _is_table_sep(line: str) -> bool:
    """A GFM table separator row, e.g. ``| --- | :--: | ---: |``."""
    s = line.strip()
    if "|" not in s or "-" not in s:
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c or "") for c in cells)


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _cell_align(spec: str) -> str:
    spec = spec.strip()
    if spec.startswith(":") and spec.endswith(":"):
        return "center"
    if spec.endswith(":"):
        return "right"
    return "left"


def markdown_to_html(md: str) -> str:
    """Convert a markdown body to an HTML fragment.

    Handles headings, bold/italic/code, ordered/unordered lists, blockquotes,
    fenced code, GFM pipe tables, and horizontal rules -- enough structure for a
    presentable final-report PDF (test17 item 5) without a markdown dependency.
    """
    lines = (md or "").replace("\r\n", "\n").split("\n")
    html_parts: list[str] = []
    list_stack: list[str] = []  # "ul" / "ol"
    in_code = False
    code_buf: list[str] = []
    para_buf: list[str] = []

    def flush_para() -> None:
        if para_buf:
            html_parts.append(f"<p>{_inline(' '.join(para_buf))}</p>")
            para_buf.clear()

    def close_lists() -> None:
        while list_stack:
            html_parts.append(f"</{list_stack.pop()}>")

    i = 0
    n = len(lines)
    while i < n:
        raw_line = lines[i]
        line = raw_line.rstrip()
        fence = line.strip().startswith("```")
        if fence:
            if in_code:
                html_parts.append(
                    "<pre><code>" + _html.escape("\n".join(code_buf)) + "</code></pre>"
                )
                code_buf.clear()
                in_code = False
            else:
                flush_para()
                close_lists()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(raw_line)
            i += 1
            continue
        if not line.strip():
            flush_para()
            close_lists()
            i += 1
            continue
        # GFM pipe table: a header row followed by a separator row.
        if "|" in line and i + 1 < n and _is_table_sep(lines[i + 1]):
            flush_para()
            close_lists()
            headers = _split_row(line)
            aligns = [_cell_align(c) for c in _split_row(lines[i + 1])]
            rows: list[list[str]] = []
            j = i + 2
            while j < n and "|" in lines[j] and lines[j].strip():
                rows.append(_split_row(lines[j]))
                j += 1
            thead = "".join(
                f'<th style="text-align:{aligns[k] if k < len(aligns) else "left"}">{_inline(h)}</th>'
                for k, h in enumerate(headers)
            )
            body_rows = []
            for r in rows:
                tds = "".join(
                    f'<td style="text-align:{aligns[k] if k < len(aligns) else "left"}">{_inline(c)}</td>'
                    for k, c in enumerate(r)
                )
                body_rows.append(f"<tr>{tds}</tr>")
            html_parts.append(
                f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
            )
            i = j
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_para()
            close_lists()
            level = len(heading.group(1))
            html_parts.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if re.fullmatch(r"\s*([-*_])\s*(\1\s*){2,}", line):
            flush_para()
            close_lists()
            html_parts.append("<hr>")
            i += 1
            continue
        ol = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        ul = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if ol or ul:
            flush_para()
            want = "ol" if ol else "ul"
            if not list_stack or list_stack[-1] != want:
                close_lists()
                html_parts.append(f"<{want}>")
                list_stack.append(want)
            item = (ol or ul).group(1)
            html_parts.append(f"<li>{_inline(item)}</li>")
            i += 1
            continue
        if line.strip().startswith(">"):
            flush_para()
            close_lists()
            html_parts.append(f"<blockquote>{_inline(line.strip()[1:].strip())}</blockquote>")
            i += 1
            continue
        para_buf.append(line.strip())
        i += 1

    if in_code and code_buf:
        html_parts.append("<pre><code>" + _html.escape("\n".join(code_buf)) + "</code></pre>")
    flush_para()
    close_lists()
    return "\n".join(html_parts)


# Visual language ported from the media-strategy plugin's report view
# (``plugins/media-strategy/ui/dist/index.html`` ``.report`` rules): a teal
# ``--primary`` (#0F766E) accent, a readable 1.8 line height, headings that step
# down with clear rhythm (h1 underlined, h2 with a teal left rule, h3/h4 tinted),
# rounded bordered tables with a teal header band + subtle zebra rows, soft
# blockquotes, and a gradient horizontal rule. Tuned for print (A4) rather than
# screen: slightly larger body text, page-break-avoidance on headings, and
# repeating table headers so tables that span a page keep their header row.
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
  @page {{ size: A4; margin: 18mm 16mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Microsoft YaHei","PingFang SC","Noto Sans CJK SC","Source Han Sans SC","Segoe UI",Arial,sans-serif;
    color: #0f172a; font-size: 12.5px; line-height: 1.8; margin: 0;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }}
  .doc-header {{ border-bottom: 3px solid #0F766E; padding-bottom: 13px; margin-bottom: 24px; }}
  .doc-title {{ font-size: 23px; font-weight: 700; color: #0F766E; margin: 0; letter-spacing: .3px; }}
  .doc-meta {{ color: #64748b; font-size: 11px; margin-top: 7px; }}
  h1, h2, h3, h4, h5, h6 {{ font-weight: 700; line-height: 1.4; color: #0f172a;
    page-break-after: avoid; break-after: avoid; }}
  h1 {{ font-size: 20px; border-bottom: 1px solid #cbd5e1;
    padding-bottom: 8px; margin: 26px 0 14px; }}
  h2 {{ font-size: 16.5px; margin: 22px 0 10px;
    border-left: 4px solid #0F766E; padding-left: 11px; }}
  h3 {{ font-size: 14.5px; color: #0F766E; margin: 18px 0 8px; }}
  h4 {{ font-size: 13px; color: #0D9488; margin: 14px 0 7px; }}
  h5, h6 {{ font-size: 12.5px; color: #475569; margin: 12px 0 6px; }}
  .doc-body > :first-child {{ margin-top: 0; }}
  p {{ margin: 9px 0; }}
  strong {{ color: #0f172a; font-weight: 700; }}
  ul, ol {{ margin: 9px 0; padding-left: 26px; }}
  li {{ margin: 5px 0; }}
  li > ul, li > ol {{ margin: 4px 0; }}
  code {{ background: #f0fdfa; padding: 1.5px 6px; border-radius: 5px; border: 1px solid #cbd5e1;
    font-family: Consolas,Menlo,"Courier New",monospace; font-size: 11.5px; color: #0f766e; }}
  pre {{ background: #0f172a; color: #e2e8f0; padding: 13px 15px; border-radius: 8px; line-height: 1.6;
    overflow-x: auto; page-break-inside: avoid; break-inside: avoid; font-size: 11.5px; }}
  pre code {{ background: transparent; color: inherit; padding: 0; border: 0; }}
  blockquote {{ border-left: 3px solid #0F766E; margin: 12px 0; padding: 8px 14px;
    color: #475569; background: #f0fdfa; border-radius: 0 8px 8px 0; }}
  a {{ color: #0F766E; text-decoration: none;
    border-bottom: 1px dotted rgba(15,118,110,.45); word-break: break-all; }}
  hr {{ height: 1px; border: 0; margin: 20px 0;
    background: linear-gradient(90deg, transparent, #cbd5e1, transparent); }}
  table {{ width: 100%; border-collapse: separate; border-spacing: 0; margin: 14px 0;
    font-size: 11.5px; border: 1px solid #cbd5e1; border-radius: 10px; overflow: hidden; }}
  thead {{ display: table-header-group; }}
  tr {{ page-break-inside: avoid; break-inside: avoid; }}
  th, td {{ padding: 8px 11px; border-bottom: 1px solid #e2e8f0; vertical-align: top; text-align: left; }}
  th {{ background: #f0fdfa; color: #0f766e; font-weight: 700; border-bottom: 2px solid #99f6e4; }}
  tbody tr:nth-child(even) {{ background: #f8fafc; }}
  tbody tr:last-child td {{ border-bottom: 0; }}
</style></head><body>
<div class="doc-header">
  <p class="doc-title">{title}</p>
  <p class="doc-meta">{meta}</p>
</div>
<div class="doc-body">
{body}
</div>
</body></html>"""


# Chromium print footer: attribution on the left, page numbers on the right.
# The ``.pageNumber`` / ``.totalPages`` spans are populated by Chromium; the
# explicit font-size is required because the template default is ~0.
_FOOTER_TEMPLATE = (
    '<div style="font-size:8px;width:100%;margin:0 16mm;color:#94a3b8;'
    'display:flex;justify-content:space-between;align-items:center;'
    'border-top:1px solid #e2e8f0;padding-top:4px;">'
    "<span>{note}</span>"
    '<span>第 <span class="pageNumber"></span> / <span class="totalPages"></span> 页</span>'
    "</div>"
)


def build_report_html(*, title: str, meta: str, markdown_body: str) -> str:
    return _HTML_TEMPLATE.format(
        title=_html.escape(title or "交付报告"),
        meta=_html.escape(meta or ""),
        body=markdown_to_html(markdown_body),
    )


def _configure_launch() -> dict:
    """Mirror fin-pulse: prefer a bundled Chromium when packaged."""
    kwargs: dict = {"headless": True}
    try:
        from openakita.plugins import sdk  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pass
    return kwargs


async def render_markdown_to_pdf(
    *,
    markdown_body: str,
    out_path: str,
    title: str = "交付报告",
    meta: str = "",
    footer_note: str = "OpenAkita 组织编排",
) -> str | None:
    """Render ``markdown_body`` to a PDF at ``out_path``. Returns the path or None.

    Best-effort: any failure (Playwright missing, Chromium not installed,
    render error) returns ``None`` so the caller keeps the markdown fallback.
    """
    if not pdf_rendering_enabled():
        return None
    if not isinstance(markdown_body, str) or not markdown_body.strip():
        return None
    try:
        from playwright.async_api import async_playwright
    except Exception:  # noqa: BLE001 -- playwright not installed
        _LOGGER.debug("pdf render skipped: playwright unavailable")
        return None
    html_doc = build_report_html(title=title, meta=meta, markdown_body=markdown_body)
    footer_html = _FOOTER_TEMPLATE.format(note=_html.escape(footer_note or "OpenAkita 组织编排"))
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**_configure_launch())
            try:
                page = await browser.new_page()
                await page.set_content(html_doc, wait_until="load")
                # displayHeaderFooter with an empty header suppresses Chromium's
                # default date/title banner; the footer carries attribution +
                # page numbers. Bottom margin must leave room for the footer.
                await page.pdf(
                    path=str(out_path),
                    format="A4",
                    print_background=True,
                    display_header_footer=True,
                    header_template="<div></div>",
                    footer_template=footer_html,
                    margin={"top": "16mm", "right": "14mm", "bottom": "18mm", "left": "14mm"},
                )
                await page.close()
            finally:
                await browser.close()
    except Exception:  # noqa: BLE001 -- best-effort; keep md fallback
        _LOGGER.debug("pdf render failed for %s", out_path, exc_info=True)
        return None
    return str(out_path)
