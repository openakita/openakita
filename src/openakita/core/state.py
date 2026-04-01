"""
轻量全局状态管理

参考 Claude Code 的 createStore 模式:
- getState / setState / subscribe
- 不可变状态更新
- 订阅者通知机制
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class StateStore(Generic[T]):
    """轻量级状态存储。

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
        """获取当前状态（只读引用）。"""
        return self._state

    def set_state(self, updater: Callable[[T], T]) -> None:
        """通过更新函数设置新状态，通知所有订阅者。"""
        new_state = updater(self._state)
        if new_state is self._state:
            return  # No change
        self._state = new_state
        self._notify()

    def subscribe(self, listener: Callable[[T], None]) -> Callable[[], None]:
        """订阅状态变更。

        Returns:
            取消订阅的函数
        """
        self._listeners.append(listener)

        def unsubscribe():
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    def _notify(self) -> None:
        """通知所有订阅者。"""
        for listener in self._listeners:
            try:
                listener(self._state)
            except Exception as e:
                logger.warning("State listener error: %s", e)


@dataclass(frozen=True)
class AppState:
    """应用级全局状态（不可变）。"""

    # Agent 状态
    active_sessions: dict[str, Any] = field(default_factory=dict)
    agent_profiles: dict[str, Any] = field(default_factory=dict)

    # LLM 状态
    llm_endpoints_healthy: dict[str, bool] = field(default_factory=dict)
    total_tokens_used: int = 0
    total_cost: float = 0.0

    # Runtime
    multi_agent_enabled: bool = True
    current_model: str = ""


# Global app state store
_app_store: StateStore[AppState] | None = None


def get_app_store() -> StateStore[AppState]:
    """获取全局应用状态存储。"""
    global _app_store
    if _app_store is None:
        _app_store = StateStore(AppState())
    return _app_store
