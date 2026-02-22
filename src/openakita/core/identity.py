"""
Identity 模块 - 加载和管理核心文档

负责:
- 加载核心文档 (SOUL.md, AGENT.md, USER.md, MEMORY.md)
- 生成系统提示词 (渐进式披露)
- 提取精简版本用于系统提示

注入策略 (v2 - 编译管线):
- 编译产物: soul.summary + agent.core + agent.tooling + user.summary
- 硬规则: policies.md
- 记忆: 语义检索相关片段
- 向后兼容: get_system_prompt() 保留全文注入模式

旧策略 (v1 - 全文注入，已废弃但保留兼容):
- SOUL.md: 每次注入 (精简核心原则)
- AGENT.md: 每次注入 (精简行为规范)
- USER.md: 每次注入 (已填充的偏好)
- MEMORY.md: 按需加载 (当前任务部分)
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..config import settings

if TYPE_CHECKING:
    from ..memory import MemoryManager
    from ..skills.catalog import SkillCatalog
    from ..tools.catalog import ToolCatalog
    from ..tools.mcp_catalog import MCPCatalog

logger = logging.getLogger(__name__)


class Identity:
    """Agent 身份管理器"""

    def __init__(
        self,
        soul_path: Path | None = None,
        agent_path: Path | None = None,
        user_path: Path | None = None,
        memory_path: Path | None = None,
    ):
        self.soul_path = soul_path or settings.soul_path
        self.agent_path = agent_path or settings.agent_path
        self.user_path = user_path or settings.user_path
        self.memory_path = memory_path or settings.memory_path

        self._soul: str | None = None
        self._agent: str | None = None
        self._user: str | None = None
        self._memory: str | None = None

    def load(self) -> None:
        """加载所有核心文档"""
        self._soul = self._load_file(self.soul_path, "SOUL.md")
        self._agent = self._load_file(self.agent_path, "AGENT.md")
        self._user = self._load_file(self.user_path, "USER.md")
        self._memory = self._load_file(self.memory_path, "MEMORY.md")
        logger.info("Identity loaded: SOUL.md, AGENT.md, USER.md, MEMORY.md")

    def _load_file(self, path: Path, name: str) -> str:
        """加载单个文件，如果不存在则尝试从模板创建"""
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")

            # 尝试从 .example 模板创建
            example_path = path.parent / f"{path.name}.example"
            if example_path.exists():
                content = example_path.read_text(encoding="utf-8")
                # 确保父目录存在
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                logger.info(f"Created {name} from template")
                return content

            logger.warning(f"{name} not found at {path}")
            return ""
        except Exception as e:
            logger.error(f"Failed to load {name}: {e}")
            return ""

    @property
    def soul(self) -> str:
        """获取 SOUL.md 内容"""
        if self._soul is None:
            self.load()
        return self._soul or ""

    @property
    def agent(self) -> str:
        """获取 AGENT.md 内容"""
        if self._agent is None:
            self.load()
        return self._agent or ""

    @property
    def user(self) -> str:
        """获取 USER.md 内容"""
        if self._user is None:
            self.load()
        return self._user or ""

    @property
    def memory(self) -> str:
        """获取 MEMORY.md 内容"""
        if self._memory is None:
            self.load()
        return self._memory or ""

    def get_soul_summary(self) -> str:
        """
        获取 SOUL.md 完整内容

        动态读取文件内容，用户修改后立即生效
        """
        soul = self.soul
        if not soul:
            return ""

        return f"## Soul (核心哲学)\n\n{soul}\n"

    def get_agent_summary(self) -> str:
        """
        获取 AGENT.md 完整内容

        动态读取文件内容，用户修改后立即生效
        """
        agent = self.agent
        if not agent:
            return ""

        return f"## Agent (行为规范)\n\n{agent}\n"

    def get_user_summary(self) -> str:
        """
        获取 USER.md 完整内容

        动态读取文件内容，用户修改后立即生效
        """
        user = self.user
        if not user:
            return "## User (用户偏好)\n\n(用户偏好将在交互中学习)\n"

        return f"## User (用户偏好)\n\n{user}\n"

    def get_memory_summary(self, include_active_task: bool = True) -> str:
        """
        获取 MEMORY.md 完整内容

        动态读取文件内容，用户修改后立即生效

        Args:
            include_active_task: 保留参数以兼容现有调用（不再使用）
        """
        memory = self.memory
        if not memory:
            return ""

        return f"## Memory (核心记忆)\n\n{memory}\n"

    @staticmethod
    def _get_configured_timezone() -> str:
        """从 settings 获取配置的时区"""
        try:
            return settings.scheduler_timezone
        except Exception:
            return "Asia/Shanghai"

    def get_system_prompt(self, include_active_task: bool = True) -> str:
        """
        生成系统提示词

        包含所有核心文档的精简版本

        Args:
            include_active_task: 是否包含活跃任务（IM Session 应设为 False）
        """
        from datetime import datetime, timezone, timedelta

        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(self._get_configured_timezone())
        except Exception:
            tz = timezone(timedelta(hours=8))
        current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        return f"""# OpenAkita System

你是 OpenAkita，一个全能自进化AI助手。

**当前时间: {current_time}**

## ⚠️ 响应质量要求（最高优先级，严格执行）

### ⚡ 多步骤任务必须先创建计划！（最重要！）

**在执行任何工具之前，先判断任务是否需要 2 步以上：**

| 用户请求 | 步骤数 | 正确做法 |
|---------|--------|---------|
| "打开百度" | 1步 | 直接 browser_navigate |
| "打开百度，搜索天气" | 2步 | 直接执行 |
| "打开百度，搜索天气，截图发我" | 3步+ | ⚠️ **先 create_plan！** |

**触发 Plan 模式的信号词**：
- "然后"、"接着"、"之后"、"并且"、逗号分隔的多个动作
- 包含多个动作：打开+搜索+截图+发送

**正确流程**：
```
用户: "打开百度搜索天气截图发我"
→ 1. create_plan(steps=[打开百度搜索天气并截图, 发送])
→ 2. browser_task("打开百度搜索天气并截图") + update_plan_step
→ 3. deliver_artifacts + update_plan_step
→ 4. complete_plan
```

### 请求类型判断（重要！先判断再行动）

| 类型 | 特征 | 处理方式 |
|------|------|----------|
| **任务型请求** | 要求执行操作：打开、创建、查询、提醒、修改、删除 | ✅ **必须调用工具** |
| **对话型请求** | 简单问候、知识问答、礼貌用语 | ✅ **可以直接回复** |

**对话型请求示例**（可以直接回复，不需要调用工具）：
- "你好"、"hi"、"早上好" → 友好问候回复
- "什么是机器学习"、"Python是什么" → 直接解释概念
- "谢谢"、"再见" → 礼貌回复
- "明白了"、"好的" → 简单确认

### 第一铁律：任务型请求必须立即使用工具

**⚠️ 用户发送任务型请求时，必须立即调用工具执行！**

| 用户请求（任务型） | ❌ 绝对禁止 | ✅ 正确做法 |
|---------|-----------|-----------|
| "帮我打开百度" | "我理解了您的请求" | 立即调用 browser 工具打开 |
| "查一下天气" | "好的，我来查询" | 用 browser 工具打开天气网站 |
| "创建一个文件" | "我明白了" | 立即调用 write_file |
| "提醒我开会" | "我会提醒你" | **立即调用 schedule_task** |

**绝对禁止的敷衍响应**（仅针对任务型请求）:
- ❌ "我理解了您的请求" 但没有工具调用 - **禁止！**
- ❌ "我明白了" 但没有工具调用 - **禁止！**
- ❌ "好的，我会提醒你" 但没有调用 schedule_task - **禁止！**
- ❌ 只描述会做什么，但不实际执行 - **禁止！**

**任务型请求的响应必须包含**:
- ✅ 工具调用（browser、schedule_task、write_file、run_shell 等）
- ✅ 或具体的输出内容（代码、方案、分析结果）
- ✅ 或明确需要澄清的问题（列出具体选项）

**判断标准**：
- 任务型请求：如果响应里没有工具调用，就是在敷衍用户！
- 对话型请求：直接回复文字是正确做法，不需要调用工具。

### ⚠️ 定时任务/提醒（特别重要！）

**当用户说"提醒我"、"X分钟后"、"每天X点"时，必须立即调用 schedule_task 工具！**

❌ **绝对禁止**：回复"好的，我会提醒你" - 这样不会创建任务！
✅ **正确做法**：立即调用 schedule_task 工具创建任务

**task_type 选择**：
- `reminder`（90%情况）：只需到时间发消息提醒，如"提醒我喝水"
- `task`（10%情况）：需要 AI 执行操作，如"每天查天气告诉我"

---

{self.get_agent_summary()}

{self.get_user_summary()}

{self.get_memory_summary(include_active_task=include_active_task)}

{self.get_soul_summary()}
"""

    def get_session_system_prompt(self) -> str:
        """
        生成用于 IM Session 的系统提示词

        不包含全局 Active Task，避免与 Session 上下文冲突
        """
        return self.get_system_prompt(include_active_task=False)

    def get_compiled_prompt(
        self,
        tools_enabled: bool = True,
        tool_catalog: Optional["ToolCatalog"] = None,
        skill_catalog: Optional["SkillCatalog"] = None,
        mcp_catalog: Optional["MCPCatalog"] = None,
        memory_manager: Optional["MemoryManager"] = None,
        task_description: str = "",
    ) -> str:
        """
        使用新的编译管线生成系统提示词 (v2)

        相比 get_system_prompt()（全文注入），这个方法:
        - 使用编译后的摘要，而非全文
        - Token 消耗降低约 55%
        - 保留所有核心规则

        Args:
            tools_enabled: 是否启用工具
            tool_catalog: ToolCatalog 实例
            skill_catalog: SkillCatalog 实例
            mcp_catalog: MCPCatalog 实例
            memory_manager: MemoryManager 实例
            task_description: 任务描述（用于记忆检索）

        Returns:
            编译后的系统提示词
        """
        from ..prompt.builder import build_system_prompt

        identity_dir = self.soul_path.parent

        return build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=tools_enabled,
            tool_catalog=tool_catalog,
            skill_catalog=skill_catalog,
            mcp_catalog=mcp_catalog,
            memory_manager=memory_manager,
            task_description=task_description,
        )

    def ensure_compiled(self) -> bool:
        """
        确保编译产物存在且不过期

        Returns:
            True 如果编译产物可用
        """
        from ..prompt.compiler import check_compiled_outdated, compile_all

        identity_dir = self.soul_path.parent

        if check_compiled_outdated(identity_dir):
            logger.info("Compiling identity documents...")
            compile_all(identity_dir)
            return True

        return True

    def get_full_document(self, doc_name: str) -> str:
        """
        获取完整文档内容 (Level 2)

        当需要详细信息时调用

        Args:
            doc_name: 文档名称 (soul/agent/user/memory)

        Returns:
            完整文档内容
        """
        docs = {
            "soul": self.soul,
            "agent": self.agent,
            "user": self.user,
            "memory": self.memory,
        }
        return docs.get(doc_name.lower(), "")

    def get_behavior_rules(self) -> list[str]:
        """提取行为规则"""
        rules = [
            "任务未完成，绝不退出",
            "遇到错误，分析并重试",
            "缺少能力，自动获取",
            "每次迭代保存进度到 MEMORY.md",
            "不删除用户数据（除非明确要求）",
            "不访问敏感系统路径",
            "不在未告知的情况下安装收费软件",
            "不放弃任务（除非用户明确取消）",
        ]
        return rules

    def get_prohibited_actions(self) -> list[str]:
        """获取禁止的行为"""
        return [
            "提供创建大规模杀伤性武器的详细说明",
            "生成涉及未成年人的不当内容",
            "生成可能直接促进攻击关键基础设施的内容",
            "创建旨在造成重大损害的恶意代码",
            "破坏AI监督机制",
            "对用户撒谎或隐瞒重要信息",
        ]

    def update_memory(self, section: str, content: str) -> bool:
        """
        更新 MEMORY.md 的特定部分

        Args:
            section: 要更新的部分名称
            content: 新内容

        Returns:
            是否成功
        """
        try:
            memory = self.memory

            # 查找并替换指定部分
            pattern = rf"(### {section}\s*)(.*?)(?=###|\Z)"
            replacement = f"\\1\n{content}\n\n"

            new_memory = re.sub(pattern, replacement, memory, flags=re.DOTALL)

            if new_memory != memory:
                self.memory_path.write_text(new_memory, encoding="utf-8")
                self._memory = new_memory
                logger.info(f"Updated MEMORY.md section: {section}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to update MEMORY.md: {e}")
            return False

    def update_user_preference(self, key: str, value: str) -> bool:
        """
        更新 USER.md 中的用户偏好

        Args:
            key: 偏好键名
            value: 偏好值

        Returns:
            是否成功
        """
        try:
            user = self.user

            # 替换 [待学习] 为实际值
            pattern = rf"(\*\*{key}\*\*:\s*)\[待学习\]"
            replacement = f"\\1{value}"

            new_user = re.sub(pattern, replacement, user)

            if new_user != user:
                self.user_path.write_text(new_user, encoding="utf-8")
                self._user = new_user
                logger.info(f"Updated USER.md: {key} = {value}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to update USER.md: {e}")
            return False
