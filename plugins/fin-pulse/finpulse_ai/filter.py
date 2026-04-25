"""Two-stage AI filter reusing the host Brain (LLMClient).

Stage 1 (``extract_tags``) turns the operator's free-form interest blurb
into a tag taxonomy. Stage 2 (``score_batch``) scores a batch of articles
against the taxonomy on the finance-tuned 0-10 scale (see
:mod:`finpulse_ai.prompts`). Both stages:

* Go through ``api.get_brain()`` — fin-pulse does **not** ship its own
  LLM factory (the host already abstracts 10 providers with end-point
  priority, fail-over, and auth-cool-down built in).
* Fall back on per-item exceptions: a single score failure degrades to
  ``{"score": 0.0, "reason": "analysis failed"}`` without killing the
  batch (Horizon-style isolation).
* Cache the interest-text SHA-256 in ``config['ai_interests_sha256']``
  so edits to the interest blurb force a re-score pass.

The module is deliberately **side-effect-free on import** — no network
I/O happens unless the caller awaits :func:`extract_tags` or
:func:`score_batch`. This keeps the test surface flat.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Awaitable, Callable

from finpulse_ai.prompts import (
    SCORE_SYSTEM_EN,
    SCORE_SYSTEM_ZH,
    SCORE_USER_TEMPLATE,
    TAG_EXTRACTION_SYSTEM_EN,
    TAG_EXTRACTION_SYSTEM_ZH,
    TAG_EXTRACTION_USER_TEMPLATE,
    build_score_items_block,
)

logger = logging.getLogger(__name__)


BrainCall = Callable[..., Awaitable[Any]]


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>[\s\S]*?)```", re.IGNORECASE)


def interests_digest(text: str) -> str:
    """Return the SHA-256 hex digest of the interest blurb.

    ``config['ai_interests_sha256']`` is compared against this value to
    decide whether a re-score pass is needed. Whitespace-only changes
    still flip the hash, which is the desired behaviour — the operator
    meant to edit the blurb.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _strip_fence(raw: str) -> str:
    """Strip a surrounding ```json fenced block if present."""
    if not raw:
        return ""
    match = _JSON_FENCE_RE.search(raw)
    if match:
        return match.group("body").strip()
    return raw.strip()


def _parse_json(raw: str) -> Any:
    """Tolerant JSON parser — unwraps markdown fences before delegating."""
    body = _strip_fence(raw)
    if not body:
        return None
    return json.loads(body)


def _brain_content(response: Any) -> str:
    """Pull the text body out of a Brain response.

    The host LLMClient returns an object with ``.content`` (string).
    Dict returns (``{"content": "..."}``) and plain strings are
    handled for test doubles.
    """
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    return content or ""


async def extract_tags(
    brain: Any,
    *,
    interests: str,
    lang: str = "zh",
    max_tokens: int = 600,
    temperature: float = 0.1,
) -> list[dict[str, str]]:
    """Turn the free-form interest blurb into a list of tag dicts.

    Returns an empty list on any failure — the scoring stage can still
    run against an empty taxonomy (all scores will just cluster into
    a default "uncategorised" bucket).
    """
    if brain is None:
        raise RuntimeError("brain.access not granted")
    if not interests.strip():
        return []
    system = TAG_EXTRACTION_SYSTEM_ZH if lang == "zh" else TAG_EXTRACTION_SYSTEM_EN
    user = TAG_EXTRACTION_USER_TEMPLATE.format(interests=interests.strip())
    try:
        response = await brain.chat(
            messages=[{"role": "user", "content": user}],
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 — stage-boundary isolation
        logger.warning("extract_tags failed: %s", exc)
        return []
    try:
        data = _parse_json(_brain_content(response))
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_tags json parse failed: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    tags = data.get("tags") or []
    out: list[dict[str, str]] = []
    for t in tags:
        if not isinstance(t, dict):
            continue
        label = (t.get("tag") or "").strip()
        if not label:
            continue
        out.append(
            {
                "tag": label,
                "description": (t.get("description") or "").strip(),
            }
        )
    return out


async def score_batch(
    brain: Any,
    *,
    items: list[dict[str, Any]],
    tags: list[dict[str, str]],
    lang: str = "zh",
    batch_size: int = 10,
    max_tokens: int = 1500,
    temperature: float = 0.1,
) -> list[dict[str, Any]]:
    """Score ``items`` against ``tags`` in batches.

    Returns a list of ``{"id": item_id, "tag_id": int, "score": float,
    "reason": str}`` — one dict per input item. Missing rows fall back
    to ``{"score": 0.0, "reason": "analysis failed"}`` so the caller
    can write the failure state without a special-case branch.
    """
    if brain is None:
        raise RuntimeError("brain.access not granted")
    if not items:
        return []
    batch_size = max(1, min(batch_size, 20))
    system = SCORE_SYSTEM_ZH if lang == "zh" else SCORE_SYSTEM_EN
    tags_json = json.dumps(
        [{"id": i, "tag": t["tag"]} for i, t in enumerate(tags)],
        ensure_ascii=False,
    )
    results: dict[Any, dict[str, Any]] = {}

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        block = build_score_items_block(batch)
        user = SCORE_USER_TEMPLATE.format(tags_json=tags_json, items_block=block)
        try:
            response = await brain.chat(
                messages=[{"role": "user", "content": user}],
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 — batch-boundary isolation
            logger.warning("score_batch batch@%s failed: %s", start, exc)
            for it in batch:
                results[it["id"]] = {
                    "id": it["id"],
                    "tag_id": -1,
                    "score": 0.0,
                    "reason": "analysis failed",
                }
            continue
        try:
            parsed = _parse_json(_brain_content(response))
        except Exception as exc:  # noqa: BLE001 — malformed JSON is per-batch
            logger.warning(
                "score_batch parse failed @%s: %s", start, exc
            )
            for it in batch:
                results[it["id"]] = {
                    "id": it["id"],
                    "tag_id": -1,
                    "score": 0.0,
                    "reason": "analysis failed",
                }
            continue
        if not isinstance(parsed, list):
            for it in batch:
                results[it["id"]] = {
                    "id": it["id"],
                    "tag_id": -1,
                    "score": 0.0,
                    "reason": "analysis failed",
                }
            continue
        for row in parsed:
            if not isinstance(row, dict):
                continue
            iid = row.get("id")
            if iid is None:
                continue
            try:
                score = float(row.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            score = max(0.0, min(score, 10.0))
            try:
                tag_id = int(row.get("tag_id", -1))
            except (TypeError, ValueError):
                tag_id = -1
            results[iid] = {
                "id": iid,
                "tag_id": tag_id,
                "score": score,
                "reason": (row.get("reason") or "").strip(),
            }
        # Fill missing entries for this batch with the graceful fallback.
        for it in batch:
            if it["id"] not in results:
                results[it["id"]] = {
                    "id": it["id"],
                    "tag_id": -1,
                    "score": 0.0,
                    "reason": "analysis failed",
                }
    return [results[it["id"]] for it in items]


__all__ = [
    "extract_tags",
    "interests_digest",
    "score_batch",
]
