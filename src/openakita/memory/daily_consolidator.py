"""
Daily memory consolidator

Features:
1. Consolidate conversation history every morning
2. Extract essential memories using LLM
3. Refresh MEMORY.md summary
4. Clean up expired history files
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .consolidator import MemoryConsolidator
from .extractor import MemoryExtractor
from .types import MEMORY_MD_MAX_CHARS, Memory, MemoryPriority, MemoryType, truncate_memory_md

logger = logging.getLogger(__name__)


class DailyConsolidator:
    """
    Daily memory consolidator

    Responsibilities:
    - Read all conversation history from yesterday
    - Consolidate essential insights using LLM
    - Store in long-term memory
    - Refresh MEMORY.md
    """

    # Maximum characters for MEMORY.md (reference global constant)
    MEMORY_MD_MAX_CHARS = MEMORY_MD_MAX_CHARS

    def __init__(
        self,
        data_dir: Path,
        memory_md_path: Path,
        memory_manager=None,
        brain=None,
        identity_dir: Path | None = None,
    ):
        """
        Args:
            data_dir: Data directory
            memory_md_path: Path to MEMORY.md
            memory_manager: MemoryManager instance
            brain: LLM brain instance
            identity_dir: Identity directory path (for preference promotion)
        """
        self.data_dir = Path(data_dir)
        self.memory_md_path = Path(memory_md_path)
        self.memory_manager = memory_manager
        self.brain = brain
        self.identity_dir = Path(identity_dir) if identity_dir else None

        # Sub-components
        self.extractor = MemoryExtractor(brain)
        self.consolidator = MemoryConsolidator(data_dir, brain, self.extractor)

        # Daily summary directory
        self.summaries_dir = self.data_dir / "daily_summaries"
        self.summaries_dir.mkdir(parents=True, exist_ok=True)

    async def consolidate_daily(self) -> dict:
        """
        Execute daily consolidation

        Suitable to be called by scheduled task at 3:00 AM

        Returns:
            Consolidation result statistics
        """
        logger.info("Starting daily memory consolidation...")

        result = {
            "timestamp": datetime.now().isoformat(),
            "sessions_processed": 0,
            "memories_extracted": 0,
            "memories_added": 0,
            "duplicates_removed": 0,
            "memory_md_refreshed": False,
            "cleanup": {},
        }

        try:
            # 1. Consolidate all unprocessed sessions
            summaries, memories = await self.consolidator.consolidate_all_unprocessed()
            result["sessions_processed"] = len(summaries)
            result["memories_extracted"] = len(memories)

            # 2. Add new memories to MemoryManager
            if self.memory_manager and memories:
                for memory in memories:
                    if self.memory_manager.add_memory(memory):
                        result["memories_added"] += 1

            # 3. Clean duplicate memories (use LLM to judge semantic duplicates)
            result["duplicates_removed"] = await self._cleanup_duplicate_memories()

            # 4. Refresh MEMORY.md
            await self.refresh_memory_md()
            result["memory_md_refreshed"] = True

            # 4.5 Promote personality traits to identity
            persona_promoted = await self._promote_persona_traits_to_identity()
            result["persona_traits_promoted"] = persona_promoted

            # 5. Clean up expired history
            result["cleanup"] = self.consolidator.cleanup_history()

            # 6. Save daily summary
            self._save_daily_summary(result, summaries)

            logger.info(f"Daily consolidation completed: {result}")

        except Exception as e:
            logger.error(f"Daily consolidation failed: {e}")
            result["error"] = str(e)

        return result

    async def refresh_memory_md(self) -> bool:
        """
        Refresh MEMORY.md summary

        Select most important memories from memories.json and generate concise Markdown

        Returns:
            Whether successful
        """
        try:
            # Get all memories
            memories = []
            if self.memory_manager:
                memories = list(self.memory_manager._memories.values())

            # Group by type and priority
            by_type = {
                "preference": [],
                "rule": [],
                "fact": [],
                "skill": [],
            }

            for m in memories:
                # Only select permanent or long-term memories
                if m.priority not in (MemoryPriority.PERMANENT, MemoryPriority.LONG_TERM):
                    continue

                type_key = m.type.value.lower()
                if type_key in by_type:
                    by_type[type_key].append(m)

            # Sort by importance, max 3-5 per type
            for key in by_type:
                by_type[key].sort(key=lambda x: x.importance_score, reverse=True)
                by_type[key] = by_type[key][: 5 if key == "fact" else 3]

            # Generate Markdown
            content = self._generate_memory_md(by_type)

            # Check length limit
            if len(content) > self.MEMORY_MD_MAX_CHARS:
                # Compress content
                content = await self._compress_memory_md(content)

            # Write file safely (backup then write)
            if len(content.strip()) < 10:
                logger.warning("Generated MEMORY.md content too short, skipping refresh")
                return False
            from .lifecycle import _safe_write_with_backup

            _safe_write_with_backup(self.memory_md_path, content)
            logger.info("MEMORY.md refreshed")

            return True

        except Exception as e:
            logger.error(f"Failed to refresh MEMORY.md: {e}")
            return False

    def _generate_memory_md(self, by_type: dict) -> str:
        """Generate MEMORY.md content"""
        lines = [
            "# Core Memory",
            "",
            "> Agent core memory loaded in every conversation. Auto-refreshed daily at 3 AM.",
            f"> Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        # User preferences
        if by_type["preference"]:
            lines.append("## User Preferences")
            for m in by_type["preference"]:
                lines.append(f"- {m.content}")
            lines.append("")

        # Important rules
        if by_type["rule"]:
            lines.append("## Important Rules")
            for m in by_type["rule"]:
                lines.append(f"- {m.content}")
            lines.append("")

        # Key facts
        if by_type["fact"]:
            lines.append("## Key Facts")
            for m in by_type["fact"]:
                lines.append(f"- {m.content}")
            lines.append("")

        # Success patterns (optional)
        if by_type["skill"]:
            lines.append("## Success Patterns")
            for m in by_type["skill"][:2]:  # Max 2 items
                lines.append(f"- {m.content}")
            lines.append("")

        # If all categories are empty
        if not any(by_type.values()):
            lines.append("## Memory")
            lines.append("[No core memory]")
            lines.append("")

        return "\n".join(lines)

    async def _compress_memory_md(self, content: str) -> str:
        """
        Compress MEMORY.md content using LLM

        Called when content exceeds limit
        """
        if not self.brain:
            return truncate_memory_md(content, self.MEMORY_MD_MAX_CHARS)

        try:
            prompt = f"""Condense the following memory into a shorter version, retaining the most important information.

Current content:
{content}

Requirements:
- Total length not exceeding {self.MEMORY_MD_MAX_CHARS} characters
- Maintain Markdown format
- Keep the 5-10 most important memories
- Compress each memory to a single sentence"""

            response = await self.brain.think(
                prompt, system="You are a content condensation expert. Output the condensed Markdown content."
            )

            return response.content.strip()

        except Exception as e:
            logger.error(f"Failed to compress MEMORY.md: {e}")
            return truncate_memory_md(content, self.MEMORY_MD_MAX_CHARS)

    def _save_daily_summary(self, result: dict, summaries: list) -> None:
        """Save daily summary"""
        today = datetime.now().strftime("%Y-%m-%d")
        summary_file = self.summaries_dir / f"{today}.json"

        data = {
            "date": today,
            "result": result,
            "sessions": [s.to_dict() for s in summaries] if summaries else [],
        }

        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved daily summary: {summary_file}")
        except Exception as e:
            logger.error(f"Failed to save daily summary: {e}")

    def get_yesterday_summary(self) -> dict | None:
        """Get yesterday's consolidation summary"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        summary_file = self.summaries_dir / f"{yesterday}.json"

        if summary_file.exists():
            try:
                with open(summary_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        return None

    def get_recent_summaries(self, days: int = 7) -> list[dict]:
        """Get consolidation summaries from recent days"""
        summaries = []

        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            summary_file = self.summaries_dir / f"{date}.json"

            if summary_file.exists():
                try:
                    with open(summary_file, encoding="utf-8") as f:
                        summaries.append(json.load(f))
                except Exception:
                    pass

        return summaries

    # ==================== Personality Trait Promotion ====================

    async def _promote_persona_traits_to_identity(self) -> int:
        """
        Promote high-confidence PERSONA_TRAIT memories to identity/personas/user_custom.md

        Selection criteria:
        - Memory type is PERSONA_TRAIT
        - Confidence >= 0.7 or memory access/reinforcement >= 3 times
        - Group by dimension, take highest confidence per dimension

        Returns:
            Number of promoted traits
        """
        if not self.memory_manager or not self.identity_dir:
            return 0

        persona_dir = self.identity_dir / "personas"
        persona_dir.mkdir(parents=True, exist_ok=True)
        user_custom_path = persona_dir / "user_custom.md"

        # 1. Filter PERSONA_TRAIT type memories
        persona_memories = []
        for mem in self.memory_manager._memories.values():
            if mem.type == MemoryType.PERSONA_TRAIT:
                persona_memories.append(mem)

        if not persona_memories:
            return 0

        # 2. Filter high-confidence or high-access memories
        qualified = [
            m for m in persona_memories if m.importance_score >= 0.7 or m.access_count >= 3
        ]

        if not qualified:
            return 0

        # 3. Group by dimension (extract dimension:xxx from tags)
        by_dimension: dict[str, list[Memory]] = {}
        for mem in qualified:
            dimension = None
            for tag in mem.tags:
                if tag.startswith("dimension:"):
                    dimension = tag.split(":", 1)[1]
                    break
            if dimension:
                if dimension not in by_dimension:
                    by_dimension[dimension] = []
                by_dimension[dimension].append(mem)

        if not by_dimension:
            # Try parsing from content
            for mem in qualified:
                parts = mem.content.split("=", 1)
                if len(parts) == 2:
                    dim = parts[0].strip()
                    if dim not in by_dimension:
                        by_dimension[dim] = []
                    by_dimension[dim].append(mem)

        if not by_dimension:
            return 0

        # 4. Take highest confidence per dimension
        promoted_traits: dict[str, tuple[str, Memory]] = {}  # dim -> (preference, memory)
        for dim, mems in by_dimension.items():
            best = max(mems, key=lambda m: m.importance_score)
            # Extract preference value
            pref = None
            for tag in best.tags:
                if tag.startswith("preference:"):
                    pref = tag.split(":", 1)[1]
                    break
            if not pref:
                parts = best.content.split("=", 1)
                pref = parts[1].strip() if len(parts) == 2 else best.content
            promoted_traits[dim] = (pref, best)

        # 5. Generate user_custom.md content
        now = datetime.now()
        lines = [
            "# User Custom Persona",
            "",
            "> Personalized preferences aggregated from user interactions, auto-updated daily.",
            f"> Last updated: {now.strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        # Group by category
        style_traits = {}
        interaction_traits = {}
        care_traits = {}

        style_dims = {"formality", "humor", "emoji_usage", "reply_length", "address_style"}
        interaction_dims = {
            "proactiveness",
            "emotional_distance",
            "encouragement",
            "sticker_preference",
        }
        care_dims = {"care_topics"}

        for dim, (pref, mem) in promoted_traits.items():
            entry = f"- {dim}: {pref} (confidence {mem.importance_score:.2f})"
            if dim in style_dims:
                style_traits[dim] = entry
            elif dim in interaction_dims:
                interaction_traits[dim] = entry
            elif dim in care_dims:
                care_traits[dim] = entry
            else:
                style_traits[dim] = entry

        if style_traits:
            lines.append("## Communication Style Preferences")
            lines.extend(style_traits.values())
            lines.append("")

        if interaction_traits:
            lines.append("## Interaction Preferences")
            lines.extend(interaction_traits.values())
            lines.append("")

        if care_traits:
            lines.append("## Care Topics")
            lines.extend(care_traits.values())
            lines.append("")

        content = "\n".join(lines)
        user_custom_path.write_text(content, encoding="utf-8")
        logger.info(f"Promoted {len(promoted_traits)} persona traits to {user_custom_path}")

        # 6. Trigger recompilation
        try:
            from ..prompt.compiler import compile_all

            compile_all(self.identity_dir)
            logger.info("Triggered prompt recompilation after persona trait promotion")
        except Exception as e:
            logger.warning(f"Failed to recompile after persona promotion: {e}")

        return len(promoted_traits)

    # ==================== Duplicate Cleanup ====================

    # Vector similarity threshold (for initial filtering of possible duplicates)
    # 0.3 was too loose, changed to 0.15, paired with LLM secondary judgment
    DUPLICATE_DISTANCE_THRESHOLD = 0.15

    async def _cleanup_duplicate_memories(self) -> int:
        """
        Clean up duplicate memories

        Strategy:
        1. Iterate all memories grouped by type
        2. Use vector search to find similar memory pairs
        3. Use LLM to determine if truly duplicates
        4. If duplicate, keep more important/recent one, delete the other

        Returns:
            Number of deleted duplicate memories
        """
        if not self.memory_manager:
            return 0

        memories = list(self.memory_manager._memories.values())
        if len(memories) < 2:
            return 0

        logger.info(f"Checking {len(memories)} memories for duplicates...")

        deleted_ids = set()
        checked_pairs = set()  # Avoid duplicate checking of same pair

        for memory in memories:
            if memory.id in deleted_ids:
                continue

            if (
                self.memory_manager.vector_store is not None
                and self.memory_manager.vector_store.enabled
            ):
                similar = self.memory_manager.vector_store.search(
                    memory.content,
                    limit=5,
                    filter_type=memory.type.value,  # Search only within same type
                )

                for other_id, distance in similar:
                    if other_id == memory.id or other_id in deleted_ids:
                        continue

                    pair_key = tuple(sorted([memory.id, other_id]))
                    if pair_key in checked_pairs:
                        continue
                    checked_pairs.add(pair_key)

                    if distance > self.DUPLICATE_DISTANCE_THRESHOLD:
                        continue

                    other_memory = self.memory_manager._memories.get(other_id)
                    if not other_memory:
                        continue

                    is_dup = await self.memory_manager.check_duplicate_with_llm(
                        memory.content, other_memory.content
                    )

                    if is_dup:
                        keep, remove = self._decide_which_to_keep(memory, other_memory)
                        logger.info(
                            f"Duplicate found: '{remove.content}' -> keeping '{keep.content}'"
                        )
                        self.memory_manager.delete_memory(remove.id)
                        deleted_ids.add(remove.id)
            else:
                # Fallback: string prefix matching deduplication (when vector store unavailable)
                strip = self.memory_manager._strip_common_prefix
                core_a = strip(memory.content)
                for other in memories:
                    if other.id == memory.id or other.id in deleted_ids:
                        continue
                    if other.type != memory.type:
                        continue

                    pair_key = tuple(sorted([memory.id, other.id]))
                    if pair_key in checked_pairs:
                        continue
                    checked_pairs.add(pair_key)

                    core_b = strip(other.content)
                    if core_a == core_b:
                        keep, remove = self._decide_which_to_keep(memory, other)
                        logger.info(
                            f"Duplicate found (string match): "
                            f"'{remove.content}' -> keeping '{keep.content}'"
                        )
                        self.memory_manager.delete_memory(remove.id)
                        deleted_ids.add(remove.id)

        if deleted_ids:
            logger.info(f"Removed {len(deleted_ids)} duplicate memories")

        return len(deleted_ids)

    def _decide_which_to_keep(self, mem1: Memory, mem2: Memory) -> tuple[Memory, Memory]:
        """
        Decide which memory to keep

        Rules:
        1. Priority: PERMANENT > LONG_TERM > SHORT_TERM > TRANSIENT
        2. Importance: higher importance_score wins
        3. Time: more recent wins

        Returns:
            (memory to keep, memory to delete)
        """
        priority_order = {
            MemoryPriority.PERMANENT: 4,
            MemoryPriority.LONG_TERM: 3,
            MemoryPriority.SHORT_TERM: 2,
            MemoryPriority.TRANSIENT: 1,
        }

        # Compare priority
        p1 = priority_order.get(mem1.priority, 0)
        p2 = priority_order.get(mem2.priority, 0)

        if p1 != p2:
            return (mem1, mem2) if p1 > p2 else (mem2, mem1)

        # Compare importance
        if abs(mem1.importance_score - mem2.importance_score) > 0.1:
            return (mem1, mem2) if mem1.importance_score > mem2.importance_score else (mem2, mem1)

        # Compare update time
        return (mem1, mem2) if mem1.updated_at > mem2.updated_at else (mem2, mem1)
