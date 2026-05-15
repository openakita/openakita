"""Death switch tracker (C8b-1)。

替代 v1 ``PolicyEngine._on_deny`` / ``_on_allow`` 的连续 deny 计数 +
``_readonly_mode`` 切换 + ``reset_readonly_mode`` + ``security:death_switch``
broadcast。

设计要点
========

1. **Module-level singleton（仿 ``UIConfirmBus``）**：death_switch 是
   process-wide 状态——所有 PolicyEngineV2 实例共享同一份计数（dry-run
   preview engine 故意不计入，见 ``record_decision`` 的 ``count`` 参数）。

2. **Broadcast hook 解耦**：v1 直接 import ``api.routes.websocket.broadcast_event``
   产生 v2→api 反向耦合。v2 用 callable hook 注入：``set_broadcast_hook(callable)``
   由 api 启动时设置一次，tracker 触发时调；hook 抛异常吞掉不影响计数。

3. **配置来源**：tracker 不持有 ``DeathSwitchConfig`` 实例（因为 config 可能
   在 hot-reload 时变），而是每次 ``record_decision`` 接受 ``threshold`` /
   ``total_multiplier`` 参数。Engine 在调用前从 ``self._config.death_switch``
   读出来传入。这保证 hot-reload 时不需要通知 tracker。

4. **read 操作不重置**：v1 行为—— ``read_file`` / ``list_directory`` /
   ``grep`` / ``glob`` 的 ALLOW **不**重置 ``_consecutive_denials``
   （否则用户读了一次 readme 就清掉之前的连续拒绝信号）。v2 沿用这个白名单。

5. **disabled 时不动状态**：``self._config.death_switch.enabled = False``
   时 tracker 不计数也不触发 readonly_mode；与 v1 ``self_protection.enabled``
   语义对齐。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


# v1 ``_on_allow`` 不重置计数的"只读"工具集；v2 沿用。
_NON_RESETTING_READ_TOOLS = frozenset(
    {"read_file", "list_directory", "grep", "glob"}
)


BroadcastHook = Callable[[dict[str, Any]], None]


class DeathSwitchTracker:
    """连续 deny 计数 + readonly_mode 切换。

    线程安全：mutate 走 ``self._lock``。read（``is_readonly_mode`` 等）也走
    锁以避免读到半更新状态。
    """

    def __init__(self) -> None:
        self._consecutive_denials: int = 0
        self._total_denials: int = 0
        self._readonly_mode: bool = False
        self._broadcast_hook: BroadcastHook | None = None
        self._lock = threading.RLock()

    # ----- query -----------------------------------------------------------

    def is_readonly_mode(self) -> bool:
        with self._lock:
            return self._readonly_mode

    def stats(self) -> dict[str, Any]:
        """审计/调试用：返回当前计数器快照。"""
        with self._lock:
            return {
                "consecutive_denials": self._consecutive_denials,
                "total_denials": self._total_denials,
                "readonly_mode": self._readonly_mode,
            }

    # ----- mutate ----------------------------------------------------------

    def record_decision(
        self,
        *,
        action: str,
        tool_name: str,
        enabled: bool = True,
        threshold: int = 3,
        total_multiplier: int = 3,
    ) -> bool:
        """记录一次决策结果。返回是否在本次调用触发了 readonly_mode。

        Args:
            action: ``"allow"`` / ``"deny"`` / ``"confirm"`` 等。仅 ``"deny"``
                增加计数；``"allow"`` 重置 consecutive（除 read tools）；
                其他值不影响计数。
            tool_name: 用于判断"读操作不重置 consecutive"。
            enabled: 对应 v2 ``DeathSwitchConfig.enabled``。False 时此调用 no-op
                （计数不增、readonly 不切换）。
            threshold: 连续 deny 触发阈值（v2 ``DeathSwitchConfig.threshold``）。
            total_multiplier: 总 deny 触发倍数（v2 ``DeathSwitchConfig.total_multiplier``）。
        """
        if not enabled:
            return False

        with self._lock:
            if action == "deny":
                self._consecutive_denials += 1
                self._total_denials += 1

                if self._readonly_mode:
                    return False  # 已经是 readonly，无需再触发
                total_threshold = (
                    threshold * total_multiplier if threshold > 0 else 0
                )
                should_trigger = (
                    (threshold > 0 and self._consecutive_denials >= threshold)
                    or (total_threshold > 0 and self._total_denials >= total_threshold)
                )
                if should_trigger:
                    self._readonly_mode = True
                    triggered_consec = self._consecutive_denials
                    triggered_total = self._total_denials
                    # 在锁外 broadcast（避免 hook 慢调用 / 重入死锁）
                    triggered = True
                else:
                    triggered = False
            elif action == "allow":
                if tool_name not in _NON_RESETTING_READ_TOOLS:
                    self._consecutive_denials = 0
                triggered = False
            else:
                triggered = False

        if action == "deny" and triggered:
            logger.warning(
                "[PolicyV2 DeathSwitch] 触发: 连续拒绝=%d, 累计拒绝=%d, "
                "Agent 进入只读模式",
                triggered_consec,
                triggered_total,
            )
            self._maybe_broadcast(
                {
                    "active": True,
                    "consecutive": triggered_consec,
                    "total": triggered_total,
                }
            )
            return True
        return False

    def reset(self) -> None:
        """手动重置 readonly + 计数器（v1 ``reset_readonly_mode`` 等价）。

        仅清 consecutive；total 保留作为 lifetime 审计计数（与 v1 同行为：
        v1 ``reset_readonly_mode`` 也只清 consecutive_denials，不清 total）。
        """
        with self._lock:
            self._readonly_mode = False
            self._consecutive_denials = 0
        logger.info("[PolicyV2 DeathSwitch] 只读模式已重置")
        self._maybe_broadcast({"active": False})

    # ----- broadcast hook --------------------------------------------------

    def set_broadcast_hook(self, hook: BroadcastHook | None) -> None:
        """注入 broadcast 回调（避免 v2→api 反向耦合）。

        Hook 形参：dict 包含 ``active`` / 可选 ``consecutive`` / ``total``。
        Hook 抛异常被吞掉，不影响 tracker 计数。
        """
        with self._lock:
            self._broadcast_hook = hook

    def _maybe_broadcast(self, payload: dict[str, Any]) -> None:
        hook = self._broadcast_hook
        if hook is None:
            return
        try:
            hook(payload)
        except Exception as exc:
            logger.warning(
                "[PolicyV2 DeathSwitch] broadcast hook raised: %s", exc
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_singleton: DeathSwitchTracker | None = None
_singleton_lock = threading.Lock()


def get_death_switch_tracker() -> DeathSwitchTracker:
    """获取全局 death switch tracker。

    Process-wide singleton——所有 engine 共享一份计数（dry-run preview engine
    通过 ``count=False`` 类似机制故意不计入，见 ``PolicyEngineV2`` 实现）。
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = DeathSwitchTracker()
    return _singleton


def reset_death_switch_tracker() -> None:
    """测试 fixture 用——清空 singleton 并重新构造。

    生产代码不应该调；用 ``get_death_switch_tracker().reset()`` 清状态而
    不重建对象更安全（避免别处持有旧引用导致 broadcast hook 丢失）。
    """
    global _singleton
    with _singleton_lock:
        _singleton = None


__all__ = [
    "BroadcastHook",
    "DeathSwitchTracker",
    "get_death_switch_tracker",
    "reset_death_switch_tracker",
]
