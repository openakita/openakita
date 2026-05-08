"""Utilities for handling loosely structured memory text and LLM JSON."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

_CONTROL_CHARS_EXCEPT_JSON_WHITESPACE = dict.fromkeys(
    i for i in range(32) if i not in (9, 10, 13)
)


def coerce_text(value: Any) -> str:
    """Convert message/LLM content into readable text before string operations."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        text = _extract_multimodal_text(value)
        if text:
            return text
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, Iterable):
        parts = []
        for item in value:
            text = coerce_text(item).strip()
            if text:
                parts.append(text)
        return "\n".join(parts)
    return str(value)


def as_clean_str(value: Any, default: str = "") -> str:
    """Return a stripped string for scalar LLM fields."""
    text = coerce_text(value).strip()
    return text if text else default


def coerce_tool_names(value: Any) -> list[str]:
    """Normalize loosely shaped tool lists into display-safe tool names.

    Historical session and episode records may contain strings, dicts from
    tool-call receipts, or even malformed nested objects. Keep the data usable
    instead of rejecting the whole memory response.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes, dict)) or not isinstance(value, Iterable):
        items = [value]
    else:
        items = list(value)

    names: list[str] = []
    for item in items:
        raw_name: Any = item
        if isinstance(item, dict):
            raw_name = item.get("name") or item.get("tool_name")
            function = item.get("function")
            if not raw_name and isinstance(function, dict):
                raw_name = function.get("name")
            if not raw_name:
                raw_name = item
        name = as_clean_str(raw_name)
        if name:
            names.append(name)
    return names


def extract_json_array(text: str) -> str | None:
    """Extract the first JSON array-like block from LLM output."""
    return _extract_json_block(text, "[", "]")


def extract_json_object(text: str) -> str | None:
    """Extract the first JSON object-like block from LLM output."""
    return _extract_json_block(text, "{", "}")


def loads_llm_json(text: str) -> Any:
    """Parse JSON emitted by an LLM with conservative repair passes."""
    cleaned = _clean_llm_json_text(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            sanitized = cleaned.translate(_CONTROL_CHARS_EXCEPT_JSON_WHITESPACE)
            return json.loads(sanitized, strict=False)


def _extract_multimodal_text(value: dict) -> str:
    value_type = value.get("type")
    if value_type == "text" and isinstance(value.get("text"), str):
        return value["text"]
    if isinstance(value.get("content"), str):
        return value["content"]
    if isinstance(value.get("text"), str):
        return value["text"]
    return ""


def _clean_llm_json_text(text: str) -> str:
    cleaned = coerce_text(text).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def _extract_json_block(text: str, opener: str, closer: str) -> str | None:
    cleaned = coerce_text(text).strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    if fenced:
        fenced_text = fenced.group(1).strip()
        if fenced_text.startswith(opener):
            return fenced_text

    start = cleaned.find(opener)
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(cleaned)):
        char = cleaned[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return cleaned[start : idx + 1]
    return None
