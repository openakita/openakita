"""Tool side-channel hints — let handlers signal user-correctable config issues
in a structured way that's separated from the LLM-facing text.

Mechanism: handler raises :class:`ToolConfigError`. ``ToolExecutor`` catches it,
formats :meth:`ToolConfigError.to_llm_text` into the LLM-visible result string
and exposes :attr:`ToolConfigError.hint` to ``ReasoningEngine`` via the
``(text, hint)`` return tuple. ``ReasoningEngine`` forwards ``.hint`` as a
``config_hint`` SSE event — never into LLM history.

Pairs with :class:`PolicyError` / :class:`DeniedByPolicy` /
:class:`ConfirmationRequired` / :class:`DeferredApprovalRequired`
(structured exceptions for user-actionable signals).

Use this for:
  - missing API key (``error_code="missing_credential"``)
  - invalid credentials rejected by upstream (``error_code="auth_failed"``)
  - upstream throttling (``error_code="rate_limited"``)
  - transport unreachable (``error_code="network_unreachable"``)
  - upstream content policy rejection (``error_code="content_filter"``)

DON'T use this for:
  - transient errors that may succeed on retry — return a plain error string instead
  - policy denials — use :class:`DeniedByPolicy`
  - generic exceptions — let them propagate; ``ToolExecutor`` will format them

The contract is intentionally narrow so the chat UI can render an actionable
"go fix this" card with confidence (vs. a generic error toast).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ConfigHintErrorCode = Literal[
    "missing_credential",
    "auth_failed",
    "rate_limited",
    "network_unreachable",
    "content_filter",
    "unknown",
]


@dataclass(frozen=True)
class ConfigHint:
    """Frontend-facing structured hint attached to a failed tool call.

    Forwarded to the chat UI as a ``config_hint`` SSE event. Never enters the
    LLM API request body (the LLM converter only reads ``tool_use_id`` /
    ``content`` from tool_result blocks; custom fields are dropped).

    Attributes:
        scope: Logical area the hint belongs to, e.g. ``"web_search"`` /
            ``"llm"``. Frontend uses this to decide which UI surface (search
            panel vs. LLM panel) to navigate to when the user taps an action.
        error_code: Specific failure mode. UI renders different icon, color
            and default action set per code.
        title: Short headline shown at the top of the card (≤ 40 chars).
        message: One-line explanation shown under the title (optional).
        actions: List of action descriptors. Each action is either:
            - a navigation action: ``{"id", "label", "view", "section?", "anchor?"}``
            - an external URL action: ``{"id", "label", "url"}``
            UI renders the first action as primary, the rest as secondary.
    """

    scope: str
    error_code: ConfigHintErrorCode
    title: str
    message: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)


class ToolConfigError(Exception):
    """Raised by a tool handler when the call cannot proceed due to user-correctable config.

    ``ToolExecutor._execute_tool_impl`` catches this exception, converts it to
    the standard ``(text, hint)`` return tuple, and lets ``ReasoningEngine``
    forward the hint as a ``config_hint`` SSE event. The text portion goes to
    the LLM as a normal tool result; the structured hint reaches the UI via
    a side channel and never pollutes the LLM context.

    Example:
        >>> raise ToolConfigError(
        ...     scope="web_search",
        ...     error_code="missing_credential",
        ...     title="搜索源未配置",
        ...     message="当前没有可用的搜索源，请在设置中配置博查/Tavily/SearXNG。",
        ...     actions=[
        ...         {"id": "open_settings", "label": "前往配置",
        ...          "view": "config", "section": "tools-and-skills",
        ...          "anchor": "web-search"},
        ...     ],
        ... )
    """

    def __init__(
        self,
        *,
        scope: str,
        error_code: ConfigHintErrorCode,
        title: str,
        message: str,
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        self.hint = ConfigHint(
            scope=scope,
            error_code=error_code,
            title=title,
            message=message,
            actions=list(actions or []),
        )
        super().__init__(title)

    def to_llm_text(self) -> str:
        """Plain text shown to the LLM. MUST NOT contain UI markers, JSON or HTML-like tags.

        The LLM reads only this text; the structured hint reaches the UI via
        the side-channel ``config_hint`` event. Keeping this text strictly
        natural-language prevents the LLM from learning to mimic UI markers
        in its own outputs (a common hallucination pattern when models see
        embedded ``<tag>{json}</tag>`` payloads).
        """
        if self.hint.message:
            return f"[{self.hint.title}] {self.hint.message}"
        return f"[{self.hint.title}]"


__all__ = [
    "ConfigHint",
    "ConfigHintErrorCode",
    "ToolConfigError",
]
