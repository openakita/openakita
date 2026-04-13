"""
OpenAkita Prompt 管线模块

从全文注入改为"编译摘要 + 语义检索 + 预算组装"的管线模式。

模块组成:
- compiler.py: 从源 md 编译摘要
- retriever.py: 从 MEMORY.md 检索相关片段
- budget.py: Token 预算裁剪
- builder.py: 组装最终系统提示词
"""

from .budget import BudgetConfig, apply_budget
from .builder import SYSTEM_PROMPT_DYNAMIC_BOUNDARY, build_system_prompt, split_static_dynamic
from .compiler import (
    compile_agent_core,
    compile_all,
    compile_soul,
    compile_user,
)
from .retriever import retrieve_memory

__all__ = [
    # Compiler
    "compile_all",
    "compile_soul",
    "compile_agent_core",
    "compile_user",
    # Retriever
    "retrieve_memory",
    # Budget
    "apply_budget",
    "BudgetConfig",
    # Builder
    "build_system_prompt",
    "split_static_dynamic",
    "SYSTEM_PROMPT_DYNAMIC_BOUNDARY",
]
