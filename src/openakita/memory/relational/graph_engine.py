"""GraphEngine — multi-dimensional traversal and retrieval (MAGMA-inspired)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from .store import RelationalMemoryStore
from .types import (
    Dimension,
    MemoryNode,
    RetrievalResult,
)

logger = logging.getLogger(__name__)

_TIME_PATTERNS_ZH = re.compile(
    r"(昨天|前天|今天|上周|上个月|最近|刚才|之前|以前|上次|历史|时间线|过程|经过)", re.IGNORECASE
)
_TIME_PATTERNS_EN = re.compile(
    r"\b(yesterday|last week|last month|recently|before|previously|history|timeline)\b",
    re.IGNORECASE,
)
_CAUSAL_PATTERNS_ZH = re.compile(r"(为什么|原因|导致|根因|怎么回事|因为|造成)", re.IGNORECASE)
_CAUSAL_PATTERNS_EN = re.compile(
    r"\b(why|because|cause|reason|root cause|led to|resulted)\b", re.IGNORECASE
)
_ENTITY_PATTERNS_ZH = re.compile(r"(关于.+的|.+的所有|.+的完整记录|.+的历史)", re.IGNORECASE)


class GraphEngine:
    """Multi-dimensional graph traversal engine for memory retrieval."""

    def __init__(self, store: RelationalMemoryStore) -> None:
        self.store = store

    async def query(
        self,
        query: str,
        dimensions: list[Dimension] | None = None,
        max_hops: int = 2,
        limit: int = 10,
        token_budget: int = 1500,
    ) -> list[RetrievalResult]:
        """Query the memory graph across multiple dimensions.

        1. Parse query into dimension cues
        2. Find seed nodes via FTS / entity / time search
        3. Traverse along each dimension's edges
        4. Score by multi-dimensional relevance
        """
        cues = self._parse_query_cues(query)
        active_dims = dimensions or cues.get("active_dimensions", list(Dimension))

        # Find seed nodes
        seeds = self._find_seeds(query, cues, limit=max(limit * 2, 30))
        if not seeds:
            return []

        # Traverse from seeds
        visited: dict[str, float] = {}
        for seed in seeds:
            visited[seed.id] = seed.importance

        for dim in active_dims:
            dim_value = dim.value if isinstance(dim, Dimension) else dim
            for seed in seeds:
                reachable = self.store.query_reachable(seed.id, dim_value, max_hops)
                for r in reachable:
                    tid = r["target_id"]
                    hop_decay = 1.0 / (1.0 + r["hops"] * 0.3)
                    score = r["min_weight"] * hop_decay
                    if tid in visited:
                        visited[tid] = max(visited[tid], score)
                    else:
                        visited[tid] = score

        # Fetch and score nodes
        results: list[RetrievalResult] = []
        for node_id, base_score in visited.items():
            node = self.store.get_node(node_id)
            if not node:
                continue
            multi_score = self._score_node(node, cues, base_score)
            dims_matched = self._matched_dimensions(node, cues)
            results.append(
                RetrievalResult(
                    node=node,
                    score=multi_score,
                    dimensions_matched=dims_matched,
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)

        # Token budget trimming
        trimmed: list[RetrievalResult] = []
        used_tokens = 0
        for r in results:
            est_tokens = len(r.node.content) // 3 + 20
            if used_tokens + est_tokens > token_budget and trimmed:
                break
            trimmed.append(r)
            used_tokens += est_tokens
            if len(trimmed) >= limit:
                break

        for r in trimmed:
            self.store.increment_access(r.node.id)

        return trimmed

    def format_results(self, results: list[RetrievalResult]) -> str:
        """Format retrieval results into a prompt-injectable string."""
        if not results:
            return ""
        parts: list[str] = []
        for i, r in enumerate(results, 1):
            node = r.node
            dims = ", ".join(d.value for d in r.dimensions_matched)
            ents = ", ".join(e.name for e in node.entities[:3])
            header = f"[{node.node_type.value.upper()}] "
            if ents:
                header += f"({ents}) "
            header += f"[{dims}] score={r.score:.2f}"
            time_str = node.occurred_at.strftime("%m/%d %H:%M") if node.occurred_at else ""
            parts.append(f"{i}. {header}\n   {time_str} {node.content[:200]}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Query parsing
    # ------------------------------------------------------------------

    def _parse_query_cues(self, query: str) -> dict:
        cues: dict = {
            "active_dimensions": [],
            "entities": [],
            "has_temporal": False,
            "has_causal": False,
            "time_ref": None,
            "keywords": [],
        }

        if _TIME_PATTERNS_ZH.search(query) or _TIME_PATTERNS_EN.search(query):
            cues["has_temporal"] = True
            cues["active_dimensions"].append(Dimension.TEMPORAL)

        if _CAUSAL_PATTERNS_ZH.search(query) or _CAUSAL_PATTERNS_EN.search(query):
            cues["has_causal"] = True
            cues["active_dimensions"].append(Dimension.CAUSAL)

        if _ENTITY_PATTERNS_ZH.search(query):
            cues["active_dimensions"].append(Dimension.ENTITY)

        if not cues["active_dimensions"]:
            cues["active_dimensions"] = [Dimension.ENTITY, Dimension.TEMPORAL]

        # Extract potential entity names (CJK chunks and English words > 3 chars)
        words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z_]{3,}", query)
        stop_words = {
            "what",
            "when",
            "where",
            "why",
            "how",
            "the",
            "and",
            "for",
            "about",
            "with",
            "from",
            "that",
            "this",
            "have",
            "has",
        }
        cjk_stop_prefixes = [
            "什么时候",
            "有没有",
            "能不能",
            "是不是",
            "为什么",
            "怎么样",
            "什么",
            "怎么",
            "哪里",
            "关于",
            "所有",
            "如何",
        ]
        cjk_particles = set("的了在是和与把被让给对从向往到过着")
        keywords: list[str] = []
        seen_kw: set[str] = set()
        for w in words:
            if w.lower() in stop_words:
                continue
            # CJK: strip known stop-word prefixes, then generate bigrams from tail only
            prefix_stripped = False
            for sp in cjk_stop_prefixes:
                if w.startswith(sp) and len(w) > len(sp) + 1:
                    tail = w[len(sp) :]
                    if tail not in seen_kw and len(tail) >= 2:
                        keywords.append(tail)
                        seen_kw.add(tail)
                    prefix_stripped = True
                    w = tail
                    break
            if prefix_stripped and len(w) < 4:
                continue
            # CJK long chunks: generate meaningful bigrams (skip particles)
            if len(w) >= 4 and ord(w[0]) >= 0x4E00:
                for i in range(len(w) - 1):
                    bigram = w[i : i + 2]
                    if bigram[0] not in cjk_particles and bigram[1] not in cjk_particles:
                        if bigram not in seen_kw:
                            keywords.append(bigram)
                            seen_kw.add(bigram)
            elif w not in seen_kw and len(w) >= 2:
                keywords.append(w)
                seen_kw.add(w)

        cues["keywords"] = keywords
        return cues

    def _find_seeds(self, query: str, cues: dict, limit: int = 30) -> list[MemoryNode]:
        seeds: list[MemoryNode] = []
        seen: set[str] = set()

        # FTS / LIKE search (full query)
        fts_results = self.store.search_fts(query, limit=limit)
        for n in fts_results:
            if n.id not in seen:
                seen.add(n.id)
                seeds.append(n)

        # Entity index + keyword LIKE search
        for kw in cues.get("keywords", []):
            if len(seeds) >= limit:
                break
            for n in self.store.search_by_entity(kw, limit=10):
                if n.id not in seen:
                    seen.add(n.id)
                    seeds.append(n)
            for n in self.store.search_like(kw, limit=10):
                if n.id not in seen:
                    seen.add(n.id)
                    seeds.append(n)

        # Time-based search
        if cues.get("has_temporal"):
            now = datetime.now()
            start = now - timedelta(days=7)
            for n in self.store.search_by_time_range(start, now, limit=20):
                if n.id not in seen:
                    seen.add(n.id)
                    seeds.append(n)

        return seeds[:limit]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_node(self, node: MemoryNode, cues: dict, base_score: float) -> float:
        score = base_score * 0.4

        # Importance boost
        score += node.importance * 0.3

        # Recency boost
        age_days = max(0.0, (datetime.now() - node.occurred_at).total_seconds() / 86400)
        recency = 1.0 / (1.0 + age_days * 0.1)
        score += recency * 0.15

        # Access frequency boost (diminishing returns)
        access_boost = min(0.15, node.access_count * 0.02)
        score += access_boost

        # Keyword overlap
        keywords = cues.get("keywords", [])
        if keywords:
            content_lower = node.content.lower()
            hits = sum(1 for kw in keywords if kw.lower() in content_lower)
            score += min(0.2, hits * 0.05)

        return min(1.0, score)

    def _matched_dimensions(self, node: MemoryNode, cues: dict) -> list[Dimension]:
        matched: list[Dimension] = []
        if cues.get("has_temporal"):
            matched.append(Dimension.TEMPORAL)
        if cues.get("has_causal"):
            matched.append(Dimension.CAUSAL)
        if node.entities:
            matched.append(Dimension.ENTITY)
        if node.action_category:
            matched.append(Dimension.ACTION)
        if node.session_id or node.project:
            matched.append(Dimension.CONTEXT)
        return matched or [Dimension.ENTITY]
