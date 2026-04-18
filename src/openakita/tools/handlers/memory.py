"""
Memory system handler

Handles memory-related system skills:
- add_memory: add a memory
- search_memory: search memories
- get_memory_stats: get memory statistics
- list_recent_tasks: list recent tasks
- search_conversation_traces: search full conversation history
- trace_memory: cross-layer navigation (memory ↔ episode ↔ conversation)
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
    Memory system handler

    Handles all memory-related tool calls
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
        "Memory system navigation guide (shown only once)\n\n"
        "## Three-layer linking mechanism\n"
        "- Memory → Episode: each memory has a source_episode_id pointing to the task episode that produced it\n"
        "- Episode → Memory: each episode has linked_memory_ids listing the memories it produced\n"
        "- Episode → Conversation: linked to the original conversation turns via session_id\n\n"
        "## Tool details\n"
        "- search_memory — search distilled knowledge (preferences/rules/experience/skills); results include source episode IDs\n"
        "- list_recent_tasks — list recent task episodes, including linked memory count and tool list\n"
        "- trace_memory — cross-layer navigation elevator:\n"
        "  - Pass memory_id → returns source episode summary + related conversation snippets\n"
        "  - Pass episode_id → returns linked memory list + raw conversation\n"
        "- search_conversation_traces — full-text search over raw conversation (parameters + return values)\n"
        "- add_memory — proactively record experience/skill, lessons (error), preferences (preference/rule)\n\n"
        "## Search strategy: overview first, then deep dive\n"
        "1. search_memory to find existing experience/rules/facts\n"
        "2. If context is needed → trace_memory(memory_id=...) to trace back to the episode and conversation\n"
        "3. If interested in a specific episode → trace_memory(episode_id=...) to view linked memories and conversation\n"
        "4. If none of the above returns results → search_conversation_traces for full-text search\n\n"
        "## When to search\n"
        '- User asks "what was done" → list_recent_tasks\n'
        '- User mentions "previously/last time" → search_memory\n'
        "- Need operational details / specific commands → trace_memory or search_conversation_traces\n"
        "- Have done a similar task → search_memory first for experience, then trace_memory for details\n"
        "- When unsure → do not search\n\n"
        "---\n\n"
    )

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._guide_injected: bool = False
        self._recent_add_contents: list[str] = []

    def reset_guide(self) -> None:
        """Reset the one-shot guide flag (call on new session start)."""
        self._guide_injected = False
        self._recent_add_contents.clear()

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle a tool call"""
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
        """Manually trigger memory consolidation"""
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
                else "all records"
            )

            lines = ["✅ Memory consolidation complete:"]
            if result.get("unextracted_processed"):
                lines.append(f"- Newly extracted: {result['unextracted_processed']}")
            if result.get("duplicates_removed"):
                lines.append(f"- Deduplicated: {result['duplicates_removed']}")
            if result.get("memories_decayed"):
                lines.append(f"- Decayed/cleaned: {result['memories_decayed']}")

            review = result.get("llm_review", {})
            if review.get("deleted") or review.get("updated") or review.get("merged"):
                lines.append(
                    f"- LLM review: deleted {review.get('deleted', 0)}, "
                    f"updated {review.get('updated', 0)}, "
                    f"merged {review.get('merged', 0)}, "
                    f"kept {review.get('kept', 0)}"
                )

            if result.get("sessions_processed"):
                lines.append(f"- Sessions processed: {result['sessions_processed']}")
            lines.append(f"- Time range: {time_range}")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Manual memory consolidation failed: {e}", exc_info=True)
            return f"❌ Memory consolidation failed: {e}"

    def _add_memory(self, params: dict) -> str:
        """Add a memory (with content deduplication protection)"""
        from ...memory.types import Memory, MemoryPriority, MemoryType

        content = params["content"]
        mem_type_str = params["type"]
        importance = params.get("importance", 0.5)

        content_key = content.strip()[:100].lower()
        if content_key in self._recent_add_contents:
            return "This content has already been recorded; no need to save it again."

        try:
            store = self.agent.memory_manager.store
            existing_hits = store.search_semantic(content.strip(), limit=3)
            for hit in existing_hits:
                if hit.content and content.strip()[:80].lower() in hit.content.lower():
                    return "✅ Memory already exists (FTS5 pre-check hit); no need to record again. Continue with other tasks."
        except Exception:
            pass

        try:
            profile = getattr(self.agent, "user_profile", None)
            if profile:
                profile_data = profile.to_dict() if hasattr(profile, "to_dict") else {}
                profile_text = str(profile_data).lower()
                core_fact = content.strip()[:60].lower()
                if core_fact and core_fact in profile_text:
                    return "✅ This information already exists in the user profile; no need to add a duplicate memory."
        except Exception:
            pass

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
            if len(self._recent_add_contents) >= 50:
                self._recent_add_contents.pop(0)
            self._recent_add_contents.append(content_key)
            return f"✅ Remembered: [{mem_type_str}] {content}\nID: {memory_id}"
        else:
            return "Memory already exists (semantically similar); no need to record again."

    def _search_memory(self, params: dict) -> str:
        """Search memories

        Without type_filter: RetrievalEngine multi-path recall (semantic + episodic + recent + attachments)
        With type_filter: SQLite FTS5 search + type filter
        Final fallback: v1 in-memory substring match
        """
        from ...memory.types import MemoryType

        query = params["query"]
        type_filter = params.get("type")
        now = datetime.now()

        mm = self.agent.memory_manager

        # Path A: no type filter → RetrievalEngine multi-path recall
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
                        output = f"Found {len(candidates)} related memories:\n\n"
                        for c in candidates[:10]:
                            ep_hint = ""
                            if hasattr(c, "episode_id") and c.episode_id:
                                ep_hint = f", source episode: {c.episode_id[:12]}"
                            c_trunc, _ = _st(
                                c.content or "", 400, save_full=False, label="mem_search"
                            )
                            output += f"- [{c.source_type}] {c_trunc}{ep_hint}\n\n"
                        return output
                except Exception as e:
                    logger.warning(f"[search_memory] RetrievalEngine failed: {e}")

        # Path B: with type filter, or RetrievalEngine returned no results → SQLite search
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
                    output = f"Found {len(memories)} related memories:\n\n"
                    for m in memories:
                        ep_hint = (
                            f", source episode: {m.source_episode_id[:12]}" if m.source_episode_id else ""
                        )
                        output += f"- [{m.type.value}] {m.content}\n"  # Memory content kept in full
                        output += f"  (importance: {m.importance_score:.1f}, citations: {m.access_count}{ep_hint})\n\n"
                    return output
            except Exception as e:
                logger.warning(f"[search_memory] SQLite search failed: {e}")

        # Path C: final fallback → v1 in-memory substring match
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
            return f"No memories found related to '{query}'"

        cited = [{"id": m.id, "content": m.content[:200]} for m in memories]
        mm.record_cited_memories(cited)

        output = f"Found {len(memories)} related memories:\n\n"
        for m in memories:
            ep_hint = (
                f", source episode: {m.source_episode_id[:12]}" if m.source_episode_id else ""
            )  # episode ID is a fixed length
            output += f"- [{m.type.value}] {m.content}\n"
            output += f"  (importance: {m.importance_score:.1f}, citations: {m.access_count}{ep_hint})\n\n"

        return output

    def _get_memory_stats(self, params: dict) -> str:
        """Get memory statistics"""
        stats = self.agent.memory_manager.get_stats()

        output = f"""Memory system statistics:

- Total memories: {stats["total"]}
- Sessions today: {stats["sessions_today"]}
- Unprocessed sessions: {stats["unprocessed_sessions"]}

By type:
"""
        for type_name, count in stats.get("by_type", {}).items():
            output += f"  - {type_name}: {count}\n"

        output += "\nBy priority:\n"
        for priority, count in stats.get("by_priority", {}).items():
            output += f"  - {priority}: {count}\n"

        return output

    def _list_recent_tasks(self, params: dict) -> str:
        """List recently completed tasks (Episodes)"""
        days = params.get("days", 3)
        limit = params.get("limit", 15)

        mm = self.agent.memory_manager
        store = getattr(mm, "store", None)
        if not store:
            return "Memory system not initialized"

        episodes = store.get_recent_episodes(days=days, limit=limit)
        if not episodes:
            return f"No completed task records in the last {days} days."

        lines = [f"Tasks completed in the last {days} days ({len(episodes)} total):\n"]
        for i, ep in enumerate(episodes, 1):
            goal = ep.goal or "(goal not recorded)"
            outcome = ep.outcome or "completed"
            tools = ", ".join(ep.tools_used[:5]) if ep.tools_used else "no tool calls"
            sa = ep.started_at
            started = sa.strftime("%Y-%m-%d %H:%M") if hasattr(sa, "strftime") else str(sa)[:16]
            mem_count = len(ep.linked_memory_ids) if ep.linked_memory_ids else 0
            lines.append(f"{i}. [{started}] {goal}  (id: {ep.id[:12]})")
            mem_hint = f"linked memories: {mem_count} | " if mem_count else ""
            lines.append(f"   Outcome: {outcome} | {mem_hint}tools: {tools}")
            if ep.summary:
                lines.append(f"   Summary: {ep.summary[:120]}")
            lines.append("")

        return "\n".join(lines)

    def _search_conversation_traces(self, params: dict) -> str:
        """Search full conversation history (including tool calls and results)

        Searches SQLite conversation_turns first (reliable, indexed),
        then falls back to JSONL files and react_traces when needed.
        """
        keyword = params.get("keyword", "").strip()
        if not keyword:
            return "❌ Please provide a search keyword"

        session_id_filter = params.get("session_id", "")
        max_results = params.get("max_results", 10)
        days_back = params.get("days_back", 7)

        logger.info(
            f"[SearchTraces] keyword={keyword!r}, session={session_id_filter!r}, "
            f"max={max_results}, days_back={days_back}"
        )

        results: list[dict] = []

        # === Data source 1: SQLite conversation_turns (primary data source) ===
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

        # === Data source 2: react_traces (supplemental tool-call details) ===
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

        # === Data source 3: JSONL fallback (SQLite had no results, or for older history) ===
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
            return f"No conversation records found containing '{keyword}' (last {days_back} days)"

        return self._format_trace_results(results, keyword)

    def _trace_memory(self, params: dict) -> str:
        """Cross-layer navigation: memory → episode → conversation, or episode → memories + conversation"""
        memory_id = params.get("memory_id", "").strip()
        episode_id = params.get("episode_id", "").strip()

        if not memory_id and not episode_id:
            return "Please provide either memory_id or episode_id"

        mm = self.agent.memory_manager
        store = getattr(mm, "store", None)
        if not store:
            return "Memory system not initialized"

        if memory_id:
            return self._trace_from_memory(store, memory_id)
        else:
            return self._trace_from_episode(store, episode_id)

    def _trace_from_memory(self, store, memory_id: str) -> str:
        """memory_id → source episode → conversation turns"""
        mem = store.get_semantic(memory_id)
        if not mem:
            return f"Memory {memory_id} not found"

        lines = ["## Memory details\n"]
        lines.append(f"- [{mem.type.value}] {mem.content}")
        lines.append(
            f"  Importance: {mem.importance_score:.1f}, citations: {mem.access_count}, confidence: {mem.confidence:.1f}"
        )

        ep_id = mem.source_episode_id
        if not ep_id:
            lines.append("\nThis memory has no linked episode (may be manually added or extracted early on).")
            return "\n".join(lines)

        ep = store.get_episode(ep_id)
        if not ep:
            lines.append(f"\nLinked episode {ep_id} no longer exists.")
            return "\n".join(lines)

        lines.append("\n## Source episode\n")
        lines.append(f"- Goal: {ep.goal or '(not recorded)'}")
        lines.append(f"- Outcome: {ep.outcome}")
        lines.append(f"- Summary: {ep.summary[:200]}")
        sa = ep.started_at
        started = sa.strftime("%Y-%m-%d %H:%M") if hasattr(sa, "strftime") else str(sa)[:16]
        lines.append(f"- Time: {started}")
        if ep.tools_used:
            lines.append(f"- Tools: {', '.join(ep.tools_used[:8])}")

        turns = store.get_session_turns(ep.session_id)
        if turns:
            lines.append(f"\n## Related conversation ({len(turns)} turns total, showing first 6)\n")
            for t in turns[:6]:
                role = t.get("role", "?")
                content = str(t.get("content", ""))[:200]
                lines.append(f"[{role}] {content}")
                if t.get("tool_calls"):
                    tc = t["tool_calls"]
                    if isinstance(tc, list):
                        names = [c.get("name", "?") for c in tc if isinstance(c, dict)]
                        if names:
                            lines.append(f"  → tool calls: {', '.join(names)}")
                lines.append("")

        return "\n".join(lines)

    def _trace_from_episode(self, store, episode_id: str) -> str:
        """episode_id → linked memories + conversation turns"""
        ep = store.get_episode(episode_id)
        if not ep:
            return f"Episode {episode_id} not found"

        lines = ["## Episode details\n"]
        lines.append(f"- Goal: {ep.goal or '(not recorded)'}")
        lines.append(f"- Outcome: {ep.outcome}")
        lines.append(f"- Summary: {ep.summary[:200]}")
        sa = ep.started_at
        started = sa.strftime("%Y-%m-%d %H:%M") if hasattr(sa, "strftime") else str(sa)[:16]
        lines.append(f"- Time: {started}")
        if ep.tools_used:
            lines.append(f"- Tools: {', '.join(ep.tools_used[:8])}")

        if ep.linked_memory_ids:
            lines.append(f"\n## Linked memories ({len(ep.linked_memory_ids)} total)\n")
            for mid in ep.linked_memory_ids[:10]:
                mem = store.get_semantic(mid)
                if mem:
                    from openakita.core.tool_executor import smart_truncate as _st

                    mem_trunc, _ = _st(mem.content or "", 300, save_full=False, label="mem_linked")
                    lines.append(f"- [{mem.type.value}] {mem_trunc}")
                else:
                    lines.append(f"- (deleted) {mid[:12]}")
        else:
            lines.append("\nThis episode has no linked memories yet.")

        turns = store.get_session_turns(ep.session_id)
        if turns:
            lines.append(f"\n## Raw conversation ({len(turns)} turns total, showing first 8)\n")
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
        """Search react_traces/{date}/*.json"""
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
        """Search conversation_history/*.jsonl, skipping entries already returned by SQLite"""
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
        """Format search results as readable text"""
        output = f"Found {len(results)} matching records (keyword: {keyword}):\n\n"
        for i, r in enumerate(results, 1):
            source = r["source"]
            output += f"--- Record {i} [{source}] ---\n"
            if source in ("sqlite_turns", "conversation_history"):
                if r.get("session_id"):
                    output += f"Session: {r['session_id']}\n"
                elif r.get("file"):
                    output += f"File: {r['file']}\n"
                if r.get("episode_id"):
                    output += f"Linked episode: {r['episode_id'][:12]}\n"
                output += f"Time: {r.get('timestamp', 'N/A')}\n"
                output += f"Role: {r.get('role', 'N/A')}\n"
                output += f"Content: {r.get('content', '')}\n"
                if r.get("tool_calls"):
                    output += f"Tool calls: {json.dumps(r['tool_calls'], ensure_ascii=False, default=str)[:500]}\n"
                if r.get("tool_results"):
                    output += f"Tool results: {json.dumps(r['tool_results'], ensure_ascii=False, default=str)[:500]}\n"
            else:
                output += f"File: {r.get('file', 'N/A')}\n"
                output += f"Conversation: {r.get('conversation_id', 'N/A')}\n"
                output += f"Iteration: {r.get('iteration', 'N/A')}\n"
                if r.get("text_content"):
                    output += f"Text: {r['text_content']}\n"
                if r.get("tool_calls"):
                    for tc in r["tool_calls"]:
                        output += f"  Tool: {tc.get('name', 'N/A')}\n"
                        inp = tc.get("input", {})
                        if isinstance(inp, dict):
                            inp_str = json.dumps(inp, ensure_ascii=False, default=str)
                            output += f"  Params: {inp_str[:300]}\n"
                if r.get("tool_results"):
                    for tr in r["tool_results"]:
                        rc = str(tr.get("result_content", tr.get("result_preview", "")))
                        output += f"  Result: {rc[:300]}\n"
            output += "\n"
        return output

    async def _search_relational_memory(self, params: dict) -> str:
        """Search the relational memory graph (Mode 2)."""
        query = params.get("query", "")
        max_results = params.get("max_results", 10)

        if not query:
            return "❌ Please provide a search query"

        mm = self.agent.memory_manager
        if not mm._ensure_relational():
            return "⚠️ Relational memory (Mode 2) is not enabled. Please set memory_mode to mode2 or auto in the configuration."

        try:
            results = await mm.relational_graph.query(
                query,
                limit=max_results,
                token_budget=2000,
            )
        except Exception as e:
            return f"❌ Graph search failed: {e}"

        if not results:
            return f'No relational memories found related to "{query}"'

        output = f"🔗 Relational memory search results ({len(results)} found)\n\n"
        for i, r in enumerate(results, 1):
            node = r.node
            dims = ", ".join(d.value for d in r.dimensions_matched)
            ents = ", ".join(e.name for e in node.entities[:3])
            time_str = node.occurred_at.strftime("%m-%d %H:%M") if node.occurred_at else ""
            output += (
                f"--- Result {i} ---\n"
                f"Type: {node.node_type.value.upper()} | Score: {r.score:.2f} | Dimensions: {dims}\n"
            )
            if ents:
                output += f"Entities: {ents}\n"
            if time_str:
                output += f"Time: {time_str}\n"
            output += f"Content: {node.content[:300]}\n\n"
        return output

    def _get_session_context(self, params: dict) -> str:
        """Get detailed context information for the current session."""
        session = getattr(self.agent, "_current_session", None)
        if not session:
            return "❌ No active session"

        sections = params.get("sections", ["summary", "sub_agents"])
        parts: list[str] = []

        ctx = getattr(session, "context", None)

        if "summary" in sections:
            parts.append("## Session overview")
            parts.append(f"- ID: {getattr(session, 'id', 'unknown')}")
            parts.append(f"- Channel: {getattr(session, 'channel', 'unknown')}")
            msg_count = len(ctx.messages) if ctx and hasattr(ctx, "messages") else 0
            parts.append(f"- Message count: {msg_count}")
            sub_records = getattr(ctx, "sub_agent_records", None) or []
            parts.append(f"- Sub-Agent records: {len(sub_records)}")

        if "sub_agents" in sections:
            sub_records = getattr(ctx, "sub_agent_records", None) or []
            if sub_records:
                parts.append("\n## Sub-Agent execution records")
                for r in sub_records:
                    name = r.get("agent_name", "unknown")
                    parts.append(f"\n### {name}")
                    task_msg = r.get("task_message", "")
                    if task_msg:
                        parts.append(f"- Task: {task_msg[:200]}")
                    elapsed = r.get("elapsed_s", "")
                    if elapsed:
                        parts.append(f"- Elapsed: {elapsed}s")
                    tools_raw = r.get("tools_used") or []
                    tools = [
                        t if isinstance(t, str)
                        else (t.get("name") if isinstance(t, dict) and t.get("name") else str(t))
                        for t in tools_raw
                    ]
                    tools = [t for t in tools if t]
                    if tools:
                        parts.append(f"- Tools: {', '.join(tools[:10])}")
                    preview = r.get("result_preview", "")
                    if preview:
                        parts.append(f"- Result preview:\n{preview[:1000]}")
            else:
                parts.append("\n## Sub-Agent execution records\nNo sub-Agent records")

        if "tools" in sections:
            parts.append("\n## Tool usage records")
            react_traces = getattr(ctx, "react_traces", None)
            if react_traces:
                for i, trace in enumerate(react_traces[-20:], 1):
                    tool = trace.get("tool_name", "")
                    status = trace.get("status", "")
                    if tool:
                        parts.append(f"{i}. {tool} ({status})")
            else:
                parts.append("No detailed tool records (react_traces unavailable)")

        if "messages" in sections:
            parts.append("\n## Full message list")
            msgs = ctx.messages if ctx and hasattr(ctx, "messages") else []
            display_msgs = msgs[-20:] if len(msgs) > 20 else msgs
            if len(msgs) > 20:
                parts.append(f"(showing the latest 20, {len(msgs)} total)\n")
            for msg in display_msgs:
                role = msg.get("role", "?")
                ts = msg.get("timestamp", "")
                ts_display = ts[:16] if ts else ""
                content = msg.get("content", "")
                content = content[:500] if isinstance(content, str) else str(content)[:500]
                parts.append(f"[{ts_display}] {role}: {content}")

        return "\n".join(parts) if parts else "No session information available"


def create_handler(agent: "Agent"):
    """Create the memory handler"""
    handler = MemoryHandler(agent)
    agent._memory_handler = handler
    return handler.handle
