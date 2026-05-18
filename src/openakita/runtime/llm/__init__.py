"""V2 LLM helpers extracted from ``core.brain`` (continuation plan section 5).

Each submodule here is a focused, independently-testable piece of the
legacy Brain god-class:

* :class:`failover.EndpointFailoverView` -- endpoint health, fallback
  model selection, and live-priority controls over an ``LLMClient``.

The agent rewrite in P-RC-4 (``openakita.agent.brain``) composes these
helpers rather than inheriting from the giant.
"""

from __future__ import annotations

from .failover import EndpointFailoverView

__all__ = ["EndpointFailoverView"]
