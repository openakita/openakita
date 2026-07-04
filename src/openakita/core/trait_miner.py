"""Re-export shim — trait miner moved to ``agent.trait_miner``.

Canonical home: :mod:`openakita.agent.trait_miner`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.trait_miner import (
    ANSWER_ANALYSIS_PROMPT,
    ANSWER_ANALYSIS_SYSTEM,
    TRAIT_MINING_PROMPT,
    TRAIT_MINING_SYSTEM,
    TraitMiner,
)

__all__ = [
    "ANSWER_ANALYSIS_PROMPT",
    "ANSWER_ANALYSIS_SYSTEM",
    "TRAIT_MINING_PROMPT",
    "TRAIT_MINING_SYSTEM",
    "TraitMiner",
]
