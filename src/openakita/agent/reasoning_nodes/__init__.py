"""Reasoning-engine node helpers.

This subpackage holds the per-Decision data types and small node-level
helpers used by ``ReasoningEngine``. The split mirrors
``runtime/state_graph/guards/``: guards = pre/post-Decision validators
(allowed/forbidden); nodes = state transitions + Decision data shapes.

Each module owns one concern and has dedicated tests.
"""

from __future__ import annotations

__all__: list[str] = []
