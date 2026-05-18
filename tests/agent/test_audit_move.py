"""Move-compatibility tests for ``openakita.agent.audit``.

Phase 2 ports ``core/audit_logger.py`` to ``agent/audit.py`` and
leaves a re-export shim. The legacy import path is used by
``api/server.py``, ``api/routes/{config,health}.py``,
``agents/task_queue.py``, and the ``policy_v2`` adapter glue.
Verifying class / function identity here makes it impossible for
the move to silently shift ``__module__``.

The full behavioural suite is the existing
``tests/unit/test_policy_v2_c8b2_defaults.py`` plus the C16/C17/C18
audit-chain hardening tests under ``tests/unit/test_c17_*`` and
``tests/unit/test_c22_async_audit_writer.py``; none of them are
changed by this commit.
"""

from __future__ import annotations


def test_audit_logger_is_same_class_via_both_paths() -> None:
    from openakita.agent.audit import AuditLogger as Agent
    from openakita.core.audit_logger import AuditLogger as Core

    assert Agent is Core


def test_get_audit_logger_is_same_function_via_both_paths() -> None:
    from openakita.agent.audit import get_audit_logger as agent_fn
    from openakita.core.audit_logger import get_audit_logger as core_fn

    assert agent_fn is core_fn


def test_reset_audit_logger_is_same_function_via_both_paths() -> None:
    from openakita.agent.audit import reset_audit_logger as agent_fn
    from openakita.core.audit_logger import reset_audit_logger as core_fn

    assert agent_fn is core_fn


def test_default_audit_path_constant_is_same_value_via_both_paths() -> None:
    """The constant is just a string, but both modules must export
    the same literal so legacy configuration code doesn't drift.
    """
    from openakita.agent.audit import DEFAULT_AUDIT_PATH as AGENT_PATH
    from openakita.core.audit_logger import DEFAULT_AUDIT_PATH as CORE_PATH

    assert AGENT_PATH == CORE_PATH


def test_agent_namespace_re_exports_audit_symbols() -> None:
    from openakita import agent

    for sym in ("AuditLogger", "get_audit_logger", "reset_audit_logger"):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
