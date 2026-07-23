"""Session-key -> org-id lookup bridge for the v2 dispatcher.

The v2 dispatch path (``runtime.channel_routing.
dispatch_inbound_message_to_v2``) needs to answer one tiny question
on every inbound IM message: *which org is this session bound to,
if any?* The existing gateway already persists ``bound_org_id`` on
``Session.metadata`` (see ``channels/gateway.py`` ``/org bind``
handling), but the runtime layer must not depend on
``openakita.sessions`` directly -- that would re-introduce the
exact import cycle the fork-style rewrite (ADR-0001) was meant to
break.

The dependency-injection seam in this module solves that cleanly:

* Callers (today: the gateway in P-RC-1 commit 4; tomorrow: the API
  routes once they grow a v2 dispatch path) register a lookup
  callable via :func:`register_session_org_lookup`.
* The runtime calls :func:`get_org_id_for_session` and gets either
  a string org id or ``None``. Any exception in the user-provided
  lookup is swallowed and reported as ``None`` so a misbehaving
  session backend can never break the existing dispatch fallback.

This module deliberately keeps no state apart from the registry
slot, and has no internal imports outside the standard library.
See continuation plan section 2.1 for the full design.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol, runtime_checkable

__all__ = [
    "SessionOrgLookup",
    "get_org_id_for_session",
    "register_session_org_lookup",
    "reset_session_org_lookup",
]

logger = logging.getLogger(__name__)


@runtime_checkable
class SessionOrgLookup(Protocol):
    """Callable contract for the registered lookup.

    Implementations take a session key (``"<bot>:<chat>:<user>"`` per
    ``SessionManager.build_session_key``) and return the bound org
    id, or ``None`` when the session is not org-bound or does not
    exist. Implementations MUST NOT raise; doing so is caught and
    converted to ``None`` by :func:`get_org_id_for_session`.
    """

    def __call__(self, session_key: str) -> str | None: ...


_LOCK = threading.RLock()
_LOOKUP: Callable[[str], str | None] | None = None


def register_session_org_lookup(
    lookup: Callable[[str], str | None] | None,
) -> None:
    """Install (or clear, when ``None``) the process-wide lookup.

    The gateway calls this once on construction; tests use it to
    inject deterministic behaviour. Last-writer-wins -- v2 runs in a
    single process, so we keep the surface tiny rather than building
    a stack of lookups.
    """
    global _LOOKUP
    with _LOCK:
        _LOOKUP = lookup


def reset_session_org_lookup() -> None:
    """Convenience wrapper used by tests' teardown."""
    register_session_org_lookup(None)


def get_org_id_for_session(session_key: str) -> str | None:
    """Resolve the bound org id for ``session_key`` if registered.

    Returns ``None`` when:

    * no lookup has been registered (the most common case during
      tests and the canary-off production path);
    * the registered lookup returns ``None`` (session exists but is
      not org-bound, or the session does not exist at all);
    * the registered lookup raises -- the exception is logged at
      debug level and ``None`` is returned. The v2 dispatch path is
      a fallback layer; it must never let a session-store hiccup
      break the existing gateway path.

    Args:
        session_key: the canonical session key produced by
            ``SessionManager.build_session_key`` (``"<bot>:<chat>:
            <user>"`` or ``"<bot>:<chat>:<user>:<thread>"``).

    Returns:
        Org id as a non-empty string, or ``None``.
    """
    if not session_key:
        return None
    with _LOCK:
        lookup = _LOOKUP
    if lookup is None:
        return None
    try:
        result = lookup(session_key)
    except Exception as exc:  # noqa: BLE001 -- never break the dispatch
        logger.debug(
            "[session_bridge] lookup for %s raised; treating as unbound: %s",
            session_key,
            exc,
        )
        return None
    if result is None:
        return None
    if not isinstance(result, str) or not result:
        logger.debug(
            "[session_bridge] lookup for %s returned non-string %r; treating as unbound",
            session_key,
            result,
        )
        return None
    return result
