"""BP 系统入口 — 单例工厂与系统集成。

提供:
- get_bp_engine(): 延迟初始化 BPEngine/BPStateManager/BPConfigLoader/BPToolHandler
- get_bp_handler(): 获取 BPToolHandler (用于 handler_registry.register())
- get_bp_tool_definitions(): 获取 BP 工具定义列表
- get_static_prompt_section(): 获取 BP 静态 system prompt 段
- get_dynamic_prompt_section(): 获取 BP 动态 system prompt 段
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config_loader import BPConfigLoader
    from .context_bridge import ContextBridge
    from .engine import BPEngine
    from .handler import BPToolHandler
    from .prompt_loader import PromptTemplateLoader
    from .schema_chain import SchemaChain
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)

# Singleton state
_bp_engine: BPEngine | None = None
_bp_handler: BPToolHandler | None = None
_bp_state_manager: BPStateManager | None = None
_bp_config_loader: BPConfigLoader | None = None
_bp_context_bridge: ContextBridge | None = None
_bp_prompt_loader: PromptTemplateLoader | None = None
_initialized = False


def _find_bp_dirs() -> list[Path]:
    """搜索 best_practice/ 目录位置。"""
    candidates = []

    # 1. 项目根目录 (CWD 或 git root)
    cwd = Path.cwd()
    bp_dir = cwd / "best_practice"
    if bp_dir.is_dir():
        candidates.append(bp_dir)

    # 2. 用户数据目录
    try:
        from seeagent.config import settings
        data_bp = Path(settings.data_dir) / "best_practice"
        if data_bp.is_dir() and data_bp not in candidates:
            candidates.append(data_bp)
    except (ImportError, Exception):
        pass

    return candidates


def init_bp_system(
    profile_store: Any = None,
    search_paths: list[Path] | None = None,
) -> bool:
    """初始化 BP 子系统。返回是否成功加载了配置。

    通常在 Agent._init_handlers() 或 main._init_orchestrator() 时调用。
    """
    global _bp_engine, _bp_handler, _bp_state_manager
    global _bp_config_loader, _bp_context_bridge, _bp_prompt_loader
    global _initialized

    if _initialized:
        return bool(_bp_config_loader and _bp_config_loader.configs)

    from .config_loader import BPConfigLoader
    from .context_bridge import ContextBridge
    from .engine import BPEngine
    from .handler import BPToolHandler
    from .prompt_loader import PromptTemplateLoader
    from .schema_chain import SchemaChain
    from .state_manager import BPStateManager

    # 搜索路径
    paths = search_paths or _find_bp_dirs()
    if not paths:
        logger.debug("[BP] No best_practice/ directory found")
        _initialized = True
        return False

    # 初始化组件
    _bp_state_manager = BPStateManager()
    schema_chain = SchemaChain()
    _bp_engine = BPEngine(state_manager=_bp_state_manager, schema_chain=schema_chain)
    _bp_context_bridge = ContextBridge(state_manager=_bp_state_manager)
    _bp_prompt_loader = PromptTemplateLoader()

    # 加载配置 + profiles
    _bp_config_loader = BPConfigLoader(
        search_paths=paths,
        profile_store=profile_store,
    )
    configs = _bp_config_loader.load_all()

    if not configs:
        logger.debug("[BP] No BP configs loaded")
        _initialized = True
        return False

    # 创建 handler
    _bp_handler = BPToolHandler(
        engine=_bp_engine,
        state_manager=_bp_state_manager,
        context_bridge=_bp_context_bridge,
        config_registry=configs,
    )

    _initialized = True
    logger.info(f"[BP] System initialized with {len(configs)} configs: {list(configs.keys())}")
    return True


def get_bp_engine() -> BPEngine | None:
    if not _initialized:
        init_bp_system()
    return _bp_engine


def get_bp_handler() -> BPToolHandler | None:
    if not _initialized:
        init_bp_system()
    return _bp_handler


def get_bp_state_manager() -> BPStateManager | None:
    if not _initialized:
        init_bp_system()
    return _bp_state_manager


def get_bp_context_bridge() -> ContextBridge | None:
    if not _initialized:
        init_bp_system()
    return _bp_context_bridge


def get_bp_config_loader() -> BPConfigLoader | None:
    if not _initialized:
        init_bp_system()
    return _bp_config_loader


# ── Prompt injection ───────────────────────────────────────────


def get_static_prompt_section() -> str:
    """BP 静态 system prompt 段: 能力描述 + 可用模板列表 + 交互规则。"""
    if not _initialized:
        init_bp_system()
    if not _bp_config_loader or not _bp_prompt_loader:
        return ""

    configs = _bp_config_loader.configs
    if not configs:
        return ""

    bp_list_lines = []
    for bp_id, config in configs.items():
        triggers_desc = ""
        for t in config.triggers:
            if t.type.value == "command":
                triggers_desc += f" (命令: \"{t.pattern}\")"
            elif t.type.value == "context":
                triggers_desc += f" (关键词: {', '.join(t.conditions)})"

        subtask_names = " → ".join(s.name for s in config.subtasks)
        bp_list_lines.append(
            f"- **{config.name}** (`{bp_id}`){triggers_desc}: {config.description}\n"
            f"  流程: {subtask_names}"
        )

    bp_list = "\n".join(bp_list_lines)
    return _bp_prompt_loader.render("system_static", bp_list=bp_list)


def get_dynamic_prompt_section(session_id: str) -> str:
    """BP 动态 system prompt 段: 当前状态 + 活跃上下文 + 意图路由。"""
    if not _initialized:
        init_bp_system()
    if not _bp_state_manager or not _bp_prompt_loader:
        return ""

    status_table = _bp_state_manager.get_status_table(session_id)
    if not status_table:
        return ""

    # 活跃实例上下文
    active = _bp_state_manager.get_active(session_id)
    active_context = ""
    intent_routing = ""

    if active:
        bp_name = active.bp_config.name if active.bp_config else active.bp_id
        idx = active.current_subtask_index
        total = len(active.subtask_statuses)
        done = sum(1 for v in active.subtask_statuses.values() if v == "done")
        active_context = (
            f"**当前活跃任务**: {bp_name} (进度: {done}/{total})\n"
        )

        # 暂停点意图路由
        if active.bp_config and idx > 0:
            prev_status = list(active.subtask_statuses.values())[idx - 1] if idx <= total else ""
            if prev_status == "done":
                intent_routing = (
                    "用户可能想要:\n"
                    "A) 修改上一步结果 (bp_edit_output)\n"
                    "B) 继续下一步 (bp_continue)\n"
                    "C) 切换到其他任务 (bp_switch_task)\n"
                    "D) 询问相关问题\n"
                    "E) 开始新话题"
                )

    # 冷却
    cooldown = _bp_state_manager.get_cooldown(session_id)
    if cooldown > 0:
        active_context += f"\n⚠️ BP 推断冷却中 (剩余 {cooldown} 轮)，COMMAND 触发仍生效。\n"

    return _bp_prompt_loader.render(
        "system_dynamic",
        status_table=status_table,
        active_context=active_context,
        intent_routing=intent_routing,
    )
