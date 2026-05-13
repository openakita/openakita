"""
记忆系统处理器

处理记忆相关的系统技能：
- add_memory: 添加记忆
- search_memory: 搜索记忆
- get_memory_stats: 获取记忆统计
- list_recent_tasks: 列出最近任务
- search_conversation_traces: 搜索完整对话历史
- trace_memory: 跨层导航（记忆↔情节↔对话）
"""

import json
import logging
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.policy_v2 import ApprovalClass
from ...memory.json_utils import coerce_text, coerce_tool_names

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class MemoryHandler:
    """
    记忆系统处理器

    处理所有记忆相关的工具调用
    """

    TOOLS = [
        "consolidate_memories",
        "add_memory",
        "search_memory",
        "get_memory_stats",
        "list_recent_tasks",
        "search_conversation_traces",
        "trace_memory",
        "search_relational_memory",
        "get_session_context",
        "memory_delete_by_query",
    ]

    # C7 explicit ApprovalClass
    TOOL_CLASSES = {
        "consolidate_memories": ApprovalClass.EXEC_LOW_RISK,
        "add_memory": ApprovalClass.EXEC_LOW_RISK,
        "search_memory": ApprovalClass.READONLY_SEARCH,
        "get_memory_stats": ApprovalClass.READONLY_GLOBAL,
        "list_recent_tasks": ApprovalClass.READONLY_GLOBAL,
        "search_conversation_traces": ApprovalClass.READONLY_SEARCH,
        "trace_memory": ApprovalClass.READONLY_GLOBAL,
        "search_relational_memory": ApprovalClass.READONLY_SEARCH,
        "get_session_context": ApprovalClass.READONLY_GLOBAL,
        "memory_delete_by_query": ApprovalClass.DESTRUCTIVE,
    }

    _SEARCH_TOOLS = frozenset(
        {
            "search_memory",
            "list_recent_tasks",
            "trace_memory",
            "search_conversation_traces",
            "search_relational_memory",
        }
    )

    _NAVIGATION_GUIDE = (
        "📖 记忆系统导航指南（仅显示一次）\n\n"
        "## 三层关联机制\n"
        "- 记忆 → 情节：每条记忆有 source_episode_id，指向产生它的任务情节\n"
        "- 情节 → 记忆：每个情节有 linked_memory_ids，列出它产出的记忆\n"
        "- 情节 → 对话：通过 session_id 关联到原始对话轮次\n\n"
        "## 工具详解\n"
        "- search_memory — 搜索提炼后的知识（偏好/规则/经验/技能），结果含来源情节 ID\n"
        "- list_recent_tasks — 列出最近任务情节，含关联记忆数和工具列表\n"
        "- trace_memory — 跨层导航电梯：\n"
        "  · 传 memory_id → 返回源情节摘要 + 相关对话片段\n"
        "  · 传 episode_id → 返回关联记忆列表 + 对话原文\n"
        "- search_conversation_traces — 原始对话全文搜索（参数+返回值）\n"
        "- add_memory — 主动记录经验(experience/skill)、教训(error)、偏好(preference/rule)\n\n"
        "## 搜索策略：先概览，再深入\n"
        "1. search_memory 查现成的经验/规则/事实\n"
        "2. 需要上下文 → trace_memory(memory_id=...) 溯源到情节和对话\n"
        "3. 对某个情节感兴趣 → trace_memory(episode_id=...) 查关联记忆和对话\n"
        "4. 以上都没结果 → search_conversation_traces 全文搜索\n\n"
        "## 何时搜索\n"
        '- 用户问"做了什么" → list_recent_tasks\n'
        '- 用户提到"之前/上次" → search_memory\n'
        "- 需要操作细节/具体命令 → trace_memory 或 search_conversation_traces\n"
        "- 做过类似任务 → 先 search_memory 查经验，需要细节再 trace_memory\n"
        "- 不确定时 → 不搜索\n\n"
        "---\n\n"
    )

    @staticmethod
    def _context_text(value: Any, limit: int | None = None) -> str:
        """Format persisted session values safely for human-readable output."""
        if value is None:
            text = ""
        elif isinstance(value, str):
            text = value
        elif isinstance(value, (dict, list, tuple)):
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(value)
        else:
            text = str(value)

        if limit is not None:
            return text[:limit]
        return text

    @staticmethod
    def _importance_value(value: Any, default: float = 0.5) -> float:
        """Coerce model-provided importance into the supported 0.0-1.0 range."""
        if isinstance(value, bool):
            return default
        try:
            importance = float(value)
        except (TypeError, ValueError):
            importance = default
        if not math.isfinite(importance):
            importance = default
        return max(0.0, min(1.0, importance))

    _ONE_OFF_TASK_RE = re.compile(
        r"用户(?:当前|这次|希望|想要|需要|要求|让我|要).{0,24}"
        r"(?:下载|搜索|查找|查询|整理|生成|制作|安装|启动|创建|发送|打开|访问|截图|导出|上传|配置)"
    )
    _TASK_REPORT_RE = re.compile(
        r"(?:任务执行|执行摘要|已交付文件|文件已生成|成功完成|搞定|完成项|工具调用)"
    )
    # 稳定事实 / 用户档案级信息：放宽匹配，覆盖 LLM 常见的改写句式。
    # 关键修复：原正则要求 `用户` 后紧跟动词，导致 "用户陈彦廷居住在重庆…"
    # 这种 LLM 把人名插入主语位的写法完全漏判，被错误降级为 session。
    _STABLE_FACT_RE = re.compile(
        r"(?:"
        # 1) 用户 + 任意 0-12 字 + 稳定属性动词 / 居住状态
        r"用户.{0,12}(?:是|为|叫|称呼|使用|偏好|喜欢|习惯|从事|负责|"
        r"居住|位于|来自|住在|在.{0,4}(?:工作|生活|居住|定居))"
        # 2) 用户 + 时间副词 + 任意行为（"用户每月可投入..."）
        r"|用户.{0,8}(?:每月|每周|每天|每年|每次|每隔|常常|经常|总是|始终|长期|平时)"
        # 3) 项目 / 代号 / 产品 这种命名实体出现 → 倾向于是项目档案
        r"|(?:项目代号|产品代号|代号为|代号叫|项目名|产品名|项目叫)"
        # 4) 经典身份/地理/工具关键词
        r"|职业|公司|行业|时区|操作系统|常用工具|首选|默认使用"
        # 5) 时间副词独立出现
        r"|长期|以后|每次|总是|始终|永久"
        r")"
    )
    # 用户消息里出现这些关键词 → 明确希望跨会话长期保存。
    # 由 _detect_user_persistence_intent 检索 session 上下文使用。
    _USER_PERSIST_INTENT_RE = re.compile(
        r"(?:永久(?:保存|记住|记下)|长期(?:记住|保存|留着)|"
        r"跨会话|新(?:窗口|会话|对话).{0,6}(?:也能|可以|还能).{0,6}(?:查|看|找|记)|"
        r"下次.{0,6}(?:也能|还能|可以).{0,6}(?:查|看|找|记|用)|"
        r"一直记(?:住|着)|别忘了|不要忘|永远(?:记|不要忘))"
    )
    _SUPERSEDE_RE = re.compile(r"(?:取消|不要了|不再|改用|改成|替代|更新为|撤销)")

    @staticmethod
    def _current_scope(memory_manager: Any) -> tuple[str, str]:
        """Return the active write scope if the manager exposes one."""
        scope_getter = getattr(memory_manager, "_current_write_scope", None)
        if callable(scope_getter):
            try:
                value = scope_getter()
                if (
                    isinstance(value, tuple)
                    and len(value) == 2
                    and isinstance(value[0], str)
                    and isinstance(value[1], str)
                ):
                    return value
            except Exception:
                pass
        return "user", ""

    @classmethod
    def _memory_scope_for_manual_add(
        cls,
        content: str,
        mem_type_str: str,
        memory_manager: Any,
        explicit_scope: str | None = None,
        user_intent_hint: str | None = None,
    ) -> tuple[str, str, list[str], str | None]:
        """Choose where a manual memory should live without blocking useful learning.

        Decision priority (high → low):
        1. ``explicit_scope`` — model passed scope= argument (global/session)
        2. ``user_intent_hint`` — recent user message contains explicit cross-
           session keywords ("永久保存"/"下次新会话也能查到"/...)
        3. Stable preferences/rules/skills/experiences → global by default
        4. ``_STABLE_FACT_RE`` heuristic on memory content → global
        5. One-off task or report → session (or short-term global fallback)
        6. Default → session when an active session exists
        """
        current_scope, current_owner = cls._current_scope(memory_manager)
        tags: list[str] = ["manual"]
        content = content.strip()
        mem_type = (mem_type_str or "fact").lower()
        normalized_scope = (explicit_scope or "auto").strip().lower()

        # 1) explicit scope wins — model knows best when user is explicit
        if normalized_scope == "global":
            tags.append("explicit-global")
            return "user", "", tags, "已按当前用户范围保存为跨会话长期记忆。"
        if normalized_scope == "session":
            tags.append("explicit-session")
            if current_scope == "session" and current_owner:
                return current_scope, current_owner, tags, None
            # No active session — fall back to global short-term
            return "user", "", tags, None

        # 2) user explicitly asked for cross-session persistence in their msg
        if user_intent_hint and cls._USER_PERSIST_INTENT_RE.search(user_intent_hint):
            tags.append("user-requested-global")
            return "user", "", tags, "已按当前用户范围保存为跨会话长期记忆。"

        # 3) durable types default to global unless caller is in a tight session
        if mem_type in {"preference", "rule", "skill", "error", "experience"}:
            if cls._SUPERSEDE_RE.search(content):
                tags.append("supersedes-prior-memory")
            return "user", "", tags, None

        # 4) regex on content body
        if cls._STABLE_FACT_RE.search(content):
            return "user", "", tags, None

        # 5) one-off / task report → session-scoped
        if cls._ONE_OFF_TASK_RE.search(content) or cls._TASK_REPORT_RE.search(content):
            tags.append("session-only")
            if current_scope == "session" and current_owner:
                return (
                    current_scope,
                    current_owner,
                    tags,
                    "这更像当前任务上下文，已仅保存在本会话，避免污染长期记忆。",
                )
            return (
                "user",
                "",
                tags,
                "这更像一次性任务记录，已按低优先级短期记忆保存。",
            )

        # 6) default — keep within session, but make the downgrade message
        # actionable so model knows how to escalate next time.
        if current_scope == "session" and current_owner:
            tags.append("session-only")
            return (
                current_scope,
                current_owner,
                tags,
                "未识别为长期偏好，已先保存在当前会话。"
                "如需跨会话持久化，请改传 scope=\"global\" 重试。",
            )

        return "user", "", tags, None

    def _supersede_related_memories(
        self,
        new_memory_id: str,
        content: str,
        mem_type_str: str,
        scope: str,
        scope_owner: str,
    ) -> int:
        """Mark older, related active rules/preferences as superseded by the new memory."""
        if not new_memory_id or not self._SUPERSEDE_RE.search(content):
            return 0
        if (mem_type_str or "").lower() not in {"rule", "preference", "fact"}:
            return 0

        store = getattr(getattr(self.agent, "memory_manager", None), "store", None)
        if store is None:
            return 0

        try:
            owner_getter = getattr(getattr(self.agent, "memory_manager", None), "_current_owner", None)
            user_id, workspace_id = owner_getter() if callable(owner_getter) else ("default", "default")
            hits = store.search_semantic(
                content,
                limit=10,
                scope=scope,
                scope_owner=scope_owner,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        except Exception:
            return 0

        updated = 0
        for hit in hits:
            if not hit.id or hit.id == new_memory_id or hit.superseded_by:
                continue
            if hit.type.value not in {"rule", "preference", "fact"}:
                continue
            if not self._has_meaningful_overlap(content, hit.content):
                continue
            if store.update_semantic(hit.id, {"superseded_by": new_memory_id}):
                updated += 1
        return updated

    @staticmethod
    def _has_meaningful_overlap(left: str, right: str) -> bool:
        terms_left = {
            t.lower()
            for t in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", left or "")
            if t not in {"取消", "不要", "不再", "改用", "改成", "规则", "偏好"}
        }
        terms_right = {
            t.lower()
            for t in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", right or "")
        }
        if terms_left and terms_left.intersection(terms_right):
            return True

        def cjk_bigrams(text: str) -> set[str]:
            chars = "".join(re.findall(r"[\u4e00-\u9fff]", text or ""))
            return {chars[i : i + 2] for i in range(max(0, len(chars) - 1))}

        left_bigrams = cjk_bigrams(left)
        right_bigrams = cjk_bigrams(right)
        if not left_bigrams or not right_bigrams:
            return False
        overlap = len(left_bigrams & right_bigrams) / len(left_bigrams | right_bigrams)
        return overlap >= 0.12

    # Persistent marker file path: once created, the navigation guide will
    # never be shown again — for any session, any agent re-creation, any
    # process restart. Delete this file to re-enable the guide.
    _GUIDE_MARKER_FILENAME = "memory_navigation_guide_shown.flag"

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._guide_injected: bool = False
        self._recent_add_contents: list[str] = []
        self._search_cache: dict[tuple[str, str, str], str] = {}
        # Hydrate _guide_injected from the persistent marker so AgentPool
        # eviction / process restart don't reinject the guide.
        self._guide_marker_path = self._compute_guide_marker_path()
        if self._guide_marker_path is not None and self._guide_marker_path.exists():
            self._guide_injected = True

    def _compute_guide_marker_path(self) -> Path | None:
        try:
            from ...config import settings

            base = settings.data_dir / "state"
            base.mkdir(parents=True, exist_ok=True)
            return base / self._GUIDE_MARKER_FILENAME
        except Exception:
            return None

    def _persist_guide_marker(self) -> None:
        if self._guide_marker_path is None:
            return
        try:
            self._guide_marker_path.write_text("1", encoding="utf-8")
        except Exception:
            pass

    def reset_guide(self) -> None:
        """Reset the per-session add-dedup cache.

        Note: ``_guide_injected`` is intentionally **not** reset here anymore.
        The navigation guide is meant as a one-shot onboarding artifact —
        re-emitting it on every new session burns ~800 tokens of context for
        no value, and was responsible for P1-6's "guide spam across turns".
        Once the persistent marker exists the guide stays suppressed forever;
        operators can re-enable it by deleting ``data/state/<flag>``.
        """
        self._recent_add_contents.clear()
        self._search_cache.clear()
        if self._guide_marker_path is not None and self._guide_marker_path.exists():
            self._guide_injected = True

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "consolidate_memories":
            return await self._consolidate_memories(params)
        elif tool_name == "add_memory":
            return self._add_memory(params)
        elif tool_name == "search_memory":
            result = self._search_memory(params)
        elif tool_name == "get_memory_stats":
            return self._get_memory_stats(params)
        elif tool_name == "list_recent_tasks":
            result = self._list_recent_tasks(params)
        elif tool_name == "search_conversation_traces":
            result = self._search_conversation_traces(params)
        elif tool_name == "trace_memory":
            result = self._trace_memory(params)
        elif tool_name == "search_relational_memory":
            result = await self._search_relational_memory(params)
        elif tool_name == "get_session_context":
            return self._get_session_context(params)
        elif tool_name == "memory_delete_by_query":
            return self._delete_by_query(params)
        else:
            return f"❌ Unknown memory tool: {tool_name}"

        if tool_name in self._SEARCH_TOOLS and not self._guide_injected:
            self._guide_injected = True
            self._persist_guide_marker()
            return self._NAVIGATION_GUIDE + result
        return result

    async def _consolidate_memories(self, params: dict) -> str:
        """手动触发记忆整理"""
        try:
            from ...config import settings
            from ...scheduler.consolidation_tracker import ConsolidationTracker

            tracker = ConsolidationTracker(settings.project_root / "data" / "scheduler")
            since, until = tracker.get_memory_consolidation_time_range()

            result = await self.agent.memory_manager.consolidate_daily()

            tracker.record_memory_consolidation(result)

            time_range = (
                f"{since.strftime('%m-%d %H:%M')} → {until.strftime('%m-%d %H:%M')}"
                if since
                else "全部记录"
            )

            lines = ["✅ 记忆整理完成:"]
            if result.get("unextracted_processed"):
                lines.append(f"- 新提取: {result['unextracted_processed']} 条")
            if result.get("duplicates_removed"):
                lines.append(f"- 去重: {result['duplicates_removed']} 条")
            if result.get("memories_decayed"):
                lines.append(f"- 衰减清理: {result['memories_decayed']} 条")

            review = result.get("llm_review", {})
            if review.get("deleted") or review.get("updated") or review.get("merged"):
                lines.append(
                    f"- LLM 审查: 删除 {review.get('deleted', 0)}, "
                    f"更新 {review.get('updated', 0)}, "
                    f"合并 {review.get('merged', 0)}, "
                    f"保留 {review.get('kept', 0)}"
                )

            if result.get("sessions_processed"):
                lines.append(f"- 处理会话: {result['sessions_processed']}")
            lines.append(f"- 时间范围: {time_range}")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Manual memory consolidation failed: {e}", exc_info=True)
            return f"❌ 记忆整理失败: {e}"

    def _add_memory(self, params: dict) -> str:
        """添加记忆（含内容去重保护）"""
        from ...memory.types import Memory, MemoryPriority, MemoryType

        content = params["content"].strip()
        if not content:
            return "未记录：记忆内容为空。"
        mem_type_str = params.get("type", "fact")
        importance = self._importance_value(params.get("importance", 0.5))
        explicit_scope_raw = params.get("scope")
        explicit_scope: str | None = None
        if isinstance(explicit_scope_raw, str):
            normalized = explicit_scope_raw.strip().lower()
            if normalized in {"global", "session", "auto"}:
                explicit_scope = normalized
            elif normalized in {"permanent", "long_term", "long-term", "longterm"}:
                explicit_scope = "global"
            elif normalized in {"short_term", "short-term", "shortterm", "temporary"}:
                explicit_scope = "session"
        # 取最近一条用户消息作为意图判断依据：当用户口头说
        # "永久保存 / 下次新会话也能查到 / 长期记住" 等，但模型仍传
        # scope=auto 时，由 hint 兜底升级为 global，避免出现
        # "模型说存了 / 实际只在本会话可见" 的撕裂。
        user_intent_hint: str | None = None
        try:
            recent_user = getattr(self.agent, "_current_user_message", "") or ""
            if isinstance(recent_user, str) and recent_user:
                user_intent_hint = recent_user
        except Exception:
            user_intent_hint = None
        scope, scope_owner, tags, scope_note = self._memory_scope_for_manual_add(
            content,
            mem_type_str,
            self.agent.memory_manager,
            explicit_scope=explicit_scope,
            user_intent_hint=user_intent_hint,
        )

        content_key = content.strip()[:100].lower()
        if content_key in self._recent_add_contents:
            return "该内容已记录过，无需重复保存。"

        try:
            store = self.agent.memory_manager.store
            owner_getter = getattr(self.agent.memory_manager, "_current_owner", None)
            user_id, workspace_id = owner_getter() if callable(owner_getter) else ("default", "default")
            existing_hits = store.search_semantic(
                content.strip(),
                limit=3,
                scope=scope,
                scope_owner=scope_owner,
                user_id=user_id,
                workspace_id=workspace_id,
            )
            for hit in existing_hits:
                if hit.content and content.strip()[:80].lower() in hit.content.lower():
                    return "✅ 记忆已存在（FTS5 预检命中），无需重复记录。请继续执行其他任务。"
        except Exception:
            pass

        try:
            # Agent 上挂的属性是 profile_manager（UserProfileManager），
            # 不是 user_profile。旧代码 getattr(self.agent, "user_profile", None)
            # 永远拿到 None，导致这段去重永远失效。同时支持任一字段以保持兼容。
            profile_mgr = getattr(self.agent, "profile_manager", None) or getattr(
                self.agent, "user_profile", None
            )
            if profile_mgr is not None:
                if hasattr(profile_mgr, "state") and hasattr(
                    profile_mgr.state, "collected_items"
                ):
                    profile_text = str(profile_mgr.state.collected_items).lower()
                elif hasattr(profile_mgr, "to_dict"):
                    profile_text = str(profile_mgr.to_dict()).lower()
                else:
                    profile_text = ""
                core_fact = content.strip()[:60].lower()
                if core_fact and profile_text and core_fact in profile_text:
                    return "✅ 该信息已存在于用户画像中，无需重复添加记忆。"
        except Exception:
            pass

        type_map = {
            "fact": MemoryType.FACT,
            "preference": MemoryType.PREFERENCE,
            "skill": MemoryType.SKILL,
            "error": MemoryType.ERROR,
            "rule": MemoryType.RULE,
            "experience": MemoryType.EXPERIENCE,
        }
        mem_type = type_map.get(mem_type_str, MemoryType.FACT)

        if importance >= 0.8:
            priority = MemoryPriority.PERMANENT
        elif importance >= 0.6:
            priority = MemoryPriority.LONG_TERM
        else:
            priority = MemoryPriority.SHORT_TERM

        memory = Memory(
            type=mem_type,
            priority=priority,
            content=content,
            source="manual",
            importance_score=importance,
            tags=tags,
        )

        memory_id = self.agent.memory_manager.add_memory(
            memory,
            scope=scope,
            scope_owner=scope_owner,
        )
        if memory_id:
            superseded = self._supersede_related_memories(
                memory_id,
                content,
                mem_type_str,
                scope,
                scope_owner,
            )
            if len(self._recent_add_contents) >= 50:
                self._recent_add_contents.pop(0)
            self._recent_add_contents.append(content_key)
            lines = [f"✅ 已记住: [{mem_type_str}] {content}", f"ID: {memory_id}"]
            if scope == "user":
                lines.append("范围: 当前用户的跨会话长期记忆 (user)")
            elif scope == "global":
                lines.append("范围: 跨会话长期记忆 (global)")
            else:
                lines.append(f"范围: 仅当前会话 (session={scope_owner})")
            if superseded:
                lines.append(f"已替代旧记忆: {superseded} 条")
            if scope_note:
                lines.append(scope_note)
            return "\n".join(lines)
        else:
            return "记忆已存在（语义相似），无需重复记录。"

    def _search_memory(self, params: dict) -> str:
        """搜索记忆

        无 type_filter: RetrievalEngine 多路召回（语义+情节+最近+附件）
        有 type_filter: SQLite FTS5 搜索 + 类型过滤
        最终 fallback: v1 内存子串匹配
        """
        from ...memory.types import MemoryType

        query = params["query"]
        type_filter = params.get("type")
        now = datetime.now()

        mm = self.agent.memory_manager
        conversation_id = (
            getattr(self.agent, "_current_conversation_id", "")
            or getattr(self.agent, "_current_session_id", "")
            or ""
        )
        cache_key = (conversation_id, str(query).strip().lower(), str(type_filter or ""))
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return cached + "\n\n（本轮重复查询，已复用缓存结果）"

        # P1-5：在调用召回前显式打日志，记录"本次召回看的是哪个 (user, workspace) 范围"，
        # 与 manager.start_session 的 tenant 日志配合，可在事后从日志确认是否串了别的用户记忆。
        try:
            _u = getattr(mm, "_current_user_id", "default")
            _w = getattr(mm, "_current_workspace_id", "default")
            logger.info(
                f"[search_memory] tenant=(user={_u} workspace={_w}) "
                f"session={conversation_id or '-'} query={str(query)[:40]!r} type={type_filter or '-'}"
            )
        except Exception:
            pass

        # 路径 A: 无类型过滤 → RetrievalEngine 多路召回
        if not type_filter:
            retrieval_engine = getattr(mm, "retrieval_engine", None)
            if retrieval_engine:
                try:
                    scope_setter = getattr(mm, "_set_retrieval_scope_context", None)
                    if scope_setter:
                        scope_setter()
                    candidates = retrieval_engine.retrieve_candidates(
                        query=query,
                        recent_messages=getattr(mm, "_recent_messages", None),
                    )
                    if candidates:
                        from openakita.core.tool_executor import smart_truncate as _st

                        logger.info(
                            f"[search_memory] RetrievalEngine: {len(candidates)} candidates for '{query[:50]}'"
                        )
                        cited = [
                            {"id": c.memory_id, "content": c.content[:200]}
                            for c in candidates[:10]
                            if c.memory_id
                        ]
                        if cited:
                            mm.record_cited_memories(cited)
                        output = f"找到 {len(candidates)} 条相关记忆:\n\n"
                        for c in candidates[:10]:
                            ep_hint = ""
                            if hasattr(c, "episode_id") and c.episode_id:
                                ep_hint = f", 来源情节: {c.episode_id[:12]}"
                            c_trunc, _ = _st(
                                c.content or "", 400, save_full=False, label="mem_search"
                            )
                            output += f"- [{c.source_type}] {c_trunc}{ep_hint}\n\n"
                        self._remember_search_result(cache_key, output)
                        return output
                except Exception as e:
                    logger.warning(f"[search_memory] RetrievalEngine failed: {e}")

        # 路径 B: 有类型过滤 或 RetrievalEngine 无结果 → SQLite 搜索
        store = getattr(mm, "store", None)
        if store:
            try:
                search_visible = getattr(mm, "search_visible_semantic", None)
                if search_visible:
                    memories = search_visible(query, limit=10, filter_type=type_filter)
                else:
                    memories = store.search_semantic(query, limit=10, filter_type=type_filter)
                memories = [m for m in memories if not m.expires_at or m.expires_at >= now]
                if memories:
                    logger.info(
                        f"[search_memory] SQLite: {len(memories)} results for '{query[:50]}'"
                    )
                    cited = [{"id": m.id, "content": m.content[:200]} for m in memories]
                    mm.record_cited_memories(cited)
                    output = f"找到 {len(memories)} 条相关记忆:\n\n"
                    for m in memories:
                        ep_hint = (
                            f", 来源情节: {m.source_episode_id[:12]}" if m.source_episode_id else ""
                        )
                        output += f"- [{m.type.value}] {m.content}\n"  # Memory content 完整保留
                        output += f"  (重要性: {m.importance_score:.1f}, 引用: {m.access_count}{ep_hint})\n\n"
                    self._remember_search_result(cache_key, output)
                    return output
            except Exception as e:
                logger.warning(f"[search_memory] SQLite search failed: {e}")

        # 路径 C: 最终 fallback → v1 内存子串匹配
        mem_type = None
        if type_filter:
            type_map = {
                "fact": MemoryType.FACT,
                "preference": MemoryType.PREFERENCE,
                "skill": MemoryType.SKILL,
                "error": MemoryType.ERROR,
                "rule": MemoryType.RULE,
                "experience": MemoryType.EXPERIENCE,
            }
            mem_type = type_map.get(type_filter)

        current_scope = getattr(mm, "_current_write_scope", lambda: ("global", ""))()
        memories = mm.search_memories(
            query=query,
            memory_type=mem_type,
            limit=10,
            scope=current_scope[0],
            scope_owner=current_scope[1],
        )
        memories = [m for m in memories if not m.expires_at or m.expires_at >= now]

        if not memories:
            output = f"未找到与 '{query}' 相关的记忆"
            self._remember_search_result(cache_key, output)
            return output

        cited = [{"id": m.id, "content": m.content[:200]} for m in memories]
        mm.record_cited_memories(cited)

        output = f"找到 {len(memories)} 条相关记忆:\n\n"
        for m in memories:
            ep_hint = (
                f", 来源情节: {m.source_episode_id[:12]}" if m.source_episode_id else ""
            )  # episode ID 是固定长度
            output += f"- [{m.type.value}] {m.content}\n"
            output += f"  (重要性: {m.importance_score:.1f}, 引用: {m.access_count}{ep_hint})\n\n"

        self._remember_search_result(cache_key, output)
        return output

    def _remember_search_result(self, key: tuple[str, str, str], value: str) -> None:
        if len(self._search_cache) >= 64:
            oldest = next(iter(self._search_cache))
            self._search_cache.pop(oldest, None)
        self._search_cache[key] = value

    def _get_memory_stats(self, params: dict) -> str:
        """获取记忆统计"""
        stats = self.agent.memory_manager.get_stats()

        output = f"""记忆系统统计:

- 总记忆数: {stats["total"]}
- 今日会话: {stats["sessions_today"]}
- 待处理会话: {stats["unprocessed_sessions"]}

按类型:
"""
        for type_name, count in stats.get("by_type", {}).items():
            output += f"  - {type_name}: {count}\n"

        output += "\n按优先级:\n"
        for priority, count in stats.get("by_priority", {}).items():
            output += f"  - {priority}: {count}\n"

        return output

    def _list_recent_tasks(self, params: dict) -> str:
        """列出最近完成的任务（Episode）"""
        days = params.get("days", 3)
        limit = params.get("limit", 15)

        mm = self.agent.memory_manager
        store = getattr(mm, "store", None)
        if not store:
            return "记忆系统未初始化"

        episodes = store.get_recent_episodes(days=days, limit=limit)
        if not episodes:
            return f"最近 {days} 天没有已完成的任务记录。"

        lines = [f"最近 {days} 天完成的任务（共 {len(episodes)} 条）：\n"]
        for i, ep in enumerate(episodes, 1):
            goal = ep.goal or "(未记录目标)"
            outcome = ep.outcome or "completed"
            tool_names = coerce_tool_names(ep.tools_used)
            tools = ", ".join(tool_names[:5]) if tool_names else "无工具调用"
            sa = ep.started_at
            started = sa.strftime("%Y-%m-%d %H:%M") if hasattr(sa, "strftime") else str(sa)[:16]
            mem_count = len(ep.linked_memory_ids) if ep.linked_memory_ids else 0
            lines.append(f"{i}. [{started}] {goal}  (id: {ep.id[:12]})")
            mem_hint = f"关联记忆: {mem_count}条 | " if mem_count else ""
            lines.append(f"   结果: {outcome} | {mem_hint}工具: {tools}")
            if ep.summary:
                lines.append(f"   摘要: {ep.summary[:120]}")
            lines.append("")

        return "\n".join(lines)

    def _search_conversation_traces(self, params: dict) -> str:
        """搜索完整对话历史（含工具调用和结果）

        优先从 SQLite conversation_turns 搜索（可靠、有索引），
        不足时再 fallback 到 JSONL 文件和 react_traces。
        """
        keyword = params.get("keyword", "").strip()
        if not keyword:
            return "❌ 请提供搜索关键词"

        session_id_filter = params.get("session_id", "")
        max_results = params.get("max_results", 10)
        days_back = params.get("days_back", 7)

        logger.info(
            f"[SearchTraces] keyword={keyword!r}, session={session_id_filter!r}, "
            f"max={max_results}, days_back={days_back}"
        )

        results: list[dict] = []
        keywords = self._split_trace_keywords(keyword)

        # === 数据源 0: 当前活跃 session（尚未落库/索引的最新轮次） ===
        self._search_current_session_messages(
            keyword,
            keywords,
            session_id_filter,
            max_results,
            results,
        )

        # === 数据源 1: SQLite conversation_turns（主数据源） ===
        store = getattr(self.agent.memory_manager, "store", None)
        if store:
            try:
                rows = []
                seen_row_keys: set[tuple[str, str, str]] = set()
                for kw in keywords:
                    for row in store.search_turns(
                        keyword=kw,
                        session_id=session_id_filter or None,
                        days_back=days_back,
                        limit=max_results,
                    ):
                        key = (
                            str(row.get("session_id", "")),
                            str(row.get("timestamp", "")),
                            str(row.get("role", "")),
                        )
                        if key in seen_row_keys:
                            continue
                        seen_row_keys.add(key)
                        rows.append(row)
                        if len(rows) >= max_results:
                            break
                    if len(rows) >= max_results:
                        break
                for row in rows:
                    results.append(
                        {
                            "source": "sqlite_turns",
                            "session_id": row.get("session_id", ""),
                            "episode_id": row.get("episode_id", ""),
                            "timestamp": row.get("timestamp", ""),
                            "role": row.get("role", ""),
                            "content": coerce_text(row.get("content"))[:500],
                            "tool_calls": row.get("tool_calls") or [],
                            "tool_results": row.get("tool_results") or [],
                        }
                    )
            except Exception as e:
                logger.warning(f"[SearchTraces] SQLite search failed, will try JSONL: {e}")

        # === 数据源 2: react_traces（补充工具调用细节） ===
        if len(results) < max_results:
            cutoff = datetime.now() - timedelta(days=days_back)
            from ...config import settings

            data_root = settings.project_root / "data"

            traces_dir = data_root / "react_traces"
            if traces_dir.exists():
                remaining = max_results - len(results)
                seen_timestamps = {r.get("timestamp", "") for r in results}
                self._search_react_traces(
                    traces_dir,
                    keyword,
                    session_id_filter,
                    cutoff,
                    remaining,
                    results,
                    seen_timestamps,
                )

        # === 数据源 3: JSONL fallback（SQLite 无结果或更早历史） ===
        if len(results) < max_results:
            cutoff = datetime.now() - timedelta(days=days_back)
            from ...config import settings

            data_root = settings.project_root / "data"

            history_dir = data_root / "memory" / "conversation_history"
            if history_dir.exists():
                remaining = max_results - len(results)
                seen_timestamps = {r.get("timestamp", "") for r in results}
                self._search_jsonl_history(
                    history_dir,
                    keyword,
                    session_id_filter,
                    cutoff,
                    remaining,
                    results,
                    seen_timestamps,
                )

        if not results:
            return f"未找到包含 '{keyword}' 的对话记录（最近 {days_back} 天）"

        return self._format_trace_results(results, keyword)

    @staticmethod
    def _split_trace_keywords(keyword: str) -> list[str]:
        parts = [p for p in re.split(r"[\s,，;；|/]+", keyword.strip()) if p]
        return list(dict.fromkeys(parts or [keyword.strip()]))

    def _search_current_session_messages(
        self,
        keyword: str,
        keywords: list[str],
        session_id_filter: str,
        limit: int,
        results: list[dict],
    ) -> None:
        session = getattr(self.agent, "_current_session", None)
        if session is None:
            return
        sid = str(getattr(session, "id", "") or getattr(session, "session_id", "") or "")
        if session_id_filter and session_id_filter != sid:
            return
        messages = getattr(session, "messages", None) or []
        lowered_keywords = [k.lower() for k in keywords if k]
        for msg in reversed(list(messages)):
            if len(results) >= limit:
                return
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = coerce_text(msg.get("content"))
                ts = msg.get("timestamp", "")
                tool_calls = msg.get("tool_calls") or []
                tool_results = msg.get("tool_results") or []
            else:
                role = getattr(msg, "role", "")
                content = coerce_text(getattr(msg, "content", ""))
                ts = getattr(msg, "timestamp", "")
                tool_calls = getattr(msg, "tool_calls", []) or []
                tool_results = getattr(msg, "tool_results", []) or []
            hay = " ".join([
                content,
                json.dumps(tool_calls, ensure_ascii=False, default=str),
                json.dumps(tool_results, ensure_ascii=False, default=str),
            ]).lower()
            if keyword.lower() not in hay and not any(k in hay for k in lowered_keywords):
                continue
            results.append({
                "source": "current_session",
                "session_id": sid,
                "timestamp": str(ts),
                "role": str(role),
                "content": content[:500],
                "tool_calls": tool_calls,
                "tool_results": tool_results,
            })

    def _trace_memory(self, params: dict) -> str:
        """跨层导航：从记忆→情节→对话，或从情节→记忆+对话"""
        memory_id = params.get("memory_id", "").strip()
        episode_id = params.get("episode_id", "").strip()

        if not memory_id and not episode_id:
            return "请提供 memory_id 或 episode_id 其中一个"

        mm = self.agent.memory_manager
        store = getattr(mm, "store", None)
        if not store:
            return "记忆系统未初始化"

        if memory_id:
            return self._trace_from_memory(store, memory_id)
        else:
            return self._trace_from_episode(store, episode_id)

    def _trace_from_memory(self, store, memory_id: str) -> str:
        """memory_id → source episode → conversation turns"""
        mem = store.get_semantic(memory_id)
        if not mem:
            return f"未找到记忆 {memory_id}"

        lines = ["## 记忆详情\n"]
        lines.append(f"- [{mem.type.value}] {mem.content}")
        lines.append(
            f"  重要性: {mem.importance_score:.1f}, 引用: {mem.access_count}, 置信度: {mem.confidence:.1f}"
        )

        ep_id = mem.source_episode_id
        if not ep_id:
            lines.append("\n该记忆没有关联情节（可能是手动添加或早期提取的）。")
            return "\n".join(lines)

        ep = store.get_episode(ep_id)
        if not ep:
            lines.append(f"\n关联情节 {ep_id} 已不存在。")
            return "\n".join(lines)

        lines.append("\n## 来源情节\n")
        lines.append(f"- 目标: {ep.goal or '(未记录)'}")
        lines.append(f"- 结果: {ep.outcome}")
        lines.append(f"- 摘要: {ep.summary[:200]}")
        sa = ep.started_at
        started = sa.strftime("%Y-%m-%d %H:%M") if hasattr(sa, "strftime") else str(sa)[:16]
        lines.append(f"- 时间: {started}")
        tool_names = coerce_tool_names(ep.tools_used)
        if tool_names:
            lines.append(f"- 工具: {', '.join(tool_names[:8])}")

        turns = store.get_session_turns(ep.session_id)
        if turns:
            lines.append(f"\n## 相关对话（共 {len(turns)} 轮，显示前 6 轮）\n")
            for t in turns[:6]:
                role = t.get("role", "?")
                content = coerce_text(t.get("content"))[:200]
                lines.append(f"[{role}] {content}")
                if t.get("tool_calls"):
                    tc = t["tool_calls"]
                    if isinstance(tc, list):
                        names = [c.get("name", "?") for c in tc if isinstance(c, dict)]
                        if names:
                            lines.append(f"  → 工具调用: {', '.join(names)}")
                lines.append("")

        return "\n".join(lines)

    def _trace_from_episode(self, store, episode_id: str) -> str:
        """episode_id → linked memories + conversation turns"""
        ep = store.get_episode(episode_id)
        if not ep:
            return f"未找到情节 {episode_id}"

        lines = ["## 情节详情\n"]
        lines.append(f"- 目标: {ep.goal or '(未记录)'}")
        lines.append(f"- 结果: {ep.outcome}")
        lines.append(f"- 摘要: {ep.summary[:200]}")
        sa = ep.started_at
        started = sa.strftime("%Y-%m-%d %H:%M") if hasattr(sa, "strftime") else str(sa)[:16]
        lines.append(f"- 时间: {started}")
        tool_names = coerce_tool_names(ep.tools_used)
        if tool_names:
            lines.append(f"- 工具: {', '.join(tool_names[:8])}")

        if ep.linked_memory_ids:
            lines.append(f"\n## 关联记忆（{len(ep.linked_memory_ids)} 条）\n")
            for mid in ep.linked_memory_ids[:10]:
                mem = store.get_semantic(mid)
                if mem:
                    from openakita.core.tool_executor import smart_truncate as _st

                    mem_trunc, _ = _st(mem.content or "", 300, save_full=False, label="mem_linked")
                    lines.append(f"- [{mem.type.value}] {mem_trunc}")
                else:
                    lines.append(f"- (已删除) {mid[:12]}")
        else:
            lines.append("\n该情节尚无关联记忆。")

        turns = store.get_session_turns(ep.session_id)
        if turns:
            lines.append(f"\n## 对话原文（共 {len(turns)} 轮，显示前 8 轮）\n")
            for t in turns[:8]:
                role = t.get("role", "?")
                content = coerce_text(t.get("content"))[:300]
                lines.append(f"[{role}] {content}")
                if t.get("tool_calls"):
                    tc = t["tool_calls"]
                    if isinstance(tc, list):
                        for c in tc[:3]:
                            if isinstance(c, dict):
                                lines.append(
                                    f"  → {c.get('name', '?')}: {json.dumps(c.get('input', {}), ensure_ascii=False, default=str)[:200]}"
                                )
                lines.append("")

        return "\n".join(lines)

    def _search_react_traces(
        self,
        traces_dir: Path,
        keyword: str,
        session_id_filter: str,
        cutoff: datetime,
        limit: int,
        results: list[dict],
        seen_timestamps: set[str],
    ) -> None:
        """搜索 react_traces/{date}/*.json"""
        count = 0
        for date_dir in sorted(traces_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            try:
                dir_date = datetime.strptime(date_dir.name, "%Y%m%d")
                if dir_date < cutoff:
                    continue
            except ValueError:
                continue
            for trace_file in sorted(date_dir.glob("*.json"), reverse=True):
                if session_id_filter and session_id_filter not in trace_file.stem:
                    continue
                try:
                    raw = trace_file.read_text(encoding="utf-8")
                    if keyword.lower() not in raw.lower():
                        continue
                    trace_data = json.loads(raw)
                except Exception:
                    continue
                for it in trace_data.get("iterations", []):
                    it_str = json.dumps(it, ensure_ascii=False, default=str)
                    if keyword.lower() not in it_str.lower():
                        continue
                    results.append(
                        {
                            "source": "react_trace",
                            "file": f"{date_dir.name}/{trace_file.name}",
                            "conversation_id": trace_data.get("conversation_id", ""),
                            "iteration": it.get("iteration", 0),
                            "tool_calls": it.get("tool_calls", []),
                            "tool_results": it.get("tool_results", []),
                            "text_content": str(it.get("text_content", ""))[:300],
                        }
                    )
                    count += 1
                    if count >= limit:
                        return
                if count >= limit:
                    return
            if count >= limit:
                return

    def _search_jsonl_history(
        self,
        history_dir: Path,
        keyword: str,
        session_id_filter: str,
        cutoff: datetime,
        limit: int,
        results: list[dict],
        seen_timestamps: set[str],
    ) -> None:
        """搜索 conversation_history/*.jsonl，跳过 SQLite 已返回的条目"""
        count = 0
        for jsonl_file in sorted(history_dir.glob("*.jsonl"), reverse=True):
            if session_id_filter and session_id_filter not in jsonl_file.stem:
                continue
            try:
                file_mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime)
                if file_mtime < cutoff:
                    continue
            except Exception:
                continue
            try:
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    if keyword.lower() not in line.lower():
                        continue
                    try:
                        turn = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = turn.get("timestamp", "")
                    if ts in seen_timestamps:
                        continue
                    results.append(
                        {
                            "source": "conversation_history",
                            "file": jsonl_file.name,
                            "timestamp": ts,
                            "role": turn.get("role", ""),
                            "content": coerce_text(turn.get("content"))[:500],
                            "tool_calls": turn.get("tool_calls", []),
                            "tool_results": turn.get("tool_results", []),
                        }
                    )
                    seen_timestamps.add(ts)
                    count += 1
                    if count >= limit:
                        return
            except Exception as e:
                logger.debug(f"Error reading {jsonl_file}: {e}")
            if count >= limit:
                return

    @staticmethod
    def _format_trace_results(results: list[dict], keyword: str) -> str:
        """格式化搜索结果为可读文本"""
        output = f"找到 {len(results)} 条匹配记录（关键词: {keyword}）:\n\n"
        for i, r in enumerate(results, 1):
            source = r["source"]
            output += f"--- 记录 {i} [{source}] ---\n"
            if source in ("current_session", "sqlite_turns", "conversation_history"):
                if r.get("session_id"):
                    output += f"会话: {r['session_id']}\n"
                elif r.get("file"):
                    output += f"文件: {r['file']}\n"
                if r.get("episode_id"):
                    output += f"关联情节: {r['episode_id'][:12]}\n"
                output += f"时间: {r.get('timestamp', 'N/A')}\n"
                output += f"角色: {r.get('role', 'N/A')}\n"
                output += f"内容: {r.get('content', '')}\n"
                if r.get("tool_calls"):
                    output += f"工具调用: {json.dumps(r['tool_calls'], ensure_ascii=False, default=str)[:500]}\n"
                if r.get("tool_results"):
                    output += f"工具结果: {json.dumps(r['tool_results'], ensure_ascii=False, default=str)[:500]}\n"
            else:
                output += f"文件: {r.get('file', 'N/A')}\n"
                output += f"会话: {r.get('conversation_id', 'N/A')}\n"
                output += f"迭代: {r.get('iteration', 'N/A')}\n"
                if r.get("text_content"):
                    output += f"文本: {r['text_content']}\n"
                if r.get("tool_calls"):
                    for tc in r["tool_calls"]:
                        output += f"  工具: {tc.get('name', 'N/A')}\n"
                        inp = tc.get("input", tc.get("arguments", {}))
                        if isinstance(inp, dict):
                            inp_str = json.dumps(inp, ensure_ascii=False, default=str)
                            output += f"  参数: {inp_str[:300]}\n"
                if r.get("tool_results"):
                    for tr in r["tool_results"]:
                        rc = str(tr.get("result_content", tr.get("result_preview", "")))
                        output += f"  结果: {rc[:300]}\n"
            output += "\n"
        return output

    async def _search_relational_memory(self, params: dict) -> str:
        """Search the relational memory graph (Mode 2)."""
        query = params.get("query", "")
        max_results = params.get("max_results", 10)

        if not query:
            return "❌ 请提供搜索查询"

        mm = self.agent.memory_manager
        if not mm._ensure_relational():
            return "⚠️ 关系型记忆（Mode 2）未启用。请在配置中设置 memory_mode 为 mode2 或 auto。"

        try:
            results = await mm.relational_graph.query(
                query,
                limit=max_results,
                token_budget=2000,
            )
        except Exception as e:
            return f"❌ 图搜索失败: {e}"

        if not results:
            return f'未找到与 "{query}" 相关的关系型记忆'

        output = f"🔗 关系型记忆搜索结果（{len(results)} 条）\n\n"
        for i, r in enumerate(results, 1):
            node = r.node
            dims = ", ".join(d.value for d in r.dimensions_matched)
            ents = ", ".join(e.name for e in node.entities[:3])
            time_str = node.occurred_at.strftime("%m-%d %H:%M") if node.occurred_at else ""
            output += (
                f"--- 结果 {i} ---\n"
                f"类型: {node.node_type.value.upper()} | 分数: {r.score:.2f} | 维度: {dims}\n"
            )
            if ents:
                output += f"实体: {ents}\n"
            if time_str:
                output += f"时间: {time_str}\n"
            output += f"内容: {node.content[:300]}\n\n"
        return output

    def _get_session_context(self, params: dict) -> str:
        """获取当前会话的详细上下文信息。"""
        session = getattr(self.agent, "_current_session", None)
        if not session:
            return "❌ 当前无活跃会话"

        sections = params.get("sections", ["summary", "sub_agents"])
        parts: list[str] = []

        ctx = getattr(session, "context", None)

        if "summary" in sections:
            parts.append("## 会话概况")
            parts.append(f"- ID: {getattr(session, 'id', 'unknown')}")
            parts.append(f"- 通道: {getattr(session, 'channel', 'unknown')}")
            msg_count = len(ctx.messages) if ctx and hasattr(ctx, "messages") else 0
            parts.append(f"- 消息数: {msg_count}")
            effective_model = {}
            try:
                effective_model = session.get_metadata("effective_model") or {}
            except Exception:
                effective_model = {}
            if isinstance(effective_model, dict) and effective_model.get("effective_model"):
                parts.append(f"- 当前模型: {effective_model.get('effective_model')}")
                if effective_model.get("effective_endpoint"):
                    parts.append(f"- 当前端点: {effective_model.get('effective_endpoint')}")
            sub_records = getattr(ctx, "sub_agent_records", None) or []
            parts.append(f"- 子Agent记录: {len(sub_records)} 条")

        if "sub_agents" in sections:
            sub_records = getattr(ctx, "sub_agent_records", None) or []
            if sub_records:
                parts.append("\n## 子Agent执行记录")
                for raw_record in sub_records:
                    r = raw_record if isinstance(raw_record, dict) else {}
                    name = self._context_text(r.get("agent_name", "unknown")) or "unknown"
                    parts.append(f"\n### {name}")
                    task_msg = self._context_text(r.get("task_message", ""), 200)
                    if task_msg:
                        parts.append(f"- 任务: {task_msg}")
                    elapsed = r.get("elapsed_s", "")
                    if elapsed not in (None, ""):
                        parts.append(f"- 耗时: {self._context_text(elapsed)}s")
                    tools = coerce_tool_names(r.get("tools_used") or [])
                    if tools:
                        parts.append(f"- 工具: {', '.join(tools[:10])}")
                    preview = self._context_text(r.get("result_preview", ""), 1000)
                    if preview:
                        parts.append(f"- 结果预览:\n{preview}")
            else:
                parts.append("\n## 子Agent执行记录\n无子Agent记录")

        if "tools" in sections:
            parts.append("\n## 工具使用记录")
            react_traces = getattr(ctx, "react_traces", None)
            if react_traces:
                for i, raw_trace in enumerate(react_traces[-20:], 1):
                    trace = raw_trace if isinstance(raw_trace, dict) else {}
                    tool = self._context_text(trace.get("tool_name", ""))
                    status = self._context_text(trace.get("status", ""))
                    if tool:
                        parts.append(f"{i}. {tool} ({status})")
            else:
                parts.append("无详细工具记录（react_traces 不可用）")

        if "messages" in sections:
            parts.append("\n## 完整消息列表")
            msgs = ctx.messages if ctx and hasattr(ctx, "messages") else []
            display_msgs = msgs[-20:] if len(msgs) > 20 else msgs
            if len(msgs) > 20:
                parts.append(f"（显示最近 20 条，共 {len(msgs)} 条）\n")
            for raw_msg in display_msgs:
                msg = raw_msg if isinstance(raw_msg, dict) else {}
                role = self._context_text(msg.get("role", "?")) or "?"
                ts_display = self._context_text(msg.get("timestamp", ""), 16)
                content = self._context_text(msg.get("content", ""), 500)
                parts.append(f"[{ts_display}] {role}: {content}")

        return "\n".join(parts) if parts else "无可用会话信息"

    def _delete_by_query(self, params: dict) -> str:
        """受控的按查询条件批量删除记忆 (PR-A3)。

        前置条件（任一）：
        - 用户已通过 RiskGate 授权（session.metadata 含 risk_authorized_intent_active）
        - 调用方显式 dry_run=True 仅预览

        参数：
        - query: 必填，按内容关键字过滤
        - source: 可选，按 source 过滤（如 "profile_fallback"）
        - memory_type: 可选，按 MemoryType 过滤
        - dry_run: 默认 True，先返回预览再要求 dry_run=False 真删
        - max_delete: 默认 50，硬上限 200，避免一次性误删
        - confirm_token: dry_run=False 时必填，由前一次 dry_run 返回（防止 LLM 自我授权）
        """
        try:
            from ...memory.types import MemoryType
        except Exception as exc:
            return f"❌ memory_delete_by_query 不可用: {exc}"

        query = (params.get("query") or "").strip()
        source = (params.get("source") or "").strip() or None
        type_str = (params.get("memory_type") or "").strip()
        dry_run = params.get("dry_run", True)
        if not isinstance(dry_run, bool):
            dry_run = str(dry_run).lower() not in ("false", "0", "no")
        try:
            max_delete = int(params.get("max_delete") or 50)
        except (TypeError, ValueError):
            max_delete = 50
        max_delete = max(1, min(max_delete, 200))
        confirm_token = (params.get("confirm_token") or "").strip()

        # 真删前必须有 dry_run 预览返回的 token，避免 LLM 直接 dry_run=False 误删或无匹配时静默返回
        if not dry_run and not confirm_token:
            return (
                "❌ 拒绝执行：`dry_run=False` 时必须提供 `confirm_token`（由上一次 "
                "`dry_run=True` 预览末尾给出）。请先预览再确认删除。"
            )

        if not query and not source and not type_str:
            return (
                "❌ memory_delete_by_query 至少需要 query / source / memory_type 之一。"
                "拒绝执行无差别删除。"
            )

        memory_type: MemoryType | None = None
        if type_str:
            try:
                memory_type = MemoryType(type_str.lower())
            except ValueError:
                return (
                    f"❌ memory_type 无效: {type_str}. "
                    f"可用值: {', '.join(t.value for t in MemoryType)}"
                )

        mm = getattr(self.agent, "memory_manager", None)
        if mm is None or not hasattr(mm, "search_memories"):
            return "❌ memory_manager 不可用，无法删除"

        try:
            candidates = mm.search_memories(
                query=query,
                memory_type=memory_type,
                limit=max_delete + 1,
            )
        except Exception as exc:
            return f"❌ 搜索候选记忆失败: {exc}"

        if source:
            candidates = [
                m for m in candidates
                if str(getattr(m, "source", "") or "") == source
            ]

        candidates = candidates[:max_delete]
        if not candidates:
            return f"未找到符合条件的记忆（query={query!r}, source={source!r}）。"

        preview_lines = [f"将删除 {len(candidates)} 条记忆，预览前 5 条："]
        for mem in candidates[:5]:
            content = (getattr(mem, "content", "") or "")[:120]
            mem_id = str(getattr(mem, "id", ""))[:12]
            mem_source = str(getattr(mem, "source", "") or "?")
            preview_lines.append(
                f"- [{mem_id}] type={getattr(mem, 'type', '?')} source={mem_source}\n"
                f"  内容: {content}"
            )

        # 生成 token 以防 LLM 在没有 dry_run 预览的情况下直接删
        try:
            import hashlib

            token_seed = "|".join(str(getattr(m, "id", "")) for m in candidates)
            expected_token = hashlib.sha256(token_seed.encode("utf-8")).hexdigest()[:16]
        except Exception:
            expected_token = ""

        if dry_run:
            preview_lines.append("")
            preview_lines.append(
                "（这只是预览，未执行删除。如确认无误，请用相同参数 + "
                f"`dry_run=False` 且 `confirm_token=\"{expected_token}\"` 再调一次。）"
            )
            return "\n".join(preview_lines)

        if expected_token and confirm_token != expected_token:
            preview_lines.append("")
            preview_lines.append(
                "❌ 拒绝执行：confirm_token 不匹配。请先以 dry_run=True 预览，"
                "拷贝返回的 confirm_token 再调用。"
            )
            return "\n".join(preview_lines)

        # 同时检查 RiskGate 已授权，二次保险
        try:
            session = getattr(self.agent, "current_session", None)
            authorized = False
            if session is not None:
                intent = session.get_metadata("risk_authorized_intent_active")
                if isinstance(intent, dict) and intent.get("operation") == "memory_delete":
                    authorized = True
            if not authorized:
                preview_lines.append("")
                preview_lines.append(
                    "❌ 拒绝执行：未检测到用户在 RiskGate 中确认的授权范围。"
                    "请引导用户重新发起删除请求并通过弹窗确认。"
                )
                return "\n".join(preview_lines)
        except Exception:
            pass

        deleted = 0
        for mem in candidates:
            try:
                mem_id = str(getattr(mem, "id", ""))
                if mem_id and mm.delete_memory(mem_id):
                    deleted += 1
            except Exception as exc:
                logger.warning("[memory_delete_by_query] delete %s failed: %s", mem_id, exc)

        # 消费授权
        try:
            session = getattr(self.agent, "current_session", None)
            if session is not None:
                session.set_metadata("risk_authorized_intent_active", None)
        except Exception:
            pass

        return (
            f"✅ 已删除 {deleted}/{len(candidates)} 条记忆。\n"
            f"前 5 条预览见上一步 dry_run 输出。"
        )


def create_handler(agent: "Agent"):
    """创建记忆处理器"""
    handler = MemoryHandler(agent)
    agent._memory_handler = handler
    return handler.handle
