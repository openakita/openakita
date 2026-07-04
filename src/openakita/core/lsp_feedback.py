"""Re-export shim — LSP feedback collector moved to ``agent.lsp_feedback``.

Canonical home: :mod:`openakita.agent.lsp_feedback`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.lsp_feedback import (
    Diagnostic,
    DiagnosticBackend,
    DiagnosticReport,
    LSPFeedbackCollector,
    RuffBackend,
    TypeScriptBackend,
)

__all__ = [
    "Diagnostic",
    "DiagnosticBackend",
    "DiagnosticReport",
    "LSPFeedbackCollector",
    "RuffBackend",
    "TypeScriptBackend",
]
