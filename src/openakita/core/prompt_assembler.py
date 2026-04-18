"""
Prompt Assembler

System prompt construction logic extracted from agent.py, responsible for:
- Building complete system prompts (identity, skill catalog, MCP, memory, tool list)
- Compilation pipeline v2 (low-token version)
"""

import logging
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)


class PromptAssembler:
    """
    System Prompt Assembler.

    Integrates identity information, skill catalog, MCP catalog,
    memory context, tool list, and environment information to build
    a complete system prompt.
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
        Build the complete system prompt (using compilation pipeline v2).

        Args:
            task_description: Task description (used for memory retrieval)
            session_type: Session type, "cli" or "im"

        Returns:
            The complete system prompt
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
        Build system prompt using compilation pipeline (v2) - async version.

        Args:
            task_description: Task description
            session_type: Session type
            context_window: Target model context window size (enables adaptive budget when >0)
            is_sub_agent: Whether this is a sub-agent call
            tools_enabled: Whether tools are enabled
            model_display_name: Display name of the current LLM model
            session_context: Session metadata
            mode: Current mode (ask/plan/agent)
            model_id: Model identifier
            skip_catalogs: Whether to skip the Catalogs layer (backwards compat, prefer prompt_profile)
            prompt_profile: Product scenario profile
            prompt_tier: Context window tier

        Returns:
            The compiled system prompt
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
        """Sync version: build the initial system prompt at startup"""
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

