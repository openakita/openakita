"""Chapter-card PNG renderer (mode 4: ``chapter_cards``).

Per ``docs/media-post-plan.md`` §6.5: A path uses Playwright to render
HTML templates inside a shared Chromium instance; B path falls back to
``ffmpeg drawtext`` so the mode degrades gracefully on systems without
Playwright or CJK fonts.

Templates support Pixelle's two self-describing conventions:

- ``<meta name="media-size" content="WxH">`` declares the canvas size
  (``_parse_media_size_from_meta``).
- ``{{name:type=default}}`` placeholders are extracted from the
  template body so the UI can auto-render a settings form
  (``parse_template_parameters``).

Both helpers are stdlib-``re``-only so we do not add BeautifulSoup4 to
the dependency wedge (red-line §13).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mediapost_models import MediaPostError

logger = logging.getLogger(__name__)


DEFAULT_TEMPLATE_WIDTH = 1280
DEFAULT_TEMPLATE_HEIGHT = 720

# DSL: ``{{name:type=default}}`` — type is one of: text / int / color / image.
_PARAM_RE = re.compile(
    r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_]+)\s*(?:=\s*([^}]*))?\}\}"
)

# ``<meta name="media-size" content="WxH">`` — case-insensitive.
_META_SIZE_RE = re.compile(
    r"""<meta\s+[^>]*name\s*=\s*["']media-size["'][^>]*content\s*=\s*["'](\d+)\s*[xX]\s*(\d+)["'][^>]*/?\s*>""",
    re.IGNORECASE,
)


@dataclass
class ChapterCardSpec:
    """Single chapter card to render."""

    chapter_index: int
    title: str
    subtitle: str = ""
    template_id: str = "modern"
    width: int = DEFAULT_TEMPLATE_WIDTH
    height: int = DEFAULT_TEMPLATE_HEIGHT
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChapterRenderContext:
    """Per-task config for :func:`render_chapter_cards`."""

    out_dir: Path
    chapters: list[ChapterCardSpec]
    templates_dir: Path | None = None
    builtin_templates: dict[str, str] = field(default_factory=dict)
    prefer_playwright: bool = True
    drawtext_font: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Template introspection (Pixelle A4 + A5 ports)
# ---------------------------------------------------------------------------


def parse_media_size_from_meta(html: str, *, fallback: tuple[int, int]) -> tuple[int, int]:
    """Read ``<meta name="media-size" content="WxH">`` from a template body."""
    if not html:
        return fallback
    m = _META_SIZE_RE.search(html)
    if not m:
        return fallback
    try:
        return int(m.group(1)), int(m.group(2))
    except ValueError:
        return fallback


def parse_template_parameters(html: str) -> list[dict[str, str]]:
    """Extract ``{{name:type=default}}`` declarations from a template.

    Returns ``[{"name", "type", "default"}, ...]`` deduped by name in
    first-seen order. The UI uses this to render a per-template form.
    """
    if not html:
        return []
    seen: dict[str, dict[str, str]] = {}
    for m in _PARAM_RE.finditer(html):
        name = m.group(1)
        if name in seen:
            continue
        seen[name] = {
            "name": name,
            "type": m.group(2),
            "default": (m.group(3) or "").strip(),
        }
    return list(seen.values())


def replace_parameters(html: str, values: dict[str, Any]) -> str:
    """Substitute ``{{name:type=default}}`` placeholders.

    Missing keys fall back to the template's declared default. Unknown
    placeholder types are passed through untouched (Pixelle behavior).
    """
    if not html:
        return ""

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        default = (m.group(3) or "").strip()
        v = values.get(name, default)
        return "" if v is None else str(v)

    return _PARAM_RE.sub(_sub, html)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def render_chapter_cards(
    ctx: ChapterRenderContext,
    *,
    progress_cb: Callable[[float, str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Render every spec in ``ctx.chapters`` and return DB-ready rows.

    Each row::

        {
          "chapter_index": int,
          "title": str,
          "subtitle": str,
          "template_id": str,
          "png_path": str,
          "width": int, "height": int,
          "render_path": "playwright" | "drawtext",
          "extra_meta": dict,
        }

    The renderer chooses A path (Playwright) or B path (drawtext)
    once per task — if Playwright cannot start, the entire task uses
    drawtext for consistency. Per-chapter failures raise
    :class:`MediaPostError("dependency", ...)``.
    """
    if not ctx.chapters:
        raise MediaPostError("format", "no chapters to render")

    ctx.out_dir.mkdir(parents=True, exist_ok=True)

    use_playwright = ctx.prefer_playwright and await _playwright_available()
    render_path = "playwright" if use_playwright else "drawtext"

    rows: list[dict[str, Any]] = []
    total = len(ctx.chapters)

    if use_playwright:
        async with _PlaywrightSession() as session:
            for i, chapter in enumerate(ctx.chapters):
                if progress_cb:
                    await _safe_call(
                        progress_cb,
                        (i + 1) / total,
                        f"rendering chapter {chapter.chapter_index}",
                    )
                rows.append(await _render_one_playwright(chapter, ctx, session))
    else:
        for i, chapter in enumerate(ctx.chapters):
            if progress_cb:
                await _safe_call(
                    progress_cb,
                    (i + 1) / total,
                    f"drawtext chapter {chapter.chapter_index}",
                )
            rows.append(await _render_one_drawtext(chapter, ctx))

    for r in rows:
        r["render_path"] = render_path
    return rows


# ---------------------------------------------------------------------------
# A path — Playwright (lazy import; never required at module import)
# ---------------------------------------------------------------------------


async def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401  (existence check)
    except ImportError:
        return False
    return True


class _PlaywrightSession:
    """Async-context wrapper for a single shared Chromium instance."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None

    async def __aenter__(self) -> _PlaywrightSession:
        try:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
        except Exception as exc:
            logger.warning("Playwright launch failed: %s", exc)
            await self.__aexit__(type(exc), exc, None)
            raise MediaPostError(
                "dependency",
                f"Playwright failed to start; install with `pip install playwright` "
                f"and `python -m playwright install chromium`. {exc}",
            ) from exc
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("Playwright browser close error", exc_info=True)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                logger.debug("Playwright stop error", exc_info=True)
        self._browser = None
        self._pw = None

    async def render(
        self,
        html: str,
        *,
        width: int,
        height: int,
        out_path: Path,
    ) -> None:
        assert self._browser is not None
        page = await self._browser.new_page(viewport={"width": width, "height": height})
        try:
            await page.set_content(html, wait_until="load")
            await page.screenshot(path=str(out_path), full_page=False, omit_background=True)
        finally:
            await page.close()


async def _render_one_playwright(
    chapter: ChapterCardSpec,
    ctx: ChapterRenderContext,
    session: _PlaywrightSession,
) -> dict[str, Any]:
    template_html = _load_template(chapter.template_id, ctx)
    width, height = parse_media_size_from_meta(
        template_html, fallback=(chapter.width, chapter.height)
    )
    values: dict[str, Any] = {
        "title": chapter.title,
        "subtitle": chapter.subtitle,
        "chapter_index": chapter.chapter_index,
        **chapter.extra_params,
    }
    rendered = replace_parameters(template_html, values)
    out_path = ctx.out_dir / f"chapter_{chapter.chapter_index:03d}.png"
    try:
        await session.render(rendered, width=width, height=height, out_path=out_path)
    except Exception as exc:
        if _is_font_error(exc):
            raise MediaPostError(
                "dependency",
                f"Playwright font issue: {exc}. Falling back to drawtext is recommended.",
            ) from exc
        raise MediaPostError("dependency", f"Playwright render failed: {exc}") from exc
    return _row_for(chapter, out_path, width, height, render_path="playwright")


def _is_font_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "fontconfig" in msg or "font" in msg


def _load_template(template_id: str, ctx: ChapterRenderContext) -> str:
    """User templates_dir overrides built-ins (Pixelle A8)."""
    if ctx.templates_dir is not None:
        path = ctx.templates_dir / f"{template_id}.html"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    if template_id in ctx.builtin_templates:
        return ctx.builtin_templates[template_id]
    raise MediaPostError(
        "format",
        f"unknown template_id={template_id!r}; available: "
        f"{sorted(ctx.builtin_templates.keys())}",
    )


# ---------------------------------------------------------------------------
# B path — ffmpeg drawtext fallback
# ---------------------------------------------------------------------------


async def _render_one_drawtext(
    chapter: ChapterCardSpec,
    ctx: ChapterRenderContext,
) -> dict[str, Any]:
    width, height = chapter.width, chapter.height
    out_path = ctx.out_dir / f"chapter_{chapter.chapter_index:03d}.png"
    title_text = _escape_drawtext(chapter.title)
    subtitle_text = _escape_drawtext(chapter.subtitle)
    parts = [
        f"drawtext=text='{title_text}':fontsize=72:fontcolor=white:"
        "x=(w-text_w)/2:y=(h-text_h)/2-60",
    ]
    if subtitle_text:
        parts.append(
            f"drawtext=text='{subtitle_text}':fontsize=36:fontcolor=#cccccc:"
            "x=(w-text_w)/2:y=(h-text_h)/2+40"
        )
    if ctx.drawtext_font:
        parts = [
            f"{p}:fontfile='{_escape_drawtext(ctx.drawtext_font)}'" for p in parts
        ]
    vf = ",".join(parts)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        f"color=c=#101418:s={width}x{height}",
        "-vf",
        vf,
        "-frames:v",
        "1",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-300:]
        raise MediaPostError(
            "dependency",
            f"ffmpeg drawtext failed (rc={proc.returncode}): {tail}",
        )
    return _row_for(chapter, out_path, width, height, render_path="drawtext")


def _escape_drawtext(text: str) -> str:
    """Escape characters that have special meaning in ffmpeg drawtext."""
    if not text:
        return ""
    # Per ffmpeg docs: backslash-escape ``\``, ``:``, ``'``.
    return (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\\'")
    )


def _row_for(
    chapter: ChapterCardSpec,
    out_path: Path,
    width: int,
    height: int,
    *,
    render_path: str,
) -> dict[str, Any]:
    return {
        "chapter_index": chapter.chapter_index,
        "title": chapter.title,
        "subtitle": chapter.subtitle,
        "template_id": chapter.template_id,
        "png_path": str(out_path),
        "width": width,
        "height": height,
        "render_path": render_path,
        "extra_meta": dict(chapter.extra_params),
    }


# ---------------------------------------------------------------------------
# Built-in template helper (loaded lazily by plugin.py)
# ---------------------------------------------------------------------------


def builtin_template_ids() -> Iterable[str]:
    """The 5 names referenced in §3.1 mode 4."""
    return ("modern", "minimal", "retro", "youtube_style", "custom")


async def probe_playwright_runtime() -> dict[str, Any]:
    """Public Settings-UI probe.

    Returns a small dict with import + browser launch results so the renderer
    selection (A path vs B path) is observable without firing a full task.
    Browser launch is wrapped in a 10s timeout so the route stays responsive
    even when Chromium is missing.
    """
    result: dict[str, Any] = {
        "import_ok": False,
        "browser_ok": False,
        "error": "",
    }
    try:
        import playwright  # noqa: F401
    except ImportError as exc:
        result["error"] = f"playwright not installed: {exc}"
        return result
    result["import_ok"] = True
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await asyncio.wait_for(
                pw.chromium.launch(headless=True), timeout=10.0
            )
            try:
                result["browser_ok"] = True
            finally:
                await browser.close()
    except Exception as exc:  # pragma: no cover — purely informational
        result["error"] = str(exc)
    return result


async def _safe_call(cb: Callable[..., Any], *args: Any) -> None:
    try:
        result = cb(*args)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("progress_cb raised", exc_info=True)


__all__ = [
    "probe_playwright_runtime",
    "ChapterCardSpec",
    "ChapterRenderContext",
    "DEFAULT_TEMPLATE_HEIGHT",
    "DEFAULT_TEMPLATE_WIDTH",
    "builtin_template_ids",
    "parse_media_size_from_meta",
    "parse_template_parameters",
    "render_chapter_cards",
    "replace_parameters",
]
