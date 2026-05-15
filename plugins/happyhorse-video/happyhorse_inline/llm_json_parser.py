"""Robust JSON extractor for messy LLM output.

Vendored from ``openakita_plugin_sdk.contrib.llm_json_parser`` (SDK 0.6.0)
into happyhorse-video in 1.0.0 (forked from ``plugins/happyhorse-video/happyhorse_inline``);
see ``happyhorse_inline/__init__.py``. Provides the 5-level fallback parser
used by ``avatar_pipeline.image_compose`` to extract clean JSON from
qwen-vl-max scene/prompt suggestions (Pixelle A6).
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
    """Find the substring from the first ``open_ch`` to its matching ``close_ch``."""
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
    """Extract a JSON value from possibly noisy LLM output."""
    if not isinstance(text, str):
        if errors is not None:
            errors.append(f"L0: input not a string ({type(text).__name__})")
        return fallback

    s = text.strip()
    if not s:
        if errors is not None:
            errors.append("L0: empty input")
        return fallback

    v = _try_load(s, expect, errors, "L1 direct")
    if v is not _SENTINEL:
        return v

    inner = _strip_fence(s)
    if inner:
        v = _try_load(inner, expect, errors, "L2 fence")
        if v is not _SENTINEL:
            return v

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

    if errors is not None:
        errors.append("L5: all levels exhausted, returning fallback")
    return fallback


def parse_llm_json_object(
    text: str,
    *,
    fallback: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Convenience: ``parse_llm_json`` with ``expect=dict`` and dict fallback."""
    fb: dict[str, Any] = {} if fallback is None else fallback
    out = parse_llm_json(text, fallback=fb, expect=dict, errors=errors)
    return out if isinstance(out, dict) else fb


def parse_llm_json_array(
    text: str,
    *,
    fallback: list[Any] | None = None,
    errors: list[str] | None = None,
) -> list[Any]:
    """Convenience: ``parse_llm_json`` with ``expect=list`` and list fallback."""
    fb: list[Any] = [] if fallback is None else fallback
    out = parse_llm_json(text, fallback=fb, expect=list, errors=errors)
    return out if isinstance(out, list) else fb
