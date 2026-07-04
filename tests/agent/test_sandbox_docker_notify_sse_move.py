"""Move-compatibility tests for commit 11 (sandbox / docker_backend /
desktop_notify / sse_replay).

Phase 2 commit 11 ports four execution-and-transport modules into
the ``agent/`` package:

* ``core/sandbox.py``        → ``agent/sandbox.py``
* ``core/docker_backend.py`` → ``agent/docker_backend.py``
* ``core/desktop_notify.py`` → ``agent/desktop_notify.py``
* ``core/sse_replay.py``     → ``agent/sse_replay.py``

Each legacy path is now a re-export shim. The tests below pin
classes / functions / constants to a single identity across both
import paths so existing callers (run_shell tool, docker-backend
config UI, scheduler notification flow, /api/chat SSE resume)
keep working unchanged.
"""

from __future__ import annotations


def test_sandbox_match_across_paths() -> None:
    from openakita.agent.sandbox import (
        CommandSandbox as A_CS,
    )
    from openakita.agent.sandbox import (
        SandboxExecutor as A_SE,
    )
    from openakita.agent.sandbox import (
        SandboxPolicy as A_SP,
    )
    from openakita.agent.sandbox import (
        SandboxResult as A_SR,
    )
    from openakita.agent.sandbox import (
        SandboxVerdict as A_SV,
    )
    from openakita.agent.sandbox import (
        get_sandbox_executor as a_get,
    )
    from openakita.core.sandbox import (
        CommandSandbox as C_CS,
    )
    from openakita.core.sandbox import (
        SandboxExecutor as C_SE,
    )
    from openakita.core.sandbox import (
        SandboxPolicy as C_SP,
    )
    from openakita.core.sandbox import (
        SandboxResult as C_SR,
    )
    from openakita.core.sandbox import (
        SandboxVerdict as C_SV,
    )
    from openakita.core.sandbox import (
        get_sandbox_executor as c_get,
    )

    assert A_CS is C_CS
    assert A_SE is C_SE
    assert A_SP is C_SP
    assert A_SR is C_SR
    assert A_SV is C_SV
    assert a_get is c_get
    assert a_get() is c_get()


def test_docker_backend_match_across_paths() -> None:
    from openakita.agent.docker_backend import (
        DockerBackend as A_DB,
    )
    from openakita.agent.docker_backend import (
        DockerConfig as A_DC,
    )
    from openakita.agent.docker_backend import (
        DockerResult as A_DR,
    )
    from openakita.agent.docker_backend import (
        configure_docker as a_conf,
    )
    from openakita.agent.docker_backend import (
        get_docker_backend as a_get,
    )
    from openakita.core.docker_backend import (
        DockerBackend as C_DB,
    )
    from openakita.core.docker_backend import (
        DockerConfig as C_DC,
    )
    from openakita.core.docker_backend import (
        DockerResult as C_DR,
    )
    from openakita.core.docker_backend import (
        configure_docker as c_conf,
    )
    from openakita.core.docker_backend import (
        get_docker_backend as c_get,
    )

    assert A_DB is C_DB
    assert A_DC is C_DC
    assert A_DR is C_DR
    assert a_conf is c_conf
    assert a_get is c_get


def test_desktop_notify_match_across_paths() -> None:
    from openakita.agent.desktop_notify import (
        notify_task_completed as a_ntc,
    )
    from openakita.agent.desktop_notify import (
        notify_task_completed_async as a_ntc_async,
    )
    from openakita.agent.desktop_notify import (
        send_desktop_notification as a_send,
    )
    from openakita.agent.desktop_notify import (
        send_desktop_notification_async as a_send_async,
    )
    from openakita.core.desktop_notify import (
        notify_task_completed as c_ntc,
    )
    from openakita.core.desktop_notify import (
        notify_task_completed_async as c_ntc_async,
    )
    from openakita.core.desktop_notify import (
        send_desktop_notification as c_send,
    )
    from openakita.core.desktop_notify import (
        send_desktop_notification_async as c_send_async,
    )

    assert a_send is c_send
    assert a_send_async is c_send_async
    assert a_ntc is c_ntc
    assert a_ntc_async is c_ntc_async


def test_sse_replay_match_across_paths() -> None:
    from openakita.agent.sse_replay import (
        DEFAULT_MAXLEN as A_MAXLEN,
    )
    from openakita.agent.sse_replay import (
        DEFAULT_TTL_SECONDS as A_TTL,
    )
    from openakita.agent.sse_replay import (
        MAX_SESSIONS as A_MS,
    )
    from openakita.agent.sse_replay import (
        SSEEvent as A_EV,
    )
    from openakita.agent.sse_replay import (
        SSESession as A_SS,
    )
    from openakita.agent.sse_replay import (
        SSESessionRegistry as A_REG,
    )
    from openakita.agent.sse_replay import (
        format_sse_frame as a_fmt,
    )
    from openakita.agent.sse_replay import (
        get_registry as a_reg,
    )
    from openakita.agent.sse_replay import (
        parse_last_event_id as a_parse,
    )
    from openakita.agent.sse_replay import (
        reset_registry_for_testing as a_reset,
    )
    from openakita.core.sse_replay import (
        DEFAULT_MAXLEN as C_MAXLEN,
    )
    from openakita.core.sse_replay import (
        DEFAULT_TTL_SECONDS as C_TTL,
    )
    from openakita.core.sse_replay import (
        MAX_SESSIONS as C_MS,
    )
    from openakita.core.sse_replay import (
        SSEEvent as C_EV,
    )
    from openakita.core.sse_replay import (
        SSESession as C_SS,
    )
    from openakita.core.sse_replay import (
        SSESessionRegistry as C_REG,
    )
    from openakita.core.sse_replay import (
        format_sse_frame as c_fmt,
    )
    from openakita.core.sse_replay import (
        get_registry as c_reg,
    )
    from openakita.core.sse_replay import (
        parse_last_event_id as c_parse,
    )
    from openakita.core.sse_replay import (
        reset_registry_for_testing as c_reset,
    )

    assert A_MAXLEN == C_MAXLEN
    assert A_TTL == C_TTL
    assert A_MS == C_MS
    assert A_EV is C_EV
    assert A_SS is C_SS
    assert A_REG is C_REG
    assert a_fmt is c_fmt
    assert a_reg is c_reg
    assert a_parse is c_parse
    assert a_reset is c_reset


def test_agent_namespace_re_exports_commit11_symbols() -> None:
    from openakita import agent

    for sym in (
        "CommandSandbox",
        "SandboxExecutor",
        "SandboxPolicy",
        "SandboxResult",
        "SandboxVerdict",
        "get_sandbox_executor",
        "DockerBackend",
        "DockerConfig",
        "DockerResult",
        "configure_docker",
        "get_docker_backend",
        "send_desktop_notification",
        "send_desktop_notification_async",
        "notify_task_completed",
        "notify_task_completed_async",
        "SSEEvent",
        "SSESession",
        "SSESessionRegistry",
        "DEFAULT_MAXLEN",
        "DEFAULT_TTL_SECONDS",
        "MAX_SESSIONS",
        "format_sse_frame",
        "get_registry",
        "parse_last_event_id",
        "reset_registry_for_testing",
    ):
        assert hasattr(agent, sym), sym
        assert sym in agent.__all__, sym
