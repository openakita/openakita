"""
Backward-compatibility layer -- re-exports all public symbols from submodules.

External code continues to use: from ..tools.handlers.plan import has_active_todo, PlanHandler, ...

The original plan.py (~1172 lines) has been split into three focused submodules:
- todo_state.py:      Session state management + lifecycle functions
- todo_heuristics.py:  Multi-step task heuristic detection
- todo_handler.py:     PlanHandler class + create_todo_handler factory
"""

from .todo_handler import *  # noqa: F401,F403
from .todo_heuristics import *  # noqa: F401,F403
from .todo_state import *  # noqa: F401,F403

# Explicitly ensure transition-period private symbols are importable externally (not relying on __all__)
from .todo_state import (  # noqa: F401
    _emit_todo_lifecycle_event,
    _session_active_todos,
    _session_handlers,
    _session_todo_required,
)
