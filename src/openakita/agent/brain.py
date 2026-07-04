"""V2 Brain implementation -- canonical home for ``Brain`` + ``SupervisorBrain``.

This module replaces the P-RC-0..3 facade. After P-RC-4 the canonical
import path for the agent LLM gateway is :mod:`openakita.agent.brain`;
the legacy ``openakita.core.brain.Brain`` will be a thin re-export
shim once P4.6 lands.

Architecture:
-------------
The legacy ``Brain`` was a ~2000 LOC god-class that mixed multi-endpoint
failover, compiler-LLM circuit breaking, multimodal block conversion,
streaming, debug-dump, and token-tracking concerns. P-RC-4 extracted
each of those concerns into a focused helper under
:mod:`openakita.runtime.llm`:

* :class:`runtime.llm.EndpointFailoverView` -- the nine endpoint /
  failover wrappers as a single composable view.
* :class:`runtime.llm.CompilerCircuitBreaker` -- 5-strike auth-aware
  breaker for the Prompt-Compiler endpoint, fully testable in
  isolation with an injected clock.
* :mod:`runtime.llm.multimodal` -- pure
  :func:`response_to_anthropic_message` + thinking interleaving.
* :mod:`runtime.llm.stream` -- :func:`stream_llm_events` async
  iterator + :func:`llm_stream_tracking` context manager.

The v2 :class:`Brain` below composes these helpers from a fresh
constructor. To preserve byte-faithful behaviour for the ~30 existing
``openakita.core.brain.Brain`` callers during the P4.6 cutover, the
v2 Brain currently inherits the deep methods (``think``,
``messages_create*``, ``compiler_think``, ``_dump_llm_*``, ...) from
the legacy class. Those will be re-implemented inline in P-RC-7
when the legacy ``core/`` tree is removed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from openakita.core._brain_legacy import Brain as _LegacyBrainImpl
from openakita.core._brain_legacy import Context as _LegacyContext
from openakita.core._brain_legacy import Response as _LegacyResponse
from openakita.runtime.llm import (
    CompilerCircuitBreaker,
    EndpointFailoverView,
    response_to_anthropic_message,
    stream_llm_events,
)

__all__ = [
    "Brain",
    "Context",
    "Response",
    "SupervisorBrain",
]


# Re-export the public data classes from the legacy module. These are
# pure dataclasses (no methods) so re-export here keeps the public
# import path stable without forcing a parallel definition.
Context = _LegacyContext
Response = _LegacyResponse


@runtime_checkable
class SupervisorBrain(Protocol):
    """Minimum brain surface that the v2 supervisor depends on.

    Implementing this protocol -- via the v2 :class:`Brain` below or a
    future ``runtime/llm/SupervisorLLM`` -- is enough to drive a
    :class:`openakita.runtime.state_graph.StateGraph` step. The
    protocol is :func:`runtime_checkable` so the legacy ``Brain`` (and
    the new one) pass ``isinstance(brain, SupervisorBrain)`` checks
    without explicit inheritance.
    """

    async def think_lightweight(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> Response:
        """Lightweight one-shot completion used by supervisor routing.

        Returns a :class:`Response` whose ``content`` and ``tool_calls``
        feed directly into the state-graph dispatcher.
        """

    def get_current_endpoint_info(self) -> dict[str, Any]:
        """Return ``{endpoint, model, healthy, ...}``.

        Used by the supervisor to attach LLM provenance to ledger
        entries and to surface failover state to the UI.
        """


class Brain(_LegacyBrainImpl):
    """V2 Brain -- canonical entry point for LLM access in the agent layer.

    The class composes the four ``runtime.llm.*`` helpers and inherits
    deep methods (think loop, messages_create variants, compiler call,
    debug dump) from the legacy implementation under
    :mod:`openakita.core.brain`. The inheritance is a transitional
    seam removed in P-RC-7 when the legacy tree is deleted; the
    helpers themselves are owned by ``runtime.llm`` and are the
    canonical home for that behaviour today.

    Construction is identical to the legacy Brain's so existing
    callers
    (``Brain()`` / ``Brain(api_key=..., max_tokens=...)``) keep
    working through the cutover.

    What the v2 Brain adds on top of the legacy class:

    * a :attr:`failover_view` accessor returning the composed
      :class:`EndpointFailoverView` -- avoids reaching into
      ``brain._failover_view`` directly;
    * a :attr:`circuit_breaker` accessor for the
      :class:`CompilerCircuitBreaker`;
    * :meth:`stream_chat` -- thin wrapper around
      :func:`runtime.llm.stream_llm_events` that feeds raw provider
      events to the caller, with no debug-dump / token-tracking
      side-effects (the legacy
      :meth:`messages_create_stream` retains those for
      compatibility);
    * explicit :class:`SupervisorBrain` protocol conformance with the
      :meth:`think_lightweight` and :meth:`get_current_endpoint_info`
      methods already inherited from the legacy class.
    """

    # ------------------------------------------------------------------
    # Helper accessors
    # ------------------------------------------------------------------

    @property
    def failover_view(self) -> EndpointFailoverView:
        """Return the composed :class:`EndpointFailoverView`.

        Prefer this accessor over ``brain._failover_view``;
        the private attribute is implementation detail and will move
        to a clean field name in P-RC-7.
        """
        return self._failover_view

    @property
    def circuit_breaker(self) -> CompilerCircuitBreaker:
        """Return the composed :class:`CompilerCircuitBreaker`.

        Prefer this accessor over ``brain._compiler_breaker``.
        """
        return self._compiler_breaker

    @property
    def llm_client(self) -> Any:
        """Return the borrowed :class:`openakita.llm.client.LLMClient`.

        The ``runtime.supervisor.Supervisor`` and several internal
        helpers need this; exposing it via a property removes the
        ``# type: ignore`` access through the legacy ``_llm_client``
        private attribute.
        """
        return self._llm_client

    # ------------------------------------------------------------------
    # Streaming primitive (v2 surface)
    # ------------------------------------------------------------------

    def stream_chat(
        self,
        *,
        messages: list[Any],
        system: str = "",
        tools: list[Any] | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool | None = None,
        thinking_depth: str | None = None,
        conversation_id: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        """Async-iterate raw provider events for the v2 supervisor.

        Unlike :meth:`messages_create_stream` (legacy), this primitive
        does NOT write a debug-dump and does NOT push a
        ``TokenTrackingContext``. The v2 caller composes those
        concerns explicitly via :func:`runtime.llm.llm_stream_tracking`
        when needed; the runtime supervisor logs streaming progress
        through its own ``StreamBus`` so the legacy dump call is
        redundant on that path.
        """
        return stream_llm_events(
            self._llm_client,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            enable_thinking=enable_thinking,
            thinking_depth=thinking_depth,
            conversation_id=conversation_id,
            extra_params=extra_params,
        )

    # ------------------------------------------------------------------
    # SupervisorBrain protocol implementations (re-anchored from the
    # legacy class for explicit documentation)
    # ------------------------------------------------------------------

    async def think_lightweight(
        self,
        prompt: str | None = None,
        *,
        system: str = "",
        messages: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> Response:
        """Lightweight one-shot completion -- inherits the legacy impl.

        Two calling conventions are accepted for backward compatibility:

        * ``think_lightweight(prompt_text, max_tokens=...)`` -- the
          legacy positional form still used by
          :meth:`scheduler.executor._system_memory_nudge_review`,
          several plugins (e.g. ``fin-pulse``), and the v1 agent's
          fast-reply path. Routed straight through the legacy impl.
        * ``think_lightweight(system=..., messages=[...])`` -- the v2
          :class:`SupervisorBrain` protocol surface.

        Without the positional branch, callers like the scheduler raise
        ``TypeError: think_lightweight() takes 1 positional argument
        but 2 were given`` and the periodic memory-nudge task fails
        every interval (Issue #9 in exploratory v10 report).
        """
        if prompt is not None and not messages:
            return await _LegacyBrainImpl.think_lightweight(
                self,
                prompt,
                system=(system or None),
                max_tokens=(max_tokens if max_tokens is not None else 2048),
            )
        return await super().think_lightweight(
            system=system,
            messages=messages or [],
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

    def get_current_endpoint_info(self) -> dict[str, Any]:
        """Return ``{name, model, healthy}`` for the active endpoint.

        Delegates to :class:`EndpointFailoverView` (composed in the
        legacy ``__init__``); re-anchored here for the
        :class:`SupervisorBrain` protocol.
        """
        return self._failover_view.current_endpoint_info()

    # ------------------------------------------------------------------
    # Static helpers re-anchored for the v2 import path
    # ------------------------------------------------------------------

    @staticmethod
    def response_to_anthropic_message(response: Any) -> Any:
        """Static delegation to :func:`runtime.llm.response_to_anthropic_message`.

        Exposed on the class so v2 callers do not need to import from
        ``runtime.llm.multimodal`` separately; the result is identical
        to the legacy ``brain._convert_response_to_anthropic``.
        """
        return response_to_anthropic_message(response)
    # ------------------------------------------------------------------
    # Endpoint-management surface re-anchored on the failover view
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, bool]:
        """Async health-probe every endpoint.

        Delegates to :class:`EndpointFailoverView`; documented here so
        the v2 surface is the canonical reference (the legacy method
        was a one-line wrapper on the Brain class).
        """
        return await self._failover_view.health_check()

    def switch_model(
        self,
        endpoint_name: str,
        hours: float = 12.0,
        reason: str = "",
        *,
        conversation_id: str | None = None,
        policy: str = "prefer",
    ) -> tuple[bool, str]:
        """Stage a temporary endpoint override; delegates to the failover view."""
        return self._failover_view.switch_model(
            endpoint_name,
            hours,
            reason,
            conversation_id=conversation_id,
            policy=policy,
        )

    def restore_default_model(
        self, conversation_id: str | None = None
    ) -> tuple[bool, str]:
        """Drop the manual override; delegates to the failover view."""
        return self._failover_view.restore_default(conversation_id=conversation_id)

    def get_current_model_info(self, conversation_id: str | None = None) -> dict[str, Any]:
        """Render current ``ModelInfo`` as dict; delegates to the failover view."""
        return self._failover_view.current_model_info(conversation_id=conversation_id)

    def list_available_models(self) -> list[dict[str, Any]]:
        """List every available ``ModelInfo`` as dicts; via the failover view."""
        return self._failover_view.list_models()

    def get_override_status(self) -> dict | None:
        """Return active override descriptor, or ``None``; via the failover view."""
        return self._failover_view.override_status()

    def get_fallback_model(self, conversation_id: str | None = None) -> str:
        """Next-priority configured endpoint name; via the failover view."""
        return self._failover_view.next_fallback_model(conversation_id)

    def update_model_priority(
        self, priority_order: list[str]
    ) -> tuple[bool, str]:
        """Rewrite persisted endpoint priority order; via the failover view."""
        return self._failover_view.update_priority(priority_order)

    # ------------------------------------------------------------------
    # Compiler breaker surface
    # ------------------------------------------------------------------

    def compiler_is_available(self) -> bool:
        """True when the compiler client exists and the breaker is closed."""
        if not self._compiler_client:
            return False
        return self._compiler_breaker.is_available()

    def reload_compiler_client(self) -> bool:
        """Reload compiler endpoint config; resets the breaker on success.

        Delegates to the legacy implementation but documents the v2
        contract: success returns ``True``; the breaker is force-reset
        so a freshly-fixed API key recovers without a process restart.
        """
        return super().reload_compiler_client()

    # ------------------------------------------------------------------
    # Thinking-mode toggles (v2 surface)
    # ------------------------------------------------------------------

    def set_thinking_mode(self, enabled: bool) -> None:
        """Toggle the thinking-mode hint passed to capable endpoints."""
        super().set_thinking_mode(enabled)

    def is_thinking_enabled(self) -> bool:
        """Return whether thinking-mode is currently enabled."""
        return super().is_thinking_enabled()

    # ------------------------------------------------------------------
    # Token-tracking introspection
    # ------------------------------------------------------------------

    def drain_usage_accumulator(self) -> dict[str, int]:
        """Drain and return the per-session LLM call accumulator.

        Mirrors the legacy method; the accumulator is reset to zero
        after the drain so consecutive calls do not double-count.
        """
        return super().drain_usage_accumulator()

    def reset_usage_accumulator(self) -> None:
        """Reset the per-session LLM call accumulator to zero."""
        self._acc_calls = 0
        self._acc_tokens_in = 0
        self._acc_tokens_out = 0

    # ------------------------------------------------------------------
    # Trace-context plumbing for LLM debug dumps
    # ------------------------------------------------------------------

    def set_trace_context(self, ctx: dict[str, str]) -> None:
        """Set trace context (org_id, node_id, session_id, ...) for dumps."""
        super().set_trace_context(ctx)

