"""
OpenAkita 核心模块。

This package intentionally avoids eager imports so submodules like
`openakita.core.errors` can be imported without dragging in the full agent
stack and creating circular dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._agent_legacy import Agent
    from ._brain_legacy import Brain
    from ._reasoning_engine_legacy import ReasoningEngine
    from .agent_state import AgentState, TaskState, TaskStatus
    from .errors import UserCancelledError
    from .identity import Identity
    from .ralph import RalphLoop

__all__ = [
    "Agent",
    "AgentState",
    "TaskState",
    "TaskStatus",
    "Brain",
    "Identity",
    "RalphLoop",
    "ReasoningEngine",
    "UserCancelledError",
]

_LAZY_IMPORTS = {
    "Agent": ("._agent_legacy", "Agent"),
    "AgentState": (".agent_state", "AgentState"),
    "TaskState": (".agent_state", "TaskState"),
    "TaskStatus": (".agent_state", "TaskStatus"),
    "Brain": ("._brain_legacy", "Brain"),
    "Identity": (".identity", "Identity"),
    "RalphLoop": (".ralph", "RalphLoop"),
    "ReasoningEngine": ("._reasoning_engine_legacy", "ReasoningEngine"),
    "UserCancelledError": (".errors", "UserCancelledError"),
}


def __getattr__(name: str) -> Any:
    """Lazily expose the traditional package-level symbols."""
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    # smoke-F6: pre-load openakita.agent so the brain/llm/errors cycle resolves
    # in the safe order when ``openakita.core`` is the FIRST entry point.
    if module_name in ("._brain_legacy", "._reasoning_engine_legacy", "._agent_legacy"):
        import_module("openakita.agent")
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

