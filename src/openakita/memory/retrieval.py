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

    QUERY_DECOMPOSE_PROMPT = (
        "从用户消息中提取用于记忆检索的搜索关键词。\n\n"
        "用户消息: {query}\n"
        "{context_hint}"
        "\n规则:\n"
        "1. 提取核心实体、名称、主题词，去掉语气词/助词/代词\n"
        "2. 如果涉及文件/图片/视频，提取描述性关键词（如\"猫\"\"报告\"）和可能的文件名\n"
        "3. 保留专有名词、技术术语原样\n"
        "4. 输出 JSON: {{\"keywords\": [\"关键词1\", \"关键词2\", ...], "
        "\"intent\": \"search_memory|search_file|general\"}}\n"
        "5. keywords 最多 6 个，每个 1-4 个词\n"
        "只输出 JSON，不要其他内容。"
    )

    def __init__(self, store: UnifiedStore, brain=None) -> None:
        self.store = store
        self.brain = brain
        self._decompose_cache: dict[str, dict] = {}

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
        recent_results = self._search_recent(days=3)
        attachment_results = self._search_attachments(
            query, search_keywords, intent,
        )

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
        decomposed = self._decompose_query(query, recent_messages)
        search_keywords = decomposed.get("keywords", [])
        intent = decomposed.get("intent", "general")

        enhanced = self._build_enhanced_query(query, recent_messages, search_keywords)

        semantic = self._search_semantic(enhanced)
        episodes = self._search_episodes(enhanced)
        recent = self._search_recent(days=3)
        attachments = self._search_attachments(query, search_keywords, intent)

        candidates = self._merge_and_deduplicate(semantic, episodes, recent, attachments)
        ranked = self._rerank(candidates, query)
        return ranked[:limit]

    # ==================================================================
    # Multi-way Recall
    # ==================================================================

    def _search_semantic(self, query: str, limit: int = 15) -> list[RetrievalCandidate]:
        memories = self.store.search_semantic(query, limit=limit)
        now = datetime.now()
        candidates = []
        for mem in memories:
            if mem.expires_at and mem.expires_at < now:
                continue
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
        now = datetime.now()
        candidates = []
        for mem in memories:
            if mem.expires_at and mem.expires_at < now:
                continue
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

    _MEDIA_KEYWORDS = (
        "图片", "照片", "图", "photo", "image", "picture",
        "视频", "video", "clip",
        "文件", "文档", "file", "document", "doc", "pdf",
        "音频", "语音", "audio", "voice",
        "发给你的", "给你的", "上次的", "那个", "那张", "那份",
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
        has_media_hint = (
            intent == "search_file"
            or any(kw in raw_query.lower() for kw in self._MEDIA_KEYWORDS)
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
                relevance=0.85,
                recency_score=self._compute_recency(att.created_at),
                importance_score=0.7,
                access_frequency_score=0.3,
                raw_data=att.to_dict(),
            ))
        return candidates

    @staticmethod
    def _get_attachment_search_terms(
        raw_query: str, search_keywords: list[str] | None
    ) -> list[str]:
        """从拆解关键词中筛选适合附件搜索的词（过滤掉媒体类型词本身）."""
        _STOP_WORDS = {
            "图片", "照片", "图", "photo", "image", "picture",
            "视频", "video", "clip", "文件", "文档", "file",
            "document", "doc", "pdf", "音频", "语音", "audio", "voice",
            "发给你的", "给你的", "上次的", "那个", "那张", "那份",
            "给我", "找到", "一下", "看看", "的", "了", "吧", "呢",
            "在哪", "哪里", "怎么",
        }

        def _is_valid(token: str) -> bool:
            if not token or token.lower() in _STOP_WORDS:
                return False
            has_cjk = any('\u4e00' <= c <= '\u9fff' for c in token)
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

        if self.brain:
            result = self._decompose_with_llm(query, recent_messages)
            if result:
                self._decompose_cache[cache_key] = result
                return result

        result = self._decompose_with_rules(query)
        self._decompose_cache[cache_key] = result
        return result

    def _decompose_with_llm(
        self, query: str, recent_messages: list[dict] | None = None,
    ) -> dict | None:
        """调用 think_lightweight (compiler model) 做查询拆解."""
        context_hint = ""
        if recent_messages:
            recent_texts = []
            for msg in recent_messages[-2:]:
                c = msg.get("content", "")
                if c and isinstance(c, str):
                    recent_texts.append(f"[{msg.get('role', '?')}]: {c[:80]}")
            if recent_texts:
                context_hint = f"近期对话:\n{''.join(recent_texts)}\n"

        prompt = self.QUERY_DECOMPOSE_PROMPT.format(
            query=query[:300], context_hint=context_hint,
        )

        try:
            think_lw = getattr(self.brain, "think_lightweight", None)
            think_fn = think_lw if (think_lw and callable(think_lw)) else getattr(self.brain, "think", None)
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
                f"[Retrieval] LLM decompose: \"{query[:50]}\" → "
                f"keywords={keywords}, intent={intent}"
            )
            return {"keywords": keywords, "intent": intent}

        except Exception as e:
            logger.debug(f"[Retrieval] LLM decompose failed, falling back to rules: {e}")
            return None

    @staticmethod
    def _decompose_with_rules(query: str) -> dict:
        """规则降级: 正则 + 停用词过滤."""
        _STOP = {
            "的", "了", "吗", "吧", "呢", "啊", "哦", "嗯", "是", "在",
            "有", "和", "与", "或", "但", "不", "也", "都", "就", "还",
            "要", "会", "能", "可以", "这个", "那个", "什么", "怎么",
            "为什么", "哪个", "哪里", "多少", "一下", "一些", "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "this", "that", "what", "how", "where", "when", "who", "which",
            "do", "does", "did", "will", "would", "can", "could", "should",
            "给我", "帮我", "请", "看看", "找到", "告诉我",
        }

        keywords = []
        intent = "general"

        _FILE_HINTS = {"图片", "照片", "图", "文件", "文档", "视频", "音频", "语音",
                       "photo", "image", "file", "video", "audio", "document"}
        if any(h in query.lower() for h in _FILE_HINTS):
            intent = "search_file"

        for m in re.finditer(r'[A-Za-z]:[\\\/][^\s"\']+', query):
            keywords.append(m.group(0))
        for m in re.finditer(r'[\w.-]+\.(?:py|js|ts|md|json|yaml|toml|jpg|png|pdf|docx|mp4|mp3)\b', query):
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

    _ACTION_WORDS = frozenset({
        "打开", "关闭", "运行", "执行", "安装", "部署", "启动", "停止",
        "创建", "删除", "修改", "搜索", "下载", "上传", "去", "进入", "访问",
    })

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
