"""
Dependency Injection System

Modeled after Claude Code's QueryDeps pattern:
- Scope is intentionally narrow (only exposes injectable functions that are needed)
- production_deps() returns the real implementations
- Pass in mock/fake functions during testing
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class ReasoningDeps:
    """Dependency injection container for ReasoningEngine.

    Scope is intentionally narrow to prove the pattern.
    Only includes core dependencies that need to be replaced in tests.
    """

    call_model: Callable[..., Coroutine]  # Brain.chat or Brain.chat_stream
    call_model_stream: Callable[..., Any] | None = None  # Brain.chat_stream
    compress: Callable[..., Coroutine] | None = None  # ContextManager.compress_if_needed
    microcompact: Callable[..., Any] | None = None  # microcompact function
    uuid: Callable[[], str] = field(default_factory=lambda: lambda: str(uuid4()))


def production_deps(
    brain: Any,
    context_mgr: Any = None,
) -> ReasoningDeps:
    """Create production dependency instance.

    Args:
        brain: Brain instance
        context_mgr: ContextManager instance (optional)
    """
    from .microcompact import microcompact as mc_fn

    deps = ReasoningDeps(
        call_model=brain.messages_create_async,
        microcompact=mc_fn,
    )

    if hasattr(brain, "chat_stream"):
        deps.call_model_stream = brain.chat_stream

    if context_mgr:
        deps.compress = context_mgr.compress_if_needed

    return deps


@dataclass
class ToolExecutorDeps:
    """Dependency injection container for ToolExecutor."""

    execute_handler: Callable[..., Coroutine] | None = None
    get_tool_schema: Callable[..., dict | None] | None = None
    check_permission: Callable[..., Any] | None = None
    track_file_edit: Callable[..., Any] | None = None


@dataclass
class AgentDeps:
    """Agent-level dependency injection container."""

    reasoning_deps: ReasoningDeps | None = None
    tool_executor_deps: ToolExecutorDeps | None = None
    hook_executor: Any | None = None
    file_history: Any | None = None
