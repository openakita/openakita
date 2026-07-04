"""Move-compatibility tests for commit 10 (file_history / trusted_paths /
domain_allowlist / lsp_feedback).

Phase 2 commit 10 ports four self-contained modules into the
``agent/`` package:

* ``core/file_history.py``     → ``agent/file_history.py``
* ``core/trusted_paths.py``    → ``agent/trusted_paths.py``
* ``core/domain_allowlist.py`` → ``agent/domain_allowlist.py``
* ``core/lsp_feedback.py``     → ``agent/lsp_feedback.py``

Each legacy path is now a re-export shim. The tests below pin
classes / functions / constants to a single identity across both
import paths so existing callers (Agent, ReasoningEngine,
WebFetch, plan/edit risk-gate) keep working unchanged.
"""

from __future__ import annotations


def test_file_history_match_across_paths() -> None:
    from openakita.agent.file_history import (
        HISTORY_BASE_DIR as A_DIR,
    )
    from openakita.agent.file_history import (
        MAX_SNAPSHOTS as A_MAX,
    )
    from openakita.agent.file_history import (
        BackupInfo as A_BI,
    )
    from openakita.agent.file_history import (
        FileHistoryManager as A_FHM,
    )
    from openakita.agent.file_history import (
        FileSnapshot as A_FS,
    )
    from openakita.core.file_history import (
        HISTORY_BASE_DIR as C_DIR,
    )
    from openakita.core.file_history import (
        MAX_SNAPSHOTS as C_MAX,
    )
    from openakita.core.file_history import (
        BackupInfo as C_BI,
    )
    from openakita.core.file_history import (
        FileHistoryManager as C_FHM,
    )
    from openakita.core.file_history import (
        FileSnapshot as C_FS,
    )

    assert A_BI is C_BI
    assert A_FS is C_FS
    assert A_FHM is C_FHM
    assert A_MAX == C_MAX
    assert A_DIR == C_DIR


def test_trusted_paths_match_across_paths() -> None:
    from openakita.agent.trusted_paths import (
        SESSION_KEY as A_KEY,
    )
    from openakita.agent.trusted_paths import (
        clear_session_trust as a_clear,
    )
    from openakita.agent.trusted_paths import (
        consume_session_trust as a_consume,
    )
    from openakita.agent.trusted_paths import (
        get_session_overrides as a_get,
    )
    from openakita.agent.trusted_paths import (
        grant_session_trust as a_grant,
    )
    from openakita.agent.trusted_paths import (
        is_trusted_workspace_path as a_is_trusted,
    )
    from openakita.core.trusted_paths import (
        SESSION_KEY as C_KEY,
    )
    from openakita.core.trusted_paths import (
        clear_session_trust as c_clear,
    )
    from openakita.core.trusted_paths import (
        consume_session_trust as c_consume,
    )
    from openakita.core.trusted_paths import (
        get_session_overrides as c_get,
    )
    from openakita.core.trusted_paths import (
        grant_session_trust as c_grant,
    )
    from openakita.core.trusted_paths import (
        is_trusted_workspace_path as c_is_trusted,
    )

    assert A_KEY == C_KEY
    assert a_clear is c_clear
    assert a_consume is c_consume
    assert a_get is c_get
    assert a_grant is c_grant
    assert a_is_trusted is c_is_trusted


def test_domain_allowlist_match_across_paths() -> None:
    from openakita.agent.domain_allowlist import (
        DomainAllowlist as A_DA,
    )
    from openakita.agent.domain_allowlist import (
        get_domain_allowlist as a_get,
    )
    from openakita.core.domain_allowlist import (
        DomainAllowlist as C_DA,
    )
    from openakita.core.domain_allowlist import (
        get_domain_allowlist as c_get,
    )

    assert A_DA is C_DA
    assert a_get is c_get
    assert a_get() is c_get()


def test_lsp_feedback_match_across_paths() -> None:
    from openakita.agent.lsp_feedback import (
        Diagnostic as A_DG,
    )
    from openakita.agent.lsp_feedback import (
        DiagnosticBackend as A_DB,
    )
    from openakita.agent.lsp_feedback import (
        DiagnosticReport as A_DR,
    )
    from openakita.agent.lsp_feedback import (
        LSPFeedbackCollector as A_LFC,
    )
    from openakita.agent.lsp_feedback import (
        RuffBackend as A_RB,
    )
    from openakita.agent.lsp_feedback import (
        TypeScriptBackend as A_TB,
    )
    from openakita.core.lsp_feedback import (
        Diagnostic as C_DG,
    )
    from openakita.core.lsp_feedback import (
        DiagnosticBackend as C_DB,
    )
    from openakita.core.lsp_feedback import (
        DiagnosticReport as C_DR,
    )
    from openakita.core.lsp_feedback import (
        LSPFeedbackCollector as C_LFC,
    )
    from openakita.core.lsp_feedback import (
        RuffBackend as C_RB,
    )
    from openakita.core.lsp_feedback import (
        TypeScriptBackend as C_TB,
    )

    assert A_DG is C_DG
    assert A_DB is C_DB
    assert A_DR is C_DR
    assert A_LFC is C_LFC
    assert A_RB is C_RB
    assert A_TB is C_TB


def test_agent_namespace_re_exports_commit10_symbols() -> None:
    from openakita import agent

    for sym in (
        "FileHistoryManager",
        "FileSnapshot",
        "BackupInfo",
        "MAX_SNAPSHOTS",
        "HISTORY_BASE_DIR",
        "DomainAllowlist",
        "get_domain_allowlist",
        "SESSION_KEY",
        "is_trusted_workspace_path",
        "grant_session_trust",
        "consume_session_trust",
        "clear_session_trust",
        "get_session_overrides",
        "Diagnostic",
        "DiagnosticBackend",
        "DiagnosticReport",
        "LSPFeedbackCollector",
        "RuffBackend",
        "TypeScriptBackend",
    ):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
