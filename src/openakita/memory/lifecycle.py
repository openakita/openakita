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
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .extractor import MemoryExtractor
from .types import (
    ConversationTurn,
    MemoryPriority,
    MemoryType,
    SemanticMemory,
)
from .unified_store import UnifiedStore

logger = logging.getLogger(__name__)


def _safe_write_with_backup(path: Path, content: str) -> None:
    """安全写入文件：先备份再写入，写失败则恢复"""
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
        """Rebuild vector store index from current SQLite data."""
        try:
            if not hasattr(self.store, "search") or not self.store.search:
                return
            all_mems = self.store.load_all_memories()
            mem_ids = {m.id for m in all_mems}
            search = self.store.search
            if hasattr(search, "delete_not_in"):
                search.delete_not_in(mem_ids)
                logger.info(f"[Lifecycle] Vector store synced ({len(mem_ids)} memories)")
            elif hasattr(search, "_collection"):
                existing = set(search._collection.get()["ids"])
                stale = existing - mem_ids
                if stale:
                    search._collection.delete(ids=list(stale))
                    logger.info(f"[Lifecycle] Removed {len(stale)} stale vectors")
        except Exception as e:
            logger.debug(f"[Lifecycle] Vector store sync skipped: {e}")

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

            if effective_score < 0.1 and mem.access_count < 3:
                self.store.delete_semantic(mem.id)
                decayed += 1
            elif effective_score < 0.3:
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
                    logger.info(f"[Lifecycle] Cleaned {count} stale attachments (>{max_age_days} days, no content)")
                return count
            except Exception as e:
                logger.error(f"[Lifecycle] Attachment cleanup failed: {e}")
                return 0

    # ==================================================================
    # Refresh MEMORY.md
    # ==================================================================

    # ==================================================================
    # LLM-driven Memory Review
    # ==================================================================

    MEMORY_REVIEW_PROMPT = """你是记忆质量审查专家。请逐条审查以下记忆，判断每条是否值得长期保留。

## 审查标准

**保留**（真正的长期信息）：
- 用户身份：名字、称呼、职业
- 用户长期偏好：沟通风格、语言习惯、通知渠道偏好
- 持久行为规则：用户对 AI 行为的长期要求
- 技术环境：OS、常用工具、技术栈
- 可复用经验：特定类型问题的通用解决方法
- 有价值的教训：需要长期避免的操作模式
- **高引用记忆**（cited>=5 次）：说明实际使用中多次被证实有用，除非明显过期否则应保留

**删除**（不应存在的垃圾）：
- 一次性任务请求：「需要XX照片」「下载XX」「帮我搜索XX」「整理XX新闻」
- 任务产物细节：文件大小、分辨率、下载链接、具体文件路径
- 任务执行报告：「成功完成: ...」「搞定老板...」等 AI 回复摘要
- 过期的临时信息：特定时间点、一次性定时任务参数
- 重复/冗余：与其他记忆语义重复的
- 无上下文的碎片：缺乏主语、无法独立理解的短句
- **零引用+低分记忆**（cited=0 且 score<0.5）：从未被证实有用，优先清理

**合并**：如果两条记忆说的是同一件事，标记为 merge 并给出合并后的内容。

## 待审查记忆

{memories_text}

## 输出格式

对每条记忆输出 JSON 数组：
[
  {{
    "id": "记忆ID",
    "action": "keep|delete|merge|update",
    "reason": "简要理由（10字内）",
    "merged_with": "合并目标ID（仅 merge 时）",
    "new_content": "更新后的内容（仅 update/merge 时）",
    "new_importance": 0.5-1.0
  }}
]

只输出 JSON 数组，不要其他内容。"""

    async def review_memories_with_llm(self) -> dict:
        """
        使用 LLM 审查所有记忆，清理垃圾、合并重复、更新过期内容。

        Returns:
            审查报告 {deleted, updated, merged, kept, errors}
        """
        import json
        import re

        all_memories = self.store.load_all_memories()
        if not all_memories:
            return {"deleted": 0, "updated": 0, "merged": 0, "kept": 0}

        if not self.extractor or not self.extractor.brain:
            logger.warning("[Lifecycle] No LLM available for memory review, skipping")
            return {"deleted": 0, "updated": 0, "merged": 0, "kept": len(all_memories)}

        report = {"deleted": 0, "updated": 0, "merged": 0, "kept": 0, "errors": 0}

        batch_size = 15
        for i in range(0, len(all_memories), batch_size):
            batch = all_memories[i : i + batch_size]

            memories_text = "\n".join(
                f"- ID={m.id} | type={m.type.value} | score={m.importance_score:.2f} "
                f"| cited={m.access_count} | subject={m.subject or ''} | content={m.content}"
                for m in batch
            )

            prompt = self.MEMORY_REVIEW_PROMPT.format(memories_text=memories_text)

            try:
                # Memory review is critical — use main model, not think_lightweight
                response = await self.extractor.brain.think(
                    prompt,
                    system="你是记忆质量审查专家。只输出 JSON 数组。",
                )
                text = (getattr(response, "content", None) or str(response)).strip()

                json_match = re.search(r"\[[\s\S]*\]", text)
                if not json_match:
                    logger.warning(f"[Lifecycle] LLM review batch {i // batch_size}: no JSON output")
                    report["kept"] += len(batch)
                    continue

                decisions = json.loads(json_match.group())
                if not isinstance(decisions, list):
                    report["kept"] += len(batch)
                    continue

                # Guardrail: avoid catastrophic over-pruning from noisy LLM output.
                destructive = 0
                for d in decisions:
                    if not isinstance(d, dict):
                        continue
                    action = str(d.get("action", "keep")).lower()
                    if action in ("delete", "merge"):
                        destructive += 1
                if destructive > max(3, int(len(batch) * 0.4)):
                    logger.warning(
                        "[Lifecycle] Skip risky review batch %s: destructive=%s/%s",
                        i // batch_size,
                        destructive,
                        len(batch),
                    )
                    report["kept"] += len(batch)
                    continue

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
                logger.error(f"[Lifecycle] LLM review batch {i // batch_size} failed: {e}")
                report["errors"] += 1
                report["kept"] += len(batch)

        logger.info(
            f"[Lifecycle] Memory review complete: "
            f"deleted={report['deleted']}, updated={report['updated']}, "
            f"merged={report['merged']}, kept={report['kept']}"
        )
        return report

    # ==================================================================
    # Experience Synthesis (归纳经验记忆为通用原则)
    # ==================================================================

    EXPERIENCE_SYNTHESIS_PROMPT = """你是经验归纳专家。以下是近期积累的具体经验/教训/技能记忆。
请判断其中是否有多条经验可以归纳为一条**更通用的原则**。

## 经验记忆列表

{experience_memories}

## 归纳规则

- 如果 2+ 条经验描述的是同一类问题的不同方面，归纳为一条通用原则
- 归纳后的原则应该比原始经验更抽象、更具指导性
- 不要强行归纳不相关的经验
- 如果没有可归纳的，输出空数组

## 输出格式

[
  {{
    "synthesized_from": ["源记忆ID1", "源记忆ID2"],
    "content": "归纳后的通用原则",
    "subject": "主题",
    "predicate": "经验类型",
    "importance": 0.8-1.0
  }}
]

只输出 JSON 数组。如果没有可归纳的经验，输出 []。"""

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
                prompt, system="你是经验归纳专家。只输出 JSON 数组。",
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
                logger.info(f"[Lifecycle] Synthesized {saved} experience principles from {len(experiences)} memories")
            return saved

        except Exception as e:
            logger.error(f"[Lifecycle] Experience synthesis failed: {e}")
            return 0

    # ==================================================================
    # Refresh MEMORY.md (post-review, no keyword filter needed)
    # ==================================================================

    def refresh_memory_md(self, identity_dir: Path) -> None:
        """刷新 MEMORY.md — LLM 审查后直接选取 top-K（无需关键词过滤）"""
        memories = self.store.query_semantic(min_importance=0.5, limit=100)

        by_type: dict[str, list[SemanticMemory]] = defaultdict(list)
        for mem in memories:
            by_type[mem.type.value].append(mem)

        lines: list[str] = ["# 核心记忆\n"]
        type_labels = {
            "preference": "偏好",
            "rule": "规则",
            "fact": "事实",
            "error": "教训",
            "skill": "技能",
            "experience": "经验",
        }

        total_chars = 0
        max_chars = 1200

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

        _action_words = {"打开", "关闭", "运行", "执行", "安装", "部署", "启动", "停止",
                         "创建", "删除", "修改", "搜索", "下载", "上传", "编译", "测试",
                         "去", "进入", "访问", "登录", "检查", "查看", "发送"}
        user_facts = [
            m for m in user_facts
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
