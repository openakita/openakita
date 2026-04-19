"""
Memory retrieval engine

Multi-way recall + reranking:
- Semantic search (SearchBackend)
- Episode search (entity/tool name association)
- Time-based search (last N days)
- Attachment search (files/media)
- LLM query decomposition (compiler model): natural language → search keywords
- Composite ranking: relevance × recency × importance × access_freq
- Token budget control
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from .types import Attachment, Episode
from .unified_store import UnifiedStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalCandidate:
    """Retrieval candidate with composite score"""

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
    """Memory retrieval engine with multi-way recall and reranking"""

    # Ranking weights
    W_RELEVANCE = 0.40
    W_RECENCY = 0.20
    W_IMPORTANCE = 0.20
    W_ACCESS = 0.20

    MIN_RERANK_SCORE = 0.35

    QUERY_DECOMPOSE_PROMPT = (
        "Extract search keywords from user messages for memory retrieval.\n\n"
        "User message: {query}\n"
        "{context_hint}"
        "\nRules:\n"
        "1. Extract core entities, names, subject terms; remove particles/pronouns\n"
        '2. For files/images/videos, extract descriptive keywords (e.g., "cat", "report") and possible filenames\n'
        "3. Preserve proper nouns and technical terms as-is\n"
        '4. Output JSON: {{"keywords": ["keyword1", "keyword2", ...], '
        '"intent": "search_memory|search_file|general"}}\n'
        "5. At most 6 keywords, each 1-4 words\n"
        "Output JSON only, no other content."
    )

    def __init__(self, store: UnifiedStore, brain=None) -> None:
        self.store = store
        self.brain = brain
        self._decompose_cache: dict[str, dict] = {}
        self._external_sources: list = []
        self._plugin_hooks = None

    def retrieve(
        self,
        query: str,
        recent_messages: list[dict] | None = None,
        active_persona: str | None = None,
        max_tokens: int = 700,
    ) -> str:
        """
        Retrieve and format memory context for injection

        Returns:
            Formatted memory text suitable for system prompt injection
        """
        decomposed = self._decompose_query(query, recent_messages)
        search_keywords = decomposed.get("keywords", [])
        intent = decomposed.get("intent", "general")

        enhanced_query = self._build_enhanced_query(query, recent_messages, search_keywords)

        semantic_results = self._search_semantic(enhanced_query)
        episode_results = self._search_episodes(enhanced_query)
        recent_results = self._search_recent(days=3, query=enhanced_query)
        attachment_results = self._search_attachments(
            query,
            search_keywords,
            intent,
        )

        candidates = self._merge_and_deduplicate(
            semantic_results, episode_results, recent_results, attachment_results
        )

        if self._external_sources:
            external = self._call_external_sources_sync(query)
            candidates.extend(external)

        ranked = self._rerank(candidates, query, active_persona)

        self._dispatch_on_retrieve_sync(query, ranked)

        return self._format_within_budget(ranked, max_tokens)

    def retrieve_candidates(
        self,
        query: str,
        recent_messages: list[dict] | None = None,
        limit: int = 20,
    ) -> list[RetrievalCandidate]:
        """Return raw ranked candidates without formatting."""
        decomposed = self._decompose_query(query, recent_messages)
        search_keywords = decomposed.get("keywords", [])
        intent = decomposed.get("intent", "general")

        enhanced = self._build_enhanced_query(query, recent_messages, search_keywords)

        semantic = self._search_semantic(enhanced)
        episodes = self._search_episodes(enhanced)
        recent = self._search_recent(days=3, query=enhanced)
        attachments = self._search_attachments(query, search_keywords, intent)

        candidates = self._merge_and_deduplicate(semantic, episodes, recent, attachments)

        if self._external_sources:
            external = self._call_external_sources_sync(query)
            candidates.extend(external)

        ranked = self._rerank(candidates, query)
        self._dispatch_on_retrieve_sync(query, ranked)
        return ranked[:limit]

    # ==================================================================
    # External Plugin Sources
    # ==================================================================

    def _call_external_sources_sync(self, query: str) -> list[RetrievalCandidate]:
        """Call external retrieval sources (from plugins) with timeout isolation."""
        results: list[RetrievalCandidate] = []
        for source in self._external_sources:
            source_name = getattr(source, "source_name", "unknown")
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(
                            asyncio.run,
                            asyncio.wait_for(source.retrieve(query, 5), timeout=3.0),
                        )
                        items = future.result(timeout=5.0)
                else:
                    items = asyncio.run(asyncio.wait_for(source.retrieve(query, 5), timeout=3.0))

                for item in items or []:
                    results.append(
                        RetrievalCandidate(
                            memory_id=item.get("id", ""),
                            content=item.get("content", ""),
                            memory_type="external",
                            source_type=f"plugin:{source_name}",
                            relevance=item.get("relevance", 0.5),
                            score=item.get("relevance", 0.5),
                            raw_data=item,
                        )
                    )
            except Exception as e:
                logger.warning(
                    "External retrieval source '%s' failed: %s, skipped",
                    source_name,
                    e,
                )
        return results

    def _dispatch_on_retrieve_sync(self, query: str, candidates: list) -> None:
        """Dispatch on_retrieve hook from sync context."""
        if self._plugin_hooks is None:
            return
        callbacks = self._plugin_hooks.get_hooks("on_retrieve")
        if not callbacks:
            return

        import concurrent.futures

        error_tracker = getattr(self._plugin_hooks, "_error_tracker", None)

        for callback in callbacks:
            plugin_id = getattr(callback, "__plugin_id__", "unknown")
            if error_tracker and error_tracker.is_disabled(plugin_id):
                continue
            timeout = getattr(callback, "__hook_timeout__", 5.0)
            try:
                result = callback(query=query, candidates=candidates)
                if asyncio.iscoroutine(result):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(asyncio.run, result)
                        future.result(timeout=timeout)
            except Exception as e:
                logger.debug(f"on_retrieve hook from '{plugin_id}' error: {e}")
                if error_tracker:
                    error_tracker.record_error(plugin_id, "hook:on_retrieve", str(e))

    # ==================================================================
    # Multi-way Recall
    # ==================================================================

    def _search_semantic(self, query: str, limit: int = 15) -> list[RetrievalCandidate]:
        scored_results = self.store.search_semantic_scored(query, limit=limit)
        now = datetime.now()
        candidates = []
        for mem, raw_score in scored_results:
            if mem.expires_at and mem.expires_at < now:
                continue
            relevance = max(0.0, min(1.0, raw_score))
            candidates.append(
                RetrievalCandidate(
                    memory_id=mem.id,
                    content=mem.to_markdown(),
                    memory_type=mem.type.value,
                    source_type="semantic",
                    relevance=relevance,
                    recency_score=self._compute_recency(mem.updated_at),
                    importance_score=mem.importance_score,
                    access_frequency_score=self._compute_access_score(mem.access_count),
                    raw_data=mem.to_dict(),
                )
            )
        return candidates

    def _search_episodes(self, query: str, limit: int = 5) -> list[RetrievalCandidate]:
        entities = self._extract_query_entities(query)
        episodes: list[Episode] = []

        for entity in entities[:3]:
            found = self.store.search_episodes(entity=entity, limit=3)
            episodes.extend(found)

        candidates = []
        for ep in episodes[:limit]:
            candidates.append(
                RetrievalCandidate(
                    memory_id=ep.id,
                    content=ep.to_markdown(),
                    memory_type="episode",
                    source_type="episode",
                    relevance=0.6,
                    recency_score=self._compute_recency(ep.ended_at),
                    importance_score=ep.importance_score,
                    access_frequency_score=self._compute_access_score(ep.access_count),
                    raw_data=ep.to_dict(),
                )
            )
        return candidates

    def _search_recent(
        self,
        days: int = 3,
        limit: int = 5,
        query: str = "",
    ) -> list[RetrievalCandidate]:
        memories = self.store.query_semantic(min_importance=0.6, limit=limit)
        now = datetime.now()
        query_tokens = set(query.lower().split()) if query else set()
        candidates = []
        for mem in memories:
            if mem.expires_at and mem.expires_at < now:
                continue
            recency = self._compute_recency(mem.updated_at)
            if recency < 0.3:
                continue

            relevance = 0.5
            if query_tokens:
                content_lower = mem.content.lower()
                overlap = sum(1 for t in query_tokens if t in content_lower)
                relevance = 0.2 if overlap == 0 else min(0.7, 0.3 + 0.1 * overlap)

            candidates.append(
                RetrievalCandidate(
                    memory_id=mem.id,
                    content=mem.to_markdown(),
                    memory_type=mem.type.value,
                    source_type="recent",
                    relevance=relevance,
                    recency_score=recency,
                    importance_score=mem.importance_score,
                    access_frequency_score=self._compute_access_score(mem.access_count),
                    raw_data=mem.to_dict(),
                )
            )
        return candidates

    _MEDIA_KEYWORDS = (
        "image",
        "photo",
        "picture",
        "photo",
        "image",
        "picture",
        "video",
        "video",
        "clip",
        "file",
        "document",
        "file",
        "document",
        "doc",
        "pdf",
        "audio",
        "voice",
        "audio",
        "voice",
        "sent to you",
        "for you",
        "last time",
        "that one",
        "that picture",
        "that file",
    )

    def _search_attachments(
        self,
        raw_query: str,
        search_keywords: list[str] | None = None,
        intent: str = "general",
        limit: int = 5,
    ) -> list[RetrievalCandidate]:
        """Search for file/media attachments — triggered when user asks "give me that cat picture".

        Uses keywords from LLM decomposition to search word-by-word, merge and deduplicate.
        """
        has_media_hint = intent == "search_file" or any(
            kw in raw_query.lower() for kw in self._MEDIA_KEYWORDS
        )
        if not has_media_hint:
            return []

        seen: dict[str, Attachment] = {}

        search_terms = self._get_attachment_search_terms(raw_query, search_keywords)
        for term in search_terms:
            try:
                results = self.store.search_attachments(query=term, limit=limit)
                for att in results:
                    if att.id not in seen:
                        seen[att.id] = att
            except Exception:
                continue

        candidates = []
        for att in list(seen.values())[:limit]:
            desc_parts = []
            direction_label = "User-sent" if att.direction.value == "inbound" else "AI-generated"
            desc_parts.append(f"[{direction_label} file] {att.filename}")
            if att.description:
                desc_parts.append(att.description)
            if att.transcription:
                desc_parts.append(f"(Transcription: {att.transcription[:100]})")
            if att.local_path:
                desc_parts.append(f"Path: {att.local_path}")
            elif att.url:
                desc_parts.append(f"URL: {att.url}")
            content = " | ".join(desc_parts)

            candidates.append(
                RetrievalCandidate(
                    memory_id=f"attach:{att.id}",
                    content=content,
                    memory_type="attachment",
                    source_type="attachment",
                    relevance=0.85,
                    recency_score=self._compute_recency(att.created_at),
                    importance_score=0.7,
                    access_frequency_score=0.3,
                    raw_data=att.to_dict(),
                )
            )
        return candidates

    @staticmethod
    def _get_attachment_search_terms(
        raw_query: str, search_keywords: list[str] | None
    ) -> list[str]:
        """Filter keywords suitable for attachment search from decomposed keywords (filter out media type words)."""
        _STOP_WORDS = {
            "image",
            "photo",
            "picture",
            "photo",
            "image",
            "picture",
            "video",
            "video",
            "clip",
            "file",
            "document",
            "file",
            "document",
            "doc",
            "pdf",
            "audio",
            "voice",
            "audio",
            "voice",
            "sent to you",
            "for you",
            "last time",
            "that one",
            "that picture",
            "that file",
            "give me",
            "find",
            "briefly",
            "look",
            "of",
            "ed",
            "ok",
            "huh",
            "where",
            "here",
            "how",
        }

        def _is_valid(token: str) -> bool:
            if not token or token.lower() in _STOP_WORDS:
                return False
            has_cjk = any("\u4e00" <= c <= "\u9fff" for c in token)
            return len(token) >= 1 if has_cjk else len(token) >= 2

        terms: list[str] = []
        if search_keywords:
            for kw in search_keywords:
                kw_clean = kw.strip()
                if _is_valid(kw_clean):
                    terms.append(kw_clean)

        if not terms:
            for token in re.split(r"[\s,，。、!！?？:：;；\"'()（）【】]+", raw_query):
                token = token.strip()
                if _is_valid(token):
                    terms.append(token)
            terms = terms[:4]

        return terms if terms else [raw_query]

    # ==================================================================
    # Query Decomposition (LLM-powered)
    # ==================================================================

    def _decompose_query(
        self,
        query: str,
        recent_messages: list[dict] | None = None,
    ) -> dict:
        """Use LLM (compiler model) to decompose natural language into search keywords.

        Returns {"keywords": [...], "intent": "search_memory|search_file|general"}
        Degrades to rule-based extraction when no brain is available.
        """
        if not query or len(query.strip()) < 3:
            return {"keywords": [query.strip()], "intent": "general"}

        cache_key = query[:200]
        if cache_key in self._decompose_cache:
            return self._decompose_cache[cache_key]

        # Prevent unbounded cache growth
        if len(self._decompose_cache) > 500:
            # Clear half (FIFO approximation: dict maintains insertion order)
            keys = list(self._decompose_cache.keys())
            for k in keys[:250]:
                del self._decompose_cache[k]

        if self.brain:
            result = self._decompose_with_llm(query, recent_messages)
            if result:
                self._decompose_cache[cache_key] = result
                return result

        result = self._decompose_with_rules(query)
        self._decompose_cache[cache_key] = result
        return result

    def _decompose_with_llm(
        self,
        query: str,
        recent_messages: list[dict] | None = None,
    ) -> dict | None:
        """Call think_lightweight (compiler model) for query decomposition."""
        from openakita.core.tool_executor import smart_truncate as _st

        context_hint = ""
        if recent_messages:
            recent_texts = []
            for msg in recent_messages[-2:]:
                c = msg.get("content", "")
                if c and isinstance(c, str):
                    hint, _ = _st(c, 300, save_full=False, label="retrieval_hint")
                    recent_texts.append(f"[{msg.get('role', '?')}]: {hint}")
            if recent_texts:
                context_hint = f"Recent conversation:\n{''.join(recent_texts)}\n"

        query_trunc, _ = _st(query, 500, save_full=False, label="retrieval_query")
        prompt = self.QUERY_DECOMPOSE_PROMPT.format(
            query=query_trunc,
            context_hint=context_hint,
        )

        try:
            think_lw = getattr(self.brain, "think_lightweight", None)
            think_fn = (
                think_lw
                if (think_lw and callable(think_lw))
                else getattr(self.brain, "think", None)
            )
            if not think_fn:
                return None

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, think_fn(prompt, system="Output JSON only"))
                    response = future.result(timeout=10)
            else:
                response = asyncio.run(think_fn(prompt, system="Output JSON only"))

            text = (getattr(response, "content", None) or str(response)).strip()

            json_match = re.search(r"\{[\s\S]*\}", text)
            if not json_match:
                return None

            data = json.loads(json_match.group())
            keywords = data.get("keywords", [])
            intent = data.get("intent", "general")

            if not isinstance(keywords, list) or not keywords:
                return None

            keywords = [str(k).strip() for k in keywords if str(k).strip()][:6]
            if intent not in ("search_memory", "search_file", "general"):
                intent = "general"

            logger.info(
                f'[Retrieval] LLM decompose: "{query[:50]}" → keywords={keywords}, intent={intent}'
            )
            return {"keywords": keywords, "intent": intent}

        except Exception as e:
            logger.debug(f"[Retrieval] LLM decompose failed, falling back to rules: {e}")
            return None

    @staticmethod
    def _decompose_with_rules(query: str) -> dict:
        """Rule-based fallback: regex + stopword filtering."""
        _STOP = {
            "of",
            "ed",
            "q",
            "ok",
            "huh",
            "uh",
            "oh",
            "mm",
            "is",
            "in",
            "have",
            "and",
            "with",
            "or",
            "but",
            "not",
            "also",
            "all",
            "just",
            "still",
            "want",
            "will",
            "can",
            "could",
            "should",
            "this one",
            "that one",
            "what",
            "how",
            "where",
            "when",
            "who",
            "which",
            "do",
            "does",
            "did",
            "will",
            "would",
            "can",
            "could",
            "should",
            "give me",
            "help me",
            "please",
            "look",
            "find",
            "tell me",
        }

        keywords = []
        intent = "general"

        _FILE_HINTS = {
            "image",
            "photo",
            "picture",
            "file",
            "document",
            "video",
            "audio",
            "voice",
            "photo",
            "image",
            "file",
            "video",
            "audio",
            "document",
        }
        if any(h in query.lower() for h in _FILE_HINTS):
            intent = "search_file"

        for m in re.finditer(r'[A-Za-z]:[\\\/][^\s"\']+', query):
            keywords.append(m.group(0))
        for m in re.finditer(
            r"[\w.-]+\.(?:py|js|ts|md|json|yaml|toml|jpg|png|pdf|docx|mp4|mp3)\b", query
        ):
            keywords.append(m.group(0))

        for token in re.split(r"[\s,，。、!！?？:：;；\"'()（）【】]+", query):
            token = token.strip()
            if token and token.lower() not in _STOP and len(token) >= 2:
                keywords.append(token)

        seen: set[str] = set()
        unique_kw: list[str] = []
        for kw in keywords:
            low = kw.lower()
            if low not in seen:
                seen.add(low)
                unique_kw.append(kw)
        keywords = unique_kw[:6]

        if not keywords:
            keywords = [query.strip()]

        return {"keywords": keywords, "intent": intent}

    # ==================================================================
    # Enhanced Query
    # ==================================================================

    def _build_enhanced_query(
        self,
        query: str,
        recent_messages: list[dict] | None = None,
        search_keywords: list[str] | None = None,
    ) -> str:
        """Build enhanced query: original query + LLM decomposed keywords + recent context."""
        parts = [query]
        if search_keywords:
            for kw in search_keywords:
                if kw not in query:
                    parts.append(kw)
        if recent_messages:
            for msg in recent_messages[-3:]:
                content = msg.get("content", "")
                if content and isinstance(content, str):
                    parts.append(content[:100])
        return " ".join(parts)

    def _extract_query_entities(self, query: str) -> list[str]:
        entities = []
        for m in re.finditer(r'[A-Za-z]:[\\\/][^\s"\']+', query):
            entities.append(m.group(0))
        for m in re.finditer(r"[\w-]+\.(?:py|js|ts|md|json|yaml|toml)\b", query):
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

    _ACTION_WORDS = frozenset(
        {
            "open",
            "close",
            "run",
            "execute",
            "install",
            "deploy",
            "start",
            "stop",
            "create",
            "delete",
            "modify",
            "search",
            "download",
            "upload",
            "go",
            "enter",
            "visit",
        }
    )

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
            if c.memory_type == "fact" and any(w in c.content[:30] for w in self._ACTION_WORDS):
                c.score *= 0.3

        ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
        # 冷启动豁免：刚写入 1 小时内的记忆（recency_score >= 0.99 ≈ 1 小时），
        # 即便综合分低于 MIN_RERANK_SCORE 也保留，避免新加事实被一刀切。
        return [
            c
            for c in ranked
            if c.score >= self.MIN_RERANK_SCORE or c.recency_score >= 0.99
        ]

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
