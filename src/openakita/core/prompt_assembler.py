"""
提示词组装器

从 agent.py 提取的系统提示词构建逻辑，负责:
- 构建完整系统提示词（含身份、技能清单、MCP、记忆、工具列表）
- 编译管线 v2 (低 token 版本)
- 工具列表文本生成
- 系统环境信息注入
"""

import asyncio
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

        # 工具使用指南
        tools_guide = self._build_tools_guide()

        # 核心原则
        core_principles = self._build_core_principles()

        return f"""{base_prompt}

{system_info}
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
    ) -> str:
        """
        使用编译管线构建系统提示词 (v2) - 异步版本。

        Token 消耗降低约 55%。

        Args:
            task_description: 任务描述
            session_type: 会话类型

        Returns:
            编译后的系统提示词
        """
        from ..prompt.builder import build_system_prompt
        from ..prompt.compiler import check_compiled_outdated, compile_all
        from ..prompt.retriever import retrieve_memory

        identity_dir = settings.identity_path

        if check_compiled_outdated(identity_dir):
            logger.info("Compiled identity files outdated, recompiling...")
            compile_all(identity_dir)

        precomputed_memory = ""
        if self._memory_manager and task_description:
            try:
                precomputed_memory = await asyncio.to_thread(
                    retrieve_memory,
                    query=task_description,
                    memory_manager=self._memory_manager,
                    max_tokens=400,
                )
            except Exception as e:
                logger.warning(f"Async memory retrieval failed: {e}")

        return build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=True,
            tool_catalog=self._tool_catalog,
            skill_catalog=self._skill_catalog,
            mcp_catalog=self._mcp_catalog,
            memory_manager=self._memory_manager,
            task_description=task_description,
            include_tools_guide=True,
            session_type=session_type,
            precomputed_memory=precomputed_memory,
            persona_manager=self._persona_manager,
        )

    def _build_compiled_sync(self, task_description: str = "", session_type: str = "cli") -> str:
        """同步版本：启动时构建初始系统提示词"""
        from ..prompt.builder import build_system_prompt
        from ..prompt.compiler import check_compiled_outdated, compile_all

        identity_dir = settings.identity_path

        if check_compiled_outdated(identity_dir):
            logger.info("Compiled identity files outdated, recompiling...")
            compile_all(identity_dir)

        return build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=True,
            tool_catalog=self._tool_catalog,
            skill_catalog=self._skill_catalog,
            mcp_catalog=self._mcp_catalog,
            memory_manager=self._memory_manager,
            task_description=task_description,
            include_tools_guide=True,
            session_type=session_type,
            persona_manager=self._persona_manager,
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
                "browser_task", "browser_open", "browser_status", "browser_list_tabs",
                "browser_navigate", "browser_new_tab", "browser_switch_tab",
                "browser_click", "browser_type", "browser_get_content", "browser_screenshot",
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
| 浏览器 | **已关闭** | 必须先调用 `browser_status` 确认 |
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

    @staticmethod
    def _build_core_principles() -> str:
        """构建核心原则"""
        return """## 核心原则 (最高优先级!!!)

### 第一铁律：任务型请求必须使用工具

**⚠️ 先判断请求类型，再决定是否调用工具！**

| 请求类型 | 示例 | 处理方式 |
|---------|------|----------|
| **任务型** | "打开百度"、"提醒我开会"、"查天气" | ✅ **必须调用工具** |
| **对话型** | "你好"、"什么是机器学习"、"谢谢" | ✅ 可直接回复 |

### 第二铁律：没有工具就创造工具

**绝不说"我没有这个能力"！立即行动：**
- 临时脚本 → write_file + run_shell
- 搜索安装 → search_github → install_skill
- 创建技能 → skill-creator → load_skill

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
