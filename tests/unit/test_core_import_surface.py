"""Regression tests for the ``openakita.core`` public import surface.

These guards lock in the smoke-fix-1 (F-0 / F-6 / F-7) repairs landed in
sub-commits ``smoke-fix-1a`` (circular-import break) and ``smoke-fix-1c``
(``ReasoningEngine`` lazy export).  They are intentionally tiny: each test
performs a single import round-trip so a regression in either fix shows
up as an immediate failing assertion rather than a downstream attribute
error.

Findings reference: ``tmp_p10/_smoke_report.md`` (Round F-0, F-6, F-7) and
the per-fix commit bodies (``smoke-F0-F6`` / ``smoke-F7``).
"""

from __future__ import annotations


def test_prompt_compiler_import_surface() -> None:
    """F-0: ``openakita.prompt.compiler.check_compiled_outdated`` must import.

    Pre-fix this raised ``ImportError`` because the ``prompt -> builder ->
    skills -> agent -> core._agent_legacy -> ..skills (re-entry)`` cycle
    aborted ``skills/__init__`` mid-load.
    """
    from openakita.prompt.compiler import check_compiled_outdated

    assert callable(check_compiled_outdated)


def test_core_brain_lazy_export() -> None:
    """F-6: ``from openakita.core import Brain`` must succeed.

    Same root cause as F-0; the lazy ``__getattr__`` in
    ``openakita/core/__init__.py`` now pre-loads ``openakita.agent`` so the
    ``_brain_legacy -> llm.client -> core.errors -> agent.errors`` chain
    resolves in the safe order.
    """
    from openakita.core import Brain

    assert isinstance(Brain, type)
    assert Brain.__name__ == "Brain"


def test_core_reasoning_engine_lazy_export() -> None:
    """F-7: ``from openakita.core import ReasoningEngine`` must succeed.

    Pre-fix the lazy ``_LAZY_IMPORTS`` mapping omitted ``ReasoningEngine``,
    so attribute access raised ``AttributeError``.  Closed by sub-commit
    ``smoke-fix-1c`` (single mapping entry).
    """
    from openakita.core import ReasoningEngine

    assert isinstance(ReasoningEngine, type)
    assert ReasoningEngine.__name__ == "ReasoningEngine"
