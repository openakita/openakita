# ruff: noqa: N999
"""Classify exceptions into the nine canonical ``error_kind`` values.

The classifier mirrors ``footage-gate``'s ``_classify_error`` (pipeline
L208-L246) but targets the network / LLM-heavy failure surface of
``fin-pulse``. Keep the table in sync with :data:`finpulse_models.ERROR_HINTS`
so the UI badge + hint block always render consistently.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from finpulse_models import ERROR_HINTS, ERROR_KINDS

logger = logging.getLogger(__name__)


_NETWORK_PATTERNS = re.compile(
    r"(connection|dns|getaddrinfo|connect\s+timed\s+out|network\s+unreachable|ssl|"
    r"remote\s+end\s+closed|name\s+resolution)",
    re.IGNORECASE,
)
_TIMEOUT_PATTERNS = re.compile(
    r"(timed\s*out|timeout|asyncio\.TimeoutError|read\s+timed)",
    re.IGNORECASE,
)
_RATE_LIMIT_PATTERNS = re.compile(r"(429|rate\s*limit|too\s*many\s*requests)", re.IGNORECASE)
_AUTH_PATTERNS = re.compile(
    r"(401|403|unauthor|forbidden|invalid\s+(api[_\s]*key|token|signature))",
    re.IGNORECASE,
)
_QUOTA_PATTERNS = re.compile(
    r"(quota|insufficient\s+balance|exceed.*quota|billing|payment\s+required|402)",
    re.IGNORECASE,
)
_NOT_FOUND_PATTERNS = re.compile(
    r"(\b404\b|not\s*found|no\s+such\s+file|cursor\s+out\s+of\s+range)",
    re.IGNORECASE,
)
_DEPENDENCY_PATTERNS = re.compile(
    r"(ModuleNotFoundError|ImportError|execjs|no\s+such\s+executable|command\s+not\s+found|"
    r"missing\s+runtime|feedparser|pyexecjs|node\s+is\s+not)",
    re.IGNORECASE,
)
_MODERATION_PATTERNS = re.compile(
    r"(content\s*moderation|content_filter|safety\s*policy|flagged|prohibited)",
    re.IGNORECASE,
)


def classify(exc: BaseException) -> str:
    """Return one of the nine :data:`ERROR_KINDS` for ``exc``.

    The classifier never raises — an unrecognised exception falls back
    to ``"unknown"`` so the caller can still write a row.
    """
    name = type(exc).__name__
    text = f"{name}: {exc}"

    if "Timeout" in name or _TIMEOUT_PATTERNS.search(text):
        return "timeout"

    if name in {"ConnectionError", "ConnectionRefusedError", "ConnectionResetError"}:
        return "network"
    if _NETWORK_PATTERNS.search(text):
        return "network"

    if _RATE_LIMIT_PATTERNS.search(text):
        return "rate_limit"
    if _AUTH_PATTERNS.search(text):
        return "auth"
    if _QUOTA_PATTERNS.search(text):
        return "quota"
    if _NOT_FOUND_PATTERNS.search(text) or name == "FileNotFoundError":
        return "not_found"
    if name in {"ImportError", "ModuleNotFoundError"} or _DEPENDENCY_PATTERNS.search(text):
        return "dependency"
    if _MODERATION_PATTERNS.search(text):
        return "moderation"
    return "unknown"


def hints_for(kind: str, locale: str = "zh") -> list[str]:
    """Return the localised hint list for an ``error_kind``.

    ``locale`` is clamped to the supported set (``zh`` / ``en``) with a
    defensive fallback to ``zh`` so the UI never renders an empty block.
    """
    locale = locale if locale in {"zh", "en"} else "zh"
    data = ERROR_HINTS.get(kind) or ERROR_HINTS["unknown"]
    return list(data.get(locale) or data["zh"])


def map_exception(exc: BaseException, *, locale: str = "zh") -> tuple[str, str, list[str]]:
    """Classify ``exc`` and return ``(error_kind, message, hints)``.

    ``error_kind`` is always a member of :data:`ERROR_KINDS`; ``message``
    is a short ``str(exc)`` suitable for UI display; ``hints`` is the
    bilingual-aware list of operator hints pulled from
    :data:`ERROR_HINTS`.
    """
    kind = classify(exc)
    if kind not in ERROR_KINDS:
        kind = "unknown"
    return kind, str(exc) or type(exc).__name__, hints_for(kind, locale)


def build_error_envelope(exc: BaseException, *, locale: str = "zh") -> dict[str, Any]:
    """Serialise ``exc`` into the canonical ``{error_kind,…}`` envelope.

    Used by route handlers and the pipeline so the UI always receives a
    structured failure payload rather than a raw stack trace.
    """
    kind, message, hints = map_exception(exc, locale=locale)
    return {
        "ok": False,
        "error_kind": kind,
        "error_message": message,
        "error_hints": hints,
    }


__all__ = [
    "build_error_envelope",
    "classify",
    "hints_for",
    "map_exception",
]
