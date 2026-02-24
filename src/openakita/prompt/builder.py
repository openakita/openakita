"""
Prompt Builder - 消息组装模块

组装最终的系统提示词，整合编译产物、清单和记忆。

组装顺序:
1. Identity 层: soul.summary + agent.core + agent.tooling + policies
2. Persona 层: 当前人格描述（预设 + 用户自定义 + 上下文适配）
3. Runtime 层: runtime_facts (OS/CWD/时间)
4. Catalogs 层: tools + skills + mcp 清单
5. Memory 层: retriever 输出
6. User 层: user.summary
"""

import logging
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .budget import BudgetConfig, apply_budget, estimate_tokens
from .compiler import check_compiled_outdated, compile_all, get_compiled_content
from .retriever import retrieve_memory

if TYPE_CHECKING:
    from ..core.persona import PersonaManager
    from ..memory import MemoryManager
    from ..skills.catalog import SkillCatalog
    from ..tools.catalog import ToolCatalog
    from ..tools.mcp_catalog import MCPCatalog

logger = logging.getLogger(__name__)

_DEFAULT_POLICIES = """\
# OpenAkita Policies

## 三条红线（必须遵守）
1. **不编造**：不确定的信息必须说明是推断，不能假装成事实
2. **不假装执行**：必须真正调用工具，不能只说"我会..."而不行动
3. **需要外部信息时必须查**：不能凭记忆回答需要实时数据的问题

## 工具选择优先级（严格遵守）
收到任务后，按以下顺序决策：
1. **技能优先**：查已有技能清单，有匹配的直接用
2. **获取技能**：没有合适技能 → 搜索网络安装，或自己编写 SKILL.md 并加载
3. **持久化规则**：同类操作第二次出现时，必须封装为技能
4. **内置工具**：使用系统内置工具完成任务
5. **临时脚本**：一次性数据处理/格式转换 → 写文件+执行
6. **Shell 命令**：仅用于简单系统查询、安装包等一行命令

## 边界条件
- **工具不可用时**：可以纯文本完成，解释限制并给出手动步骤
- **关键输入缺失时**：调用 `ask_user` 工具进行澄清提问
- **技能配置缺失时**：主动辅助用户完成配置，不要直接拒绝
- **任务失败时**：说明原因 + 替代建议 + 需要用户提供什么
- **ask_user 超时**：系统等待约 2 分钟，未回复则自行决策或终止

## 记忆与事实
- 用户提到"之前/上次/我说过" → 主动 search_memory 查记忆
- 涉及用户偏好的任务 → 先查记忆和 profile 再行动
- 工具查到的信息 = 事实；凭知识回答需说明

## 输出格式
**任务型回复**：已执行 → 发现 → 下一步（如有）
**陪伴型回复**：自然对话，符合当前角色风格
"""


def build_system_prompt(
    identity_dir: Path,
    tools_enabled: bool = True,
    tool_catalog: Optional["ToolCatalog"] = None,
    skill_catalog: Optional["SkillCatalog"] = None,
    mcp_catalog: Optional["MCPCatalog"] = None,
    memory_manager: Optional["MemoryManager"] = None,
    task_description: str = "",
    budget_config: BudgetConfig | None = None,
    include_tools_guide: bool = False,
    session_type: str = "cli",  # 建议 8: 区分 CLI/IM
    precomputed_memory: str | None = None,
    persona_manager: Optional["PersonaManager"] = None,
) -> str:
    """
    组装系统提示词

    Args:
        identity_dir: identity 目录路径
        tools_enabled: 是否启用工具（影响 agent.tooling 注入）
        tool_catalog: ToolCatalog 实例（用于生成工具清单）
        skill_catalog: SkillCatalog 实例（用于生成技能清单）
        mcp_catalog: MCPCatalog 实例（用于 MCP 清单）
        memory_manager: MemoryManager 实例（用于记忆检索）
        task_description: 任务描述（用于记忆检索）
        budget_config: 预算配置
        include_tools_guide: 是否包含工具使用指南（向后兼容）
        session_type: 会话类型 "cli" 或 "im"（建议 8）

    Returns:
        完整的系统提示词
    """
    if budget_config is None:
        budget_config = BudgetConfig()

    # 目标：在单个 system_prompt 字符串内显式分段，模拟 system/developer/user/tool 结构
    system_parts: list[str] = []
    developer_parts: list[str] = []
    tool_parts: list[str] = []
    user_parts: list[str] = []

    # 1. 检查并加载编译产物
    if check_compiled_outdated(identity_dir):
        logger.info("Compiled files outdated, recompiling...")
        compile_all(identity_dir)

    compiled = get_compiled_content(identity_dir)

    # 2. 构建 Identity 层
    identity_section = _build_identity_section(
        compiled=compiled,
        identity_dir=identity_dir,
        tools_enabled=tools_enabled,
        budget_tokens=budget_config.identity_budget,
    )
    if identity_section:
        system_parts.append(identity_section)

    # 2.5 构建 Persona 层（新增: 在 Identity 和 Runtime 之间）
    if persona_manager:
        persona_section = _build_persona_section(persona_manager)
        if persona_section:
            system_parts.append(persona_section)

    # 3. 构建 Runtime 层
    runtime_section = _build_runtime_section()
    system_parts.append(runtime_section)

    # 3.5 构建会话类型规则（建议 8）
    persona_active = persona_manager.is_persona_active() if persona_manager else False
    session_rules = _build_session_type_rules(session_type, persona_active=persona_active)
    if session_rules:
        developer_parts.append(session_rules)

    # 4. 构建 Catalogs 层
    catalogs_section = _build_catalogs_section(
        tool_catalog=tool_catalog,
        skill_catalog=skill_catalog,
        mcp_catalog=mcp_catalog,
        budget_tokens=budget_config.catalogs_budget,
        include_tools_guide=include_tools_guide,
    )
    if catalogs_section:
        tool_parts.append(catalogs_section)

    # 5. 构建 Memory 层（支持预计算的异步结果，避免阻塞事件循环）
    if precomputed_memory is not None:
        memory_section = precomputed_memory
    else:
        memory_section = _build_memory_section(
            memory_manager=memory_manager,
            task_description=task_description,
            budget_tokens=budget_config.memory_budget,
        )
    if memory_section:
        developer_parts.append(memory_section)

    # 6. 构建 User 层
    user_section = _build_user_section(
        compiled=compiled,
        budget_tokens=budget_config.user_budget,
    )
    if user_section:
        user_parts.append(user_section)

    # 组装最终提示词
    sections: list[str] = []
    if system_parts:
        sections.append("## System\n\n" + "\n\n".join(system_parts))
    if developer_parts:
        sections.append("## Developer\n\n" + "\n\n".join(developer_parts))
    if user_parts:
        sections.append("## User\n\n" + "\n\n".join(user_parts))
    if tool_parts:
        sections.append("## Tool\n\n" + "\n\n".join(tool_parts))

    system_prompt = "\n\n---\n\n".join(sections)

    # 记录 token 统计
    total_tokens = estimate_tokens(system_prompt)
    logger.info(f"System prompt built: {total_tokens} tokens")

    return system_prompt


def _build_persona_section(persona_manager: "PersonaManager") -> str:
    """
    构建 Persona 层

    位于 Identity 和 Runtime 之间，注入当前人格描述。

    Args:
        persona_manager: PersonaManager 实例

    Returns:
        人格描述文本
    """
    try:
        return persona_manager.get_persona_prompt_section()
    except Exception as e:
        logger.warning(f"Failed to build persona section: {e}")
        return ""


def _build_identity_section(
    compiled: dict[str, str],
    identity_dir: Path,
    tools_enabled: bool,
    budget_tokens: int,
) -> str:
    """构建 Identity 层"""
    parts = []

    # 标题
    parts.append("# OpenAkita System")
    parts.append("")
    parts.append("你是 OpenAkita，一个全能自进化AI助手。")
    parts.append("")

    # Soul summary (~17%)
    if compiled.get("soul"):
        soul_result = apply_budget(compiled["soul"], budget_tokens // 6, "soul")
        parts.append(soul_result.content)
        parts.append("")

    # Agent core (~17%)
    if compiled.get("agent_core"):
        core_result = apply_budget(compiled["agent_core"], budget_tokens // 6, "agent_core")
        parts.append(core_result.content)
        parts.append("")

    # Agent tooling (~17%, only if tools enabled)
    if tools_enabled and compiled.get("agent_tooling"):
        tooling_result = apply_budget(
            compiled["agent_tooling"], budget_tokens // 6, "agent_tooling"
        )
        parts.append(tooling_result.content)
        parts.append("")

    # Policies (~50%，实测 627 tokens，是 identity 中最大的部分)
    policies_path = identity_dir / "prompts" / "policies.md"
    if policies_path.exists():
        policies = policies_path.read_text(encoding="utf-8")
    else:
        policies = _DEFAULT_POLICIES
        logger.warning("policies.md not found, using built-in defaults")
    policies_result = apply_budget(policies, budget_tokens // 2, "policies")
    parts.append(policies_result.content)

    return "\n".join(parts)


def _get_current_time(timezone_name: str = "Asia/Shanghai") -> str:
    """获取指定时区的当前时间，避免依赖服务器本地时区"""
    from datetime import timezone, timedelta

    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def _build_runtime_section() -> str:
    """构建 Runtime 层（运行时信息）"""
    import locale as _locale
    import shutil as _shutil
    import sys as _sys

    from ..config import settings
    from ..runtime_env import IS_FROZEN, can_pip_install, get_python_executable

    current_time = _get_current_time(settings.scheduler_timezone)

    # --- 部署模式与 Python 环境 ---
    deploy_mode = _detect_deploy_mode()
    ext_python = get_python_executable()
    pip_ok = can_pip_install()

    python_info = _build_python_info(IS_FROZEN, ext_python, pip_ok, settings)

    # --- 版本号 ---
    try:
        from .. import get_version_string
        version_str = get_version_string()
    except Exception:
        version_str = "unknown"

    # --- 工具可用性 ---
    tool_status = []
    try:
        browser_lock = settings.project_root / "data" / "browser.lock"
        if browser_lock.exists():
            tool_status.append("- **浏览器**: 可能已启动（检测到 lock 文件）")
        else:
            tool_status.append("- **浏览器**: 未启动（需要先调用 browser_open）")
    except Exception:
        tool_status.append("- **浏览器**: 状态未知")

    try:
        mcp_config = settings.project_root / "data" / "mcp_servers.json"
        if mcp_config.exists():
            tool_status.append("- **MCP 服务**: 配置已存在")
        else:
            tool_status.append("- **MCP 服务**: 未配置")
    except Exception:
        tool_status.append("- **MCP 服务**: 状态未知")

    tool_status_text = "\n".join(tool_status) if tool_status else "- 工具状态: 正常"

    # --- Shell 提示 ---
    shell_hint = ""
    if platform.system() == "Windows":
        shell_hint = (
            "\n- **Shell 注意**: Windows 环境，复杂文本处理（正则匹配、JSON/HTML 解析、批量文件操作）"
            "请使用 `write_file` 写 Python 脚本 + `run_shell python xxx.py` 执行，避免 PowerShell 转义问题。"
            "简单系统查询（进程/服务/文件列表）可直接使用 PowerShell cmdlet。"
        )

    # --- 系统环境 ---
    system_encoding = _sys.getdefaultencoding()
    try:
        default_locale = _locale.getdefaultlocale()
        locale_str = f"{default_locale[0]}, {default_locale[1]}" if default_locale[0] else "unknown"
    except Exception:
        locale_str = "unknown"

    shell_type = "PowerShell" if platform.system() == "Windows" else "bash"

    path_tools = []
    for cmd in ("git", "python", "node", "pip", "npm", "docker", "curl"):
        if _shutil.which(cmd):
            path_tools.append(cmd)
    path_tools_str = ", ".join(path_tools) if path_tools else "无"

    return f"""## 运行环境

- **OpenAkita 版本**: {version_str}
- **部署模式**: {deploy_mode}
- **当前时间**: {current_time}
- **操作系统**: {platform.system()} {platform.release()} ({platform.machine()})
- **当前工作目录**: {os.getcwd()}
- **工作区信息**: 需要操作系统文件（日志/配置/数据/截图等）时，先调用 `get_workspace_map` 获取目录布局
- **临时目录**: data/temp/{shell_hint}

### Python 环境
{python_info}

### 系统环境
- **系统编码**: {system_encoding}
- **默认语言环境**: {locale_str}
- **Shell**: {shell_type}
- **PATH 可用工具**: {path_tools_str}

## 工具可用性
{tool_status_text}

⚠️ **重要**：服务重启后浏览器、变量、连接等状态会丢失，执行任务前必须通过工具检查实时状态。
如果工具不可用，允许纯文本回复并说明限制。"""


def _detect_deploy_mode() -> str:
    """检测当前部署模式"""
    import importlib.metadata
    import sys as _sys

    from ..runtime_env import IS_FROZEN

    if IS_FROZEN:
        return "bundled (PyInstaller 打包)"

    # 检查 editable install (pip install -e)
    try:
        dist = importlib.metadata.distribution("openakita")
        direct_url = dist.read_text("direct_url.json")
        if direct_url and '"editable"' in direct_url:
            return "editable (pip install -e)"
    except Exception:
        pass

    # 检查是否在虚拟环境 + 源码目录中
    if _sys.prefix != _sys.base_prefix:
        return "source (venv)"

    # 检查是否通过 pip 安装
    try:
        importlib.metadata.version("openakita")
        return "pip install"
    except Exception:
        pass

    return "source"


def _build_python_info(is_frozen: bool, ext_python: str | None, pip_ok: bool, settings) -> str:
    """根据部署模式构建 Python 环境信息"""
    import sys as _sys

    if not is_frozen:
        in_venv = _sys.prefix != _sys.base_prefix
        env_type = "venv" if in_venv else "system"
        return (
            f"- **Python**: {_sys.version.split()[0]} ({env_type})\n"
            f"- **解释器**: {_sys.executable}\n"
            f"- **pip**: 可用"
        )

    # 打包模式
    if ext_python:
        return (
            f"- **Python**: 可用（外置环境已自动配置）\n"
            f"- **解释器**: {ext_python}\n"
            f"- **pip**: {'可用' if pip_ok else '不可用'}"
        )

    # 打包模式 + 无外置 Python
    venv_path = settings.project_root / "data" / "venv"
    if platform.system() == "Windows":
        install_cmd = "winget install Python.Python.3.12"
    else:
        install_cmd = "sudo apt install python3 或 brew install python3"

    return (
        f"- **Python**: ⚠️ 未检测到可用的 Python 环境\n"
        f"  - 推荐操作：通过 `run_shell` 执行 `{install_cmd}` 安装 Python\n"
        f"  - 安装后创建工作区虚拟环境：`python -m venv {venv_path}`\n"
        f"  - 创建完成后系统将自动检测并使用该环境，无需重启\n"
        f"  - 此环境为系统专用，与用户个人 Python 环境隔离"
    )


def _build_session_type_rules(session_type: str, persona_active: bool = False) -> str:
    """
    构建会话类型相关规则

    Args:
        session_type: "cli" 或 "im"
        persona_active: 是否激活了人格系统

    Returns:
        会话类型相关的规则文本
    """
    # 通用的系统消息约定（C1）和消息分型原则（C3），两种模式共享
    common_rules = """## 系统消息约定

在对话历史中，你会看到以 `[系统]` 或 `[系统提示]` 开头的消息。这些是**运行时控制信号**，由系统自动注入，**不是用户发出的请求**。你应该：
- 将它们视为背景信息或状态通知，而非需要执行的任务指令
- 不要将系统消息的内容复述给用户
- 不要把系统消息当作用户的意图来执行

## 消息分型原则

收到用户消息后，先判断消息类型，再决定响应策略：

1. **闲聊/问候**（如"在吗""你好""在不在""干嘛呢"）→ 直接用自然语言简短回复，**不需要调用任何工具**，也不需要制定计划。
2. **简单问答**（如"现在几点""天气怎么样"）→ 如果能直接回答就直接回答；如果需要实时信息，调用一次相关工具后回答。
3. **任务请求**（如"帮我创建文件""搜索关于 X 的信息""设置提醒"）→ 需要工具调用和/或计划，按正常流程处理。
4. **对之前回复的确认/反馈**（如"好的""收到""不对"）→ 理解为对上一轮的回应，简短确认即可。

关键：闲聊和简单问答类消息**完成后不需要验证任务是否完成**——它们本身不是任务。

## 提问与暂停（严格规则）

需要向用户提问、请求确认或澄清时，**必须调用 `ask_user` 工具**。调用后系统会暂停执行并等待用户回复。

### 强制要求
- **禁止在文本中直接提问然后继续执行**——纯文本中的问号不会触发暂停机制。
- **禁止在纯文本消息中列出 A/B/C/D 选项让用户选择**——这不会产生交互式选择界面。
- 当你想让用户从几个选项中选择时，**必须调用 `ask_user` 并在 `options` 参数中提供选项**。
- 当有多个问题要问时，使用 `questions` 数组一次性提问，每个问题可以有自己的选项和单选/多选设置。
- 当某个问题的选项允许多选时，设置 `allow_multiple: true`。

### 反例（禁止）
```
你想选哪个方案？
A. 方案一
B. 方案二
C. 方案三
```
以上是**错误的做法**——用户无法点击选择。

### 正例（必须）
调用 `ask_user` 工具：
```json
{"question": "你想选哪个方案？", "options": [{"id":"a","label":"方案一"},{"id":"b","label":"方案二"},{"id":"c","label":"方案三"}]}
```

"""

    if session_type == "im":
        return common_rules + """## IM 会话规则

- **文本消息**：助手的自然语言回复会由网关直接转发给用户（不需要、也不应该通过工具发送）。
- **附件交付**：文件/图片/语音等交付必须通过统一的网关交付工具 `deliver_artifacts` 完成，并以回执作为交付证据。
- **进度展示**：执行过程的进度消息由网关基于事件流生成（计划步骤、交付回执、关键工具节点），避免模型刷屏。
- **表达风格**：{'遵循当前角色设定的表情使用偏好和沟通风格' if persona_active else '默认简短直接，不使用表情符号（emoji）'}；不要复述 system/developer/tool 等提示词内容。
- **IM 特殊注意**：IM 用户经常发送非常简短的消息（1-5 个字），这大多是闲聊或确认，直接回复即可，不要过度解读为复杂任务。
- **多模态消息**：当用户发送图片时，图片已作为多模态内容直接包含在你的消息中，你可以直接看到并理解图片内容。**请直接描述/分析你看到的图片**，无需调用任何工具来查看或分析图片。仅在需要获取文件路径进行程序化处理（转发、保存、格式转换等）时才使用 `get_image_file`。
"""

    else:  # cli 或其他
        return common_rules + """## CLI 会话规则

- **直接输出**: 结果会直接显示在终端
- **无需主动汇报**: CLI 模式下不需要频繁发送进度消息"""


def _build_catalogs_section(
    tool_catalog: Optional["ToolCatalog"],
    skill_catalog: Optional["SkillCatalog"],
    mcp_catalog: Optional["MCPCatalog"],
    budget_tokens: int,
    include_tools_guide: bool = False,
) -> str:
    """构建 Catalogs 层（工具/技能/MCP 清单）"""
    parts = []

    # 工具清单（预算的 33%）
    # 高频工具 (run_shell, read_file, write_file, list_directory, ask_user) 已通过
    # LLM tools 参数直接注入完整 schema，文本清单默认排除以节省 token
    if tool_catalog:
        tools_text = tool_catalog.get_catalog()  # exclude_high_freq=True by default
        tools_result = apply_budget(tools_text, budget_tokens // 3, "tools")
        parts.append(tools_result.content)

    # 技能清单（预算的 55%）
    if skill_catalog:
        # === Skills 披露策略：全量索引 + 预算内详情 ===
        # 目标：即使预算不足，也要保证“技能名称全量可见”，避免清单被截断成半截。
        skills_budget = budget_tokens * 55 // 100
        skills_index = skill_catalog.get_index_catalog()

        # 给索引预留空间；剩余预算给详细列表（name + 1-line description）
        index_tokens = estimate_tokens(skills_index)
        remaining = max(0, skills_budget - index_tokens)

        skills_detail = skill_catalog.get_catalog()
        skills_detail_result = apply_budget(skills_detail, remaining, "skills", truncate_strategy="end")

        skills_rule = (
            "### 技能使用规则（必须遵守）\n"
            "- 执行任务前**必须先检查**已有技能清单，优先使用已有技能\n"
            "- 没有合适技能时，搜索安装或使用 skill-creator 创建，然后加载使用\n"
            "- 同类操作重复出现时，**必须**封装为永久技能\n"
            "- Shell 命令仅用于一次性简单操作，不是默认选择\n"
        )

        parts.append("\n\n".join([skills_index, skills_rule, skills_detail_result.content]).strip())

    # MCP 清单（预算的 10%）
    if mcp_catalog:
        mcp_text = mcp_catalog.get_catalog()
        if mcp_text:
            mcp_result = apply_budget(mcp_text, budget_tokens // 10, "mcp")
            parts.append(mcp_result.content)

    # 工具使用指南（可选，向后兼容）
    if include_tools_guide:
        parts.append(_get_tools_guide_short())

    return "\n\n".join(parts)


_MEMORY_SYSTEM_GUIDE = """## 你的记忆系统

你有一个持久化记忆系统，能跨对话记住信息。系统后台自动从对话中提取有价值的信息，每天自动整理去重，不重要的记忆会随时间衰减清理。

### 记忆中存放了什么
- **用户偏好/规则**：习惯、喜好、对你行为的要求
- **事实信息**：用户身份、项目信息、配置路径
- **技能经验/错误教训**：可复用的解决方案、需要避免的操作
- **历史操作记录**：过去对话的摘要（目标、结果、使用的工具）
- **原始对话存档**：完整的对话原文，包含工具调用的参数和返回值

### 搜索工具（三级）
需要回忆时，按需选择：

**`list_recent_tasks`** — 列出最近完成的任务
→ 用户问"你做了什么/干了什么/之前做了哪些事" → **优先用这个**，一次调用直接获取任务列表
→ 不需要猜关键词，比搜索快得多

**`search_memory`** — 搜索提炼后的知识
→ 用户偏好、规则、经验教训
→ 适合：了解用户习惯、查找经验、确认项目信息

**`search_conversation_traces`** — 搜索原始对话记录
→ 完整的工具调用（名称、参数、返回值）、逐轮对话原文
→ 仅当需要操作细节时才用（"上次用了什么命令"、"那次搜索的结果是什么"）

### 何时搜索
不需要每次都搜索。仅在以下情况按需使用：
- 用户问"做了什么/干了什么" → `list_recent_tasks`
- 用户提到"之前/上次/我说过" → `search_memory` 搜索确认
- 任务涉及用户偏好或习惯 → 查记忆
- 觉得之前做过类似任务 → 查可复用经验
- 不确定时 → 不搜索，避免旧信息干扰当前判断

### 何时主动写入
后台自动提取已覆盖日常信息，你只需在以下情况用 `add_memory` 记录：
- 完成复杂任务后总结可复用经验 → type=skill
- 犯错后找到正确方法 → type=error
- 发现用户深层需求或偏好 → type=preference/rule

### 当前注入的信息
下方是用户核心档案和当前任务状态，仅供快速参考。更多记忆请按需搜索。"""


def _build_memory_section(
    memory_manager: Optional["MemoryManager"],
    task_description: str,
    budget_tokens: int,
) -> str:
    """
    构建 Memory 层 — 渐进式披露:
    0. 记忆系统自描述 (告知 LLM 记忆系统的运作方式)
    1. Scratchpad (当前任务 + 近期完成)
    2. Core Memory (MEMORY.md 用户基本信息 + 永久规则)

    Dynamic Memories 不再自动注入，由 LLM 按需调用 search_memory 检索。
    """
    if not memory_manager:
        return ""

    parts: list[str] = []

    # Layer 0: 记忆系统自描述
    parts.append(_MEMORY_SYSTEM_GUIDE)

    # Layer 1: Scratchpad (当前任务)
    scratchpad_text = _build_scratchpad_section(memory_manager)
    if scratchpad_text:
        parts.append(scratchpad_text)

    # Layer 2: Core Memory (MEMORY.md — 用户基本信息 + 永久规则)
    core_budget = min(budget_tokens // 2, 300)
    core_memory = _get_core_memory(memory_manager, max_chars=core_budget * 3)
    if core_memory:
        parts.append(f"## 核心记忆\n\n{core_memory}")

    return "\n\n".join(parts)


def _build_scratchpad_section(memory_manager: Optional["MemoryManager"]) -> str:
    """从 UnifiedStore 读取 Scratchpad，注入当前任务 + 近期完成"""
    store = getattr(memory_manager, "store", None)
    if store is None:
        return ""
    try:
        pad = store.get_scratchpad()
        if pad:
            md = pad.to_markdown()
            if md:
                return md
    except Exception:
        pass
    return ""


def _get_core_memory(memory_manager: Optional["MemoryManager"], max_chars: int = 600) -> str:
    """获取 MEMORY.md 核心记忆（损坏时自动 fallback 到 .bak）"""
    memory_path = getattr(memory_manager, "memory_md_path", None)
    if not memory_path:
        return ""

    content = ""
    for path_to_try in [memory_path, memory_path.with_suffix(memory_path.suffix + ".bak")]:
        if not path_to_try.exists():
            continue
        try:
            content = path_to_try.read_text(encoding="utf-8").strip()
            if content:
                break
        except Exception:
            continue

    if not content:
        return ""

    if len(content) > max_chars:
        lines = content.split("\n")
        result_lines: list[str] = []
        current_len = 0
        for line in reversed(lines):
            if current_len + len(line) + 1 > max_chars:
                break
            result_lines.insert(0, line)
            current_len += len(line) + 1
        return "\n".join(result_lines)
    return content


def _build_user_section(
    compiled: dict[str, str],
    budget_tokens: int,
) -> str:
    """构建 User 层（用户信息）"""
    if not compiled.get("user"):
        return ""

    user_result = apply_budget(compiled["user"], budget_tokens, "user")
    return user_result.content


def _get_tools_guide_short() -> str:
    """获取简化版工具使用指南"""
    return """## 工具体系

你有三类工具可用：

1. **系统工具**：文件操作、浏览器、命令执行等
   - 查看清单 → `get_tool_info(tool_name)` → 直接调用

2. **Skills 技能**：可扩展能力模块
   - 查看清单 → `get_skill_info(name)` → `run_skill_script()`

3. **MCP 服务**：外部 API 集成
   - 查看清单 → `call_mcp_tool(server, tool, args)`

**原则**：
- 需要执行操作时使用工具；纯问答、闲聊、信息查询直接文字回复
- 任务完成后，用简洁的文字告知用户结果，不要继续调用工具
- 不要为了使用工具而使用工具"""


def get_prompt_debug_info(
    identity_dir: Path,
    tool_catalog: Optional["ToolCatalog"] = None,
    skill_catalog: Optional["SkillCatalog"] = None,
    mcp_catalog: Optional["MCPCatalog"] = None,
    memory_manager: Optional["MemoryManager"] = None,
    task_description: str = "",
) -> dict:
    """
    获取 prompt 调试信息

    用于 `openakita prompt-debug` 命令。

    Returns:
        包含各部分 token 统计的字典
    """
    budget_config = BudgetConfig()

    # 获取编译产物
    compiled = get_compiled_content(identity_dir)

    info = {
        "compiled_files": {
            "soul": estimate_tokens(compiled.get("soul", "")),
            "agent_core": estimate_tokens(compiled.get("agent_core", "")),
            "agent_tooling": estimate_tokens(compiled.get("agent_tooling", "")),
            "user": estimate_tokens(compiled.get("user", "")),
        },
        "catalogs": {},
        "memory": 0,
        "total": 0,
    }

    # 清单统计
    if tool_catalog:
        tools_text = tool_catalog.get_catalog()
        info["catalogs"]["tools"] = estimate_tokens(tools_text)

    if skill_catalog:
        skills_text = skill_catalog.get_catalog()
        info["catalogs"]["skills"] = estimate_tokens(skills_text)

    if mcp_catalog:
        mcp_text = mcp_catalog.get_catalog()
        info["catalogs"]["mcp"] = estimate_tokens(mcp_text) if mcp_text else 0

    # 记忆统计
    if memory_manager:
        memory_context = retrieve_memory(
            query=task_description,
            memory_manager=memory_manager,
            max_tokens=budget_config.memory_budget,
        )
        info["memory"] = estimate_tokens(memory_context)

    # 总计
    info["total"] = (
        sum(info["compiled_files"].values()) + sum(info["catalogs"].values()) + info["memory"]
    )

    info["budget"] = {
        "identity": budget_config.identity_budget,
        "catalogs": budget_config.catalogs_budget,
        "user": budget_config.user_budget,
        "memory": budget_config.memory_budget,
        "total": budget_config.total_budget,
    }

    return info
