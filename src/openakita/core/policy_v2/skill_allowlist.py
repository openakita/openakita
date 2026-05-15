"""Skill allowlist manager (C8b-1)。

替代 v1 ``PolicyEngine._skill_allowlists`` dict + ``add_skill_allowlist`` /
``remove_skill_allowlist`` / ``clear_skill_allowlists`` / ``_is_skill_allowed``。

设计要点
========

1. **Module-level singleton（仿 ``UIConfirmBus``）**：skill 临时授权是
   process-wide ephemeral state，不属于任何单一 PolicyEngineV2 实例。
   ``get_skill_allowlist_manager()`` 是唯一入口；测试用 ``reset_skill_allowlist_manager()``。

2. **不持久化**：与 v1 一致——skill_allowlists 是 session 内的临时白名单，
   skill uninstall / agent restart 后即失效。不写 YAML、不读 YAML。

3. **bypass 边界**：v1 ``_is_skill_allowed`` 命中后仍要走：
   - 死亡开关 → 不可绕过
   - self-protection → 不可绕过
   - zone DELETE/RECURSIVE_DELETE → 仍要 confirm
   - shell 命令 risk check → DENY 仍 DENY
   v2 把 skill bypass 放到 step 9 ``_check_user_allowlist`` 内（在 safety_immune /
   owner_only / channel_compat / matrix DENY 之后），**结构上**已经保证
   safety 类不可绕过；shell DENY 由 classifier shell_risk + matrix 提前拦截。

4. **仅读取 ApprovalClass**：v2 step 9 命中 skill_allowlist 后产出 ALLOW；
   reasoning_engine 后续若分发到 shell tool，shell_risk classification 已在
   step 2 完成（class 已锁定）；矩阵 DENY 在 step 6 短路；safety_immune 在
   step 3 短路。所以 step 9 命中即可直接 ALLOW。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable

logger = logging.getLogger(__name__)


class SkillAllowlistManager:
    """临时 skill 授权管理（与 v1 ``_skill_allowlists`` 字段同结构）。

    内部存储 ``dict[skill_id → frozenset[tool_name]]``。frozenset 防止外部
    caller 改动后让 ``is_allowed`` 行为漂移；新增/删除时整体替换。
    """

    def __init__(self) -> None:
        self._allowlists: dict[str, frozenset[str]] = {}
        self._lock = threading.RLock()

    # ----- mutate ----------------------------------------------------------

    def add(self, skill_id: str, tool_names: Iterable[str]) -> None:
        """授权 ``skill_id`` 临时使用 ``tool_names`` 列表中的工具。

        与 v1 ``add_skill_allowlist`` 行为一致：tool_names 为空时不写入
        （避免无意义的空 entry 干扰 ``is_allowed`` 的 ``any(...)`` 短路）。
        """
        if not skill_id:
            return
        names = frozenset(t for t in tool_names if t)
        if not names:
            return
        with self._lock:
            self._allowlists[skill_id] = names
            logger.debug(
                "[PolicyV2 SkillAllowlist] '%s' granted: %s", skill_id, sorted(names)
            )

    def remove(self, skill_id: str) -> bool:
        """撤销 ``skill_id`` 的所有临时授权；返回是否真的删了一个 entry。"""
        with self._lock:
            removed = self._allowlists.pop(skill_id, None)
        if removed is not None:
            logger.debug(
                "[PolicyV2 SkillAllowlist] '%s' revoked: %s",
                skill_id,
                sorted(removed),
            )
            return True
        return False

    def clear(self) -> None:
        """撤销所有 skill 授权（agent reset / session 切换时调）。"""
        with self._lock:
            self._allowlists.clear()

    # ----- query -----------------------------------------------------------

    def is_allowed(self, tool_name: str) -> bool:
        """工具是否被任一 active skill 授权。

        与 v1 ``_is_skill_allowed`` 等价。``any`` 短路保证 O(N_skills) 但
        N 一般 < 5，开销可忽略。
        """
        if not tool_name:
            return False
        with self._lock:
            return any(tool_name in tools for tools in self._allowlists.values())

    def granted_by(self, tool_name: str) -> list[str]:
        """返回授权了 ``tool_name`` 的 skill_id 列表（审计用）。"""
        with self._lock:
            return [sid for sid, tools in self._allowlists.items() if tool_name in tools]

    def snapshot(self) -> dict[str, list[str]]:
        """API/调试用：当前授权快照（sorted 便于稳定输出）。"""
        with self._lock:
            return {sid: sorted(tools) for sid, tools in self._allowlists.items()}


# ---------------------------------------------------------------------------
# Module-level singleton（仿 ``UIConfirmBus`` 模式）
# ---------------------------------------------------------------------------

_singleton: SkillAllowlistManager | None = None
_singleton_lock = threading.Lock()


def get_skill_allowlist_manager() -> SkillAllowlistManager:
    """获取全局 skill allowlist manager。

    与 ``UIConfirmBus`` 一样，是 process-wide ephemeral state——不绑定到
    特定 PolicyEngineV2 实例（dry-run preview 引擎也不应"借"现成的 skill
    授权，避免预览结果与实际不一致）。
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = SkillAllowlistManager()
    return _singleton


def reset_skill_allowlist_manager() -> None:
    """测试 fixture 用——清空 singleton 并重新构造。

    生产代码不应该调这个；用 ``get_skill_allowlist_manager().clear()``
    清空状态而不重建对象更安全（避免别处持有旧引用导致 split-brain）。
    """
    global _singleton
    with _singleton_lock:
        _singleton = None


__all__ = [
    "SkillAllowlistManager",
    "get_skill_allowlist_manager",
    "reset_skill_allowlist_manager",
]
