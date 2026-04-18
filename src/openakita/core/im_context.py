"""
IM context (coroutine isolation)

Historically the project used `Agent._current_im_session/_current_im_gateway` as class-level
globals, which caused cross-talk under concurrency (multiple IM sessions / parallel scheduled tasks).

This module uses contextvars to provide per-coroutine/task isolated context.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

# Session / MessageGateway types are defined in other modules; use Any here to avoid circular imports
current_im_session: ContextVar[Any | None] = ContextVar("current_im_session", default=None)
current_im_gateway: ContextVar[Any | None] = ContextVar("current_im_gateway", default=None)


def get_im_session() -> Any | None:
    return current_im_session.get()


def get_im_gateway() -> Any | None:
    return current_im_gateway.get()


def set_im_context(*, session: Any | None, gateway: Any | None) -> tuple[Any, Any]:
    """
    Set IM context; returns tokens for later reset.
    """
    tok1 = current_im_session.set(session)
    tok2 = current_im_gateway.set(gateway)
    return tok1, tok2


def reset_im_context(tokens: tuple[Any, Any]) -> None:
    tok1, tok2 = tokens
    current_im_session.reset(tok1)
    current_im_gateway.reset(tok2)
