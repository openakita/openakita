"""LLM error classification enum.

Consolidates error categories previously scattered across
LLMProvider._classify_error (string return) and
LLMClient._resolve_providers_with_fallback / _friendly_error_hint (string comparison)
into a single enum, eliminating typo risks and providing a single classification entry point.
"""

from __future__ import annotations

from enum import StrEnum


class FailoverReason(StrEnum):
    """LLM endpoint error classification.

    Values are kept consistent with the original ``LLMProvider._error_category``
    strings so that existing calls like ``mark_unhealthy(category=...)`` require
    no signature changes.
    """

    QUOTA = "quota"
    AUTH = "auth"
    STRUCTURAL = "structural"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"
