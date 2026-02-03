"""
Prompt Builder - 消息组装模块

组装最终的系统提示词，整合编译产物、清单和记忆。

组装顺序:
1. Identity 层: soul.summary + agent.core + agent.tooling + policies
2. Runtime 层: runtime_facts (OS/CWD/时间)
3. Catalogs 层: tools + skills + mcp 清单
4. Memory 层: retriever 输出
5. User 层: user.summary
"""

import logging
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .compiler import get_compiled_content, check_compiled_outdated, compile_all
from .retriever import retrieve_memory
from .budget import apply_budget, BudgetConfig, estimate_tokens

if TYPE_CHECKING:
    from ..memory import MemoryManager
    from ..tools.catalog import ToolCatalog
    from ..skills.catalog import SkillCatalog
    from ..tools.mcp_catalog import MCPCatalog

logger = logging.getLogger(__name__)


def build_system_prompt(
    identity_dir: Path,
    tools_enabled: bool = True,
    tool_catalog: Optional["ToolCatalog"] = None,
    skill_catalog: Optional["SkillCatalog"] = None,
    mcp_catalog: Optional["MCPCatalog"] = None,
    memory_manager: Optional["MemoryManager"] = None,
    task_description: str = "",
    budget_config: Optional[BudgetConfig] = None,
    include_tools_guide: bool = False,
    session_type: str = "cli",  # 建议 8: 区分 CLI/IM
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
    
    sections = []
    
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
        sections.append(identity_section)
    
    # 3. 构建 Runtime 层
    runtime_section = _build_runtime_section()
    sections.append(runtime_section)
    
    # 3.5 构建会话类型规则（建议 8）
    session_rules = _build_session_type_rules(session_type)
    if session_rules:
        sections.append(session_rules)
    
    # 4. 构建 Catalogs 层
    catalogs_section = _build_catalogs_section(
        tool_catalog=tool_catalog,
        skill_catalog=skill_catalog,
        mcp_catalog=mcp_catalog,
        budget_tokens=budget_config.catalogs_budget,
        include_tools_guide=include_tools_guide,
    )
    if catalogs_section:
        sections.append(catalogs_section)
    
    # 5. 构建 Memory 层
    memory_section = _build_memory_section(
        memory_manager=memory_manager,
        task_description=task_description,
        budget_tokens=budget_config.memory_budget,
    )
    if memory_section:
        sections.append(memory_section)
    
    # 6. 构建 User 层
    user_section = _build_user_section(
        compiled=compiled,
        budget_tokens=budget_config.user_budget,
    )
    if user_section:
        sections.append(user_section)
    
    # 组装最终提示词
    system_prompt = '\n\n'.join(sections)
    
    # 记录 token 统计
    total_tokens = estimate_tokens(system_prompt)
    logger.info(f"System prompt built: {total_tokens} tokens")
    
    return system_prompt


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
    
    # Soul summary
    if compiled.get("soul"):
        soul_result = apply_budget(compiled["soul"], budget_tokens // 4, "soul")
        parts.append(soul_result.content)
        parts.append("")
    
    # Agent core
    if compiled.get("agent_core"):
        core_result = apply_budget(compiled["agent_core"], budget_tokens // 4, "agent_core")
        parts.append(core_result.content)
        parts.append("")
    
    # Agent tooling (only if tools enabled)
    if tools_enabled and compiled.get("agent_tooling"):
        tooling_result = apply_budget(compiled["agent_tooling"], budget_tokens // 4, "agent_tooling")
        parts.append(tooling_result.content)
        parts.append("")
    
    # Policies
    policies_path = identity_dir / "prompts" / "policies.md"
    if policies_path.exists():
        policies = policies_path.read_text(encoding="utf-8")
        policies_result = apply_budget(policies, budget_tokens // 4, "policies")
        parts.append(policies_result.content)
    
    return '\n'.join(parts)


def _build_runtime_section() -> str:
    """构建 Runtime 层（运行时信息）"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 建议 32: 检测工具可用性
    tool_status = []
    
    # 检查浏览器状态
    try:
        from pathlib import Path
        browser_lock = Path("data/browser.lock")
        if browser_lock.exists():
            tool_status.append("- **浏览器**: 可能已启动（检测到 lock 文件）")
        else:
            tool_status.append("- **浏览器**: 未启动（需要先调用 browser_open）")
    except:
        tool_status.append("- **浏览器**: 状态未知")
    
    # 检查 MCP 服务
    try:
        mcp_config = Path("data/mcp_servers.json")
        if mcp_config.exists():
            tool_status.append("- **MCP 服务**: 配置已存在")
        else:
            tool_status.append("- **MCP 服务**: 未配置")
    except:
        tool_status.append("- **MCP 服务**: 状态未知")
    
    tool_status_text = "\n".join(tool_status) if tool_status else "- 工具状态: 正常"
    
    from ..config import settings
    identity_path = settings.identity_path
    
    return f"""## 运行环境

- **当前时间**: {current_time}
- **操作系统**: {platform.system()} {platform.release()}
- **当前工作目录**: {os.getcwd()}
- **Identity 目录**: {identity_path}
  - SOUL.md, AGENT.md, USER.md, MEMORY.md 均在此目录
- **临时目录**: data/temp/

## 工具可用性（建议 32）
{tool_status_text}

⚠️ **重要**：服务重启后浏览器、变量、连接等状态会丢失，执行任务前必须通过工具检查实时状态。
如果工具不可用，允许纯文本回复并说明限制。"""


def _build_session_type_rules(session_type: str) -> str:
    """
    构建会话类型相关规则（建议 8）
    
    Args:
        session_type: "cli" 或 "im"
    
    Returns:
        会话类型相关的规则文本
    """
    if session_type == "im":
        return """## IM 会话规则

- **send_to_chat**: 可以使用，用于向用户发送消息
- **主动汇报**: 执行过程中可以发送进度消息
- **图片/文件**: 可以通过 send_to_chat 发送给用户"""
    
    else:  # cli 或其他
        return """## CLI 会话规则

- **send_to_chat**: 当前为 CLI 模式，此工具会静默成功但不发送消息
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
    
    # 工具清单（预算的 50%）
    if tool_catalog:
        tools_text = tool_catalog.generate_catalog()
        tools_result = apply_budget(tools_text, budget_tokens // 2, "tools")
        parts.append(tools_result.content)
    
    # 技能清单（预算的 33%）
    if skill_catalog:
        skills_text = skill_catalog.generate_catalog()
        skills_result = apply_budget(skills_text, budget_tokens // 3, "skills")
        parts.append(skills_result.content)
    
    # MCP 清单（预算的 17%）
    if mcp_catalog:
        mcp_text = mcp_catalog.generate_catalog()
        if mcp_text:
            mcp_result = apply_budget(mcp_text, budget_tokens // 6, "mcp")
            parts.append(mcp_result.content)
    
    # 工具使用指南（可选，向后兼容）
    if include_tools_guide:
        parts.append(_get_tools_guide_short())
    
    return '\n\n'.join(parts)


def _build_memory_section(
    memory_manager: Optional["MemoryManager"],
    task_description: str,
    budget_tokens: int,
) -> str:
    """构建 Memory 层（记忆检索）"""
    if not memory_manager:
        return ""
    
    memory_context = retrieve_memory(
        query=task_description,
        memory_manager=memory_manager,
        max_tokens=budget_tokens,
    )
    
    return memory_context


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

**原则**：任务型请求必须使用工具，无工具则创造工具！"""


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
        tools_text = tool_catalog.generate_catalog()
        info["catalogs"]["tools"] = estimate_tokens(tools_text)
    
    if skill_catalog:
        skills_text = skill_catalog.generate_catalog()
        info["catalogs"]["skills"] = estimate_tokens(skills_text)
    
    if mcp_catalog:
        mcp_text = mcp_catalog.generate_catalog()
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
        sum(info["compiled_files"].values()) +
        sum(info["catalogs"].values()) +
        info["memory"]
    )
    
    info["budget"] = {
        "identity": budget_config.identity_budget,
        "catalogs": budget_config.catalogs_budget,
        "user": budget_config.user_budget,
        "memory": budget_config.memory_budget,
        "total": budget_config.total_budget,
    }
    
    return info
