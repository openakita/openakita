"""
OpenAkita Prompt pipeline module.

Replaced full-text injection with a pipeline of "compile summary + semantic
retrieval + budget assembly".

Module components:
- compiler.py: Compile summaries from source .md files
- retriever.py: Retrieve relevant segments from MEMORY.md
- budget.py: Token budget trimming
- builder.py: Assemble the final system prompt
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
