"""V2 context management surface -- canonical home for ``ContextManager``.

This module replaces the P-RC-0..3 facade. After P-RC-4 the canonical
import path for the agent's context compressor, token estimator, and
pressure snapshot is :mod:`openakita.agent.context`; the legacy
``openakita.core.context_manager`` will be a thin re-export shim once
P4.15 lands.

Architecture
------------
The legacy ``ContextManager`` was a 1799-LOC god-class that mixed
message grouping, token estimation, budget computation, multi-tier
compression (microcompact -> chunked summary -> hard truncation),
boundary-aware rewrite, media-block scrubbing, and pressure tracing.
P-RC-4 extracted the leaf-level pure concerns into focused modules:

* :mod:`runtime.context.grouping` -- :func:`group_messages` rule
  table for tool_use/tool_result pairing.
* :mod:`runtime.context.budget_trace` -- :func:`calc_context_budget`,
  :func:`estimate_tokens`, :func:`payload_size_bytes`,
  :data:`DEFAULT_MAX_CONTEXT_TOKENS`.
* :mod:`runtime.context.compress` -- :func:`pre_request_cleanup`,
  :func:`sanitize_tool_pairs`.

The v2 :class:`ContextManager` below composes those helpers from a
fresh constructor. To preserve byte-faithful behaviour for the ~20
existing callers (``ReasoningEngine``, ``Brain``, session turn
handler), it currently inherits the deep methods
(``compress_if_needed``, ``reactive_compact``,
``_summarize_messages_chunked``, ``rewrite_after_compression``, ...)
from the legacy class. Those will be re-implemented inline in
P-RC-7 once the legacy ``core/`` tree is removed.

Migration guidance
------------------
* New code: ``from openakita.agent.context import ContextManager``
* Old code (still allowed during cutover): ``from openakita.core.context_manager import ContextManager``
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from openakita.core._context_manager_legacy import (
    CHARS_PER_TOKEN as _LEGACY_CHARS_PER_TOKEN,
)
from openakita.core._context_manager_legacy import (
    CHUNK_MAX_TOKENS as _LEGACY_CHUNK_MAX_TOKENS,
)
from openakita.core._context_manager_legacy import (
    CONTEXT_BOUNDARY_MARKER as _LEGACY_CONTEXT_BOUNDARY_MARKER,
)
from openakita.core._context_manager_legacy import (
    ContextManager as _LegacyContextManagerImpl,
)
from openakita.core._context_manager_legacy import (
    ContextPressure as _LegacyContextPressure,
)
from openakita.core.context_utils import (
    DEFAULT_MAX_CONTEXT_TOKENS as _LEGACY_DEFAULT_MAX_CONTEXT_TOKENS,
)
from openakita.core.context_utils import (
    estimate_tokens as _legacy_estimate_tokens,
)
from openakita.core.context_utils import (
    get_max_context_tokens as _legacy_get_max_context_tokens,
)
from openakita.runtime.context import (
    calc_context_budget,
    group_messages,
    payload_size_bytes,
    pre_request_cleanup,
    sanitize_tool_pairs,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "CHUNK_MAX_TOKENS",
    "CONTEXT_BOUNDARY_MARKER",
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "ContextManager",
    "ContextManagerProtocol",
    "ContextPressure",
    "calc_context_budget",
    "estimate_tokens",
    "get_max_context_tokens",
    "group_messages",
    "payload_size_bytes",
    "pre_request_cleanup",
    "sanitize_tool_pairs",
]


# ---- Re-anchored public surface ----

CHARS_PER_TOKEN: int = _LEGACY_CHARS_PER_TOKEN
CHUNK_MAX_TOKENS: int = _LEGACY_CHUNK_MAX_TOKENS
CONTEXT_BOUNDARY_MARKER: str = _LEGACY_CONTEXT_BOUNDARY_MARKER
DEFAULT_MAX_CONTEXT_TOKENS: int = _LEGACY_DEFAULT_MAX_CONTEXT_TOKENS

ContextPressure = _LegacyContextPressure
estimate_tokens = _legacy_estimate_tokens
get_max_context_tokens = _legacy_get_max_context_tokens


@runtime_checkable
class ContextManagerProtocol(Protocol):
    """Minimal v2 surface that agent.* callers depend on.

    The legacy class exposes ~40 public + private methods; the
    Protocol below names the handful that v2 callers inside
    ``agent.*`` (Brain, ReasoningEngine, session turn handler)
    actually depend on so concrete v2 managers can satisfy it
    without inheriting the deep legacy class.
    """

    def estimate_tokens(self, text: str) -> int:
        """CJK-aware token estimator for a single string."""

    def estimate_messages_tokens(self, messages: list[dict]) -> int:
        """Total tokens (with structure overhead) for a message list."""

    def estimate_tools_tokens(self, tools: list | None) -> int:
        """Tokens occupied by the tool schema/catalog."""

    def calculate_context_pressure(
        self,
        messages: list[dict],
        tools: list | None,
        *,
        conversation_id: str | None = None,
    ) -> Any:
        """Snapshot of token usage vs. budget for the next request."""

    def pre_request_cleanup(self, messages: list[dict]) -> list[dict]:
        """Microcompact pass run before every LLM call."""

    async def compress_if_needed(
        self, messages: list[dict], **kwargs: Any
    ) -> list[dict]:
        """Main compression entry point; returns rewritten history."""


class ContextManager(_LegacyContextManagerImpl):
    """V2 ContextManager with v2-flavoured composition.

    Inherits the legacy 1799-LOC implementation for byte-faithful
    behaviour during the P4.15 cutover. Adds:

    * a public :meth:`group_messages_v2` that always routes through
      :func:`runtime.context.group_messages` so the leaf rule lives
      in one place.
    * :meth:`pre_request_cleanup_v2` -- v2 cleanup pass.
    * :meth:`sanitize_tool_pairs` -- public re-anchor of the orphan
      filter.
    * :meth:`calc_budget` -- staticmethod wrapper over
      :func:`runtime.context.calc_context_budget`.
    * :meth:`payload_size_bytes` -- staticmethod wrapper over
      :func:`runtime.context.payload_size_bytes`.
    * :meth:`describe_runtime` -- diagnostic snapshot used by the
      setup-center UI.

    Deep methods (``compress_if_needed``, ``reactive_compact``,
    ``_summarize_messages_chunked``, ``rewrite_after_compression``,
    ``_hard_truncate_if_needed``, ...) are inherited unchanged.
    """

    # ---- v2 leaf re-anchors ----

    @staticmethod
    def group_messages_v2(messages: list[dict]) -> list[list[dict]]:
        """Partition messages into tool-interaction groups."""
        return group_messages(messages)

    @staticmethod
    def sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
        """Drop orphan ``tool_use`` / ``tool_result`` blocks."""
        return sanitize_tool_pairs(messages)

    @staticmethod
    def calc_budget(endpoint: Any, fallback_window: int) -> int:
        """Endpoint -> effective context-window budget."""
        return calc_context_budget(endpoint, fallback_window)

    @staticmethod
    def payload_size_bytes(messages: list[dict]) -> int:
        """JSON-serialised byte size of a message list."""
        return payload_size_bytes(messages)

    def pre_request_cleanup_v2(self, messages: list[dict]) -> list[dict]:
        """V2 microcompact pass via :func:`runtime.context.pre_request_cleanup`.

        Equivalent to the inherited :meth:`pre_request_cleanup` but
        routed through the v2 helper so callers can rely on a single
        canonical implementation. The inherited method is preserved
        for byte-faithful behaviour with the legacy class.
        """
        return pre_request_cleanup(messages)

    # ---- v2 introspection ----

    def describe_runtime(self) -> dict[str, Any]:
        """JSON-friendly snapshot of v2 context-manager config.

        Used by the setup-center UI ``/api/agent/diagnostics`` panel.
        """
        return {
            "default_max_context_tokens": DEFAULT_MAX_CONTEXT_TOKENS,
            "chunk_max_tokens": CHUNK_MAX_TOKENS,
            "chars_per_token": CHARS_PER_TOKEN,
            "context_boundary_marker": CONTEXT_BOUNDARY_MARKER,
            "brain_attached": self.brain is not None,
            "cancel_event_installed": self._cancel_event is not None,
        }

    # ---- v2 lifecycle ----

    async def aclose(self) -> None:
        """V2 lifecycle hook for clean shutdown.

        Drops the token-estimation cache so memory is reclaimed
        deterministically. The legacy class relies on GC; v2
        contracts callers to call ``await ctx.aclose()`` from the
        agent teardown path so a long-running session doesn't
        accumulate cache entries across hot reloads.
        """
        try:
            cache = getattr(self, "_token_cache", None)
            if cache is not None:
                cache.clear()
        except Exception:  # noqa: BLE001
            # Best-effort; teardown must never raise.
            pass

    def reset_runtime_state(self) -> None:
        """Drop the token-estimation cache, leave config intact.

        Used by integration tests that share a ContextManager across
        cases. The :attr:`brain` reference and the cancel event are
        preserved.
        """
        cache = getattr(self, "_token_cache", None)
        if cache is not None:
            cache.clear()

    # ---- v2 composed operations ----

    def estimate_messages_tokens_v2(self, messages: list[dict]) -> int:
        """Total tokens for ``messages`` using the v2 estimator.

        The legacy :meth:`estimate_messages_tokens` adds a fixed
        per-message structure overhead (role / tool_use_id ~ 10
        tokens). The v2 variant routes through the v2 estimator
        but preserves the same overhead so the returned number is
        directly comparable to the legacy budget snapshot.
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += estimate_tokens(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text_blob = item.get("text", "") or item.get("content", "")
                        if isinstance(text_blob, str) and text_blob:
                            total += estimate_tokens(text_blob)
            # Fixed structure overhead (role, ids, etc.)
            total += 10
        return max(total, 1)

    def estimate_tools_tokens_v2(self, tools: list | None) -> int:
        """Tokens occupied by the tool schema/catalog -- v2 path."""
        if not tools:
            return 0
        import json as _json

        tools_text = _json.dumps(tools, ensure_ascii=False, default=str)
        return estimate_tokens(tools_text)

    def context_pressure_v2(
        self,
        messages: list[dict],
        tools: list | None,
        *,
        endpoint: Any = None,
        fallback_window: int = 32000,
    ) -> dict[str, int]:
        """Return a v2 pressure snapshot as a plain dict.

        Differs from the legacy :meth:`calculate_context_pressure` in
        that the v2 variant returns a JSON-friendly dict instead of a
        dataclass. Useful for the setup-center UI panel which has no
        runtime access to the legacy dataclass.
        """
        msg_tokens = self.estimate_messages_tokens_v2(messages)
        tool_tokens = self.estimate_tools_tokens_v2(tools)
        used = msg_tokens + tool_tokens
        if endpoint is not None:
            budget = calc_context_budget(endpoint, fallback_window)
        else:
            budget = DEFAULT_MAX_CONTEXT_TOKENS
        remaining = max(0, budget - used)
        pressure_pct = round(100 * used / max(budget, 1), 1)
        return {
            "messages_tokens": msg_tokens,
            "tools_tokens": tool_tokens,
            "used_tokens": used,
            "budget_tokens": budget,
            "remaining_tokens": remaining,
            "pressure_pct": pressure_pct,
        }

    @classmethod
    def with_brain(cls, brain: Any) -> ContextManager:
        """Construct a v2 ContextManager bound to ``brain``.

        Convenience builder for tests; equivalent to::

            ContextManager(brain=brain)
        """
        return cls(brain=brain)

    def estimate_text_tokens(self, text: str) -> int:
        """V2 single-string estimator delegating to runtime.context."""
        return estimate_tokens(text)

    @property
    def has_brain(self) -> bool:
        """True iff a brain instance is wired into this manager."""
        return self.brain is not None

    @property
    def has_cancel_event(self) -> bool:
        """True iff a cancel event was installed via :meth:`set_cancel_event`."""
        return self._cancel_event is not None
