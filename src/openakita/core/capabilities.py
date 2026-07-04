"""Re-export shim — capability descriptors moved to ``agent.capabilities``.

Canonical home: :mod:`openakita.agent.capabilities`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Active callers include skill registry, plugin manager, agent
profile loader and the scheduler's task-source registry.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.capabilities import (
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityOrigin,
    CapabilityVisibility,
    build_capability_id,
    build_namespace,
    normalize_slug,
)

__all__ = [
    "CapabilityDescriptor",
    "CapabilityKind",
    "CapabilityOrigin",
    "CapabilityVisibility",
    "build_capability_id",
    "build_namespace",
    "normalize_slug",
]
