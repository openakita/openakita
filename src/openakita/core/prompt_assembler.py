"""
提示词组装器

从 agent.py 提取的系统提示词构建逻辑，负责:
- 构建完整系统提示词（含身份、技能清单、MCP、记忆、工具列表）
- 编译管线 v2 (低 token 版本)
"""

import logging
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)


class PromptAssembler:
    """
    系统提示词组装器。

    集成身份信息、技能清单、MCP 清单、记忆上下文、
    工具列表和环境信息来构建完整的系统提示词。
    """

    def __init__(
        self,
        tool_catalog: Any,
        skill_catalog: Any,
        mcp_catalog: Any,
        memory_manager: Any,
        profile_manager: Any,
        brain: Any,
        persona_manager: Any = None,
    ) -> None:
        self._tool_catalog = tool_catalog
        self._skill_catalog = skill_catalog
        self._mcp_catalog = mcp_catalog
        self._plugin_catalog: Any = None
        self._memory_manager = memory_manager
        self._profile_manager = profile_manager
        self._brain = brain
        self._persona_manager = persona_manager

    def build_system_prompt(
        self,
        task_description: str = "",
        session_type: str = "cli",
    ) -> str:
        """
        构建完整的系统提示词（使用编译管线 v2）。

        Args:
            task_description: 任务描述（用于记忆检索）
            session_type: 会话类型 "cli" 或 "im"

        Returns:
            完整的系统提示词
        """
        return self._build_compiled_sync(task_description, session_type=session_type)

    async def build_system_prompt_compiled(
        self,
        task_description: str = "",
        session_type: str = "cli",
        context_window: int = 0,
        is_sub_agent: bool = False,
        tools_enabled: bool = True,
        memory_keywords: list[str] | None = None,
        model_display_name: str = "",
        session_context: dict | None = None,
        mode: str = "agent",
        model_id: str = "",
        skip_catalogs: bool = False,
        user_input_tokens: int = 0,
        prompt_profile: "Any | None" = None,
        prompt_tier: "Any | None" = None,
    ) -> str:
        """
        使用编译管线构建系统提示词 (v2) - 异步版本。

        Args:
            task_description: 任务描述
            session_type: 会话类型
            context_window: 目标模型上下文窗口大小（>0 时启用自适应预算）
            is_sub_agent: 是否为子 Agent 调用
            tools_enabled: 是否启用工具
            model_display_name: 当前 LLM 模型显示名称
            session_context: 会话元数据
            mode: 当前模式 (ask/plan/agent)
            model_id: 模型标识
            skip_catalogs: 是否跳过 Catalogs 层（向后兼容，优先使用 prompt_profile）
            prompt_profile: 产品场景 profile
            prompt_tier: 上下文窗口分档

        Returns:
            编译后的系统提示词
        """
        from ..prompt.budget import BudgetConfig
        from ..prompt.builder import build_system_prompt

        identity_dir = settings.identity_path

        budget_config = (
            BudgetConfig.for_context_window(context_window) if context_window > 0 else None
        )

        return build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=tools_enabled,
            tool_catalog=self._tool_catalog if tools_enabled else None,
            skill_catalog=self._skill_catalog if tools_enabled else None,
            mcp_catalog=self._mcp_catalog if tools_enabled else None,
            plugin_catalog=self._plugin_catalog if tools_enabled else None,
            memory_manager=self._memory_manager,
            task_description=task_description,
            budget_config=budget_config,
            include_tools_guide=tools_enabled,
            session_type=session_type,
            persona_manager=self._persona_manager,
            is_sub_agent=is_sub_agent,
            memory_keywords=memory_keywords,
            model_display_name=model_display_name,
            session_context=session_context,
            mode=mode,
            model_id=model_id,
            skip_catalogs=skip_catalogs,
            user_input_tokens=user_input_tokens,
            context_window=context_window,
            prompt_profile=prompt_profile,
            prompt_tier=prompt_tier,
        )

    def _build_compiled_sync(
        self,
        task_description: str = "",
        session_type: str = "cli",
        context_window: int = 0,
        is_sub_agent: bool = False,
    ) -> str:
        """同步版本：启动时构建初始系统提示词"""
        from ..prompt.budget import BudgetConfig
        from ..prompt.builder import build_system_prompt
        from ..prompt.compiler import check_compiled_outdated, compile_all

        identity_dir = settings.identity_path

        if check_compiled_outdated(identity_dir):
            logger.info("Compiled identity files outdated, recompiling...")
            compile_all(identity_dir)

        budget_config = (
            BudgetConfig.for_context_window(context_window) if context_window > 0 else None
        )

        return build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=True,
            tool_catalog=self._tool_catalog,
            skill_catalog=self._skill_catalog,
            mcp_catalog=self._mcp_catalog,
            plugin_catalog=self._plugin_catalog,
            memory_manager=self._memory_manager,
            task_description=task_description,
            budget_config=budget_config,
            include_tools_guide=True,
            session_type=session_type,
            persona_manager=self._persona_manager,
            is_sub_agent=is_sub_agent,
        )

