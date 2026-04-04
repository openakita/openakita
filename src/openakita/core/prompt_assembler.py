"""
提示词组装器

从 agent.py 提取的系统提示词构建逻辑，负责:
- 构建完整系统提示词（含身份、技能清单、MCP、记忆、工具列表）
- 编译管线 v2 (低 token 版本)
- 工具列表文本生成
- 系统环境信息注入
"""

import logging
import os
import platform
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
        base_prompt: str,
        tools: list[dict],
        *,
        task_description: str = "",
        use_compiled: bool = False,
        session_type: str = "cli",
        skill_catalog_text: str = "",
    ) -> str:
        """
        构建完整的系统提示词。

        Args:
            base_prompt: 基础提示词（身份信息）
            tools: 当前工具列表
            task_description: 任务描述（用于记忆检索）
            use_compiled: 是否使用编译管线 v2
            session_type: 会话类型 "cli" 或 "im"
            skill_catalog_text: 技能清单文本

        Returns:
            完整的系统提示词
        """
        if use_compiled:
            return self._build_compiled_sync(task_description, session_type=session_type)

        # 技能清单
        skill_catalog = skill_catalog_text or self._skill_catalog.generate_catalog()

        # MCP 清单（从 MCPCatalog 获取，内部自动缓存和失效）
        mcp_catalog = self._mcp_catalog.get_catalog() if self._mcp_catalog else ""

        # 插件清单
        plugin_catalog = ""
        if self._plugin_catalog is not None:
            try:
                plugin_catalog = self._plugin_catalog.get_catalog()
            except Exception:
                pass

        # 相关记忆
        memory_context = self._memory_manager.get_injection_context(task_description)

        # 工具列表
        tools_text = self._generate_tools_text(tools)

        # 用户档案
        profile_prompt = ""
        if self._profile_manager.is_first_use():
            profile_prompt = self._profile_manager.get_onboarding_prompt()
        else:
            profile_prompt = self._profile_manager.get_daily_question_prompt()

        # 系统环境信息
        system_info = self._build_system_info()

        # 环境快照 (Agent Harness)
        env_snapshot = self.build_environment_snapshot()

        # 工具使用指南
        tools_guide = self._build_tools_guide()

        # 核心原则
        core_principles = self._build_core_principles()

        return f"""{base_prompt}

{system_info}
{env_snapshot}
{skill_catalog}
{plugin_catalog}
{mcp_catalog}
{memory_context}

{tools_text}

{tools_guide}

{core_principles}
{profile_prompt}"""

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
    ) -> str:
        """
        使用编译管线构建系统提示词 (v2) - 异步版本。

        渐进式披露：不再预注入动态记忆，由 _build_memory_section 注入
        记忆系统自描述 + Scratchpad + Core Memory，LLM 按需搜索。

        Args:
            task_description: 任务描述
            session_type: 会话类型
            context_window: 目标模型上下文窗口大小（>0 时启用自适应预算）
            is_sub_agent: 是否为子 Agent 调用（子 Agent 不注入委派优先声明）
            tools_enabled: 是否启用工具（CHAT 轻量路径传 False 跳过 Catalogs 层）
            model_display_name: 当前 LLM 模型显示名称（动态注入）
            session_context: 会话元数据（session_id、通道、类型等）
            mode: 当前模式 (ask/plan/agent)
            model_id: 模型标识（用于 per-model 基础 prompt）
            skip_catalogs: 是否跳过 Catalogs 层（CHAT 意图使用）

        Returns:
            编译后的系统提示词
        """
        from ..prompt.budget import BudgetConfig
        from ..prompt.builder import build_system_prompt

        identity_dir = settings.identity_path

        budget_config = (
            BudgetConfig.for_context_window(context_window)
            if context_window > 0
            else None
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
            BudgetConfig.for_context_window(context_window)
            if context_window > 0
            else None
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

    def _generate_tools_text(self, tools: list[dict]) -> str:
        """[DEPRECATED] 工具清单已迁移至 ToolCatalog.generate_catalog()"""
        return ""

    @staticmethod
    def build_environment_snapshot(
        *,
        plan_section: str = "",
        budget_summary: dict | None = None,
        recent_errors: list[str] | None = None,
        scratchpad_summary: str = "",
    ) -> str:
        """
        构建环境快照 (Agent Harness: Environment Snapshot)。

        在任务开始时确定性地生成环境信息，减少 Agent 用工具探索环境的 token 消耗。
        注入到 system_prompt 中，放在系统信息之后。
        """
        parts = ["## 环境快照"]

        # 顶层文件树（工作目录已在 _build_system_info 中输出，此处不重复）
        try:
            cwd = os.getcwd()
            entries = sorted(os.listdir(cwd))[:30]
            dirs = [e for e in entries if os.path.isdir(os.path.join(cwd, e)) and not e.startswith(".")]
            files = [e for e in entries if os.path.isfile(os.path.join(cwd, e)) and not e.startswith(".")]
            if dirs:
                parts.append(f"- 子目录: {', '.join(dirs[:15])}")
            if files:
                parts.append(f"- 根文件: {', '.join(files[:10])}")
        except Exception:
            pass

        # Plan 状态
        if plan_section:
            parts.append(f"\n### 当前计划\n{plan_section}")

        # 预算状态
        if budget_summary:
            budget_parts = []
            tokens = budget_summary.get("tokens_used", 0)
            iterations = budget_summary.get("iterations_used", 0)
            elapsed = budget_summary.get("elapsed_seconds", 0)
            limits = budget_summary.get("limits", {})

            if tokens:
                max_t = limits.get("max_tokens", 0)
                budget_parts.append(f"tokens: {tokens}" + (f"/{max_t}" if max_t else ""))
            if iterations:
                max_i = limits.get("max_iterations", 0)
                budget_parts.append(f"iterations: {iterations}" + (f"/{max_i}" if max_i else ""))
            if elapsed > 0:
                budget_parts.append(f"elapsed: {elapsed:.0f}s")

            if budget_parts:
                parts.append(f"- 资源使用: {', '.join(budget_parts)}")

        # 最近错误
        if recent_errors:
            parts.append("\n### 最近错误")
            for err in recent_errors[-3:]:
                parts.append(f"- {err[:200]}")

        # 工作记忆
        if scratchpad_summary:
            parts.append(f"\n### 工作记忆\n{scratchpad_summary}")

        return "\n".join(parts)

    @staticmethod
    def _build_system_info() -> str:
        """构建系统环境信息"""
        return f"""## 运行环境

- **操作系统**: {platform.system()} {platform.release()}
- **当前工作目录**: {os.getcwd()}
- **临时目录**:
  - Windows: 使用当前目录下的 `data/temp/` 或 `%TEMP%`
  - Linux/macOS: 使用当前目录下的 `data/temp/` 或 `/tmp`
- **建议**: 创建临时文件时优先使用 `data/temp/` 目录

## ⚠️ 重要：运行时状态不持久化

**服务重启后以下状态会丢失：**

| 状态 | 重启后 | 正确做法 |
|------|--------|----------|
| 浏览器 | **已关闭** | 必须先调用 `browser_open` 确认状态 |
| 变量/内存数据 | **已清空** | 通过工具重新获取 |
| 临时文件 | **可能清除** | 重新检查文件是否存在 |
| 网络连接 | **已断开** | 需要重新建立连接 |"""

    @staticmethod
    def _build_tools_guide() -> str:
        """[DEPRECATED] 已迁移至 prompt.builder._get_tools_guide_short()"""
        return ""

    @staticmethod
    def _build_core_principles() -> str:
        """[DEPRECATED] 已迁移至 AGENT.md + SOUL.md + prompt.builder._CORE_RULES"""
        return ""
