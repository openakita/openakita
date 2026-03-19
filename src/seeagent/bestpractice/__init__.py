"""
bestpractice — BP 任务编排系统。

通过预定义模板驱动多子任务串行/DAG 执行，
支持手动/自动模式、Chat-to-Edit、多任务切换、上下文压缩恢复。
"""

from .facade import (
    init_bp_system,
    get_bp_engine,
    get_bp_handler,
    get_bp_state_manager,
    get_bp_context_bridge,
    get_bp_config_loader,
    get_static_prompt_section,
    get_dynamic_prompt_section,
)

__all__ = [
    "init_bp_system",
    "get_bp_engine",
    "get_bp_handler",
    "get_bp_state_manager",
    "get_bp_context_bridge",
    "get_bp_config_loader",
    "get_static_prompt_section",
    "get_dynamic_prompt_section",
]
