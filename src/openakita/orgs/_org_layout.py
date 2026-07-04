"""Initial tree-layout + name-normalisation helpers for v2 ``OrgManager`` (P-RC-9 P9.5).

Lifted byte-for-byte from v1
``openakita.orgs.manager._apply_initial_tree_layout`` +
``_normalize_org_name`` so the v2 ``runtime/orgs/manager.py``
can stay under the Nit-4 350 LOC pre-split ceiling. The two
helpers are purely structural (no I/O, no Organization
imports) so factoring them into their own module is the
cleanest split-point per the G-RC-9.2 Nit-4 ruling
("if projected > 350 LOC, split; optionally org_layout.py").

The 1:1 fidelity with v1 is REQUIRED by the P-RC-9-PLAN
section 5.2 OrgManager parity contract: ``assert create() ->
dict -> Organization.to_dict() round-trip; assert dir layout
is identical for data/orgs/<id>/``. The tree-layout helper
runs ONLY on the template-creation path
(``create_from_template``); template fixtures may carry stale
coordinates and the layout step normalises them once before
persistence so the v1 == v2 parity assertion holds. The
name-normalisation helper underpins case- and
whitespace-insensitive uniqueness across ``create`` /
``update`` / ``duplicate`` / ``find_by_name``.

ADR refs: ADR-0011 (subsystem decomposition -- OrgManager is
charter subsystem #5; this is a sibling helper module, not a
Protocol surface); ADR-0012 (no shim under v1; this file
duplicates the v1 logic by intent so the v1 file remains
untouched until P9.9).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "LAYOUT_GAP_X",
    "LAYOUT_GAP_Y",
    "LAYOUT_NODE_H",
    "LAYOUT_NODE_W",
    "apply_initial_tree_layout",
    "normalize_org_name",
]


# ---------------------------------------------------------------------------
# Layout constants (v1 ``manager._LAYOUT_*`` lifted verbatim)
# ---------------------------------------------------------------------------


LAYOUT_NODE_W = 240
"""Canvas-pixel width of one org-node tile (v1 default; UI assumes 240)."""

LAYOUT_NODE_H = 100
"""Canvas-pixel height of one org-node tile (v1 default; UI assumes 100)."""

LAYOUT_GAP_X = 40
"""Horizontal gap between sibling tiles on the same depth level."""

LAYOUT_GAP_Y = 80
"""Vertical gap between parent and child tile rows."""


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------


def normalize_org_name(name: str | None) -> str:
    """Return the canonical form used for uniqueness comparison.

    Strips leading/trailing whitespace and applies ``str.casefold``
    so ``" Acme  "`` collides with ``"acme"`` and ``"ACME"``. The
    user-visible name retains its original casing -- this helper
    only powers the "already exists?" comparison done in
    ``_ensure_name_unique`` / ``find_by_name`` /
    ``resolve_id_by_name_or_id``.

    Empty / None input collapses to an empty string so the
    "no candidate" branch in ``find_by_name`` short-circuits
    without a TypeError.
    """
    return (name or "").strip().casefold()


# ---------------------------------------------------------------------------
# Initial tree layout (template-create path)
# ---------------------------------------------------------------------------


def apply_initial_tree_layout(data: dict[str, Any]) -> None:
    """Assign a readable first-open canvas layout to template-created orgs.

    Built-in and user-saved templates may carry stale or overly
    compact node coordinates. Creating from a template is a
    fresh org, so the helper recomputes a deterministic
    breadth-first tree layout once before persistence:

    * Roots = nodes with no incoming edge (``target``-set
      complement). If the graph has no clear root, the first
      node in document order is chosen.
    * Each BFS level is laid out horizontally, centred on
      the widest level so the tree is symmetric.
    * Orphans (nodes unreachable from any root) are appended
      to the deepest existing level so they remain visible.

    The function MUTATES ``data["nodes"]`` in place: each node
    dict gets a ``position`` key with ``{"x": int, "y": int}``.
    Edges (``data["edges"]``) are read but never mutated. If
    the org has zero nodes the function is a no-op.

    Byte-for-byte fidelity with v1
    ``manager._apply_initial_tree_layout`` is REQUIRED for the
    P-RC-9-PLAN section 5.2 dir-layout parity assertion (the
    persisted ``org.json`` must contain the same ``position``
    values that v1 would have written).
    """
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    if not nodes:
        return

    node_ids = [str(n.get("id") or "") for n in nodes if isinstance(n, dict) and n.get("id")]
    node_id_set = set(node_ids)
    if not node_id_set:
        return

    children_map: dict[str, list[str]] = {}
    parent_set: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in node_id_set or target not in node_id_set or source == target:
            continue
        children_map.setdefault(source, []).append(target)
        parent_set.add(target)

    roots = [node_id for node_id in node_ids if node_id not in parent_set]
    if not roots:
        roots = node_ids[:1]

    levels: list[list[str]] = []
    visited: set[str] = set()
    queue = list(roots)
    while queue:
        level: list[str] = []
        next_queue: list[str] = []
        for node_id in queue:
            if node_id in visited:
                continue
            visited.add(node_id)
            level.append(node_id)
            for child_id in children_map.get(node_id, []):
                if child_id not in visited:
                    next_queue.append(child_id)
        if level:
            levels.append(level)
        queue = next_queue

    orphaned = [node_id for node_id in node_ids if node_id not in visited]
    if orphaned:
        if levels:
            levels[-1].extend(orphaned)
        else:
            levels.append(orphaned)

    max_level_width = max(len(level) for level in levels)
    total_w = max_level_width * (LAYOUT_NODE_W + LAYOUT_GAP_X) - LAYOUT_GAP_X
    pos_map: dict[str, dict[str, int]] = {}
    for level_index, level in enumerate(levels):
        level_w = len(level) * (LAYOUT_NODE_W + LAYOUT_GAP_X) - LAYOUT_GAP_X
        offset_x = (total_w - level_w) // 2
        for node_index, node_id in enumerate(level):
            pos_map[node_id] = {
                "x": offset_x + node_index * (LAYOUT_NODE_W + LAYOUT_GAP_X),
                "y": level_index * (LAYOUT_NODE_H + LAYOUT_GAP_Y),
            }

    for node in nodes:
        if isinstance(node, dict) and node.get("id") in pos_map:
            node["position"] = pos_map[str(node["id"])]
