"""Session allowlist manager (C8b-3)。

替代 v1 ``PolicyEngine._session_allowlist`` dict + ``mark_confirmed`` 的
session-scope 行为 + ``_confirmed_cache`` TTL 兜底。

设计要点
========

1. **Module-level singleton（仿 ``UIConfirmBus`` / ``SkillAllowlistManager``）**：
   v1 的 ``_session_allowlist`` 实际上是 process-wide global state（v1
   ``cleanup_session(sid)`` 不论 sid 都 wipe 全部），不是真正的 session-scoped。
   v2 保留 v1 等价语义以避免 C8b-3 引入新行为差异；真正的 per-session 隔离
   留到 C12 一起做。

2. **不持久化**：与 v1 一致——session 内有效，agent restart / process
   shutdown 后即失效。``allow_always`` 走 ``UserAllowlistManager`` 路径
   写 YAML，**不**走 SessionAllowlistManager（但通常调用方会同时 add
   到这两边，让"YAML reload 完成前"也立即生效）。

3. **Keying**：与 v1 ``_confirm_cache_key`` 完全等价——
   ``md5(tool_name + command + path)``。这意味着同一 tool_name + 相同
   command/path 的不同字段（如 retry_attempt id）不影响命中。**特别注意**：
   v1 keying **只看** ``params.command`` / ``params.path``，其他 params 字段
   的差异会被忽略——这是 v1 已知行为，C8b-3 完全保留以确保 retry 路径不
   变；如有 fine-grained 需求请打开新讨论。

4. **TTL 移除**：v1 ``_confirmed_cache`` 用 TTL（confirm_ttl seconds）
   提供"短时间内同一调用免 confirm"的兜底。在 v2 中：
   - "allow_once" 由 reasoning_engine 一次性放行处理（不写 manager）
   - "allow_session" 直接落到本 manager（无 TTL；session 内永久有效）
   - "allow_always" 落到 UserAllowlistManager（持久化）+ 本 manager（即时生效）
   TTL 缓存层属于 v1 hack，v2 简化为二态：要么 session 永久允许，要么
   下次还问。

5. **bypass 边界**：本 manager 命中 → step 9 ALLOW relax。与 skill
   allowlist 同等强度——不绕过 safety_immune（step 3）/ owner_only
   （step 4）/ matrix DENY（step 6）/ shell DENY（classifier）/
   death_switch（step 10）。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


def _make_key(tool_name: str, params: dict[str, Any]) -> str:
    """Generate a stable cache key for a (tool, params) pair.

    精确复刻 v1 ``PolicyEngine._confirm_cache_key`` —— md5(tool_name +
    params.command + params.path)。其他字段被故意忽略（保留 v1 keying 行为）。
    """
    param_str = (
        f"{tool_name}:"
        f"{params.get('command', '') if params else ''}"
        f"{params.get('path', '') if params else ''}"
    )
    return hashlib.md5(param_str.encode()).hexdigest()


class SessionAllowlistManager:
    """Session-scope ephemeral allowlist。

    内部存储 ``dict[content_key → entry_dict]``。entry 形如
    ``{"needs_sandbox": bool, "tool_name": str, "added_at": float}``。
    ``tool_name`` / ``added_at`` 仅供调试 / snapshot；命中判定看 key。
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    # ----- mutate ----------------------------------------------------------

    def add(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        needs_sandbox: bool = False,
    ) -> None:
        """记录 ``(tool_name, params)`` 在 session 内已被允许。

        与 v1 ``mark_confirmed(scope='session')`` / ``mark_confirmed(scope='always')``
        / ``mark_confirmed(scope='sandbox')`` 的 session 写入面等价——
        scope 区分由 caller 处理（``allow_always`` 同时调 UserAllowlistManager
        持久化，``allow_once`` 不调本方法）。
        """
        if not tool_name:
            return
        import time

        key = _make_key(tool_name, params or {})
        with self._lock:
            self._entries[key] = {
                "tool_name": tool_name,
                "needs_sandbox": bool(needs_sandbox),
                "added_at": time.time(),
            }
            logger.debug(
                "[PolicyV2 SessionAllowlist] '%s' added (needs_sandbox=%s, key=%s)",
                tool_name,
                needs_sandbox,
                key[:8],
            )

    def clear(self) -> None:
        """清空全部 session 允许（agent reset / session 切换时调）。

        注意：与 v1 ``cleanup_session(session_id)`` 行为一致——**不**按
        session_id 过滤，全部 wipe。v1 当年这么做的原因是 ``_session_allowlist``
        本身就不存 session_id；v2 保留同行为避免引入新差异。
        """
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
        if count:
            logger.debug(
                "[PolicyV2 SessionAllowlist] cleared %d entries", count
            )

    # ----- query -----------------------------------------------------------

    def is_allowed(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """查 ``(tool_name, params)`` 是否已 session-allowed。

        返回 entry dict（含 ``needs_sandbox``）或 None。命中后 caller 应该
        把 ``needs_sandbox`` 透传到下游 sandbox 决策（与 v1 ``_check_user_confirm``
        Tier 2 的 contract 一致）。
        """
        if not tool_name:
            return None
        key = _make_key(tool_name, params or {})
        with self._lock:
            entry = self._entries.get(key)
        return dict(entry) if entry is not None else None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """调试 / API 用——当前 session-allow 快照。

        返回 ``dict[content_key → entry]`` 的 deep copy。caller 不能 mutate
        本 manager 内部状态。
        """
        with self._lock:
            return {k: dict(v) for k, v in self._entries.items()}

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_singleton: SessionAllowlistManager | None = None
_singleton_lock = threading.Lock()


def get_session_allowlist_manager() -> SessionAllowlistManager:
    """获取全局 session allowlist manager。

    与 ``UIConfirmBus`` / ``SkillAllowlistManager`` 一样是 process-wide
    ephemeral state——不绑定到特定 PolicyEngineV2 实例（dry-run preview
    engine 不应"借"现成的 session 授权，避免预览结果与实际不一致）。
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = SessionAllowlistManager()
    return _singleton


def reset_session_allowlist_manager() -> None:
    """测试 fixture 用——清空 singleton 并重新构造。

    生产代码请用 ``get_session_allowlist_manager().clear()``，避免别处持
    有旧引用导致 split-brain。
    """
    global _singleton
    with _singleton_lock:
        _singleton = None


__all__ = [
    "SessionAllowlistManager",
    "get_session_allowlist_manager",
    "reset_session_allowlist_manager",
]
