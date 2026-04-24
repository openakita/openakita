"""Three-tier JSON parser for LLM responses — Phase 0 placeholder.

Layered fallback (Phase 3 implementation):
    1. ``json.loads`` strict parse;
    2. regex extract first ``{ ... }`` block then parse;
    3. retry the LLM call with a stricter system prompt.
"""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class LlmJsonParseError(ValueError):
    """Raised when all three fallback layers fail."""

    error_kind = "format"


def parse_llm_json(raw: str, *, expected_keys: list[str] | None = None) -> dict[str, Any]:
    """Layer 1 + 2 only (no LLM retry); Layer 3 needs an LLM client.

    Returns the parsed dict if successful, else raises LlmJsonParseError.
    """

    text = (raw or "").strip()
    if not text:
        raise LlmJsonParseError("Empty LLM response")

    try:
        parsed = json.loads(text)
        _ensure_keys(parsed, expected_keys)
        return parsed
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            raise LlmJsonParseError(f"No JSON object found in {text[:80]!r}") from None
        try:
            parsed = json.loads(match.group(0))
            _ensure_keys(parsed, expected_keys)
            return parsed
        except json.JSONDecodeError as exc:
            raise LlmJsonParseError(f"Regex-extracted block is invalid JSON: {exc}") from exc


def _ensure_keys(parsed: Any, expected_keys: list[str] | None) -> None:
    if not expected_keys:
        return
    if not isinstance(parsed, dict):
        raise LlmJsonParseError(f"Expected JSON object, got {type(parsed).__name__}")
    missing = [k for k in expected_keys if k not in parsed]
    if missing:
        raise LlmJsonParseError(f"Missing expected keys: {missing}")


__all__ = ["LlmJsonParseError", "parse_llm_json"]
