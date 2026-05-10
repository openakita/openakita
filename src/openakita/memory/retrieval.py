"""
记忆检索引擎

多路召回 + 重排序:
- 语义搜索 (SearchBackend)
- 情节搜索 (实体/工具名关联)
- 时间搜索 (最近 N 天)
- 附件搜索 (文件/媒体)
- LLM 查询拆解 (compiler model): 自然语言 → 搜索关键词
- 综合排序: relevance × recency × importance × access_freq
- Token 预算控制
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
    W_RELEVANCE = 0.40
    W_RECENCY = 0.20
    W_IMPORTANCE = 0.20
    W_ACCESS = 0.20

    MIN_RERANK_SCORE = 0.35

    QUERY_DECOMPOSE_PROMPT = (
        "从用户消息中提取用于记忆检索的搜索关键词。\n\n"
        "用户消息: {query}\n"
        "{context_hint}"
        "\n规则:\n"
        "1. 提取核心实体、名称、主题词，去掉语气词/助词/代词\n"
        '2. 如果涉及文件/图片/视频，提取描述性关键词（如"猫""报告"）和可能的文件名\n'
        "3. 保留专有名词、技术术语原样\n"
        '4. 输出 JSON: {{"keywords": ["关键词1", "关键词2", ...], '
        '"intent": "search_memory|search_file|general"}}\n'
        "5. keywords 最多 6 个，每个 1-4 个词\n"
        "只输出 JSON，不要其他内容。"
    )

    def __init__(self, store: UnifiedStore, brain=None) -> None:
        self.store = store
        self.brain = brain
        self._decompose_cache: dict[str, dict] = {}
        self._external_sources: list = []
        self._plugin_hooks = None
        self._scope_pairs: list[tuple[str, str, str, str]] = [
            ("user", "", "default", "default")
        ]

    def set_scope_context(self, scope_pairs: list[tuple] | None = None) -> None:
        """Set the visible memory scopes for this retrieval engine."""
        pairs = []
        for entry in scope_pairs or [("user", "", "default", "default")]:
            if len(entry) >= 4:
                scope, owner, user_id, workspace_id = entry[:4]
            else:
                scope, owner = entry[:2]
                user_id, workspace_id = "default", "default"
            norm_scope = (scope or "global").strip() or "global"
            if norm_scope == "global":
                norm_scope = "user"
            norm_owner = (owner or "").strip()
            norm_user = (user_id or "default").strip() or "default"
            norm_workspace = (workspace_id or "default").strip() or "default"
            pair = (norm_scope, norm_owner, norm_user, norm_workspace)
            if pair not in pairs:
                pairs.append(pair)
        self._scope_pairs = pairs or [("user", "", "default", "default")]

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
        now = datetime.now()
        candidates = []
        seen: set[str] = set()
        for scope, scope_owner, user_id, workspace_id in self._scope_pairs:
            scored_results = self.store.search_semantic_scored(
                query,
                limit=limit,
                scope=scope,
                scope_owner=scope_owner,
                user_id=user_id,
                workspace_id=workspace_id,
            )
            for mem, raw_score in scored_results:
                if mem.id in seen:
                    continue
                if mem.expires_at and mem.expires_at < now:
                    continue
                seen.add(mem.id)
                relevance = max(0.0, min(1.0, raw_score))
                candidates.append(
                    RetrievalCandidate(
                        memory_id=mem.id,
                        content=mem.to_markdown(),
                        memory_type=mem.type.value,
                        source_type=f"semantic:{scope}",
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
        session_ids = [owner for scope, owner, _user, _workspace in self._scope_pairs if scope == "session" and owner]

        for entity in entities[:3]:
            if session_ids:
                for session_id in session_ids:
                    found = self.store.search_episodes(
                        entity=entity,
                        session_id=session_id,
                        limit=3,
                    )
                    episodes.extend(found)
            else:
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
        now = datetime.now()
        query_tokens = set(query.lower().split()) if query else set()
        candidates = []
        seen: set[str] = set()
        for scope, scope_owner, user_id, workspace_id in self._scope_pairs:
            memories = self.store.query_semantic(
                min_importance=0.6,
                scope=scope,
                scope_owner=scope_owner,
                user_id=user_id,
                workspace_id=workspace_id,
                limit=limit,
            )
            for mem in memories:
                if mem.id in seen:
                    continue
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

                seen.add(mem.id)
                candidates.append(
                    RetrievalCandidate(
                        memory_id=mem.id,
                        content=mem.to_markdown(),
                        memory_type=mem.type.value,
                        source_type=f"recent:{scope}",
                        relevance=relevance,
                        recency_score=recency,
                        importance_score=mem.importance_score,
                        access_frequency_score=self._compute_access_score(mem.access_count),
                        raw_data=mem.to_dict(),
                    )
                )
        return candidates

    _MEDIA_KEYWORDS = (
        "图片",
        "照片",
        "图",
        "photo",
        "image",
        "picture",
        "视频",
        "video",
        "clip",
        "文件",
        "文档",
        "file",
        "document",
        "doc",
        "pdf",
        "音频",
        "语音",
        "audio",
        "voice",
        "发给你的",
        "给你的",
        "上次的",
        "那个",
        "那张",
        "那份",
    )

    def _search_attachments(
        self,
        raw_query: str,
        search_keywords: list[str] | None = None,
        intent: str = "general",
        limit: int = 5,
    ) -> list[RetrievalCandidate]:
        """搜索文件/媒体附件 — 用户问"给我那张猫图"时触发.

        使用 LLM 拆解后的关键词逐词搜索，合并去重。
        """
        has_media_hint = intent == "search_file" or any(
            kw in raw_query.lower() for kw in self._MEDIA_KEYWORDS
        )
        if not has_media_hint:
            return []

        seen: dict[str, Attachment] = {}

        search_terms = self._get_attachment_search_terms(raw_query, search_keywords)
        session_id = ""
        for scope, owner, _user, _workspace in self._scope_pairs:
            if scope == "session" and owner:
                session_id = owner
                break

        for term in search_terms:
            try:
                results = self.store.search_attachments(
                    query=term,
                    session_id=session_id or None,
                    limit=limit,
                )
                for att in results:
                    if att.id not in seen:
                        seen[att.id] = att
            except Exception:
                continue

        candidates = []
        for att in list(seen.values())[:limit]:
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
        """从拆解关键词中筛选适合附件搜索的词（过滤掉媒体类型词本身）."""
        _STOP_WORDS = {
            "图片",
            "照片",
            "图",
            "photo",
            "image",
            "picture",
            "视频",
            "video",
            "clip",
            "文件",
            "文档",
            "file",
            "document",
            "doc",
            "pdf",
            "音频",
            "语音",
            "audio",
            "voice",
            "发给你的",
            "给你的",
            "上次的",
            "那个",
            "那张",
            "那份",
            "给我",
            "找到",
            "一下",
            "看看",
            "的",
            "了",
            "吧",
            "呢",
            "在哪",
            "哪里",
            "怎么",
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
        """用 LLM (compiler model) 把自然语言拆解为搜索关键词.

        返回 {"keywords": [...], "intent": "search_memory|search_file|general"}
        无 brain 时降级为规则提取。
        """
        if not query or len(query.strip()) < 3:
            return {"keywords": [query.strip()], "intent": "general"}

        cache_key = query[:200]
        if cache_key in self._decompose_cache:
            return self._decompose_cache[cache_key]

        # 防止缓存无限增长
        if len(self._decompose_cache) > 500:
            # 清掉一半（FIFO 近似：dict 保持插入顺序）
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
        """调用 think_lightweight (compiler model) 做查询拆解."""
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
                context_hint = f"近期对话:\n{''.join(recent_texts)}\n"

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
                    future = pool.submit(asyncio.run, think_fn(prompt, system="只输出JSON"))
                    response = future.result(timeout=10)
            else:
                response = asyncio.run(think_fn(prompt, system="只输出JSON"))

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
        """规则降级: 正则 + 停用词过滤."""
        _STOP = {
            "的",
            "了",
            "吗",
            "吧",
            "呢",
            "啊",
            "哦",
            "嗯",
            "是",
            "在",
            "有",
            "和",
            "与",
            "或",
            "但",
            "不",
            "也",
            "都",
            "就",
            "还",
            "要",
            "会",
            "能",
            "可以",
            "这个",
            "那个",
            "什么",
            "怎么",
            "为什么",
            "哪个",
            "哪里",
            "多少",
            "一下",
            "一些",
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "this",
            "that",
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
            "给我",
            "帮我",
            "请",
            "看看",
            "找到",
            "告诉我",
        }

        keywords = []
        intent = "general"

        _FILE_HINTS = {
            "图片",
            "照片",
            "图",
            "文件",
            "文档",
            "视频",
            "音频",
            "语音",
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
        """构建增强查询: 原始 query + LLM 拆解关键词 + 近期上下文."""
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
            "去",
            "进入",
            "访问",
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

        # Fix-9：先剔除"自动复盘 / 元监控"类经验噪声（这些条目对 LLM
        # 当前任务推理没价值，但会占用 memory_budget）。
        candidates = self._strip_experience_noise(candidates)

        # Fix-8：注入前对 ``fact`` 类型按 ``(subject, predicate)`` 去重，
        # 同主题谓词只保留第一条（candidates 已经按综合得分降序，因此
        # 第一条就是 score 最高且最新被访问/更新的版本）。这是一个**纯
        # 注入层**的过滤——不删除底层任何 fact，只是不让两条互斥的同主
        # 题事实同时出现在 system prompt 里（避免 LLM 看到"张三 35 岁 +
        # 张三 32 岁"这种自相矛盾历史而做不必要的歧义判断）。
        candidates = self._dedupe_facts_by_subject_predicate(candidates)

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

    # Fix-9：被识别为"元监控/自动复盘"的 source 标签集合。
    # 这些条目通常是 daily_consolidator/auto_postmortem 等后台任务自己写
    # 入的状态文本（路径、耗时、统计数字等），对 LLM 解决用户当前任务
    # 几乎无信息增益，反而稀释了真正有用的 fact/preference。
    _NOISY_EXPERIENCE_SOURCES = frozenset(
        {
            "auto_postmortem",
            "auto-postmortem",
            "system:auto_postmortem",
            "system:daily_memory",
            "system:memory_nudge",
            "consolidator",
            "daily_consolidator",
            "self_metric",
        }
    )

    # Fix-9：低置信度阈值。confidence < 0.6 且 memory_type ∈ {fact, experience}
    # 的条目不进 prompt（仍可在 MemoryView 中被用户检索/合并）。
    _MIN_INJECT_CONFIDENCE = 0.6

    @classmethod
    def _strip_experience_noise(
        cls,
        candidates: list[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        """Drop low-signal experience/fact entries before prompt injection.

        Two filters layered (no removal of underlying memory rows):

        - ``source`` 命中 ``_NOISY_EXPERIENCE_SOURCES`` → 直接丢弃。
        - ``memory_type`` ∈ {fact, experience} 且 ``confidence`` 小于
          ``_MIN_INJECT_CONFIDENCE`` → 丢弃。其他 type（episode、attachment、
          recent、plugin source 等）一律保留，避免误删低置信度但仍可能
          相关的检索结果。
        """
        out: list[RetrievalCandidate] = []
        for c in candidates:
            raw = c.raw_data or {}
            src = (raw.get("source") or "").strip().lower()
            if src in cls._NOISY_EXPERIENCE_SOURCES:
                continue
            mt = (c.memory_type or "").lower()
            if mt in ("fact", "experience"):
                # confidence 字段可能从 raw_data 里读到（SemanticMemory.to_dict）
                conf_raw = raw.get("confidence")
                try:
                    conf = float(conf_raw) if conf_raw is not None else None
                except (TypeError, ValueError):
                    conf = None
                if conf is not None and conf < cls._MIN_INJECT_CONFIDENCE:
                    continue
            out.append(c)
        return out

    @staticmethod
    def _dedupe_facts_by_subject_predicate(
        candidates: list[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        """Keep first occurrence per ``(subject, predicate)`` for fact memories.

        Non-fact candidates (episodes, attachments, recent turns, plugin
        sources) are passed through unchanged. ``subject``/``predicate`` come
        from ``raw_data`` (populated by ``_search_semantic``); when missing,
        the candidate is also passed through.
        """
        seen: set[tuple[str, str]] = set()
        out: list[RetrievalCandidate] = []
        for c in candidates:
            if (c.memory_type or "").lower() != "fact":
                out.append(c)
                continue
            raw = c.raw_data or {}
            subj = (raw.get("subject") or "").strip().lower()
            pred = (raw.get("predicate") or "").strip().lower()
            if not subj:
                # 无 subject 元数据 — 不能安全去重，放行
                out.append(c)
                continue
            key = (subj, pred)
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out
