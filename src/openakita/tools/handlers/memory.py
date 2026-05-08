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
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    ]

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

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._guide_injected: bool = False
        self._recent_add_contents: list[str] = []

    def reset_guide(self) -> None:
        """Reset the one-shot guide flag (call on new session start)."""
        self._guide_injected = False
        self._recent_add_contents.clear()

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
        else:
            return f"❌ Unknown memory tool: {tool_name}"

        if tool_name in self._SEARCH_TOOLS and not self._guide_injected:
            self._guide_injected = True
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

        content = params["content"]
        mem_type_str = params.get("type", "fact")
        importance = params.get("importance", 0.5)

        content_key = content.strip()[:100].lower()
        if content_key in self._recent_add_contents:
            return "该内容已记录过，无需重复保存。"

        try:
            store = self.agent.memory_manager.store
            existing_hits = store.search_semantic(content.strip(), limit=3)
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
        )

        memory_id = self.agent.memory_manager.add_memory(memory)
        if memory_id:
            if len(self._recent_add_contents) >= 50:
                self._recent_add_contents.pop(0)
            self._recent_add_contents.append(content_key)
            return f"✅ 已记住: [{mem_type_str}] {content}\nID: {memory_id}"
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

        # 路径 A: 无类型过滤 → RetrievalEngine 多路召回
        if not type_filter:
            retrieval_engine = getattr(mm, "retrieval_engine", None)
            if retrieval_engine:
                try:
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
                        return output
                except Exception as e:
                    logger.warning(f"[search_memory] RetrievalEngine failed: {e}")

        # 路径 B: 有类型过滤 或 RetrievalEngine 无结果 → SQLite 搜索
        store = getattr(mm, "store", None)
        if store:
            try:
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

        memories = mm.search_memories(query=query, memory_type=mem_type, limit=10)
        memories = [m for m in memories if not m.expires_at or m.expires_at >= now]

        if not memories:
            return f"未找到与 '{query}' 相关的记忆"

        cited = [{"id": m.id, "content": m.content[:200]} for m in memories]
        mm.record_cited_memories(cited)

        output = f"找到 {len(memories)} 条相关记忆:\n\n"
        for m in memories:
            ep_hint = (
                f", 来源情节: {m.source_episode_id[:12]}" if m.source_episode_id else ""
            )  # episode ID 是固定长度
            output += f"- [{m.type.value}] {m.content}\n"
            output += f"  (重要性: {m.importance_score:.1f}, 引用: {m.access_count}{ep_hint})\n\n"

        return output

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
            tools = ", ".join(ep.tools_used[:5]) if ep.tools_used else "无工具调用"
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

        # === 数据源 1: SQLite conversation_turns（主数据源） ===
        store = getattr(self.agent.memory_manager, "store", None)
        if store:
            try:
                rows = store.search_turns(
                    keyword=keyword,
                    session_id=session_id_filter or None,
                    days_back=days_back,
                    limit=max_results,
                )
                for row in rows:
                    results.append(
                        {
                            "source": "sqlite_turns",
                            "session_id": row.get("session_id", ""),
                            "episode_id": row.get("episode_id", ""),
                            "timestamp": row.get("timestamp", ""),
                            "role": row.get("role", ""),
                            "content": str(row.get("content", ""))[:500],
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
        if ep.tools_used:
            lines.append(f"- 工具: {', '.join(ep.tools_used[:8])}")

        turns = store.get_session_turns(ep.session_id)
        if turns:
            lines.append(f"\n## 相关对话（共 {len(turns)} 轮，显示前 6 轮）\n")
            for t in turns[:6]:
                role = t.get("role", "?")
                content = str(t.get("content", ""))[:200]
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
        if ep.tools_used:
            lines.append(f"- 工具: {', '.join(ep.tools_used[:8])}")

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
                content = str(t.get("content", ""))[:300]
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
                            "content": str(turn.get("content", ""))[:500],
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
            if source in ("sqlite_turns", "conversation_history"):
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
                    tools_raw = r.get("tools_used") or []
                    if isinstance(tools_raw, (str, bytes)) or not hasattr(tools_raw, "__iter__"):
                        tools_raw = [tools_raw]
                    tools = [
                        self._context_text(
                            t if isinstance(t, str)
                            else (t.get("name") if isinstance(t, dict) and t.get("name") else t)
                        )
                        for t in tools_raw
                    ]
                    tools = [t for t in tools if t]
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


def create_handler(agent: "Agent"):
    """创建记忆处理器"""
    handler = MemoryHandler(agent)
    agent._memory_handler = handler
    return handler.handle
