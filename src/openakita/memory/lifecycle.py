"""
Memory lifecycle management.

Unified consolidation + decay + deduplication:
- Process un-consolidated raw turns → generate Episode → extract semantic memories
- O(n log n) clustering-based dedup (replaces O(n^2))
- Decay computation and archival
- Refresh MEMORY.md / USER.md
- Promote PERSONA_TRAIT
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .extractor import MemoryExtractor
from .storage import _is_db_locked

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable
from .types import (
    MEMORY_MD_MAX_CHARS,
    ConversationTurn,
    MemoryPriority,
    MemoryType,
    SemanticMemory,
)
from .unified_store import UnifiedStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level helpers (shared by LifecycleManager methods)
# ---------------------------------------------------------------------------

_jieba_mod: object | None = None
_jieba_loaded = False


def _tokenize_for_dedup(text: str) -> set[str]:
    """Tokenize *text* for word-overlap comparison.

    Uses jieba ``cut_for_search`` for Chinese-aware segmentation;
    falls back to whitespace split when jieba is unavailable.
    Tokens shorter than 2 chars are discarded to reduce noise.
    """
    global _jieba_mod, _jieba_loaded  # noqa: PLW0603
    if not _jieba_loaded:
        try:
            import jieba

            jieba.setLogLevel(logging.WARNING)
            _jieba_mod = jieba
        except ImportError:
            pass
        _jieba_loaded = True

    lowered = text.lower()
    if _jieba_mod is not None:
        tokens = set(_jieba_mod.cut_for_search(lowered))
    else:
        tokens = set(lowered.split())
    return {t for t in tokens if len(t) >= 2}


def _fast_content_dedup(new: str, existing: str) -> str:
    """Fast local content similarity check.

    Returns
    -------
    "exact"  – definitely duplicate (safe to merge without LLM)
    "likely" – might be duplicate (would need LLM to confirm)
    "no"     – not duplicate
    """
    if not new or not existing:
        return "no"
    a, b = new.lower().strip(), existing.lower().strip()
    if a == b:
        return "exact"
    if len(a) > 15 and len(b) > 15 and (a in b or b in a):
        return "exact"
    if len(a) >= 10 and len(b) >= 10:
        bigrams_a = {a[i : i + 2] for i in range(len(a) - 1)}
        bigrams_b = {b[i : i + 2] for i in range(len(b) - 1)}
        if bigrams_a and bigrams_b:
            overlap = len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)
            if overlap > 0.8:
                return "exact"
            if overlap > 0.3:
                return "likely"
    return "no"


def _safe_write_with_backup(path: Path, content: str) -> None:
    """Safely write a file: back it up first, then write; restore on failure."""
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        if path.exists():
            import shutil

            shutil.copy2(path, backup)
    except Exception as e:
        logger.warning(f"Failed to create backup of {path}: {e}")

    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to write {path}: {e}")
        if backup.exists():
            try:
                import shutil

                shutil.copy2(backup, path)
                logger.info(f"Restored {path} from backup")
            except Exception as e2:
                logger.error(f"Failed to restore {path} from backup: {e2}")
        raise


class LifecycleManager:
    """Memory lifecycle manager."""

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
    # Daily Consolidation (early-morning task orchestration)
    # ==================================================================

    async def consolidate_daily(self) -> dict:
        """
        Main early-morning consolidation flow; returns a statistics report.
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

        review_result = await self.review_memories_with_llm()
        report["llm_review"] = review_result

        synthesized = await self.synthesize_experiences()
        report["experience_synthesized"] = synthesized

        if self.identity_dir:
            self.refresh_memory_md(self.identity_dir)
            await self.refresh_user_md(self.identity_dir)

        self._sync_vector_store()

        report["finished_at"] = datetime.now().isoformat()
        logger.info(f"[Lifecycle] Daily consolidation complete: {report}")
        return report

    def _sync_vector_store(self) -> None:
        """Rebuild vector store index from current SQLite data.

        双向同步：
        - 删 stale：SQLite 已不存在的 id 从向量库剔除
        - 补 missing：SQLite 有但向量库无的 id 重新嵌入（避免 Chroma 启动期
          竞态导致的"写入失败 + 后续无补全"洞口，参考 vector_store.py 300s 冷却）
        """
        try:
            if not hasattr(self.store, "search") or not self.store.search:
                return
            all_mems = self.store.load_all_memories()
            mem_ids = {m.id for m in all_mems}
            search = self.store.search

            existing_ids: set[str] | None = None
            if hasattr(search, "_collection"):
                try:
                    existing_ids = set(search._collection.get()["ids"])
                except Exception:
                    existing_ids = None

            if hasattr(search, "delete_not_in"):
                search.delete_not_in(mem_ids)
                logger.info(f"[Lifecycle] Vector store synced ({len(mem_ids)} memories)")
            elif existing_ids is not None:
                stale = existing_ids - mem_ids
                if stale:
                    search._collection.delete(ids=list(stale))
                    logger.info(f"[Lifecycle] Removed {len(stale)} stale vectors")

            if existing_ids is not None and hasattr(search, "add"):
                missing = [m for m in all_mems if m.id not in existing_ids]
                if missing:
                    added = 0
                    for mem in missing:
                        try:
                            search.add(
                                mem.id,
                                mem.content,
                                {
                                    "type": mem.type.value,
                                    "priority": mem.priority.value,
                                    "importance": mem.importance_score,
                                    "tags": mem.tags,
                                },
                            )
                            added += 1
                        except Exception as _e:
                            logger.debug(
                                f"[Lifecycle] backfill embed failed for {mem.id}: {_e}"
                            )
                    if added:
                        logger.info(
                            f"[Lifecycle] Backfilled {added}/{len(missing)} missing vectors"
                        )
        except Exception as e:
            logger.debug(f"[Lifecycle] Vector store sync skipped: {e}")

    # ==================================================================
    # Process Unextracted Turns
    # ==================================================================

    async def process_unextracted_turns(self) -> int:
        """Process un-consolidated raw turns → generate Episode → extract semantic memories."""
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
                    timestamp=datetime.fromisoformat(t["timestamp"])
                    if t.get("timestamp")
                    else datetime.now(),
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
        content = (item.get("content") or "").strip()
        subject = item.get("subject", "")
        predicate = item.get("predicate", "")

        if importance >= 0.85 or mem_type == MemoryType.RULE:
            priority = MemoryPriority.PERMANENT
        elif importance >= 0.6:
            priority = MemoryPriority.LONG_TERM
        else:
            priority = MemoryPriority.SHORT_TERM

        # --- Dedup layer 1: subject+predicate match (always, not only is_update) ---
        if subject and predicate:
            existing = self.store.find_similar(subject, predicate)
            if existing and not existing.superseded_by:
                updates: dict = {
                    "importance_score": max(existing.importance_score, importance),
                    "confidence": min(1.0, existing.confidence + 0.1),
                }
                should_update = (
                    item.get("is_update")
                    or importance > existing.importance_score
                    or (
                        importance >= existing.importance_score
                        and len(content) > len(existing.content or "")
                    )
                )
                if should_update:
                    updates["content"] = content
                self.store.update_semantic(existing.id, updates)
                logger.debug(f"[Lifecycle] Dedup L1: evolved {existing.id[:8]} (subject+predicate)")
                return

        # --- Dedup layer 2: content similarity via search backend ---
        if content and len(content) >= 10:
            try:
                similar = self.store.search_semantic(content, limit=5)
                for s in similar:
                    if s.superseded_by or s.type != mem_type:
                        continue
                    level = _fast_content_dedup(content, s.content or "")
                    if level == "exact":
                        self.store.update_semantic(
                            s.id,
                            {
                                "importance_score": max(s.importance_score, importance),
                                "confidence": min(1.0, s.confidence + 0.1),
                            },
                        )
                        logger.debug(f"[Lifecycle] Dedup L2: evolved {s.id[:8]} (content match)")
                        return
            except Exception as e:
                logger.debug(f"[Lifecycle] Dedup search failed: {e}")

        # --- No duplicate found — save new memory ---
        mem = SemanticMemory(
            type=mem_type,
            priority=priority,
            content=content,
            source="daily_consolidation",
            subject=subject,
            predicate=predicate,
            importance_score=importance,
            source_episode_id=episode_id,
            tags=[item.get("type", "fact").lower()],
        )
        self.store.save_semantic(mem)

    # ==================================================================
    # Deduplication (O(n log n))
    # ==================================================================

    async def deduplicate_batch(self) -> int:
        """Clustering-based batch deduplication."""
        all_memories = self.store.load_all_memories()
        if len(all_memories) < 2:
            return 0

        by_type: dict[str, list[SemanticMemory]] = defaultdict(list)
        for mem in all_memories:
            if mem.superseded_by:
                continue
            by_type[mem.type.value].append(mem)

        deleted = 0
        for _mem_type, group in by_type.items():
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
        """Clustering by token-overlap similarity.

        Uses jieba segmentation (via ``_tokenize_for_dedup``) so that
        Chinese text is properly tokenised instead of being treated as a
        single whitespace-delimited "word".
        """
        clusters: list[list[SemanticMemory]] = []
        assigned: set[str] = set()

        token_cache: dict[str, set[str]] = {}
        for mem in memories:
            token_cache[mem.id] = _tokenize_for_dedup(mem.content)

        for i, mem_a in enumerate(memories):
            if mem_a.id in assigned:
                continue
            cluster = [mem_a]
            assigned.add(mem_a.id)

            words_a = token_cache[mem_a.id]
            for j in range(i + 1, len(memories)):
                mem_b = memories[j]
                if mem_b.id in assigned:
                    continue
                words_b = token_cache[mem_b.id]
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

            if effective_score < 0.1 and mem.access_count < 3:
                self.store.delete_semantic(mem.id)
                decayed += 1
            elif effective_score < 0.3:
                self.store.update_semantic(
                    mem.id,
                    {
                        "priority": MemoryPriority.TRANSIENT.value,
                        "importance_score": effective_score,
                    },
                )
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
        """Clean up stale blank attachments (no description + no links + past age limit)."""
        db = self.store.db
        if not db._conn:
            return 0
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        with db._lock:
            try:
                cursor = db._conn.execute(
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
                    db._conn.commit()
                    logger.info(
                        f"[Lifecycle] Cleaned {count} stale attachments (>{max_age_days} days, no content)"
                    )
                return count
            except Exception as e:
                if _is_db_locked(e):
                    raise
                logger.error(f"[Lifecycle] Attachment cleanup failed: {e}")
                return 0

    # ==================================================================
    # Refresh MEMORY.md
    # ==================================================================

    # ==================================================================
    # LLM-driven Memory Review
    # ==================================================================

    MEMORY_REVIEW_PROMPT = """You are a memory quality reviewer. Review each of the memories below and decide whether it is worth keeping long-term.

## Review criteria

**Keep** (truly long-term information):
- User identity: name, how they like to be addressed, profession
- Long-term user preferences: communication style, language habits, preferred notification channels
- Persistent behavioral rules: long-standing user requirements for AI behavior
- Technical environment: OS, commonly used tools, tech stack
- Reusable experience: general solutions for specific kinds of problems
- Valuable lessons: operational patterns that should be avoided long-term
- **Highly cited memories** (cited>=5): proven useful through repeated real-world use; keep unless obviously stale

**Delete** (garbage that should not exist):
- One-off task requests: "need an XX photo", "download XX", "search for XX", "compile XX news"
- Task-artifact details: file sizes, resolutions, download links, specific file paths
- Task-execution reports: AI reply summaries like "Successfully completed: ..." or "Done, boss..."
- Expired transient info: specific timestamps, one-off scheduled-task parameters
- Duplicates / redundant: semantically overlap with other memories
- Context-free fragments: short phrases that lack a subject and cannot stand alone
- **Zero-citation + low-score memories** (cited=0 and score<0.5): never proven useful; clean up first

**Merge**: if two memories describe the same thing, mark the action as merge and provide the merged content.

## Memories to review

{memories_text}

## Output format

Return a JSON array, one entry per memory:
[
  {{
    "id": "memory ID",
    "action": "keep|delete|merge|update",
    "reason": "short justification (<=10 words)",
    "merged_with": "merge-target ID (only when action is merge)",
    "new_content": "updated content (only for update/merge)",
    "new_importance": 0.5-1.0
  }}
]

Output only the JSON array, nothing else."""

    async def review_memories_with_llm(
        self,
        progress_callback: Callable[[dict], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """
        Use the LLM to review all memories: clean up garbage, merge duplicates, update stale content.

        Args:
            progress_callback: called after each batch completes, receives the current progress dict
            cancel_event: if set, abort before the next batch

        Returns:
            Review report {deleted, updated, merged, kept, errors}
        """
        import json
        import math
        import re

        all_memories = self.store.load_all_memories()
        if not all_memories:
            return {"deleted": 0, "updated": 0, "merged": 0, "kept": 0}

        if not self.extractor or not self.extractor.brain:
            logger.warning("[Lifecycle] No LLM available for memory review, skipping")
            return {"deleted": 0, "updated": 0, "merged": 0, "kept": len(all_memories)}

        report = {"deleted": 0, "updated": 0, "merged": 0, "kept": 0, "errors": 0}

        batch_size = 15
        total_batches = math.ceil(len(all_memories) / batch_size)
        consecutive_risky_skips = 0
        max_consecutive_risky = 3

        for batch_idx, i in enumerate(range(0, len(all_memories), batch_size)):
            if cancel_event and cancel_event.is_set():
                logger.info("[Lifecycle] Memory review cancelled by user")
                break

            batch = all_memories[i : i + batch_size]

            if progress_callback:
                progress_callback(
                    {
                        "phase": "llm_calling",
                        "batch": batch_idx,
                        "total_batches": total_batches,
                        "total_memories": len(all_memories),
                        "processed": i,
                        "report": dict(report),
                    }
                )

            memories_text = "\n".join(
                f"- ID={m.id} | type={m.type.value} | score={m.importance_score:.2f} "
                f"| cited={m.access_count} | subject={m.subject or ''} | content={m.content}"
                for m in batch
            )

            prompt = self.MEMORY_REVIEW_PROMPT.format(memories_text=memories_text)

            try:
                response = await self.extractor.brain.think(
                    prompt,
                    system="You are a memory quality reviewer. Output only a JSON array.",
                )
                text = (getattr(response, "content", None) or str(response)).strip()

                json_match = re.search(r"\[[\s\S]*\]", text)
                if not json_match:
                    logger.warning(f"[Lifecycle] LLM review batch {batch_idx}: no JSON output")
                    report["kept"] += len(batch)
                    continue

                decisions = json.loads(json_match.group())
                if not isinstance(decisions, list):
                    report["kept"] += len(batch)
                    continue

                destructive = 0
                for d in decisions:
                    if not isinstance(d, dict):
                        continue
                    action = str(d.get("action", "keep")).lower()
                    if action in ("delete", "merge"):
                        destructive += 1
                if destructive > max(3, int(len(batch) * 0.4)):
                    consecutive_risky_skips += 1
                    logger.warning(
                        "[Lifecycle] Skip risky review batch %s: destructive=%s/%s "
                        "(consecutive=%s/%s)",
                        batch_idx,
                        destructive,
                        len(batch),
                        consecutive_risky_skips,
                        max_consecutive_risky,
                    )
                    report["kept"] += len(batch)
                    if consecutive_risky_skips >= max_consecutive_risky:
                        remaining = len(all_memories) - (i + len(batch))
                        logger.warning(
                            "[Lifecycle] Aborting LLM review: %s consecutive risky "
                            "batches — LLM appears unreliable for this corpus. "
                            "Keeping remaining %s memories as-is.",
                            max_consecutive_risky,
                            remaining,
                        )
                        report["kept"] += remaining
                        break
                    continue
                consecutive_risky_skips = 0

                decision_map = {d["id"]: d for d in decisions if isinstance(d, dict) and "id" in d}

                for mem in batch:
                    dec = decision_map.get(mem.id)
                    if not dec:
                        report["kept"] += 1
                        continue

                    action = dec.get("action", "keep")

                    if action == "delete":
                        self.store.delete_semantic(mem.id)
                        report["deleted"] += 1
                        logger.debug(
                            f"[Lifecycle] Review DELETE: {mem.content[:50]} "
                            f"({dec.get('reason', '')})"
                        )

                    elif action == "update":
                        updates: dict = {}
                        if dec.get("new_content"):
                            updates["content"] = dec["new_content"]
                        if dec.get("new_importance"):
                            updates["importance_score"] = float(dec["new_importance"])
                        if updates:
                            self.store.update_semantic(mem.id, updates)
                            report["updated"] += 1
                        else:
                            report["kept"] += 1

                    elif action == "merge":
                        target_id = dec.get("merged_with")
                        new_content = dec.get("new_content")
                        if target_id and new_content:
                            self.store.update_semantic(target_id, {"content": new_content})
                            self.store.delete_semantic(mem.id)
                            report["merged"] += 1
                        else:
                            report["kept"] += 1

                    else:
                        report["kept"] += 1

            except Exception as e:
                logger.error(f"[Lifecycle] LLM review batch {batch_idx} failed: {e}")
                report["errors"] += 1
                report["kept"] += len(batch)

            if progress_callback:
                progress_callback(
                    {
                        "phase": "batch_done",
                        "batch": batch_idx + 1,
                        "total_batches": total_batches,
                        "total_memories": len(all_memories),
                        "processed": min(i + batch_size, len(all_memories)),
                        "report": dict(report),
                    }
                )

        cancelled = cancel_event.is_set() if cancel_event else False

        if progress_callback:
            progress_callback(
                {
                    "phase": "done",
                    "batch": total_batches,
                    "total_batches": total_batches,
                    "total_memories": len(all_memories),
                    "processed": len(all_memories),
                    "report": dict(report),
                    "done": True,
                    "cancelled": cancelled,
                }
            )

        # All batches failed → LLM completely unavailable, re-raise so the
        # scheduler can mark_failed() and trigger its existing notification.
        # Partial failure (some batches OK) is tolerated: succeeded batches
        # take effect, failed ones keep memories as-is.
        if not cancelled and report["errors"] >= total_batches > 0:
            from ..llm.types import AllEndpointsFailedError

            raise AllEndpointsFailedError(f"LLM review failed: all {total_batches} batches errored")

        logger.info(
            f"[Lifecycle] Memory review complete: "
            f"deleted={report['deleted']}, updated={report['updated']}, "
            f"merged={report['merged']}, kept={report['kept']}"
            f"{' (cancelled)' if cancelled else ''}"
        )
        return report

    # ==================================================================
    # Experience Synthesis (synthesize experience memories into general principles)
    # ==================================================================

    EXPERIENCE_SYNTHESIS_PROMPT = """You are an experience-synthesis expert. Below are specific experience / lesson / skill memories accumulated recently.
Decide whether multiple of them can be synthesized into a **more general principle**.

## Experience memory list

{experience_memories}

## Synthesis rules

- If 2+ experiences describe different facets of the same kind of problem, synthesize them into one general principle
- The synthesized principle should be more abstract and more instructive than the originals
- Do not force-synthesize unrelated experiences
- If nothing can be synthesized, output an empty array

## Output format

[
  {{
    "synthesized_from": ["source memory ID 1", "source memory ID 2"],
    "content": "the synthesized general principle",
    "subject": "subject",
    "predicate": "experience type",
    "importance": 0.8-1.0
  }}
]

Output only the JSON array. If nothing can be synthesized, output []."""

    async def synthesize_experiences(self) -> int:
        """Synthesize specific experience memories into general principles."""
        import json
        import re

        exp_types = {MemoryType.EXPERIENCE.value, MemoryType.SKILL.value, MemoryType.ERROR.value}
        all_mems = self.store.load_all_memories()
        experiences = [m for m in all_mems if m.type.value in exp_types]

        if len(experiences) < 3:
            return 0

        if not self.extractor or not self.extractor.brain:
            return 0

        exp_text = "\n".join(
            f"- ID={m.id} | type={m.type.value} | cited={m.access_count} | content={m.content}"
            for m in experiences[:30]
        )

        prompt = self.EXPERIENCE_SYNTHESIS_PROMPT.format(experience_memories=exp_text)

        try:
            response = await self.extractor.brain.think(
                prompt,
                system="You are an experience-synthesis expert. Output only a JSON array.",
            )
            text = (getattr(response, "content", None) or str(response)).strip()
            json_match = re.search(r"\[[\s\S]*\]", text)
            if not json_match:
                return 0

            syntheses = json.loads(json_match.group())
            if not isinstance(syntheses, list):
                return 0

            saved = 0
            for synth in syntheses:
                if not isinstance(synth, dict):
                    continue
                content = (synth.get("content") or "").strip()
                source_ids = synth.get("synthesized_from", [])
                if len(content) < 10 or len(source_ids) < 2:
                    continue

                # Dedup: skip if a similar experience already exists
                dup_target: SemanticMemory | None = None
                try:
                    similar = self.store.search_semantic(content, limit=3)
                    for s in similar:
                        if s.superseded_by:
                            continue
                        if _fast_content_dedup(content, s.content or "") == "exact":
                            dup_target = s
                            break
                except Exception:
                    pass

                if dup_target is not None:
                    for sid in source_ids:
                        self.store.update_semantic(sid, {"superseded_by": dup_target.id})
                    logger.debug(f"[Lifecycle] Synthesis dedup: reused {dup_target.id[:8]}")
                    continue

                mem = SemanticMemory(
                    type=MemoryType.EXPERIENCE,
                    priority=MemoryPriority.LONG_TERM,
                    content=content,
                    source="experience_synthesis",
                    subject=(synth.get("subject") or "").strip(),
                    predicate=(synth.get("predicate") or "").strip(),
                    importance_score=min(1.0, max(0.7, float(synth.get("importance", 0.85)))),
                    confidence=0.8,
                )
                self.store.save_semantic(mem)
                saved += 1

                # Mark source memories as superseded
                for sid in source_ids:
                    self.store.update_semantic(sid, {"superseded_by": mem.id})

            if saved:
                logger.info(
                    f"[Lifecycle] Synthesized {saved} experience principles from {len(experiences)} memories"
                )
            return saved

        except Exception as e:
            logger.error(f"[Lifecycle] Experience synthesis failed: {e}")
            return 0

    # ==================================================================
    # Refresh MEMORY.md (post-review, no keyword filter needed)
    # ==================================================================

    def refresh_memory_md(self, identity_dir: Path) -> None:
        """Refresh MEMORY.md — after LLM review, simply pick the top-K (no keyword filter needed)."""
        memories = self.store.query_semantic(min_importance=0.5, limit=100)

        by_type: dict[str, list[SemanticMemory]] = defaultdict(list)
        for mem in memories:
            by_type[mem.type.value].append(mem)

        lines: list[str] = ["# Core Memories\n"]
        type_labels = {
            "preference": "Preferences",
            "rule": "Rules",
            "fact": "Facts",
            "error": "Lessons",
            "skill": "Skills",
            "experience": "Experience",
        }

        total_chars = 0
        max_chars = MEMORY_MD_MAX_CHARS

        for type_key, label in type_labels.items():
            group = by_type.get(type_key, [])
            if not group:
                continue
            group.sort(key=lambda m: m.importance_score, reverse=True)
            lines.append(f"\n## {label}")
            for mem in group[:4]:
                line = f"- {mem.content}"
                if total_chars + len(line) > max_chars:
                    break
                lines.append(line)
                total_chars += len(line)

        memory_md = identity_dir / "MEMORY.md"
        new_content = "\n".join(lines)

        if len(new_content.strip()) < 10:
            logger.warning("[Lifecycle] Generated MEMORY.md content too short, skipping refresh")
            return

        _safe_write_with_backup(memory_md, new_content)
        logger.info(f"[Lifecycle] Refreshed MEMORY.md ({total_chars} chars)")

    # ==================================================================
    # Refresh USER.md
    # ==================================================================

    async def refresh_user_md(self, identity_dir: Path) -> None:
        """Auto-populate USER.md from semantic memories."""
        user_facts = self.store.query_semantic(subject="用户", limit=50)
        if not user_facts:
            return

        categories: dict[str, list[str]] = {
            "basic": [],
            "tech": [],
            "preferences": [],
            "projects": [],
        }

        _action_words = {
            "打开",
            "关闭",
            "运行",
            "执行",
            "安装",
            "部署",
            "启动",
            "停止",
            "创建",
            "删除",
            "修改",
            "搜索",
            "下载",
            "上传",
            "编译",
            "测试",
            "去",
            "进入",
            "访问",
            "登录",
            "检查",
            "查看",
            "发送",
        }
        user_facts = [
            m
            for m in user_facts
            if not any(w in (m.predicate or "") for w in _action_words)
            and not any(w in (m.content or "")[:20] for w in _action_words)
        ]

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

        lines = ["# User Profile\n", "> Auto-generated by the memory system\n"]

        section_map = {
            "basic": "Basic Info",
            "tech": "Tech Stack",
            "preferences": "Preferences",
            "projects": "Projects",
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
