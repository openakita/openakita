"""V2 reasoning-engine guard helpers, extracted from ``core/reasoning_engine.py``.

This subpackage holds the pre/post-Decision validators the legacy
``ReasoningEngine`` ran inline as module-level helpers
(``_check_source_tag_consistency``, ``_check_tool_failure_acknowledgement``,
``_guard_unbacked_action_claim``, ``_looks_like_waiting_for_user_response``,
``_has_recoverable_tool_issue``, ``_is_recap_context``). Each module owns one
guard, has its own focused test suite under
``tests/runtime/state_graph/guards/``, and is reimported by the
legacy file during the P-RC-5 transition so existing call sites stay
working without a flag day.

Per continuation plan section 6, these guards drive the v2
``runtime.state_graph.StateGraph`` post-Decision routing (the legacy
giant calls them inline today; ``agent/reasoning.py`` will compose
them explicitly into the StateGraph dispatch table once P5.7 lands).
"""

from __future__ import annotations

__all__: list[str] = []
