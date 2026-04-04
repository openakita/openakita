"""
向后兼容层 -- 所有公开符号从子模块 re-export。

外部代码继续使用: from ..tools.handlers.plan import has_active_todo, PlanHandler, ...

原始 plan.py（~1172 行）已拆分为三个职责明确的子模块：
- todo_state.py:      Session 状态管理 + 生命周期函数
- todo_heuristics.py:  多步骤任务启发式检测
- todo_handler.py:     PlanHandler 类 + create_todo_handler 工厂
"""

from .todo_state import *        # noqa: F401,F403
from .todo_heuristics import *   # noqa: F401,F403
from .todo_handler import *      # noqa: F401,F403

# 显式确保过渡期私有符号可被外部 import（不依赖 __all__）
from .todo_state import (        # noqa: F401
    _session_active_todos,
    _session_todo_required,
    _session_handlers,
    _emit_todo_lifecycle_event,
)
