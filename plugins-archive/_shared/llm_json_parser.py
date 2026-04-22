"""Robust JSON extractor for messy LLM output.

LLM responses often contain prose, markdown fences, or trailing punctuation
around the actual JSON payload.  This module provides a 5-level fallback
parser that gives plugins a single canonical helper instead of every plugin
re-inventing ``text.find('{')`` (which is fragile when the prose itself
contains brace characters).

Inspired by patterns in:

- CutClaw ``src/utils/media_utils.py:295-364`` (parse_structure_proposal_output)
- video-translator's previous ``_safe_json_array`` (now migrated to here)
- seedance-video ``long_video.py`` ``decompose_storyboard`` JSON extraction

Levels:

1. **Direct** — ``json.loads(text)`` works, return it.
2. **Fence strip** — strip ``\u0060\u0060\u0060json...\u0060\u0060\u0060`` (or unfenced ``\u0060\u0060\u0060...\u0060\u0060\u0060``) and retry.
3. **Outer brace span** — ``text[first_brace : last_matching_brace]``, retry.
4. **Regex scan** — find all balanced ``{...}`` / ``[...]`` substrings and try
   each one in order.
5. **Fallback** — return the caller-supplied default, optionally appending
   error reasons to the ``errors`` list (useful for prompt feedback).

Design rules:

- **Zero extra deps** (stdlib ``json`` + ``re``).
- **Type-safe**: caller specifies ``expect=dict | list | (dict, list)`` to
  control what counts as success.
- **Never raises** for parsing failures — returns ``fallback`` instead.
  Programmer errors (bad ``expect`` arg) still raise.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "parse_llm_json",
    "parse_llm_json_array",
    "parse_llm_json_object",
]


_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n?(.*?)\n?```",
    re.DOTALL,
)


def _strip_fence(text: str) -> str | None:
    m = _FENCE_RE.search(text)
    if m:
        inner = m.group(1).strip()
        return inner or None
    return None


def _outer_span(text: str, open_ch: str, close_ch: str) -> str | None:
    """Find the substring from the first ``open_ch`` to its matching ``close_ch``.

    Uses a depth counter (string-aware) so that braces inside JSON strings do
    not throw the matcher off.
    """
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _all_balanced_spans(text: str, open_ch: str, close_ch: str) -> list[str]:
    """Yield every balanced ``open_ch...close_ch`` substring (string-aware)."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != open_ch:
            i += 1
            continue
        depth = 0
        in_str = False
        escape = False
        for j in range(i, n):
            c = text[j]
            if in_str:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    out.append(text[i : j + 1])
                    i = j + 1
                    break
        else:
            break
    return out


def _matches_expect(value: Any, expect: type | tuple[type, ...]) -> bool:
    if isinstance(expect, tuple):
        return any(isinstance(value, t) for t in expect)
    return isinstance(value, expect)


def _try_load(
    raw: str,
    expect: type | tuple[type, ...],
    errors: list[str] | None,
    label: str,
) -> Any:
    try:
        v = json.loads(raw)
    except (ValueError, TypeError) as e:
        if errors is not None:
            errors.append(f"{label}: {e.__class__.__name__}: {str(e)[:120]}")
        return _SENTINEL
    if _matches_expect(v, expect):
        return v
    if errors is not None:
        errors.append(f"{label}: type mismatch (got {type(v).__name__})")
    return _SENTINEL


_SENTINEL: Any = object()


def parse_llm_json(
    text: str,
    *,
    fallback: Any = None,
    expect: type | tuple[type, ...] = (dict, list),
    errors: list[str] | None = None,
) -> Any:
    """Extract a JSON value from possibly noisy LLM output.

    Args:
        text: Raw LLM response (may include markdown fences, prose, ...).
        fallback: Returned when every level fails.  Defaults to ``None``.
        expect: Acceptable top-level JSON type(s).  Defaults to ``(dict, list)``.
            Pass ``dict`` to require an object, ``list`` to require an array,
            or e.g. ``(dict, list, str)`` to also accept primitives.
        errors: Optional list to which level-by-level failure reasons are
            appended.  Useful when retrying with the LLM (e.g. include the
            failure reason in the next prompt — see CutClaw
            ``Screenwriter_scene_short.py:401-411`` ``last_feedback`` pattern).

    Returns:
        Parsed JSON value if any level succeeds, else ``fallback``.

    Examples:
        >>> parse_llm_json('{"a": 1}')
        {'a': 1}
        >>> parse_llm_json('好的, {"a": 1} 完。')
        {'a': 1}
        >>> parse_llm_json('```json\\n[1, 2, 3]\\n```', expect=list)
        [1, 2, 3]
        >>> parse_llm_json('NO JSON HERE', fallback={})
        {}
    """
    if not isinstance(text, str):
        if errors is not None:
            errors.append(f"L0: input not a string ({type(text).__name__})")
        return fallback

    s = text.strip()
    if not s:
        if errors is not None:
            errors.append("L0: empty input")
        return fallback

    # L1 — direct
    v = _try_load(s, expect, errors, "L1 direct")
    if v is not _SENTINEL:
        return v

    # L2 — strip markdown fence
    inner = _strip_fence(s)
    if inner:
        v = _try_load(inner, expect, errors, "L2 fence")
        if v is not _SENTINEL:
            return v

    # L3 — fast path: outer brace span (only when expect is a single concrete
    # type).  When expect=(dict, list) we skip L3 to avoid ambiguity (the dict
    # outer span might fail then the list outer span returns an *inner* array,
    # which is rarely what the caller wants).  L4's longest-first scan handles
    # the multi-type case correctly.
    expect_tuple = expect if isinstance(expect, tuple) else (expect,)
    if len(expect_tuple) == 1:
        only = expect_tuple[0]
        if only is dict:
            span = _outer_span(s, "{", "}")
            if span:
                v = _try_load(span, expect, errors, "L3 outer-object")
                if v is not _SENTINEL:
                    return v
        elif only is list:
            span = _outer_span(s, "[", "]")
            if span:
                v = _try_load(span, expect, errors, "L3 outer-array")
                if v is not _SENTINEL:
                    return v

    # L4 — every balanced span, longest first (longest tends to be the full payload)
    candidates: list[tuple[int, str]] = []
    if dict in expect_tuple:
        candidates.extend((len(c), c) for c in _all_balanced_spans(s, "{", "}"))
    if list in expect_tuple:
        candidates.extend((len(c), c) for c in _all_balanced_spans(s, "[", "]"))
    candidates.sort(key=lambda t: t[0], reverse=True)
    for _, raw in candidates:
        v = _try_load(raw, expect, errors, f"L4 scan(len={len(raw)})")
        if v is not _SENTINEL:
            return v

    # L5 — fallback
    if errors is not None:
        errors.append("L5: all levels exhausted, returning fallback")
    return fallback


def parse_llm_json_object(
    text: str,
    *,
    fallback: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Convenience: ``parse_llm_json`` with ``expect=dict`` and dict fallback.

    Always returns a ``dict`` (the fallback is normalised to ``{}`` if None).
    """
    fb: dict[str, Any] = {} if fallback is None else fallback
    out = parse_llm_json(text, fallback=fb, expect=dict, errors=errors)
    return out if isinstance(out, dict) else fb


def parse_llm_json_array(
    text: str,
    *,
    fallback: list[Any] | None = None,
    errors: list[str] | None = None,
) -> list[Any]:
    """Convenience: ``parse_llm_json`` with ``expect=list`` and list fallback.

    Always returns a ``list`` (the fallback is normalised to ``[]`` if None).
    """
    fb: list[Any] = [] if fallback is None else fallback
    out = parse_llm_json(text, fallback=fb, expect=list, errors=errors)
    return out if isinstance(out, list) else fb
