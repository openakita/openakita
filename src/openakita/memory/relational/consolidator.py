"""RelationalConsolidator — bio-inspired memory replay and maintenance."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .store import RelationalMemoryStore

if TYPE_CHECKING:
    from .entity_resolver import EntityResolver

logger = logging.getLogger(__name__)


class RelationalConsolidator:
    """Performs periodic graph maintenance inspired by hippocampal memory replay.

    Operations:
      1. Rebuild materialized reachable table
      2. Hebbian strengthening of co-accessed edges
      3. Temporal decay of all edge weights
      4. Prune weak edges below threshold
      5. Entity disambiguation (batch LLM if available)
    """

    def __init__(
        self,
        store: RelationalMemoryStore,
        entity_resolver: EntityResolver | None = None,
    ) -> None:
        self.store = store
        self.entity_resolver = entity_resolver

    async def consolidate(
        self,
        decay_factor: float = 0.98,
        prune_threshold: float = 0.05,
        run_entity_resolution: bool = False,
    ) -> dict:
        """Run full consolidation cycle. Returns a summary dict."""
        report: dict = {}

        # 1. Rebuild reachable table
        try:
            reachable_count = self.store.rebuild_reachable()
            report["reachable_rows"] = reachable_count
            logger.info(f"[Consolidator] Rebuilt reachable table: {reachable_count} rows")
        except Exception as e:
            logger.error(f"[Consolidator] Reachable rebuild failed: {e}")
            report["reachable_error"] = str(e)

        # 2. Temporal decay
        try:
            decayed = self.store.decay_edges(decay_factor)
            report["decayed_edges"] = decayed
        except Exception as e:
            logger.error(f"[Consolidator] Decay failed: {e}")

        # 3. Prune weak edges
        try:
            pruned = self.store.prune_weak_edges(prune_threshold)
            report["pruned_edges"] = pruned
            if pruned > 0:
                logger.info(f"[Consolidator] Pruned {pruned} weak edges")
        except Exception as e:
            logger.error(f"[Consolidator] Prune failed: {e}")

        # 4. Entity resolution (optional, requires LLM)
        if run_entity_resolution and self.entity_resolver:
            try:
                resolved = await self._run_entity_resolution()
                report["entities_resolved"] = resolved
            except Exception as e:
                logger.error(f"[Consolidator] Entity resolution failed: {e}")

        # 5. Stats
        report["total_nodes"] = self.store.count_nodes()
        report["total_edges"] = self.store.count_edges()

        logger.info(f"[Consolidator] Done: {report}")
        return report

    def strengthen_co_accessed(self, node_ids: list[str], delta: float = 0.03) -> int:
        """Hebbian strengthening: edges between co-accessed nodes get boosted."""
        count = 0
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                edges = self.store.get_edges_for_node(node_ids[i])
                for e in edges:
                    other = e.target_id if e.source_id == node_ids[i] else e.source_id
                    if other == node_ids[j]:
                        self.store.strengthen_edge(e.id, delta)
                        count += 1
        return count

    async def _run_entity_resolution(self) -> int:
        """Collect all unique entity names and run batch disambiguation."""
        if not self.entity_resolver:
            return 0

        names = self.store.get_all_entity_names(limit=500)

        if len(names) < 2:
            return 0

        resolved = await self.entity_resolver.resolve_batch_with_llm(names)
        return len([v for k, v in resolved.items() if k != v])

