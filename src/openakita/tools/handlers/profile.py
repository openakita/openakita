"""
用户档案处理器

处理用户档案相关的系统技能：
- update_user_profile: 更新档案
- skip_profile_question: 跳过问题
- get_user_profile: 获取档案
"""

import logging
from typing import TYPE_CHECKING, Any

from ...core.policy_v2 import ApprovalClass

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class ProfileHandler:
    """用户档案处理器"""

    TOOLS = [
        "update_user_profile",
        "skip_profile_question",
        "get_user_profile",
    ]

    # C7 explicit ApprovalClass
    TOOL_CLASSES = {
        "update_user_profile": ApprovalClass.MUTATING_SCOPED,
        "skip_profile_question": ApprovalClass.EXEC_LOW_RISK,
        "get_user_profile": ApprovalClass.READONLY_GLOBAL,
    }

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "update_user_profile":
            return self._update_profile(params)
        elif tool_name == "skip_profile_question":
            return self._skip_question(params)
        elif tool_name == "get_user_profile":
            return self._get_profile(params)
        else:
            return f"❌ Unknown profile tool: {tool_name}"

    def _update_profile(self, params: dict) -> str:
        """更新用户档案。

        - 已知 key（含别名归一）直接落档
        - 未知 key：在 PR-B1 (`profile_whitelist_v2`) 启用后**不再**默默落入
          ``profile_fallback`` 全局 fact 记忆（那条路径是跨会话身份污染的元凶，
          见 2026-05-09 P0-2）。返回明确错误并提示 LLM 用其它工具记录。
        """
        from ...core.user_profile import resolve_profile_key

        try:
            from ...core.feature_flags import is_enabled as _ff_enabled

            allow_legacy_fallback = not _ff_enabled("profile_whitelist_v2")
        except Exception:
            allow_legacy_fallback = True

        available_keys = self.agent.profile_manager.get_available_keys()

        def _normalize(raw_key: str) -> tuple[str, bool]:
            mapped = resolve_profile_key(raw_key)
            return mapped, mapped in available_keys

        if "key" not in params:
            updated: list[str] = []
            saved_as_memory: list[str] = []
            rejected: list[str] = []
            for k, v in params.items():
                mapped_key, ok = _normalize(k)
                if ok:
                    self.agent.profile_manager.update_profile(mapped_key, str(v))
                    if mapped_key != k:
                        updated.append(f"{mapped_key}({k}) = {v}")
                    else:
                        updated.append(f"{mapped_key} = {v}")
                elif allow_legacy_fallback and self._save_unknown_as_memory(k, v):
                    saved_as_memory.append(f"{k} = {v}")
                else:
                    rejected.append(f"{k}")
            parts: list[str] = []
            if updated:
                parts.append(f"✅ 已更新档案: {', '.join(updated)}")
            if saved_as_memory:
                parts.append(
                    f"📝 以下信息不在档案白名单内，已作为长期记忆保存: "
                    f"{', '.join(saved_as_memory)}"
                )
            if rejected:
                parts.append(
                    f"⚠️ 以下 key 不在档案白名单内，已**拒绝**写入（避免跨会话污染）: "
                    f"{', '.join(rejected)}\n"
                    f"如确属用户长期偏好，请改用 `add_memory(type=\"preference\", ...)` "
                    f"明确记录；或使用以下白名单 key 之一: {', '.join(sorted(available_keys)[:20])}…"
                )
            if parts:
                return "\n".join(parts)
            return (
                f"❌ 参数格式错误，正确用法: {{\"key\": \"name\", \"value\": \"小明\"}}\n"
                f"可用的键: {', '.join(available_keys)}"
            )

        key = params["key"]
        value = params.get("value", "")
        mapped_key, ok = _normalize(key)

        if not ok:
            if allow_legacy_fallback and self._save_unknown_as_memory(key, value):
                return (
                    f"📝 档案白名单不含 `{key}`，已作为长期记忆保存: {key} = {value}\n"
                    f"（如需正式建档请联系管理员扩展 USER_PROFILE_ITEMS）"
                )
            return (
                f"❌ 未知的档案项: `{key}`（已拒绝写入，避免跨会话污染）。\n"
                f"如属用户长期偏好，请改用 `add_memory(type=\"preference\", ...)` 记录。\n"
                f"可用的档案 key: {', '.join(sorted(available_keys)[:20])}…"
            )

        self.agent.profile_manager.update_profile(mapped_key, value)
        if mapped_key != key:
            return f"✅ 已更新档案: {mapped_key} = {value}（输入键 `{key}` 已归一为 `{mapped_key}`）"
        return f"✅ 已更新档案: {mapped_key} = {value}"

    def _save_unknown_as_memory(self, key: str, value: Any) -> bool:
        """把白名单外的 key=value 当作 fact 落入语义记忆。

        失败返回 False，由调用方决定是否报错。

        PR-B2：写入时强制带 ``scope_owner=session_id`` 和 30 天 TTL，
        避免 legacy 路径再次造成跨会话身份污染。
        """
        try:
            from datetime import datetime, timedelta

            mm = getattr(self.agent, "memory_manager", None)
            if mm is None or not hasattr(mm, "add_memory"):
                return False
            from ...memory.types import Memory, MemoryPriority, MemoryType

            try:
                from ...core.feature_flags import is_enabled as _ff_enabled

                ff_enabled = _ff_enabled("memory_session_scope_v1")
            except Exception:
                ff_enabled = True

            session = getattr(self.agent, "current_session", None)
            session_id = ""
            if session is not None:
                session_id = (
                    getattr(session, "session_key", "")
                    or getattr(session, "id", "")
                    or ""
                )

            scope = "session" if (ff_enabled and session_id) else "global"
            scope_owner = session_id if (ff_enabled and session_id) else ""
            expires_at = (
                datetime.now() + timedelta(days=30) if ff_enabled else None
            )

            content = f"用户档案补充: {key} = {value}"
            mem = Memory(
                content=content,
                type=MemoryType.FACT,
                priority=MemoryPriority.LONG_TERM,
                source="profile_fallback",
                importance_score=0.7,
                tags=["profile_extra", key],
                scope=scope,
                scope_owner=scope_owner,
                expires_at=expires_at,
            )
            mm.add_memory(mem, scope=scope, scope_owner=scope_owner)
            return True
        except Exception as e:
            logger.warning(f"[ProfileHandler] fallback to memory failed: {e}")
            return False

    def _skip_question(self, params: dict) -> str:
        """跳过档案问题"""
        key = params["key"]
        self.agent.profile_manager.skip_question(key)
        return f"✅ 已跳过问题: {key}"

    def _get_profile(self, params: dict) -> str:
        """获取用户档案"""
        summary = self.agent.profile_manager.get_profile_summary()

        if not summary:
            return "用户档案为空\n\n提示: 通过对话中分享信息来建立档案"

        return summary


def create_handler(agent: "Agent"):
    """创建用户档案处理器"""
    handler = ProfileHandler(agent)
    return handler.handle
