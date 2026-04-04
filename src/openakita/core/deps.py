"""
依赖注入系统

参考 Claude Code 的 QueryDeps 模式:
- 范围刻意窄小（只暴露必要的可注入函数）
- production_deps() 返回真实实现
- 测试中传入 mock/fake 函数
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class ReasoningDeps:
    """ReasoningEngine 的依赖注入容器。

    Scope is intentionally narrow to prove the pattern.
    只包含需要在测试中替换的核心依赖。
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
    """创建生产环境的依赖实例。

    Args:
        brain: Brain 实例
        context_mgr: ContextManager 实例（可选）
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
    """ToolExecutor 的依赖注入容器。"""

    execute_handler: Callable[..., Coroutine] | None = None
    get_tool_schema: Callable[..., dict | None] | None = None
    check_permission: Callable[..., Any] | None = None
    track_file_edit: Callable[..., Any] | None = None


@dataclass
class AgentDeps:
    """Agent 级别的依赖注入容器。"""

    reasoning_deps: ReasoningDeps | None = None
    tool_executor_deps: ToolExecutorDeps | None = None
    hook_executor: Any | None = None
    file_history: Any | None = None
