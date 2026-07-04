"""Backwards-compat shim for the pre-ADR-0003 import path.

The reasoning engine was split out of the monolithic ``openakita.core`` module
into ``openakita.core._reasoning_engine_legacy`` (with the v2 facade living in
``openakita.agent.reasoning``). This thin module re-exports the canonical
:class:`ReasoningEngine` so legacy import paths
(``from openakita.core.reasoning_engine import ReasoningEngine``) and upstream
tests keep resolving.

It re-exports through the :mod:`openakita.core` package attribute so the object
is identical to ``from openakita.core import ReasoningEngine`` and the safe lazy
import order is preserved. See ``docs/follow-ups/skipped-items-roadmap.md``.
"""

from __future__ import annotations

from openakita.config import settings
from openakita.core import ReasoningEngine

# The cancel-resume helpers (Issue #608) read these module-scoped names off the
# legacy ``core.reasoning_engine`` module; upstream tests reference them as
# ``reasoning_engine.settings`` / ``.DEFAULT_TTL_SECONDS`` /
# ``.RESUME_HINT_FRESHNESS_SECONDS``. ``settings`` is the same
# ``openakita.config.settings`` singleton the engine reads, so monkeypatching it
# here is observed by the engine; the TTL constants are re-exported from
# ``cancel_cleanup`` (their canonical home).
from openakita.core.cancel_cleanup import (  # noqa: E402
    DEFAULT_TTL_SECONDS,
    RESUME_HINT_FRESHNESS_SECONDS,
)

__all__ = [
    "ReasoningEngine",
    "settings",
    "DEFAULT_TTL_SECONDS",
    "RESUME_HINT_FRESHNESS_SECONDS",
]
