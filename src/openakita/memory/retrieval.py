"""
记忆检索引擎

多路召回 + 重排序:
- 语义搜索 (SearchBackend)
- 情节搜索 (实体/工具名关联)
- 时间搜索 (最近 N 天)
- 综合排序: relevance × recency × importance × access_freq
- 上下文感知: 构建增强查询
- Token 预算控制
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

from .types import Attachment, SemanticMemory, Episode
from .unified_store import UnifiedStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalCandidate:
    """检索候选项, 带综合评分"""
    memory_id: str = ""
    content: str = ""
    memory_type: str = ""
    source_type: str = ""  # "semantic" / "episode" / "recent" / "attachment"

    relevance: float = 0.0
    recency_score: float = 0.0
    importance_score: float = 0.0
    access_frequency_score: float = 0.0

    score: float = 0.0

    raw_data: dict = field(default_factory=dict)


class RetrievalEngine:
    """多路召回 + 重排序的记忆检索引擎"""

    # 排序权重
    W_RELEVANCE = 0.4
    W_RECENCY = 0.25
    W_IMPORTANCE = 0.2
    W_ACCESS = 0.15

    def __init__(self, store: UnifiedStore) -> None:
        self.store = store

    def retrieve(
        self,
        query: str,
        recent_messages: list[dict] | None = None,
        active_persona: str | None = None,
        max_tokens: int = 700,
    ) -> str:
        """
        检索并格式化要注入的记忆上下文

        Returns:
            格式化的记忆文本, 适合注入 system prompt
        """
        enhanced_query = self._build_enhanced_query(query, recent_messages)

        semantic_results = self._search_semantic(enhanced_query)
        episode_results = self._search_episodes(enhanced_query)
        recent_results = self._search_recent(days=3)
        attachment_results = self._search_attachments(enhanced_query)

        candidates = self._merge_and_deduplicate(
            semantic_results, episode_results, recent_results, attachment_results
        )

        ranked = self._rerank(candidates, query, active_persona)

        return self._format_within_budget(ranked, max_tokens)

    def retrieve_candidates(
        self,
        query: str,
        recent_messages: list[dict] | None = None,
        limit: int = 20,
    ) -> list[RetrievalCandidate]:
        """Return raw ranked candidates without formatting."""
        enhanced = self._build_enhanced_query(query, recent_messages)

        semantic = self._search_semantic(enhanced)
        episodes = self._search_episodes(enhanced)
        recent = self._search_recent(days=3)
        attachments = self._search_attachments(enhanced)

        candidates = self._merge_and_deduplicate(semantic, episodes, recent, attachments)
        ranked = self._rerank(candidates, query)
        return ranked[:limit]

    # ==================================================================
    # Multi-way Recall
    # ==================================================================

    def _search_semantic(self, query: str, limit: int = 15) -> list[RetrievalCandidate]:
        memories = self.store.search_semantic(query, limit=limit)
        candidates = []
        for mem in memories:
            candidates.append(RetrievalCandidate(
                memory_id=mem.id,
                content=mem.to_markdown(),
                memory_type=mem.type.value,
                source_type="semantic",
                relevance=0.8,
                recency_score=self._compute_recency(mem.updated_at),
                importance_score=mem.importance_score,
                access_frequency_score=self._compute_access_score(mem.access_count),
                raw_data=mem.to_dict(),
            ))
        return candidates

    def _search_episodes(self, query: str, limit: int = 5) -> list[RetrievalCandidate]:
        entities = self._extract_query_entities(query)
        episodes: list[Episode] = []

        for entity in entities[:3]:
            found = self.store.search_episodes(entity=entity, limit=3)
            episodes.extend(found)

        recent_eps = self.store.get_recent_episodes(days=7, limit=5)
        seen_ids = {e.id for e in episodes}
        for ep in recent_eps:
            if ep.id not in seen_ids:
                episodes.append(ep)
                seen_ids.add(ep.id)

        candidates = []
        for ep in episodes[:limit]:
            candidates.append(RetrievalCandidate(
                memory_id=ep.id,
                content=ep.to_markdown(),
                memory_type="episode",
                source_type="episode",
                relevance=0.6,
                recency_score=self._compute_recency(ep.ended_at),
                importance_score=ep.importance_score,
                access_frequency_score=self._compute_access_score(ep.access_count),
                raw_data=ep.to_dict(),
            ))
        return candidates

    def _search_recent(self, days: int = 3, limit: int = 5) -> list[RetrievalCandidate]:
        memories = self.store.query_semantic(
            min_importance=0.6, limit=limit
        )
        candidates = []
        for mem in memories:
            recency = self._compute_recency(mem.updated_at)
            if recency < 0.3:
                continue
            candidates.append(RetrievalCandidate(
                memory_id=mem.id,
                content=mem.to_markdown(),
                memory_type=mem.type.value,
                source_type="recent",
                relevance=0.5,
                recency_score=recency,
                importance_score=mem.importance_score,
                access_frequency_score=self._compute_access_score(mem.access_count),
                raw_data=mem.to_dict(),
            ))
        return candidates

    def _search_attachments(self, query: str, limit: int = 5) -> list[RetrievalCandidate]:
        """搜索文件/媒体附件 — 用户问"给我那张猫图"时触发"""
        _MEDIA_KEYWORDS = (
            "图片", "照片", "图", "photo", "image", "picture",
            "视频", "video", "clip",
            "文件", "文档", "file", "document", "doc", "pdf",
            "音频", "语音", "audio", "voice",
            "发给你的", "给你的", "上次的", "那个", "那张", "那份",
        )
        has_media_hint = any(kw in query.lower() for kw in _MEDIA_KEYWORDS)
        if not has_media_hint:
            return []

        try:
            results = self.store.search_attachments(query=query, limit=limit)
        except Exception:
            return []

        candidates = []
        for att in results:
            desc_parts = []
            direction_label = "用户发送" if att.direction.value == "inbound" else "AI生成"
            desc_parts.append(f"[{direction_label}的文件] {att.filename}")
            if att.description:
                desc_parts.append(att.description)
            if att.transcription:
                desc_parts.append(f"(转写: {att.transcription[:100]})")
            if att.local_path:
                desc_parts.append(f"路径: {att.local_path}")
            elif att.url:
                desc_parts.append(f"URL: {att.url}")
            content = " | ".join(desc_parts)

            candidates.append(RetrievalCandidate(
                memory_id=f"attach:{att.id}",
                content=content,
                memory_type="attachment",
                source_type="attachment",
                relevance=0.85 if has_media_hint else 0.5,
                recency_score=self._compute_recency(att.created_at),
                importance_score=0.7,
                access_frequency_score=0.3,
                raw_data=att.to_dict(),
            ))
        return candidates

    # ==================================================================
    # Enhanced Query
    # ==================================================================

    def _build_enhanced_query(
        self, query: str, recent_messages: list[dict] | None = None
    ) -> str:
        parts = [query]
        if recent_messages:
            for msg in recent_messages[-3:]:
                content = msg.get("content", "")
                if content and isinstance(content, str):
                    parts.append(content[:100])
        return " ".join(parts)

    def _extract_query_entities(self, query: str) -> list[str]:
        import re
        entities = []
        for m in re.finditer(r'[A-Za-z]:[\\\/][^\s"\']+', query):
            entities.append(m.group(0))
        for m in re.finditer(r'[\w-]+\.(?:py|js|ts|md|json|yaml|toml)\b', query):
            entities.append(m.group(0))
        words = [w for w in query.split() if len(w) > 2]
        entities.extend(words[:5])
        return entities

    # ==================================================================
    # Merge & Dedup
    # ==================================================================

    def _merge_and_deduplicate(
        self, *candidate_lists: list[RetrievalCandidate]
    ) -> list[RetrievalCandidate]:
        seen: dict[str, RetrievalCandidate] = {}
        for candidates in candidate_lists:
            for c in candidates:
                if c.memory_id in seen:
                    existing = seen[c.memory_id]
                    if c.relevance > existing.relevance:
                        seen[c.memory_id] = c
                else:
                    seen[c.memory_id] = c
        return list(seen.values())

    # ==================================================================
    # Reranking
    # ==================================================================

    def _rerank(
        self,
        candidates: list[RetrievalCandidate],
        query: str,
        persona: str | None = None,
    ) -> list[RetrievalCandidate]:
        for c in candidates:
            c.score = (
                c.relevance * self.W_RELEVANCE
                + c.recency_score * self.W_RECENCY
                + c.importance_score * self.W_IMPORTANCE
                + c.access_frequency_score * self.W_ACCESS
            )
            if persona and persona in ("tech_expert", "jarvis"):
                if c.memory_type in ("skill", "error"):
                    c.score *= 1.2

        return sorted(candidates, key=lambda c: c.score, reverse=True)

    # ==================================================================
    # Scoring Helpers
    # ==================================================================

    @staticmethod
    def _compute_recency(dt: datetime) -> float:
        """Compute recency score: 1.0 for now, decays over days."""
        if not dt:
            return 0.0
        try:
            delta = (datetime.now() - dt).total_seconds()
            days = max(0, delta / 86400)
            return math.exp(-0.1 * days)
        except Exception:
            return 0.0

    @staticmethod
    def _compute_access_score(access_count: int) -> float:
        """Logarithmic access frequency score."""
        return min(1.0, math.log1p(access_count) / 5.0)

    # ==================================================================
    # Formatting
    # ==================================================================

    def _format_within_budget(
        self,
        candidates: list[RetrievalCandidate],
        max_tokens: int,
    ) -> str:
        if not candidates:
            return ""

        lines: list[str] = []
        token_est = 0
        chars_per_token = 2.5

        for c in candidates:
            line = c.content
            line_tokens = len(line) / chars_per_token
            if token_est + line_tokens > max_tokens:
                break
            lines.append(line)
            token_est += line_tokens

        return "\n".join(lines)
