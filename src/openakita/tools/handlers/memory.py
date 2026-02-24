"""
记忆系统处理器

处理记忆相关的系统技能：
- add_memory: 添加记忆
- search_memory: 搜索记忆
- get_memory_stats: 获取记忆统计
- search_conversation_traces: 搜索完整对话历史
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
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "consolidate_memories":
            return await self._consolidate_memories(params)
        elif tool_name == "add_memory":
            return self._add_memory(params)
        elif tool_name == "search_memory":
            return self._search_memory(params)
        elif tool_name == "get_memory_stats":
            return self._get_memory_stats(params)
        elif tool_name == "list_recent_tasks":
            return self._list_recent_tasks(params)
        elif tool_name == "search_conversation_traces":
            return self._search_conversation_traces(params)
        else:
            return f"❌ Unknown memory tool: {tool_name}"

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
                if since else "全部记录"
            )

            v2_keys = ["unextracted_processed", "duplicates_removed", "memories_decayed"]
            if any(result.get(k) for k in v2_keys):
                return (
                    f"✅ 记忆整理完成:\n"
                    f"- 提取: {result.get('unextracted_processed', 0)} 条\n"
                    f"- 去重: {result.get('duplicates_removed', 0)} 条\n"
                    f"- 衰减: {result.get('memories_decayed', 0)} 条\n"
                    f"- 时间范围: {time_range}"
                )
            else:
                return (
                    f"✅ 记忆整理完成:\n"
                    f"- 处理会话: {result.get('sessions_processed', 0)}\n"
                    f"- 提取记忆: {result.get('memories_extracted', 0)}\n"
                    f"- 新增记忆: {result.get('memories_added', 0)}\n"
                    f"- 去重: {result.get('duplicates_removed', 0)}\n"
                    f"- 时间范围: {time_range}"
                )

        except Exception as e:
            logger.error(f"Manual memory consolidation failed: {e}", exc_info=True)
            return f"❌ 记忆整理失败: {e}"

    def _add_memory(self, params: dict) -> str:
        """添加记忆"""
        from ...memory.types import Memory, MemoryPriority, MemoryType

        content = params["content"]
        mem_type_str = params["type"]
        importance = params.get("importance", 0.5)

        type_map = {
            "fact": MemoryType.FACT,
            "preference": MemoryType.PREFERENCE,
            "skill": MemoryType.SKILL,
            "error": MemoryType.ERROR,
            "rule": MemoryType.RULE,
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
            return f"✅ 已记住: [{mem_type_str}] {content}\nID: {memory_id}"
        else:
            return "✅ 记忆已存在（语义相似），无需重复记录。请继续执行其他任务或结束。"

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

        # 路径 A: 无类型过滤 → RetrievalEngine 多路召回
        if not type_filter:
            retrieval_engine = getattr(self.agent.memory_manager, "retrieval_engine", None)
            if retrieval_engine:
                try:
                    candidates = retrieval_engine.retrieve_candidates(
                        query=query,
                        recent_messages=getattr(self.agent.memory_manager, "_recent_messages", None),
                    )
                    if candidates:
                        logger.info(f"[search_memory] RetrievalEngine: {len(candidates)} candidates for '{query[:50]}'")
                        output = f"找到 {len(candidates)} 条相关记忆:\n\n"
                        for c in candidates[:10]:
                            output += f"- [{c.source_type}] {c.content[:200]}\n\n"
                        return output
                except Exception as e:
                    logger.warning(f"[search_memory] RetrievalEngine failed: {e}")

        # 路径 B: 有类型过滤 或 RetrievalEngine 无结果 → SQLite 搜索
        store = getattr(self.agent.memory_manager, "store", None)
        if store:
            try:
                memories = store.search_semantic(query, limit=10, filter_type=type_filter)
                memories = [m for m in memories if not m.expires_at or m.expires_at >= now]
                if memories:
                    logger.info(f"[search_memory] SQLite: {len(memories)} results for '{query[:50]}'")
                    output = f"找到 {len(memories)} 条相关记忆:\n\n"
                    for m in memories:
                        output += f"- [{m.type.value}] {m.content}\n"
                        output += f"  (重要性: {m.importance_score:.1f}, 访问次数: {m.access_count})\n\n"
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
            }
            mem_type = type_map.get(type_filter)

        memories = self.agent.memory_manager.search_memories(
            query=query, memory_type=mem_type, limit=10
        )
        memories = [m for m in memories if not m.expires_at or m.expires_at >= now]

        if not memories:
            return f"未找到与 '{query}' 相关的记忆"

        output = f"找到 {len(memories)} 条相关记忆:\n\n"
        for m in memories:
            output += f"- [{m.type.value}] {m.content}\n"
            output += f"  (重要性: {m.importance_score:.1f}, 访问次数: {m.access_count})\n\n"

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
            lines.append(f"{i}. [{started}] {goal}")
            lines.append(f"   结果: {outcome} | 工具: {tools}")
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
                    results.append({
                        "source": "sqlite_turns",
                        "session_id": row.get("session_id", ""),
                        "timestamp": row.get("timestamp", ""),
                        "role": row.get("role", ""),
                        "content": str(row.get("content", ""))[:500],
                        "tool_calls": row.get("tool_calls") or [],
                        "tool_results": row.get("tool_results") or [],
                    })
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
                    traces_dir, keyword, session_id_filter, cutoff, remaining,
                    results, seen_timestamps,
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
                    history_dir, keyword, session_id_filter, cutoff, remaining,
                    results, seen_timestamps,
                )

        if not results:
            return f"未找到包含 '{keyword}' 的对话记录（最近 {days_back} 天）"

        return self._format_trace_results(results, keyword)

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
                    results.append({
                        "source": "react_trace",
                        "file": f"{date_dir.name}/{trace_file.name}",
                        "conversation_id": trace_data.get("conversation_id", ""),
                        "iteration": it.get("iteration", 0),
                        "tool_calls": it.get("tool_calls", []),
                        "tool_results": it.get("tool_results", []),
                        "text_content": str(it.get("text_content", ""))[:300],
                    })
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
                    results.append({
                        "source": "conversation_history",
                        "file": jsonl_file.name,
                        "timestamp": ts,
                        "role": turn.get("role", ""),
                        "content": str(turn.get("content", ""))[:500],
                        "tool_calls": turn.get("tool_calls", []),
                        "tool_results": turn.get("tool_results", []),
                    })
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
                        inp = tc.get("input", {})
                        if isinstance(inp, dict):
                            inp_str = json.dumps(inp, ensure_ascii=False, default=str)
                            output += f"  参数: {inp_str[:300]}\n"
                if r.get("tool_results"):
                    for tr in r["tool_results"]:
                        rc = str(tr.get("result_content", tr.get("result_preview", "")))
                        output += f"  结果: {rc[:300]}\n"
            output += "\n"
        return output


def create_handler(agent: "Agent"):
    """创建记忆处理器"""
    handler = MemoryHandler(agent)
    return handler.handle
