"""Robust JSON extractor for messy LLM output."""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["parse_llm_json", "parse_llm_json_array", "parse_llm_json_object"]

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)\n?```", re.DOTALL)
_SENTINEL: Any = object()


def _strip_fence(text: str) -> str | None:
    match = _FENCE_RE.search(text)
    if match:
        inner = match.group(1).strip()
        return inner or None
    return None


def _outer_span(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _try_load(raw: str, expect: type | tuple[type, ...], errors: list[str] | None, label: str) -> Any:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        if errors is not None:
            errors.append(f"{label}: {exc.__class__.__name__}: {str(exc)[:120]}")
        return _SENTINEL
    if isinstance(value, expect):
        return value
    if errors is not None:
        errors.append(f"{label}: type mismatch (got {type(value).__name__})")
    return _SENTINEL


def parse_llm_json(
    text: str,
    *,
    fallback: Any = None,
    expect: type | tuple[type, ...] = (dict, list),
    errors: list[str] | None = None,
) -> Any:
    if not isinstance(text, str):
        if errors is not None:
            errors.append(f"L0: input not a string ({type(text).__name__})")
        return fallback

    source = text.strip()
    if not source:
        if errors is not None:
            errors.append("L0: empty input")
        return fallback

    for label, candidate in (
        ("L1 direct", source),
        ("L2 fence", _strip_fence(source)),
        ("L3 object", _outer_span(source, "{", "}")),
        ("L4 array", _outer_span(source, "[", "]")),
    ):
        if not candidate:
            continue
        value = _try_load(candidate, expect, errors, label)
        if value is not _SENTINEL:
            return value
    return fallback


def parse_llm_json_object(text: str, *, fallback: Any = None, errors: list[str] | None = None) -> Any:
    return parse_llm_json(text, fallback=fallback, expect=dict, errors=errors)


def parse_llm_json_array(text: str, *, fallback: Any = None, errors: list[str] | None = None) -> Any:
    return parse_llm_json(text, fallback=fallback, expect=list, errors=errors)

