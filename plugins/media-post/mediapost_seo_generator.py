"""5-platform SEO pack generator (mode 3: ``seo_pack``).

Per ``docs/media-post-plan.md`` §6.4 + §2.5: 5 platform-specific
prompts (TikTok / Bilibili / WeChat / Xiaohongshu / YouTube) call
Qwen-Plus in parallel via :func:`asyncio.gather`. A failure on a
single platform does NOT abort the rest — it just yields ``None`` for
that platform, mirroring the 9-error-kind isolation principle.

JSON parsing strips an optional ```json``` markdown fence, then falls
back through three layers (full body → first ``{...}`` substring →
``None``) before declaring the platform a failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from mediapost_models import ALLOWED_PLATFORMS, MediaPostError

logger = logging.getLogger(__name__)


# Prompts are 1:1 with §2.5 — the structure is shared (style guide +
# constraints + JSON schema) but each is tuned for the platform's
# audience and length limits.
PLATFORM_PROMPTS: dict[str, str] = {
    "tiktok": """You are a TikTok / Douyin content marketing specialist writing in zh-CN.

VIDEO CONTEXT:
- Title hint: {video_title_hint}
- Subtitle excerpt (truncated to 6000 chars):
{subtitle_excerpt}
- User intent / theme: {instruction}
- Chapters JSON (may be empty []): {chapters}

STYLE: hook-driven, short sentences, emoji friendly, numbers in titles, suspense endings.

CONSTRAINTS:
- title <= 30 characters (zh chars count as 1 each)
- description <= 300 characters
- hashtags <= 5 items, each starts with `#`

OUTPUT (raw JSON, no markdown fences):
{{
  "title": "<string>",
  "description": "<string>",
  "hashtags": ["#...", ...]
}}
""",
    "bilibili": """You are a Bilibili (B 站) content marketing specialist writing in zh-CN.

VIDEO CONTEXT:
- Title hint: {video_title_hint}
- Subtitle excerpt:
{subtitle_excerpt}
- User intent / theme: {instruction}
- Chapters JSON (may be empty []): {chapters}

STYLE: ACG-friendly, allow puns / 谐音梗, longer titles ok, chapter-list friendly.

CONSTRAINTS:
- title <= 80 characters
- description <= 300 characters
- tags <= 10 items
- chapters: include if provided; mm:ss format

OUTPUT (raw JSON, no markdown fences):
{{
  "title": "<string>",
  "description": "<string>",
  "tags": ["..."],
  "chapters": [{{"timestamp": "mm:ss", "title": "..."}}]
}}
""",
    "wechat": """You are a 微信视频号 marketing specialist writing in zh-CN.

VIDEO CONTEXT:
- Title hint: {video_title_hint}
- Subtitle excerpt:
{subtitle_excerpt}
- User intent / theme: {instruction}
- Chapters JSON (may be empty []): {chapters}

STYLE: emotional resonance, mid-aged user friendly, avoid heavy internet slang.

CONSTRAINTS:
- title <= 22 characters
- description <= 200 characters
- topics <= 5 items, each prefixed with `#` and surrounded by `#...#`

OUTPUT (raw JSON, no markdown fences):
{{
  "title": "<string>",
  "description": "<string>",
  "topics": ["#...#", ...]
}}
""",
    "xiaohongshu": """You are a 小红书 content marketing specialist writing in zh-CN.

VIDEO CONTEXT:
- Title hint: {video_title_hint}
- Subtitle excerpt:
{subtitle_excerpt}
- User intent / theme: {instruction}
- Chapters JSON (may be empty []): {chapters}

STYLE: clickbait-friendly, heavy emoji, trending topic terms, strong pain-point hooks.

CONSTRAINTS:
- title <= 20 characters (with emoji)
- body <= 1000 characters
- hashtags <= 10 items

OUTPUT (raw JSON, no markdown fences):
{{
  "title": "<string>",
  "body": "<string>",
  "hashtags": ["#..."]
}}
""",
    "youtube": """You are a YouTube SEO specialist. Write in English (or English+zh if hint is zh).

VIDEO CONTEXT:
- Title hint: {video_title_hint}
- Subtitle excerpt:
{subtitle_excerpt}
- User intent / theme: {instruction}
- Chapters JSON (may be empty []): {chapters}

STYLE: SEO-optimized, keyword-dense, native English phrasing, optional chapter timestamps.

CONSTRAINTS:
- title <= 100 chars
- description <= 5000 chars
- tags <= 500 chars total comma-separated
- chapters: if provided, include with `mm:ss` timestamps inside the description

OUTPUT (raw JSON, no markdown fences):
{{
  "title": "<string>",
  "description": "<string>",
  "tags": ["..."],
  "chapters": [{{"timestamp": "mm:ss", "title": "..."}}]
}}
""",
}


# Cap subtitle excerpts at 6000 chars to stay within Qwen-Plus context budgets
# while still giving the model meaningful narrative coverage.
SUBTITLE_EXCERPT_LIMIT = 6000


async def generate_seo_pack(
    *,
    video_title_hint: str,
    subtitle_excerpt: str,
    instruction: str,
    platforms: list[str],
    qwen_plus_call: Callable[..., Any],
    include_chapters: bool = False,
    chapter_timestamps: list[dict[str, Any]] | None = None,
    model: str = "qwen-plus",
    max_tokens: int = 2000,
    progress_cb: Callable[[float, str], Any] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Run 5 platforms in parallel, returning ``{platform: payload | None}``.

    A platform whose JSON parse fails or whose API call raises is mapped
    to ``None`` so the caller can mark it as a partial failure without
    aborting the whole task. The pipeline counts ``None``-only results
    as a ``format`` failure.
    """
    requested = [p for p in platforms if p in ALLOWED_PLATFORMS]
    if not requested:
        raise MediaPostError(
            "format",
            f"no valid platforms in {platforms!r}; allowed: {sorted(ALLOWED_PLATFORMS)}",
        )

    excerpt = (subtitle_excerpt or "")[:SUBTITLE_EXCERPT_LIMIT]
    chapters_json = (
        json.dumps(chapter_timestamps or [], ensure_ascii=False)
        if include_chapters
        else "[]"
    )

    if progress_cb:
        await _safe_call(progress_cb, 0.10, f"calling {len(requested)} platforms")

    async def _one(platform: str) -> tuple[str, dict[str, Any] | None]:
        try:
            template = PLATFORM_PROMPTS[platform]
            prompt = template.format(
                video_title_hint=video_title_hint or "",
                subtitle_excerpt=excerpt,
                instruction=instruction or "",
                chapters=chapters_json,
            )
        except KeyError as exc:
            logger.warning("seo platform missing template: %s", exc)
            return platform, None

        try:
            raw = await qwen_plus_call(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                max_tokens=max_tokens,
            )
        except MediaPostError as exc:
            logger.warning("seo platform %s call failed: %s", platform, exc)
            return platform, None
        except Exception:
            logger.warning("seo platform %s unexpected error", platform, exc_info=True)
            return platform, None

        return platform, _parse_seo_json(raw, platform)

    results = await asyncio.gather(*(_one(p) for p in requested))

    if progress_cb:
        await _safe_call(progress_cb, 1.0, "done")

    out: dict[str, dict[str, Any] | None] = dict(results)
    return out


def _strip_json_fence(text: str) -> str:
    """Remove a leading/trailing ``json`` markdown fence if present."""
    if not text:
        return ""
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _parse_seo_json(raw: str, platform: str) -> dict[str, Any] | None:
    """Three-layer JSON fallback: fence-stripped → first {...} → None."""
    if not raw:
        return None
    body = _strip_json_fence(raw)
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", body, re.DOTALL)
        if not m:
            logger.debug("seo platform %s: unparsable response", platform)
            return None
        try:
            data = json.loads(m.group(0))
        except (TypeError, ValueError):
            logger.debug("seo platform %s: 2nd-pass parse failed", platform)
            return None
    if not isinstance(data, dict):
        return None
    return data


async def _safe_call(cb: Callable[..., Any], *args: Any) -> None:
    try:
        result = cb(*args)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("progress_cb raised", exc_info=True)


__all__ = [
    "PLATFORM_PROMPTS",
    "SUBTITLE_EXCERPT_LIMIT",
    "generate_seo_pack",
]
