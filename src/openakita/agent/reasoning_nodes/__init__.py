"""V2 reasoning-engine node helpers, extracted from ``core/reasoning_engine.py``.

This subpackage holds the per-Decision data types and small node-level
helpers that the legacy ``ReasoningEngine`` inlined as nested
classes / module-level utilities. The split mirrors
``runtime/state_graph/guards/``: guards = pre/post-Decision validators
(allowed/forbidden); nodes = state transitions + Decision data shapes.

Each module is small (1 concern, ~50-150 LOC, dedicated tests) so the
final ``agent/reasoning.py`` can compose them without bloating beyond
its 600 LOC budget.
"""

from __future__ import annotations

__all__: list[str] = []
