"""Per-conversation domain allow / block lists for web_fetch.

The link-reliability work surfaces ``[OPENAKITA_SOURCE]`` events in the UI:
users now see exactly which host was read. This module is the second half of
that loop — letting the user say "never again from this host in this chat"
without restarting the process.

Design notes:
* In-process only. Persisting cross-restart is intentionally out of scope:
  IM users get a fresh session each restart, and we do not want a "permanent
  block" footgun without a settings UI to undo it.
* Block list wins over allow list. An empty conversation has neither, so
  ``decide()`` returns ``"allow"`` by default (consumer-friendly).
* Keys are normalised to lowercase with the leading ``www.`` stripped so
  ``example.com`` and ``www.example.com`` count as the same host.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Decision = Literal["allow", "deny"]


def _normalise_host(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("www."):
        h = h[4:]
    return h


@dataclass
class _ConvRules:
    blocked: set[str] = field(default_factory=set)
    allowed: set[str] = field(default_factory=set)


class DomainAllowlist:
    """Thread-safe per-conversation domain rule store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rules: dict[str, _ConvRules] = {}

    def _ensure(self, conversation_id: str) -> _ConvRules:
        rules = self._rules.get(conversation_id)
        if rules is None:
            rules = _ConvRules()
            self._rules[conversation_id] = rules
        return rules

    def decide(self, conversation_id: str, host: str) -> Decision:
        """Return ``deny`` only if the user has explicitly blocked the host."""
        h = _normalise_host(host)
        if not h:
            return "allow"
        with self._lock:
            rules = self._rules.get(conversation_id)
            if not rules:
                return "allow"
            if h in rules.blocked:
                return "deny"
            return "allow"

    def block(self, conversation_id: str, host: str) -> bool:
        h = _normalise_host(host)
        if not h:
            return False
        with self._lock:
            rules = self._ensure(conversation_id)
            rules.allowed.discard(h)
            if h in rules.blocked:
                return False
            rules.blocked.add(h)
            logger.info(
                "[DomainAllowlist] BLOCK conv=%s host=%s", conversation_id, h
            )
            return True

    def unblock(self, conversation_id: str, host: str) -> bool:
        h = _normalise_host(host)
        if not h:
            return False
        with self._lock:
            rules = self._rules.get(conversation_id)
            if not rules:
                return False
            removed = h in rules.blocked
            rules.blocked.discard(h)
            if removed:
                logger.info(
                    "[DomainAllowlist] UNBLOCK conv=%s host=%s",
                    conversation_id,
                    h,
                )
            return removed

    def approve(self, conversation_id: str, host: str) -> bool:
        """Mark a host as user-approved. Currently advisory (UI hint only)."""
        h = _normalise_host(host)
        if not h:
            return False
        with self._lock:
            rules = self._ensure(conversation_id)
            rules.blocked.discard(h)
            if h in rules.allowed:
                return False
            rules.allowed.add(h)
            return True

    def list_for(self, conversation_id: str) -> dict[str, list[str]]:
        with self._lock:
            rules = self._rules.get(conversation_id)
            if not rules:
                return {"blocked": [], "allowed": []}
            return {
                "blocked": sorted(rules.blocked),
                "allowed": sorted(rules.allowed),
            }

    def clear(self, conversation_id: str | None = None) -> None:
        with self._lock:
            if conversation_id is None:
                self._rules.clear()
            else:
                self._rules.pop(conversation_id, None)


_singleton: DomainAllowlist | None = None


def get_domain_allowlist() -> DomainAllowlist:
    global _singleton
    if _singleton is None:
        _singleton = DomainAllowlist()
    return _singleton
