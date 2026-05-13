"""Apply a UI confirm decision and propagate side effects (C8b-3)。

替代 v1 ``PolicyEngine.resolve_ui_confirm`` 的对外 contract：

1. 通知 ``UIConfirmBus`` 设置决策 → 唤醒等待中的 ``wait_for_resolution``
2. 根据 decision 类型把记录写入对应 manager：

   ===========================  ============================================
   decision (UI 选项)              allowlist side-effect
   ===========================  ============================================
   ``allow_once``               无副作用（一次性放行；不写任何 manager）
   ``allow_session`` / ``sandbox``  ``SessionAllowlistManager.add(...)``
   ``allow_always``             ``SessionAllowlistManager.add(...)`` +
                                ``UserAllowlistManager.add_entry(...)`` +
                                ``UserAllowlistManager.save_to_yaml()``
   ``deny`` / 其他              无副作用（明确拒绝/未知值，下次仍然问）
   ===========================  ============================================

设计动机
========

7 个 IM/CLI/Web callsite 原来调 ``pe.resolve_ui_confirm`` 拿到"既唤醒
waiter 又写 allowlist"的一站式效果。C8b-3 删除 ``policy.py`` 后这两件事
分散到 ``UIConfirmBus`` + 两个 manager；为避免 7 个 callsite 各自串联
3-4 行重复代码（且 4 个不同 decision 类型的写入分支），用本函数封装。

| 路径 | 责任 |
|---|---|
| ``UIConfirmBus.resolve(...)`` | 唯一负责 SSE 等待/唤醒 + pending sidecar GC |
| ``UserAllowlistManager.add_entry`` | 持久化白名单 CRUD（engine-scoped） |
| ``SessionAllowlistManager.add`` | session-scope ephemeral allow |
| ``apply_resolution(...)``（本函数）| 把上面三家串起来，给 callsite 一个入口 |

bus 自身不持有任何 allowlist 引用——这样 bus 的单测/状态机 audit 不需要
mock manager。manager 也不需要知道 bus 存在。本函数是唯一耦合点。

UserAllowlistManager 是**engine-scoped**（每个 engine 实例独立）。本函数
通过 ``get_engine_v2().user_allowlist`` 拿全局 engine 的 manager；dry-run
preview 引擎走 ``make_preview_engine``，preview 不会调本函数（preview 不
产 confirm SSE）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def apply_resolution(confirm_id: str, decision: str) -> bool:
    """Resolve a pending UI confirm and propagate allowlist side effects.

    Args:
        confirm_id: SSE 事件 id（``security_confirm.id``）。caller 必须
            保证此 id 之前通过 ``UIConfirmBus.store_pending`` 注册过；
            否则本函数仅唤醒 waiter 不写 allowlist（行为与 v1 一致）。
        decision: 用户选项之一（``allow_once`` / ``allow_session`` /
            ``allow_always`` / ``sandbox`` / ``deny``）。``allow`` 自动归
            一化为 ``allow_once``（v1 兼容）。

    Returns:
        True 当 pending sidecar 被找到并处理；False 当 sidecar 不存在
        （SSE 未发出 / 已 GC / 重复 resolve）。Caller 通常用此返回值决
        定要不要给用户返回"操作不存在"提示。

    Side effects:
        - 总是：唤醒 ``UIConfirmBus`` 上 ``confirm_id`` 的 waiter。
        - decision == ``allow_session`` / ``sandbox`` → SessionAllowlistManager.add
        - decision == ``allow_always`` → 上面的 + UserAllowlistManager.add_entry
          + save_to_yaml（YAML 写失败仅 warn 不抛，与 v1 silent-fail 对齐）
        - decision == ``allow_once`` / ``deny`` / 其他 → 仅唤醒，无 allowlist 写
    """
    from ..ui_confirm_bus import get_ui_confirm_bus
    from .global_engine import get_engine_v2
    from .session_allowlist import get_session_allowlist_manager

    resolved = get_ui_confirm_bus().resolve(confirm_id, decision)
    if resolved is None:
        return False

    final_decision = resolved.get("decision", decision)
    tool_name = resolved.get("tool_name", "")
    params = resolved.get("params", {}) or {}
    needs_sandbox = bool(resolved.get("needs_sandbox", False))

    if final_decision in ("allow_session", "sandbox", "allow_always"):
        get_session_allowlist_manager().add(
            tool_name, params, needs_sandbox=needs_sandbox
        )

    if final_decision == "allow_always":
        try:
            engine = get_engine_v2()
            engine.user_allowlist.add_entry(
                tool_name, params, needs_sandbox=needs_sandbox
            )
            engine.user_allowlist.save_to_yaml()
        except Exception as exc:
            logger.warning(
                "[apply_resolution] failed to persist allow_always for '%s': %s",
                tool_name,
                exc,
            )

    return True


__all__ = ["apply_resolution"]
