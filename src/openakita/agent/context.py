"""Canonical public context-management surface.

``ContextManager`` combines the internal runtime base with focused grouping,
budgeting, compression, and sanitization helpers from ``openakita.runtime``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from openakita.core._context_runtime import (
    CHARS_PER_TOKEN as _RUNTIME_CHARS_PER_TOKEN,
)
from openakita.core._context_runtime import (
    CHUNK_MAX_TOKENS as _RUNTIME_CHUNK_MAX_TOKENS,
)
from openakita.core._context_runtime import (
    CONTEXT_BOUNDARY_MARKER as _RUNTIME_CONTEXT_BOUNDARY_MARKER,
)
from openakita.core._context_runtime import (
    ContextManager as _RuntimeContextBase,
)
from openakita.core._context_runtime import (
    ContextPressure as _RuntimeContextPressure,
)
from openakita.core.context_utils import (
    DEFAULT_MAX_CONTEXT_TOKENS as _RUNTIME_DEFAULT_MAX_CONTEXT_TOKENS,
)
from openakita.core.context_utils import (
    estimate_tokens as _runtime_estimate_tokens,
)
from openakita.core.context_utils import (
    get_max_context_tokens as _runtime_get_max_context_tokens,
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


# ---- Public surface ----

CHARS_PER_TOKEN: int = _RUNTIME_CHARS_PER_TOKEN
CHUNK_MAX_TOKENS: int = _RUNTIME_CHUNK_MAX_TOKENS
CONTEXT_BOUNDARY_MARKER: str = _RUNTIME_CONTEXT_BOUNDARY_MARKER
DEFAULT_MAX_CONTEXT_TOKENS: int = _RUNTIME_DEFAULT_MAX_CONTEXT_TOKENS

ContextPressure = _RuntimeContextPressure
estimate_tokens = _runtime_estimate_tokens
get_max_context_tokens = _runtime_get_max_context_tokens


@runtime_checkable
class ContextManagerProtocol(Protocol):
    """Minimal v2 surface that agent.* callers depend on.

    The runtime base exposes ~40 public and private methods; the
    Protocol below names the handful that v2 callers inside
    ``agent.*`` (Brain, ReasoningEngine, session turn handler)
    actually depend on so concrete v2 managers can satisfy it
    without inheriting the full runtime implementation.
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

    async def compress_if_needed(self, messages: list[dict], **kwargs: Any) -> list[dict]:
        """Main compression entry point; returns rewritten history."""


class ContextManager(_RuntimeContextBase):
    """V2 ContextManager with v2-flavoured composition.

    Inherits the complete runtime implementation and adds:

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
        for byte-faithful behaviour with the previous class.
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
        deterministically. The previous class relies on GC; v2
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

        The previous :meth:`estimate_messages_tokens` adds a fixed
        per-message structure overhead (role / tool_use_id ~ 10
        tokens). The v2 variant routes through the v2 estimator
        but preserves the same overhead so the returned number is
        directly comparable to the previous budget snapshot.
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

        Differs from the previous :meth:`calculate_context_pressure` in
        that the v2 variant returns a JSON-friendly dict instead of a
        dataclass. Useful for the setup-center UI panel which has no
        runtime access to the previous dataclass.
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
