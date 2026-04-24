"""AI-layer package for fin-pulse — Phase 3+.

V1.0 currently only exports the URL-merge + simhash helpers from
:mod:`finpulse_ai.dedupe`. The filter (tag extraction + scoring) and
thematic-cluster paths land in Phase 3, keyed off
``config['dedupe.use_llm']`` / ``config['ai_interests']``.
"""

from __future__ import annotations

from finpulse_ai.dedupe import (
    canonical_dedupe_key,
    simhash_title,
    simhash_distance,
    group_by_canonical_url,
    group_by_simhash,
)

__all__ = [
    "canonical_dedupe_key",
    "group_by_canonical_url",
    "group_by_simhash",
    "simhash_distance",
    "simhash_title",
]
