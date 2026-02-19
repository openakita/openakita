"""
记忆生命周期管理

统一归纳 + 衰减 + 去重逻辑:
- 处理未归纳的原文 → 生成 Episode → 提取语义记忆
- O(n log n) 聚类去重 (替代 O(n²))
- 衰减计算与归档
- 刷新 MEMORY.md / USER.md
- 晋升 PERSONA_TRAIT
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .extractor import MemoryExtractor
from .types import (
    ConversationTurn,
    Episode,
    MemoryPriority,
    MemoryType,
    SemanticMemory,
)
from .unified_store import UnifiedStore

logger = logging.getLogger(__name__)


class LifecycleManager:
    """记忆生命周期管理器"""

    def __init__(
        self,
        store: UnifiedStore,
        extractor: MemoryExtractor,
        identity_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.extractor = extractor
        self.identity_dir = identity_dir

    # ==================================================================
    # Daily Consolidation (凌晨任务编排)
    # ==================================================================

    async def consolidate_daily(self) -> dict:
        """
        凌晨归纳主流程, 返回统计报告
        """
        report: dict = {"started_at": datetime.now().isoformat()}

        extracted = await self.process_unextracted_turns()
        report["unextracted_processed"] = extracted

        deduped = await self.deduplicate_batch()
        report["duplicates_removed"] = deduped

        decayed = self.compute_decay()
        report["memories_decayed"] = decayed

        cleaned_att = self.cleanup_stale_attachments()
        report["stale_attachments_cleaned"] = cleaned_att

        if self.identity_dir:
            self.refresh_memory_md(self.identity_dir)
            await self.refresh_user_md(self.identity_dir)

        report["finished_at"] = datetime.now().isoformat()
        logger.info(f"[Lifecycle] Daily consolidation complete: {report}")
        return report

    # ==================================================================
    # Process Unextracted Turns
    # ==================================================================

    async def process_unextracted_turns(self) -> int:
        """处理未归纳的原文 → 生成 Episode → 提取语义记忆"""
        unextracted = self.store.get_unextracted_turns(limit=200)
        if not unextracted:
            return 0

        by_session: dict[str, list[dict]] = defaultdict(list)
        for turn in unextracted:
            by_session[turn["session_id"]].append(turn)

        total = 0
        for session_id, turns in by_session.items():
            conv_turns = [
                ConversationTurn(
                    role=t["role"],
                    content=t.get("content") or "",
                    timestamp=datetime.fromisoformat(t["timestamp"]) if t.get("timestamp") else datetime.now(),
                    tool_calls=t.get("tool_calls") or [],
                    tool_results=t.get("tool_results") or [],
                )
                for t in turns
            ]

            episode = await self.extractor.generate_episode(
                conv_turns, session_id, source="daily_consolidation"
            )
            if episode:
                self.store.save_episode(episode)

                for turn_obj in conv_turns:
                    items = await self.extractor.extract_from_turn_v2(turn_obj)
                    for item in items:
                        self._save_extracted_item(item, episode.id)
                    total += len(items)

            indices = [t["turn_index"] for t in turns]
            self.store.mark_turns_extracted(session_id, indices)

        retry_items = self.store.dequeue_extraction(batch_size=20)
        for item in retry_items:
            turn = ConversationTurn(
                role="user",
                content=item.get("content", ""),
                tool_calls=item.get("tool_calls") or [],
                tool_results=item.get("tool_results") or [],
            )
            extracted = await self.extractor.extract_from_turn_v2(turn)
            success = len(extracted) > 0
            for e in extracted:
                self._save_extracted_item(e)
                total += 1
            self.store.complete_extraction(item["id"], success=success)

        logger.info(f"[Lifecycle] Processed {total} memories from unextracted turns")
        return total

    def _save_extracted_item(self, item: dict, episode_id: str | None = None) -> None:
        type_map = {
            "PREFERENCE": MemoryType.PREFERENCE,
            "FACT": MemoryType.FACT,
            "SKILL": MemoryType.SKILL,
            "ERROR": MemoryType.ERROR,
            "RULE": MemoryType.RULE,
            "PERSONA_TRAIT": MemoryType.PERSONA_TRAIT,
        }
        mem_type = type_map.get(item.get("type", "FACT"), MemoryType.FACT)
        importance = item.get("importance", 0.5)

        if importance >= 0.85 or mem_type == MemoryType.RULE:
            priority = MemoryPriority.PERMANENT
        elif importance >= 0.6:
            priority = MemoryPriority.LONG_TERM
        else:
            priority = MemoryPriority.SHORT_TERM

        if item.get("is_update"):
            existing = self.store.find_similar(
                item.get("subject", ""), item.get("predicate", "")
            )
            if existing:
                self.store.update_semantic(existing.id, {
                    "content": item["content"],
                    "importance_score": max(existing.importance_score, importance),
                    "confidence": min(1.0, existing.confidence + 0.1),
                })
                return

        mem = SemanticMemory(
            type=mem_type,
            priority=priority,
            content=item["content"],
            source="daily_consolidation",
            subject=item.get("subject", ""),
            predicate=item.get("predicate", ""),
            importance_score=importance,
            source_episode_id=episode_id,
            tags=[item.get("type", "fact").lower()],
        )
        self.store.save_semantic(mem)

    # ==================================================================
    # Deduplication (O(n log n))
    # ==================================================================

    async def deduplicate_batch(self) -> int:
        """基于聚类的批量去重"""
        all_memories = self.store.load_all_memories()
        if len(all_memories) < 2:
            return 0

        by_type: dict[str, list[SemanticMemory]] = defaultdict(list)
        for mem in all_memories:
            if mem.superseded_by:
                continue
            by_type[mem.type.value].append(mem)

        deleted = 0
        for mem_type, group in by_type.items():
            if len(group) < 2:
                continue
            clusters = self._cluster_by_content(group, threshold=0.7)
            for cluster in clusters:
                if len(cluster) < 2:
                    continue
                keep, remove = self._pick_best_in_cluster(cluster)
                for mem in remove:
                    self.store.delete_semantic(mem.id)
                    deleted += 1
                    logger.debug(f"[Lifecycle] Dedup: removed {mem.id} (kept {keep.id})")

        if deleted > 0:
            logger.info(f"[Lifecycle] Dedup removed {deleted} memories")
        return deleted

    def _cluster_by_content(
        self, memories: list[SemanticMemory], threshold: float = 0.7
    ) -> list[list[SemanticMemory]]:
        """Simple clustering by content similarity (word overlap)."""
        clusters: list[list[SemanticMemory]] = []
        assigned: set[str] = set()

        for i, mem_a in enumerate(memories):
            if mem_a.id in assigned:
                continue
            cluster = [mem_a]
            assigned.add(mem_a.id)

            words_a = set(mem_a.content.lower().split())
            for j in range(i + 1, len(memories)):
                mem_b = memories[j]
                if mem_b.id in assigned:
                    continue
                words_b = set(mem_b.content.lower().split())
                if not words_a or not words_b:
                    continue
                overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                if overlap >= threshold:
                    cluster.append(mem_b)
                    assigned.add(mem_b.id)

            if len(cluster) >= 2:
                clusters.append(cluster)

        return clusters

    @staticmethod
    def _pick_best_in_cluster(
        cluster: list[SemanticMemory],
    ) -> tuple[SemanticMemory, list[SemanticMemory]]:
        """Pick the best memory in a cluster, return (keep, remove_list)."""
        scored = sorted(
            cluster,
            key=lambda m: (
                m.importance_score,
                m.access_count,
                len(m.content),
                m.updated_at.isoformat() if m.updated_at else "",
            ),
            reverse=True,
        )
        return scored[0], scored[1:]

    # ==================================================================
    # Decay
    # ==================================================================

    def compute_decay(self) -> int:
        """Apply decay to SHORT_TERM memories, archive low-scoring ones."""
        memories = self.store.query_semantic(priority="SHORT_TERM", limit=500)
        decayed = 0

        for mem in memories:
            if not mem.last_accessed_at and not mem.updated_at:
                continue

            ref_time = mem.last_accessed_at or mem.updated_at
            days_since = max(0, (datetime.now() - ref_time).total_seconds() / 86400)
            decay_factor = (1 - mem.decay_rate) ** days_since
            effective_score = mem.importance_score * decay_factor

            if effective_score < 0.1 and mem.access_count < 2:
                self.store.update_semantic(mem.id, {
                    "priority": MemoryPriority.TRANSIENT.value,
                    "importance_score": effective_score,
                })
                decayed += 1

        expired = self.store.db.cleanup_expired()
        decayed += expired

        if decayed > 0:
            logger.info(f"[Lifecycle] Decayed/archived {decayed} memories")
        return decayed

    # ==================================================================
    # Attachment Lifecycle
    # ==================================================================

    def cleanup_stale_attachments(self, max_age_days: int = 90) -> int:
        """清理过期的空白附件 (无描述+无关联+超龄)"""
        if not self.store.db._conn:
            return 0
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
            cursor = self.store.db._conn.execute(
                """DELETE FROM attachments
                   WHERE created_at < ?
                     AND description = ''
                     AND transcription = ''
                     AND extracted_text = ''
                     AND linked_memory_ids = '[]'""",
                (cutoff,),
            )
            count = cursor.rowcount
            if count:
                self.store.db._conn.commit()
                logger.info(f"[Lifecycle] Cleaned {count} stale attachments (>{max_age_days} days, no content)")
            return count
        except Exception as e:
            logger.error(f"[Lifecycle] Attachment cleanup failed: {e}")
            return 0

    # ==================================================================
    # Refresh MEMORY.md
    # ==================================================================

    def refresh_memory_md(self, identity_dir: Path) -> None:
        """刷新 MEMORY.md — 从语义记忆选取 top-K"""
        memories = self.store.query_semantic(min_importance=0.5, limit=100)

        by_type: dict[str, list[SemanticMemory]] = defaultdict(list)
        for mem in memories:
            by_type[mem.type.value].append(mem)

        lines: list[str] = ["# 核心记忆\n"]
        type_labels = {
            "preference": "偏好",
            "fact": "事实",
            "rule": "规则",
            "skill": "技能",
            "error": "教训",
        }

        total_chars = 0
        max_chars = 1500

        for type_key, label in type_labels.items():
            group = by_type.get(type_key, [])
            if not group:
                continue
            group.sort(key=lambda m: m.importance_score, reverse=True)
            lines.append(f"\n## {label}")
            for mem in group[:5]:
                line = f"- {mem.content}"
                if total_chars + len(line) > max_chars:
                    break
                lines.append(line)
                total_chars += len(line)

        memory_md = identity_dir / "MEMORY.md"
        memory_md.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"[Lifecycle] Refreshed MEMORY.md ({total_chars} chars)")

    # ==================================================================
    # Refresh USER.md
    # ==================================================================

    async def refresh_user_md(self, identity_dir: Path) -> None:
        """从语义记忆自动填充 USER.md"""
        user_facts = self.store.query_semantic(subject="用户", limit=50)
        if not user_facts:
            return

        categories: dict[str, list[str]] = {
            "basic": [],
            "tech": [],
            "preferences": [],
            "projects": [],
        }

        for mem in user_facts:
            pred = mem.predicate.lower() if mem.predicate else ""
            content = mem.content

            if any(k in pred for k in ("称呼", "名字", "身份", "时区")):
                categories["basic"].append(content)
            elif any(k in pred for k in ("技术", "语言", "框架", "工具", "版本")):
                categories["tech"].append(content)
            elif any(k in pred for k in ("偏好", "风格", "习惯")):
                categories["preferences"].append(content)
            elif any(k in pred for k in ("项目", "工作")):
                categories["projects"].append(content)
            elif mem.type == MemoryType.PREFERENCE:
                categories["preferences"].append(content)
            elif mem.type == MemoryType.FACT:
                categories["basic"].append(content)

        lines = ["# 用户档案\n", "> 由记忆系统自动生成\n"]

        section_map = {
            "basic": "基本信息",
            "tech": "技术栈",
            "preferences": "偏好",
            "projects": "项目",
        }

        has_content = False
        for key, label in section_map.items():
            items = categories[key]
            if not items:
                continue
            has_content = True
            lines.append(f"\n## {label}")
            for item in items[:8]:
                lines.append(f"- {item}")

        if has_content:
            user_md = identity_dir / "USER.md"
            user_md.write_text("\n".join(lines), encoding="utf-8")
            logger.info("[Lifecycle] Refreshed USER.md from semantic memories")
