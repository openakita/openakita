"""MemoryModeRouter — three-state memory mode routing (mode1 / mode2 / auto)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from .types import RetrievalResult

if TYPE_CHECKING:
    from .graph_engine import GraphEngine

logger = logging.getLogger(__name__)

_MODE2_CAUSAL = re.compile(
    r"(为什么|原因|导致|根因|怎么回事|因为|造成|because|why|cause|reason|root.?cause)",
    re.IGNORECASE,
)
_MODE2_TIMELINE = re.compile(
    r"(过程|经过|时间线|之前发生|上次|历史|最近怎么|timeline|previously|last time|history)",
    re.IGNORECASE,
)
_MODE2_CROSS_SESSION = re.compile(
    r"(类似问题|以前怎么|之前也|上回|以前遇到|similar.?issue|done.?before)",
    re.IGNORECASE,
)
_MODE2_ENTITY_TRACK = re.compile(
    r"(关于.{1,10}的所有|.{1,10}的完整记录|everything.?about|all.?records.?of)",
    re.IGNORECASE,
)


class MemoryModeRouter:
    """Routes memory retrieval to Mode 1 (fragment) or Mode 2 (relational graph).

    Three modes:
      - mode1: Only use Mode 1 fragment memory (default, zero overhead)
      - mode2: Only use Mode 2 relational graph memory
      - auto:  Both store; at retrieval time, select by query characteristics
    """

    def __init__(
        self,
        mode1_retriever: Any = None,
        mode2_engine: GraphEngine | None = None,
    ) -> None:
        self.mode1_retriever = mode1_retriever
        self.mode2_engine = mode2_engine

    async def search(
        self,
        query: str,
        config_mode: str = "mode1",
        memory_keywords: list[str] | None = None,
        token_budget: int = 1500,
    ) -> list[RetrievalResult]:
        if config_mode == "mode1":
            return self._search_mode1(query, memory_keywords, token_budget)
        elif config_mode == "mode2":
            return await self._search_mode2(query, token_budget)
        else:
            selected = self.select_mode(query)
            if selected == "mode2":
                return await self._search_mode2(query, token_budget)
            return self._search_mode1(query, memory_keywords, token_budget)

    def select_mode(self, query: str) -> str:
        """Analyze query characteristics to decide which mode to use.

        Mode 2 triggers (any match):
          - Causal keywords: why, because, cause, reason, root cause
          - Timeline keywords: timeline, previously, last time, history
          - Cross-session: similar issue, done before
          - Entity tracking: all records of, everything about

        Default → Mode 1 (faster, sufficient for simple queries)
        """
        if _MODE2_CAUSAL.search(query):
            return "mode2"
        if _MODE2_TIMELINE.search(query):
            return "mode2"
        if _MODE2_CROSS_SESSION.search(query):
            return "mode2"
        if _MODE2_ENTITY_TRACK.search(query):
            return "mode2"
        return "mode1"

    def _search_mode1(
        self, query: str, keywords: list[str] | None, budget: int
    ) -> list[RetrievalResult]:
        """Delegate to Mode 1 retrieval engine (returns empty if unavailable)."""
        if not self.mode1_retriever:
            return []
        try:
            result = self.mode1_retriever.retrieve(
                query=query,
                recent_messages=[],
                max_tokens=budget,
            )
            if isinstance(result, str) and result:
                from .types import MemoryNode, NodeType

                node = MemoryNode(content=result, node_type=NodeType.FACT)
                return [RetrievalResult(node=node, score=0.5)]
            return []
        except Exception as e:
            logger.warning(f"[ModeRouter] Mode 1 search failed: {e}")
            return []

    async def _search_mode2(self, query: str, budget: int) -> list[RetrievalResult]:
        """Delegate to Mode 2 graph engine."""
        if not self.mode2_engine:
            return []
        try:
            return await self.mode2_engine.query(query, token_budget=budget)
        except Exception as e:
            logger.warning(f"[ModeRouter] Mode 2 search failed: {e}")
            return []
