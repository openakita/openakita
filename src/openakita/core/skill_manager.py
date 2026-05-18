"""Re-export shim — SkillManager moved to ``agent.skill_manager``.

Canonical home: :mod:`openakita.agent.skill_manager`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Active callers:

* :mod:`openakita.core.agent` (constructs ``SkillManager`` during
  agent bring-up)
* :mod:`openakita.tools.handlers.install_skill`

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.skill_manager import (
    SKILL_GIT_CLONE_TIMEOUT_SECONDS,
    SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS,
    SKILL_INSTALL_CIRCUIT_THRESHOLD,
    SkillManager,
)

__all__ = [
    "SKILL_GIT_CLONE_TIMEOUT_SECONDS",
    "SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS",
    "SKILL_INSTALL_CIRCUIT_THRESHOLD",
    "SkillManager",
]
