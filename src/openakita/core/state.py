"""
Lightweight global state management

Modeled after Claude Code's createStore pattern:
- getState / setState / subscribe
- Immutable state updates
- Subscriber notification mechanism
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class StateStore(Generic[T]):
    """Lightweight state store.

    Usage:
        store = StateStore(initial_state)
        state = store.get_state()
        store.set_state(lambda s: replace(s, field=new_value))
        unsub = store.subscribe(lambda s: print(s))
    """

    def __init__(self, initial: T) -> None:
        self._state = initial
        self._listeners: list[Callable[[T], None]] = []

    def get_state(self) -> T:
        """Get current state (read-only reference)."""
        return self._state

    def set_state(self, updater: Callable[[T], T]) -> None:
        """Set new state via updater function and notify all subscribers."""
        new_state = updater(self._state)
        if new_state is self._state:
            return  # No change
        self._state = new_state
        self._notify()

    def subscribe(self, listener: Callable[[T], None]) -> Callable[[], None]:
        """Subscribe to state changes.

        Returns:
            Unsubscribe function
        """
        self._listeners.append(listener)

        def unsubscribe():
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    def _notify(self) -> None:
        """Notify all subscribers."""
        for listener in self._listeners:
            try:
                listener(self._state)
            except Exception as e:
                logger.warning("State listener error: %s", e)


@dataclass(frozen=True)
class AppState:
    """Application-level global state (immutable)."""

    # Agent state
    active_sessions: dict[str, Any] = field(default_factory=dict)
    agent_profiles: dict[str, Any] = field(default_factory=dict)

    # LLM state
    llm_endpoints_healthy: dict[str, bool] = field(default_factory=dict)
    total_tokens_used: int = 0
    total_cost: float = 0.0

    # Runtime
    multi_agent_enabled: bool = True
    current_model: str = ""


# Global app state store
_app_store: StateStore[AppState] | None = None


def get_app_store() -> StateStore[AppState]:
    """Get the global application state store."""
    global _app_store
    if _app_store is None:
        _app_store = StateStore(AppState())
    return _app_store
