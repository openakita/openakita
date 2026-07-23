"""Canonical public ``Brain`` and ``SupervisorBrain`` implementations.

The public class composes focused helpers from :mod:`openakita.runtime.llm`
while the internal runtime base retains the deep provider methods.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from openakita.core._brain_runtime import Brain as _RuntimeBrainBase
from openakita.core._brain_runtime import Context as _RuntimeContext
from openakita.core._brain_runtime import Response as _RuntimeResponse
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


# Publish the runtime data classes from the canonical agent namespace.
Context = _RuntimeContext
Response = _RuntimeResponse


@runtime_checkable
class SupervisorBrain(Protocol):
    """Minimum brain surface that the v2 supervisor depends on.

    Implementing this protocol -- via the v2 :class:`Brain` below or a
    future ``runtime/llm/SupervisorLLM`` -- is enough to drive a
    :class:`openakita.runtime.state_graph.StateGraph` step. The
    protocol is :func:`runtime_checkable` so compatible implementations pass
    ``isinstance(brain, SupervisorBrain)`` checks without explicit inheritance.
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


class Brain(_RuntimeBrainBase):
    """V2 Brain -- canonical entry point for LLM access in the agent layer.

    The class composes the ``runtime.llm.*`` helpers and inherits provider-facing
    methods from the private runtime base. Construction supports both ``Brain()``
    and ``Brain(api_key=..., max_tokens=...)``.

    The public surface adds:

    * a :attr:`failover_view` accessor returning the composed
      :class:`EndpointFailoverView` -- avoids reaching into
      ``brain._failover_view`` directly;
    * a :attr:`circuit_breaker` accessor for the
      :class:`CompilerCircuitBreaker`;
    * :meth:`stream_chat` -- thin wrapper around
      :func:`runtime.llm.stream_llm_events` that feeds raw provider
      events to the caller, with no debug-dump or token-tracking side effects;
    * explicit :class:`SupervisorBrain` protocol conformance with the
      :meth:`think_lightweight` and :meth:`get_current_endpoint_info`
      methods inherited from the runtime base.
    """

    # ------------------------------------------------------------------
    # Helper accessors
    # ------------------------------------------------------------------

    @property
    def failover_view(self) -> EndpointFailoverView:
        """Return the composed :class:`EndpointFailoverView`.

        Prefer this accessor over ``brain._failover_view``;
        the private attribute is an implementation detail.
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
        ``# type: ignore`` access through the private ``_llm_client`` attribute.
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

        Unlike :meth:`messages_create_stream`, this primitive
        does NOT write a debug-dump and does NOT push a
        ``TokenTrackingContext``. The v2 caller composes those
        concerns explicitly via :func:`runtime.llm.llm_stream_tracking`
        when needed; the runtime supervisor logs streaming progress
        through its own ``StreamBus`` so an additional dump call is
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
    # SupervisorBrain protocol implementations
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
        """Lightweight one-shot completion.

        Two calling conventions are accepted for backward compatibility:

        * ``think_lightweight(prompt_text, max_tokens=...)`` -- the
          positional form still used by
          :meth:`scheduler.executor._system_memory_nudge_review`,
          several plugins (e.g. ``fin-pulse``). Routed through the runtime base.
        * ``think_lightweight(system=..., messages=[...])`` -- the v2
          :class:`SupervisorBrain` protocol surface.

        Without the positional branch, callers like the scheduler raise
        ``TypeError: think_lightweight() takes 1 positional argument
        but 2 were given`` and the periodic memory-nudge task fails
        every interval (Issue #9 in exploratory v10 report).
        """
        if prompt is not None and not messages:
            return await _RuntimeBrainBase.think_lightweight(
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
        runtime initialization) and exposed here for the
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
        to the runtime conversion helper.
        """
        return response_to_anthropic_message(response)

    # ------------------------------------------------------------------
    # Endpoint-management surface re-anchored on the failover view
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, bool]:
        """Async health-probe every endpoint.

        Delegates to :class:`EndpointFailoverView`; documented here so
        this public surface remains the canonical reference.
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

    def restore_default_model(self, conversation_id: str | None = None) -> tuple[bool, str]:
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

    def update_model_priority(self, priority_order: list[str]) -> tuple[bool, str]:
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

        Delegates to the runtime implementation and documents the public
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

        The accumulator is reset to zero
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
