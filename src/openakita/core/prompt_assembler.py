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
        self._memory_manager = memory_manager
        self._profile_manager = profile_manager
        self._brain = brain
        self._persona_manager = persona_manager

        self._mcp_catalog_text: str = ""

    @property
    def mcp_catalog_text(self) -> str:
        return self._mcp_catalog_text

    @mcp_catalog_text.setter
    def mcp_catalog_text(self, value: str) -> None:
        self._mcp_catalog_text = value

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

        # MCP 清单
        mcp_catalog = self._mcp_catalog_text

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

        Returns:
            编译后的系统提示词
        """
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
            memory_manager=self._memory_manager,
            task_description=task_description,
            budget_config=budget_config,
            include_tools_guide=True,
            session_type=session_type,
            persona_manager=self._persona_manager,
            is_sub_agent=is_sub_agent,
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
            memory_manager=self._memory_manager,
            task_description=task_description,
            budget_config=budget_config,
            include_tools_guide=True,
            session_type=session_type,
            persona_manager=self._persona_manager,
            is_sub_agent=is_sub_agent,
        )

    def _generate_tools_text(self, tools: list[dict]) -> str:
        """从工具列表动态生成工具列表文本"""
        categories = {
            "File System": ["run_shell", "write_file", "read_file", "list_directory"],
            "Skills Management": [
                "list_skills", "get_skill_info", "run_skill_script",
                "get_skill_reference", "install_skill", "load_skill", "reload_skill",
            ],
            "Memory Management": ["add_memory", "search_memory", "get_memory_stats"],
            "Browser Automation": [
                "browser_navigate", "browser_task", "browser_open",
                "browser_get_content", "browser_screenshot", "view_image",
                "browser_close",
            ],
            "Scheduled Tasks": [
                "schedule_task", "list_scheduled_tasks",
                "cancel_scheduled_task", "trigger_scheduled_task",
            ],
        }

        tool_map = {t["name"]: t for t in tools}
        lines = ["## Available Tools"]

        for category, tool_names in categories.items():
            existing_tools = [(name, tool_map[name]) for name in tool_names if name in tool_map]
            if existing_tools:
                lines.append(f"\n### {category}")
                for name, tool_def in existing_tools:
                    desc = tool_def.get("description", "")
                    lines.append(f"- **{name}**: {desc}")

        # 未分类的工具
        categorized = set()
        for names in categories.values():
            categorized.update(names)
        uncategorized = [(t["name"], t) for t in tools if t["name"] not in categorized]
        if uncategorized:
            lines.append("\n### Other Tools")
            for name, tool_def in uncategorized:
                desc = tool_def.get("description", "")
                lines.append(f"- **{name}**: {desc}")

        return "\n".join(lines)

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
        """构建工具使用指南"""
        return """
## 工具体系说明

你有三类工具可以使用，**它们都是工具，都可以调用**：

### 1. 系统工具（渐进式披露）

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "Available System Tools" 清单 | 了解有哪些工具可用 |
| 2 | `get_tool_info(tool_name)` | 获取工具的完整参数定义 |
| 3 | 直接调用工具 | 如 `read_file(path="...")` |

### 2. Skills 技能（渐进式披露）

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "Available Skills" 清单 | 了解有哪些技能可用 |
| 2 | `get_skill_info(skill_name)` | 获取技能的详细使用说明 |
| 3 | `run_skill_script(skill_name, script_name)` | 执行技能提供的脚本 |

### 3. MCP 外部服务（全量暴露）

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "MCP Servers" 清单 | 包含完整的工具定义和参数 |
| 2 | `call_mcp_tool(server, tool_name, arguments)` | 直接调用 |

### 工具选择原则

1. **系统工具**：文件操作、命令执行、浏览器、记忆等
2. **Skills**：复杂任务、特定领域能力
3. **MCP**：外部服务集成
4. **找不到工具？使用 `skill-creator` 技能创建一个！**

**记住：这三类都是工具，都可以调用，不要说"我没有这个能力"！**
"""

    def _build_core_principles(self) -> str:
        """构建核心原则"""
        hub_enabled = settings.hub_enabled

        if hub_enabled:
            create_tool_lines = (
                "- 平台搜索 → search_hub_agents / search_store_skills → install（优先从 OpenAkita 平台查找现成 Agent 或 Skill）\n"
                "- GitHub 安装 → search_github → install_skill\n"
                "- 临时脚本 → write_file + run_shell\n"
                "- 创建技能 → skill-creator → load_skill"
            )
            create_tool_note = (
                "> 用户需要某种能力时，先搜平台（Agent Hub / Skill Store），再搜 GitHub，最后自建。\n"
                "> 平台离线时跳过平台步骤，直接走 GitHub 或自建。"
            )
        else:
            create_tool_lines = (
                "- GitHub 安装 → search_github → install_skill\n"
                "- 临时脚本 → write_file + run_shell\n"
                "- 创建技能 → skill-creator → load_skill"
            )
            create_tool_note = (
                "> 用户需要某种能力时，先搜 GitHub，再自建。"
            )

        return f"""## 核心原则 (最高优先级!!!)

### 第一铁律：任务型请求必须使用工具

**⚠️ 先判断请求类型，再决定是否调用工具！**

| 请求类型 | 示例 | 处理方式 |
|---------|------|----------|
| **任务型** | "打开百度"、"提醒我开会"、"查天气" | ✅ **必须调用工具** |
| **对话型** | "你好"、"什么是机器学习"、"谢谢" | ✅ 可直接回复 |

### 第二铁律：没有工具就创造工具

**绝不说"我没有这个能力"！立即行动：**
{create_tool_lines}

{create_tool_note}

### 第三铁律：问题自己解决

报错了？自己读日志、分析、修复。缺信息？自己用工具查找。

### 第四铁律：永不放弃

第一次失败？换个方法再试。工具不够用？创建新工具。

**禁止说"我做不到"、"这超出了我的能力"！**

---

## 重要提示

### 诚实原则 (极其重要!!!)
**绝对禁止编造不存在的功能或进度！**
用户信任比看起来厉害更重要！

### 记忆管理
**主动使用记忆功能**，学到新东西记录为 FACT，发现偏好记录为 PREFERENCE。

### 记忆使用原则
**上下文优先**：当前对话内容永远优先于记忆中的信息。不要让记忆主导对话。
"""
