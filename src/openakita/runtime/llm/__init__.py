"""V2 LLM helpers extracted from ``core.brain`` (continuation plan section 5).

Each submodule here is a focused, independently-testable piece of the
legacy Brain god-class:

* :class:`failover.EndpointFailoverView` -- endpoint health, fallback
  model selection, and live-priority controls over an ``LLMClient``.
* :class:`circuit_breaker.CompilerCircuitBreaker` -- 5-strike auth-aware
  guard for the Prompt-Compiler LLM endpoint.
* :mod:`multimodal` -- pure conversions between ``openakita.llm.types``
  blocks and Anthropic API block shapes (text / tool_use /
  ``<thinking>`` interleaving + stop-reason mapping).
* :mod:`stream` -- thin streaming primitive over ``LLMClient.chat_stream``
  with a separable token-tracking context manager.

The agent rewrite in P-RC-4 (``openakita.agent.brain``) composes these
helpers rather than inheriting from the giant.
"""

from __future__ import annotations

from .circuit_breaker import CompilerCircuitBreaker
from .failover import EndpointFailoverView
from .multimodal import (
    collect_thinking_texts,
    map_stop_reason,
    response_to_anthropic_message,
)
from .stream import llm_stream_tracking, stream_llm_events

__all__ = [
    "CompilerCircuitBreaker",
    "EndpointFailoverView",
    "collect_thinking_texts",
    "llm_stream_tracking",
    "map_stop_reason",
    "response_to_anthropic_message",
    "stream_llm_events",
]
