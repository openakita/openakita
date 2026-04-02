"""MemoryEncoder — three-layer encoding pipeline for conversation-to-graph."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from .types import (
    Dimension,
    EdgeType,
    EncodingResult,
    EntityRef,
    MemoryEdge,
    MemoryNode,
    NodeType,
)

if TYPE_CHECKING:
    from openakita.core.brain import Brain

logger = logging.getLogger(__name__)

# Tool-name → action_category mapping
_TOOL_ACTION_MAP: dict[str, str] = {
    "write_file": "create",
    "create_file": "create",
    "read_file": "analyze",
    "edit_file": "modify",
    "replace_in_file": "modify",
    "run_shell": "create",
    "search": "analyze",
    "search_files": "analyze",
    "search_memory": "analyze",
    "add_memory": "create",
    "browser_navigate": "analyze",
    "ask_user": "communicate",
}

_CAUSAL_KEYWORDS_ZH = re.compile(
    r"(因为|由于|所以|因此|导致|引起|造成|触发|为了|目的是|结果是)", re.IGNORECASE
)
_CAUSAL_KEYWORDS_EN = re.compile(
    r"\b(because|since|therefore|caused|led to|resulted in|due to|in order to)\b",
    re.IGNORECASE,
)


class MemoryEncoder:
    """Encodes conversation turns into multi-dimensional memory graph nodes and edges.

    Three-layer encoding:
      1. encode_quick — rule-based, no LLM (~10ms)
      2. backfill_from_summary — enriches from compression summary
      3. encode_session — batch LLM encoding at session end
    """

    def __init__(self, brain: Brain | None = None, session_id: str = "") -> None:
        self.brain = brain
        self.session_id = session_id

    # ------------------------------------------------------------------
    # Layer 1: Quick rule-based encoding (pre-compression)
    # ------------------------------------------------------------------

    def encode_quick(self, turns: list[dict], session_id: str = "") -> EncodingResult:
        """Extract basic nodes + temporal/entity edges without calling the LLM."""
        sid = session_id or self.session_id
        nodes: list[MemoryNode] = []
        edges: list[MemoryEdge] = []

        for turn in turns:
            role = turn.get("role", "")
            content = turn.get("content", "") or ""
            if not content or len(content) < 15:
                continue

            tool_calls = turn.get("tool_calls") or []
            entities: list[EntityRef] = []
            action_cat = ""
            action_verb = ""

            # Extract entities and actions from tool calls
            for tc in tool_calls:
                tool_name = ""
                if isinstance(tc, dict):
                    tool_name = tc.get("function", {}).get("name", "") or tc.get("name", "")
                if tool_name:
                    entities.append(EntityRef(name=tool_name, type="tool", role="instrument"))
                    action_cat = action_cat or _TOOL_ACTION_MAP.get(tool_name, "")
                    action_verb = action_verb or tool_name

            if not action_cat:
                action_cat = "communicate" if role == "user" else "analyze"
            if not action_verb:
                action_verb = "asked" if role == "user" else "responded"

            node = MemoryNode(
                content=content[:500],
                node_type=NodeType.EVENT,
                occurred_at=datetime.now(),
                entities=entities,
                action_verb=action_verb,
                action_category=action_cat,
                session_id=sid,
                importance=0.4 if role == "user" else 0.3,
            )
            nodes.append(node)

        # Build temporal chain
        for i in range(1, len(nodes)):
            edges.append(MemoryEdge(
                source_id=nodes[i - 1].id,
                target_id=nodes[i].id,
                edge_type=EdgeType.FOLLOWED_BY,
                dimension=Dimension.TEMPORAL,
                weight=0.6,
            ))

        # Build entity co-occurrence edges
        entity_node_map: dict[str, list[str]] = {}
        for n in nodes:
            for ent in n.entities:
                key = ent.name.lower()
                entity_node_map.setdefault(key, []).append(n.id)

        for ent_name, nids in entity_node_map.items():
            if len(nids) < 2:
                continue
            for i in range(len(nids)):
                for j in range(i + 1, min(i + 3, len(nids))):
                    edges.append(MemoryEdge(
                        source_id=nids[i],
                        target_id=nids[j],
                        edge_type=EdgeType.INVOLVES,
                        dimension=Dimension.ENTITY,
                        weight=0.5,
                        metadata={"entity": ent_name},
                    ))

        return EncodingResult(nodes=nodes, edges=edges)

    # ------------------------------------------------------------------
    # Layer 2: Backfill from compression summary
    # ------------------------------------------------------------------

    def backfill_from_summary(
        self, summary: str, partial_nodes: list[MemoryNode]
    ) -> EncodingResult:
        """Enrich existing nodes with causal/context edges from compression summary."""
        edges: list[MemoryEdge] = []
        new_nodes: list[MemoryNode] = []

        if not summary or not partial_nodes:
            return EncodingResult()

        # Create a summary node
        summary_node = MemoryNode(
            content=summary[:800],
            node_type=NodeType.FACT,
            occurred_at=datetime.now(),
            session_id=self.session_id,
            importance=0.6,
            action_verb="summarized",
            action_category="analyze",
        )
        new_nodes.append(summary_node)

        # Link summary to all partial nodes as context
        for pn in partial_nodes:
            edges.append(MemoryEdge(
                source_id=pn.id,
                target_id=summary_node.id,
                edge_type=EdgeType.PART_OF,
                dimension=Dimension.CONTEXT,
                weight=0.5,
            ))

        # Scan summary for causal language
        has_causal = bool(
            _CAUSAL_KEYWORDS_ZH.search(summary) or _CAUSAL_KEYWORDS_EN.search(summary)
        )
        if has_causal and len(partial_nodes) >= 2:
            edges.append(MemoryEdge(
                source_id=partial_nodes[0].id,
                target_id=partial_nodes[-1].id,
                edge_type=EdgeType.LED_TO,
                dimension=Dimension.CAUSAL,
                weight=0.4,
            ))

        return EncodingResult(nodes=new_nodes, edges=edges)

    # ------------------------------------------------------------------
    # Layer 3: Batch LLM encoding at session end
    # ------------------------------------------------------------------

    async def encode_session(
        self,
        turns: list[dict],
        existing_nodes: list[MemoryNode] | None = None,
        session_id: str = "",
    ) -> EncodingResult:
        """Full LLM-assisted encoding of an entire session."""
        sid = session_id or self.session_id
        if not self.brain:
            if existing_nodes:
                logger.debug("[MemoryEncoder] No brain, but quick-encoded nodes exist — skipping")
                return EncodingResult()
            logger.warning("[MemoryEncoder] No brain available, falling back to quick encoding")
            return self.encode_quick(turns, sid)

        conversation_text = self._turns_to_text(turns, max_chars=8000)
        if len(conversation_text) < 30:
            return EncodingResult()

        prompt = self._build_encoding_prompt(conversation_text)

        try:
            resp = await self.brain.compiler_think(
                prompt=prompt,
                system="You are a memory graph encoder. Output valid JSON only.",
                max_tokens=2048,
            )
            response_text = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            logger.warning(f"[MemoryEncoder] LLM encoding failed: {e}")
            if existing_nodes:
                return EncodingResult()
            return self.encode_quick(turns, sid)

        nodes, edges = self._parse_llm_response(response_text, sid)

        # Link existing (quick-encoded) nodes to nearest LLM-extracted nodes
        if existing_nodes and nodes:
            for en in existing_nodes:
                best_target = self._find_best_matching_node(en, nodes)
                edges.append(MemoryEdge(
                    source_id=en.id,
                    target_id=best_target.id,
                    edge_type=EdgeType.RELATED_TO,
                    dimension=Dimension.CONTEXT,
                    weight=0.4,
                ))

        return EncodingResult(nodes=nodes, edges=edges)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _text_similarity(a: str, b: str) -> bool:
        """Check if two texts are similar enough for causal linking.
        Handles both space-delimited (English) and non-delimited (Chinese) text.
        """
        words_a = set(a.split())
        words_b = set(b.split())
        word_overlap = len(words_a & words_b)
        if word_overlap >= 2:
            return True
        chars_a = set(a)
        chars_b = set(b)
        char_overlap = len(chars_a & chars_b)
        min_len = min(len(chars_a), len(chars_b))
        return min_len >= 3 and char_overlap >= max(3, min_len // 2)

    @staticmethod
    def _find_best_matching_node(source: MemoryNode, candidates: list[MemoryNode]) -> MemoryNode:
        """Find the candidate node with highest overlap to source.
        Uses both word-level (English) and character-level (CJK) matching.
        """
        if len(candidates) == 1:
            return candidates[0]
        src = source.content.lower()
        src_words = set(src.split())
        src_chars = set(src)
        best, best_score = candidates[0], 0.0
        for c in candidates:
            ct = c.content.lower()
            word_overlap = len(src_words & set(ct.split()))
            char_overlap = len(src_chars & set(ct))
            score = word_overlap * 2.0 + char_overlap * 0.1
            if score > best_score:
                best, best_score = c, score
        return best

    @staticmethod
    def _safe_importance(value: object) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.5

    def _turns_to_text(self, turns: list[dict], max_chars: int = 8000) -> str:
        parts: list[str] = []
        total = 0
        for t in turns:
            role = t.get("role", "?")
            content = (t.get("content") or "")[:600]
            line = f"[{role}] {content}"
            if total + len(line) > max_chars:
                break
            parts.append(line)
            total += len(line)
        return "\n".join(parts)

    def _build_encoding_prompt(self, conversation: str) -> str:
        return f"""Analyze the following conversation and extract a structured memory graph.

For each noteworthy event/fact/decision/goal, output a node. Output JSON array:
[
  {{
    "content": "natural language description",
    "node_type": "event|fact|decision|goal",
    "entities": [{{"name": "...", "type": "person|tool|file|concept", "role": "agent|patient|instrument"}}],
    "action_verb": "core verb",
    "action_category": "create|modify|analyze|communicate|decide",
    "importance": 0.0-1.0,
    "causal_refs": ["description of caused/causing events"]
  }}
]

Focus on:
- Cross-turn causal chains (A caused B, B caused C)
- Decisions and their rationale
- Key facts learned
- Goals established

Conversation:
{conversation}

Output ONLY valid JSON array:"""

    def _parse_llm_response(
        self, response: str, session_id: str
    ) -> tuple[list[MemoryNode], list[MemoryEdge]]:
        nodes: list[MemoryNode] = []
        edges: list[MemoryEdge] = []

        # Extract JSON from response
        json_str = response.strip()
        if "```" in json_str:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)```", json_str)
            if match:
                json_str = match.group(1).strip()

        try:
            items = json.loads(json_str)
        except json.JSONDecodeError:
            items = None
            # Try greedy first (handles nested arrays), then non-greedy (handles trailing text)
            for pattern in (r"\[[\s\S]*\]", r"\[[\s\S]*?\]"):
                match = re.search(pattern, json_str)
                if match:
                    try:
                        items = json.loads(match.group(0))
                        break
                    except json.JSONDecodeError:
                        continue
            if items is None:
                logger.warning("[MemoryEncoder] Failed to parse LLM response as JSON")
                return nodes, edges

        if not isinstance(items, list):
            items = [items]

        for item in items:
            if not isinstance(item, dict):
                continue

            nt = NodeType.EVENT
            try:
                nt = NodeType(item.get("node_type", "event"))
            except ValueError:
                pass

            entities = []
            for e in item.get("entities", []):
                if isinstance(e, dict):
                    entities.append(EntityRef(
                        name=e.get("name", ""),
                        type=e.get("type", "concept"),
                        role=e.get("role", ""),
                    ))

            node = MemoryNode(
                content=item.get("content", "")[:500],
                node_type=nt,
                occurred_at=datetime.now(),
                entities=entities,
                action_verb=item.get("action_verb", ""),
                action_category=item.get("action_category", ""),
                session_id=session_id,
                importance=self._safe_importance(item.get("importance", 0.5)),
            )
            nodes.append(node)

        # Build temporal chain
        for i in range(1, len(nodes)):
            edges.append(MemoryEdge(
                source_id=nodes[i - 1].id,
                target_id=nodes[i].id,
                edge_type=EdgeType.FOLLOWED_BY,
                dimension=Dimension.TEMPORAL,
                weight=0.7,
            ))

        # Build causal edges from causal_refs
        node_contents: list[tuple[str, str]] = [
            (n.content.lower()[:80], n.id) for n in nodes
        ]
        for item, node in zip(items, nodes, strict=False):
            if not isinstance(item, dict):
                continue
            for ref in item.get("causal_refs", []):
                if not isinstance(ref, str) or len(ref) < 3:
                    continue
                ref_lower = ref.lower()[:80]
                for content_key, target_id in node_contents:
                    if target_id == node.id:
                        continue
                    if self._text_similarity(ref_lower, content_key):
                        edges.append(MemoryEdge(
                            source_id=node.id,
                            target_id=target_id,
                            edge_type=EdgeType.LED_TO,
                            dimension=Dimension.CAUSAL,
                            weight=0.6,
                        ))
                        break

        # Entity co-occurrence
        entity_map: dict[str, list[str]] = {}
        for n in nodes:
            for ent in n.entities:
                entity_map.setdefault(ent.name.lower(), []).append(n.id)
        for ent_name, nids in entity_map.items():
            if len(nids) < 2:
                continue
            for i in range(len(nids)):
                for j in range(i + 1, min(i + 3, len(nids))):
                    edges.append(MemoryEdge(
                        source_id=nids[i],
                        target_id=nids[j],
                        edge_type=EdgeType.INVOLVES,
                        dimension=Dimension.ENTITY,
                        weight=0.5,
                        metadata={"entity": ent_name},
                    ))

        return nodes, edges
