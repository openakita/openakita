"""Entry-point classifier — single source of truth for whether a request
context is *interactive* (can drive a live security_confirm UI loop) or
*unattended* (must defer / auto-deny / route to owner via inbox).

Why this exists (C14 / R4-5/6/7/8)
==================================

Before C14, each entry point set ``Session.is_unattended`` ad-hoc:

- CLI ``run_interactive``: implicit attended, no isatty check → piping
  stdin would hang on the first ``Prompt.ask`` call.
- CLI ``openakita run "<task>"``: one-shot non-interactive, but the
  underlying ``Session`` defaulted to ``is_unattended=False`` → first
  CONFIRM-class tool would hang forever waiting for an SSE responder
  that doesn't exist.
- HTTP ``/api/chat`` (SSE): attended only via SSE — non-streaming
  clients had no fallback.
- IM webhook (``telegram`` / ``feishu`` / ...): the IM user is "live"
  but the bot has no synchronous popup channel; CONFIRM-class tools
  must defer to the owner via setup-center, not pretend to be
  interactive.
- Scheduler: already marked unattended at task spawn time (C12).

This module unifies the classification so each entry-point only has to
say "I am channel X with stdin=Y" and gets back authoritative answers.

Classification matrix
---------------------

+----------------------+--------------+----------------+----------------------+
| Entry point          | confirm_     | is_unattended  | recommended strategy |
|                      | capability   |                |                      |
+======================+==============+================+======================+
| CLI + TTY            | ``tty``      | False          | (use Rich prompt)    |
| CLI + non-TTY (pipe) | ``none``     | True           | ``ask_owner``        |
| ``openakita run``    | ``none``     | True           | ``ask_owner``        |
| HTTP /chat (SSE)     | ``sse``      | False          | (use SSE)            |
| HTTP /chat/sync      | ``none``     | True           | ``defer_to_inbox``   |
| IM webhook           | ``none``     | True           | ``ask_owner``        |
| Webhook (generic)    | ``none``     | True           | ``ask_owner``        |
| Scheduler            | ``none``     | True           | ``ask_owner``        |
| Desktop (Tauri)      | ``sse``      | False          | (use SSE)            |
+----------------------+--------------+----------------+----------------------+

Note: ``recommended strategy`` is only a default. ``Session.unattended_strategy``
(if set) overrides it. Config-level ``config.unattended.default_strategy``
overrides this default if Session leaves it empty (resolution lives in
``PolicyEngineV2._handle_unattended``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal

ConfirmCapability = Literal["sse", "tty", "none"]


@dataclass(frozen=True)
class EntryClassification:
    """Authoritative classification for an entry-point context.

    Attributes:
        is_unattended: Whether the engine should route CONFIRM-class
            decisions through ``_handle_unattended`` rather than emitting
            a live ``security_confirm`` SSE event.
        confirm_capability: How (if at all) this entry can show a confirm
            UI. ``sse`` = setup-center / desktop / web SSE,
            ``tty`` = terminal Rich prompt, ``none`` = no live channel.
        default_strategy: Recommended ``unattended_strategy`` when the
            session doesn't override. One of: ``ask_owner`` /
            ``defer_to_owner`` / ``defer_to_inbox`` / ``auto_approve`` /
            ``deny``. Empty string means "fall back to global config".
        reason: Human-readable label for debug/audit.
    """

    is_unattended: bool
    confirm_capability: ConfirmCapability
    default_strategy: str
    reason: str


# Channels known to originate from IM webhooks. Centralized so future
# adapters (Discord, Slack, Matrix, ...) only need to be added once.
IM_WEBHOOK_CHANNELS: frozenset[str] = frozenset(
    {
        "telegram",
        "feishu",
        "dingtalk",
        "wecom",
        "wework_ws",
        "qq",
        "qq_official",
        "onebot",
        "discord",
        "slack",
        "matrix",
        "wechat",
    }
)

# Channels that ALWAYS have an interactive SSE channel back to a human
# (setup-center desktop UI, browser web UI). Hard-coded; new SSE-capable
# channels should be added explicitly rather than defaulting.
SSE_INTERACTIVE_CHANNELS: frozenset[str] = frozenset(
    {
        "desktop",
        "web",
        "api",  # /api/chat (SSE streaming default)
        "setup-center",
    }
)


def classify_entry(
    channel: str,
    *,
    has_tty: bool | None = None,
    force_unattended: bool = False,
) -> EntryClassification:
    """Classify an entry point.

    Args:
        channel: ``Session.channel`` value (``cli`` / ``api`` /
            ``telegram`` / ``feishu`` / ``scheduler`` / ``api-sync`` /
            ``webhook`` / ``desktop`` / etc).
        has_tty: For ``cli`` channel only — whether ``sys.stdin.isatty()``
            is True. ``None`` triggers runtime detection. Ignored for
            non-CLI channels.
        force_unattended: Override flag — caller knows this context is
            unattended regardless of channel heuristics (e.g. one-shot
            ``openakita run`` even when stdin is a TTY).
    """
    channel_norm = (channel or "").strip().lower()

    if force_unattended:
        return EntryClassification(
            is_unattended=True,
            confirm_capability="none",
            default_strategy="ask_owner",
            reason=f"force_unattended (channel={channel_norm!r})",
        )

    if channel_norm == "cli":
        if has_tty is None:
            # NOTE: ``sys.stdin.isatty`` can raise on closed stdin (e.g.
            # daemon contexts). Treat the absence as "no TTY".
            try:
                has_tty = bool(sys.stdin.isatty())
            except (ValueError, OSError):
                has_tty = False
        if has_tty:
            return EntryClassification(
                is_unattended=False,
                confirm_capability="tty",
                default_strategy="",
                reason="cli + tty",
            )
        return EntryClassification(
            is_unattended=True,
            confirm_capability="none",
            default_strategy="ask_owner",
            reason="cli without tty (piped stdin)",
        )

    if channel_norm in SSE_INTERACTIVE_CHANNELS:
        return EntryClassification(
            is_unattended=False,
            confirm_capability="sse",
            default_strategy="",
            reason=f"{channel_norm} (sse-capable)",
        )

    if channel_norm in IM_WEBHOOK_CHANNELS:
        return EntryClassification(
            is_unattended=True,
            confirm_capability="none",
            default_strategy="ask_owner",
            reason=f"im-webhook ({channel_norm})",
        )

    if channel_norm == "api-sync":
        return EntryClassification(
            is_unattended=True,
            confirm_capability="none",
            default_strategy="defer_to_inbox",
            reason="http /api/chat/sync (no sse channel)",
        )

    if channel_norm == "scheduler":
        return EntryClassification(
            is_unattended=True,
            confirm_capability="none",
            default_strategy="",
            reason="scheduler (set by executor)",
        )

    if channel_norm == "webhook":
        return EntryClassification(
            is_unattended=True,
            confirm_capability="none",
            default_strategy="ask_owner",
            reason="generic webhook",
        )

    # Unknown channel — default to unattended (safe). Audit log should
    # surface this so new channels are explicitly classified above.
    return EntryClassification(
        is_unattended=True,
        confirm_capability="none",
        default_strategy="ask_owner",
        reason=f"unknown channel {channel_norm!r} (default unattended)",
    )


def apply_classification_to_session(session, classification: EntryClassification) -> bool:
    """Apply a classification to ``session.is_unattended`` /
    ``session.unattended_strategy`` if not already set.

    Returns True if any field was mutated. Idempotent — re-applying the
    same classification does not toggle values.

    Won't downgrade ``is_unattended=True`` to False (defense-in-depth:
    once a session is marked unattended by any code path, keep it so).
    Will fill an empty ``unattended_strategy`` but won't overwrite an
    explicit value.

    Hardening: getattr / setattr failures (e.g. a custom Session class
    with a descriptor that raises) are swallowed — this helper is called
    from hot paths (gateway.process_message, chat_sync) where a broken
    session must NOT crash request handling. Caller proceeds with
    unmodified session in that edge case.
    """
    if session is None:
        return False
    mutated = False

    try:
        current_unattended = bool(getattr(session, "is_unattended", False))
    except Exception:
        return False

    if classification.is_unattended and not current_unattended:
        try:
            session.is_unattended = True
            mutated = True
            current_unattended = True
        except Exception:
            current_unattended = False

    try:
        current_strategy = getattr(session, "unattended_strategy", "") or ""
    except Exception:
        current_strategy = ""

    if (
        classification.default_strategy
        and not current_strategy
        and current_unattended
    ):
        try:
            session.unattended_strategy = classification.default_strategy
            mutated = True
        except Exception:
            pass

    return mutated


__all__ = [
    "ConfirmCapability",
    "EntryClassification",
    "IM_WEBHOOK_CHANNELS",
    "SSE_INTERACTIVE_CHANNELS",
    "apply_classification_to_session",
    "classify_entry",
]
