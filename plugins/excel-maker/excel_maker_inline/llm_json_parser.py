"""Lenient JSON extraction for model responses."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from raw text or a markdown fenced block."""
    raw = text.strip()
    match = _FENCE_RE.search(raw)
    if match:
        raw = match.group(1).strip()
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value

