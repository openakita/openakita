"""
Memory Consolidator - Batch process conversation history

Implements the user's idea:
1. Save an entire day's conversation context
2. Auto-consolidate during idle times (e.g., early morning)
3. Distill key insights into MEMORY.md

References:
- Claude-Mem Worker Service
- LangMem Background Manager
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .extractor import MemoryExtractor
from .types import ConversationTurn, Memory, SessionSummary

logger = logging.getLogger(__name__)


class MemoryConsolidator:
    """Memory Consolidator - Batch process conversation history"""

    def __init__(
        self,
        data_dir: Path,
        brain=None,
        extractor: MemoryExtractor | None = None,
    ):
        """
        Args:
            data_dir: Data directory (stores conversation history)
            brain: LLM brain instance
            extractor: Memory extractor
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.brain = brain
        self.extractor = extractor or MemoryExtractor(brain)

        # Conversation history storage directory
        self.history_dir = self.data_dir / "conversation_history"
        self.history_dir.mkdir(exist_ok=True)

        # Processed sessions
        self.summaries_file = self.data_dir / "session_summaries.json"

    def save_conversation_turn(
        self,
        session_id: str,
        turn: ConversationTurn,
    ) -> None:
        """
        Save conversation turn (real-time save)

        One file per session, append writes
        """
        session_file = self.history_dir / f"{session_id}.jsonl"

        with open(session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(turn.to_dict(), ensure_ascii=False) + "\n")

    def load_session_history(self, session_id: str) -> list[ConversationTurn]:
        """Load session history"""
        session_file = self.history_dir / f"{session_id}.jsonl"

        if not session_file.exists():
            return []

        turns = []
        with open(session_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    turn = ConversationTurn(
                        role=data["role"],
                        content=data["content"],
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        tool_calls=data.get("tool_calls", []),
                        tool_results=data.get("tool_results", []),
                    )
                    turns.append(turn)

        return turns

    def get_today_sessions(self) -> list[str]:
        """Get all session IDs from today"""
        today = datetime.now().date()
        sessions = []

        for file in self.history_dir.glob("*.jsonl"):
            # Check file modification time
            mtime = datetime.fromtimestamp(file.stat().st_mtime)
            if mtime.date() == today:
                sessions.append(file.stem)

        return sessions

    def get_unprocessed_sessions(self) -> list[str]:
        """Get unprocessed sessions"""
        # Load processed sessions
        processed = set()
        if self.summaries_file.exists():
            with open(self.summaries_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        summary = json.loads(line)
                        processed.add(summary["session_id"])

        # Find unprocessed sessions
        unprocessed = []
        for file in self.history_dir.glob("*.jsonl"):
            if file.stem not in processed:
                unprocessed.append(file.stem)

        return unprocessed

    async def consolidate_session(
        self,
        session_id: str,
    ) -> tuple[SessionSummary, list[Memory]]:
        """
        Consolidate a single session

        1. Load conversation history
        2. Generate session summary
        3. Extract memories
        """
        turns = self.load_session_history(session_id)

        if not turns:
            return None, []

        # Generate session summary
        summary = await self._generate_summary(session_id, turns)

        # Extract memories
        memories = []

        # Rule-based extraction
        for turn in turns:
            extracted = self.extractor.extract_from_turn(turn)
            memories.extend(extracted)

        # Advanced extraction using LLM
        if self.brain:
            llm_memories = await self.extractor.extract_with_llm(
                turns, context=f"Session summary: {summary.task_description}"
            )
            memories.extend(llm_memories)

        # Deduplicate
        memories = self.extractor.deduplicate(memories, [])

        # Update memory IDs in summary
        summary.memories_created = [m.id for m in memories]

        # Save summary
        self._save_summary(summary)

        return summary, memories

    async def consolidate_all_unprocessed(self) -> tuple[list[SessionSummary], list[Memory]]:
        """
        Consolidate all unprocessed sessions

        Suitable for batch execution during idle periods (e.g., early morning)
        """
        unprocessed = self.get_unprocessed_sessions()

        all_summaries = []
        all_memories = []

        for session_id in unprocessed:
            try:
                summary, memories = await self.consolidate_session(session_id)
                if summary:
                    all_summaries.append(summary)
                    all_memories.extend(memories)
                    logger.info(f"Consolidated session {session_id}: {len(memories)} memories")
            except Exception as e:
                logger.error(f"Failed to consolidate session {session_id}: {e}")

        return all_summaries, all_memories

    async def _generate_summary(
        self,
        session_id: str,
        turns: list[ConversationTurn],
    ) -> SessionSummary:
        """Generate session summary using LLM"""

        start_time = turns[0].timestamp if turns else datetime.now()
        end_time = turns[-1].timestamp if turns else datetime.now()

        # Simple summary (without LLM)
        if not self.brain or len(turns) < 3:
            # Extract task description from user messages
            user_messages = [t.content for t in turns if t.role == "user"]
            task_desc = user_messages[0][:200] if user_messages else "Unknown task"

            return SessionSummary(
                session_id=session_id,
                start_time=start_time,
                end_time=end_time,
                task_description=task_desc,
                outcome="completed",
            )

        # Generate detailed summary using LLM
        from openakita.core.tool_executor import smart_truncate as _st

        conv_text = "\n".join(
            [
                f"[{turn.role}]: {_st(turn.content or '', 600, save_full=False, label='consol_conv')[0]}"
                for turn in turns[-30:]
            ]
        )

        prompt = f"""Summarize the following conversation session:

{conv_text}

Please provide:
1. task_description: What was the user's main task? (one sentence)
2. outcome: Task result (success/partial/failed)
3. key_actions: Key actions taken (max 5)
4. learnings: Lessons worth remembering (max 3)
5. errors: Any errors encountered (if any)

Output in JSON format.
"""

        try:
            response = await self.brain.think(
                prompt, system="You are a conversation analysis expert skilled at extracting key information. Output only JSON, no other content."
            )

            # Parse JSON
            import re

            json_match = re.search(r"\{.*\}", response.content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return SessionSummary(
                    session_id=session_id,
                    start_time=start_time,
                    end_time=end_time,
                    task_description=data.get("task_description", ""),
                    outcome=data.get("outcome", "completed"),
                    key_actions=data.get("key_actions", []),
                    learnings=data.get("learnings", []),
                    errors_encountered=data.get("errors", []),
                )
        except Exception as e:
            logger.error(f"LLM summary generation failed: {e}")

        # Fallback to simple summary
        user_messages = [t.content for t in turns if t.role == "user"]
        return SessionSummary(
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            task_description=user_messages[0][:200] if user_messages else "Unknown",
            outcome="completed",
        )

    def _save_summary(self, summary: SessionSummary) -> None:
        """Save session summary"""
        with open(self.summaries_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary.to_dict(), ensure_ascii=False) + "\n")

    def get_recent_summaries(self, days: int = 7) -> list[SessionSummary]:
        """Get session summaries from the last N days"""
        if not self.summaries_file.exists():
            return []

        cutoff = datetime.now() - timedelta(days=days)
        summaries = []

        with open(self.summaries_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    end_time = datetime.fromisoformat(data["end_time"])
                    if end_time > cutoff:
                        summaries.append(
                            SessionSummary(
                                session_id=data["session_id"],
                                start_time=datetime.fromisoformat(data["start_time"]),
                                end_time=end_time,
                                task_description=data.get("task_description", ""),
                                outcome=data.get("outcome", ""),
                                key_actions=data.get("key_actions", []),
                                learnings=data.get("learnings", []),
                                errors_encountered=data.get("errors_encountered", []),
                                memories_created=data.get("memories_created", []),
                            )
                        )

        return summaries

    def cleanup_old_history(self, days: int = 30) -> int:
        """
        Clean up old conversation history files (by days)

        Preserve summaries and memories, delete raw conversations
        """
        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0

        for file in self.history_dir.glob("*.jsonl"):
            mtime = datetime.fromtimestamp(file.stat().st_mtime)
            if mtime < cutoff:
                file.unlink()
                deleted += 1
                logger.info(f"Deleted old history file: {file.name}")

        return deleted

    # ==================== Capacity-limited cleanup ====================

    # Configuration constants
    MAX_HISTORY_DAYS = 30  # Keep at most 30 days
    MAX_HISTORY_FILES = 1000  # Keep at most 1000 files
    MAX_HISTORY_SIZE_MB = 500  # Use at most 500MB

    def cleanup_history(self) -> dict:
        """
        Clean up conversation history to prevent disk overflow

        Strategy (by priority):
        1. Delete files older than MAX_HISTORY_DAYS
        2. If file count exceeds MAX_HISTORY_FILES, delete oldest
        3. If total size exceeds MAX_HISTORY_SIZE_MB, delete oldest

        Returns:
            Cleanup stats {"by_age": n, "by_count": n, "by_size": n}
        """
        deleted = {"by_age": 0, "by_count": 0, "by_size": 0}

        # 1. Cleanup by age
        deleted["by_age"] = self.cleanup_old_history(days=self.MAX_HISTORY_DAYS)

        # Get all history files sorted by modification time (oldest first)
        files = sorted(self.history_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)

        # 2. Cleanup by file count
        if len(files) > self.MAX_HISTORY_FILES:
            to_delete = files[: len(files) - self.MAX_HISTORY_FILES]
            for f in to_delete:
                try:
                    f.unlink()
                    deleted["by_count"] += 1
                    logger.debug(f"Deleted history file (by count): {f.name}")
                except Exception as e:
                    logger.error(f"Failed to delete {f.name}: {e}")

            # Update file list
            files = files[len(to_delete) :]

        # 3. Cleanup by size
        max_size = self.MAX_HISTORY_SIZE_MB * 1024 * 1024
        total_size = sum(f.stat().st_size for f in files)

        while total_size > max_size and files:
            f = files.pop(0)
            try:
                file_size = f.stat().st_size
                f.unlink()
                total_size -= file_size
                deleted["by_size"] += 1
                logger.debug(f"Deleted history file (by size): {f.name}")
            except Exception as e:
                logger.error(f"Failed to delete {f.name}: {e}")

        total_deleted = sum(deleted.values())
        if total_deleted > 0:
            logger.info(f"History cleanup completed: {deleted}")

        return deleted

    def get_history_stats(self) -> dict:
        """
        Get conversation history statistics

        Returns:
            Statistics dictionary
        """
        files = list(self.history_dir.glob("*.jsonl"))
        total_size = sum(f.stat().st_size for f in files)

        return {
            "file_count": len(files),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "max_files": self.MAX_HISTORY_FILES,
            "max_size_mb": self.MAX_HISTORY_SIZE_MB,
            "max_days": self.MAX_HISTORY_DAYS,
        }
