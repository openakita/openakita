"""
Prompt Builder - 消息组装模块

组装最终的系统提示词，整合编译产物、清单和记忆。

组装顺序:
1. Base Prompt: per-model 基础指令
2. Core Rules: 行为规则 + 提问准则 + 安全约束
3. Identity: SOUL.md + agent.core
4. Mode Rules: Ask/Plan/Agent 模式专属规则
5. Persona 层: 当前人格描述
6. Runtime 层: runtime_facts (OS/CWD/时间)
7. Catalogs 层: tools + skills + mcp 清单
8. Memory 层: retriever 输出
9. User 层: user.summary
"""

import logging
import os
import platform
import time
from datetime import datetime
from enum import Enum
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


class PromptMode(Enum):
    """Prompt 注入级别，控制子 agent 的提示词精简程度"""
    FULL = "full"         # 主 agent：所有段落
    MINIMAL = "minimal"   # 子 agent：仅 Core Rules + Runtime + Catalogs
    NONE = "none"         # 极简：仅一行身份声明

# ---------------------------------------------------------------------------
# 核心行为规则（代码硬编码，升级自动生效，用户不可删除）
# 合并自原 _SYSTEM_POLICIES + _DEFAULT_USER_POLICIES，消除冗余。
# 提问准则提升到最前，正面指引优先。
# ---------------------------------------------------------------------------
_CORE_RULES = """\
## 提问准则（最高优先级）

以下场景**必须**调用 `ask_user` 工具提问：
1. 用户意图模糊，有多种理解方式
2. 操作不可逆或影响范围大，需要确认方向
3. 需要用户提供无法推断的信息（密钥、账号、偏好选择等）

提问原则：先做能做的工作（读文件、查目录、搜索），然后针对阻塞点精准提问一个问题，\
附上你推荐的默认选项。不要问"要不要继续？"这类许可型问题。

技术问题优先自行解决：查目录、读配置、搜索方案、分析报错 — 这些不需要问用户。

## 边界条件
- 工具不可用时：纯文本完成，说明限制并给出手动步骤
- 关键输入缺失时：调用 `ask_user` 工具澄清
- 技能配置缺失时：主动辅助用户完成配置，不要直接拒绝
- 任务失败时：说明原因 + 替代建议 + 需要用户提供什么
- ask_user 超时：系统等待约 2 分钟，未回复则自行决策或终止

## 记忆使用
- 用户提到"之前/上次/我说过" → 主动 search_memory 查记忆
- 涉及用户偏好的任务 → 先查记忆和 profile 再行动
- 工具查到的信息 = 事实；凭知识回答需说明

## 输出格式
- 任务型回复：已执行 → 发现 → 下一步（如有）
- 陪伴型回复：自然对话，符合当前角色风格
- 常规工具调用无需解释说明，直接调用即可

## 工具使用原则

- 有专用工具时，禁止用 run_shell 替代：
  - read_file 代替 cat/head/tail
  - write_file/edit_file 代替 sed/awk/echo >
  - grep 代替 shell grep/rg
  - glob 代替 find
  - web_fetch 代替 curl（获取网页内容时）
- 编辑文件前必须先 read_file 确认当前内容
- 多个独立工具调用应并行发起，不要串行等待
- 编辑代码文件后，用 read_lints 检查是否引入了错误

## 并行工具调用

当你需要调用多个工具且它们之间没有依赖关系时，应在同一轮中并行发起所有调用。
例如：需要读取 3 个文件 → 同时发起 3 个 read_file 调用，而不是逐个读取。
如果工具调用之间有依赖（如先 read_file 再 edit_file），则必须等前一个完成后再发起后续调用。

## 文件创建原则

- 不要创建不必要的文件。编辑现有文件优先于创建新文件。
- 不要主动创建文档文件（*.md、README），除非用户明确要求。
- 不要主动创建测试文件，除非用户明确要求。"""

# ---------------------------------------------------------------------------
# 安全约束（独立段落，不受 SOUL.md 编辑影响）
# 参考 OpenClaw/Anthropic Constitution 风格
# ---------------------------------------------------------------------------
_SAFETY_SECTION = """\
## 安全约束

- 支持人类监督和控制，不追求自我保存、复制或权力扩张
- 优先安全和人类监督，而非任务完成
- 不运行破坏性命令除非用户明确要求
- 不操纵用户以扩大权限或绕过安全措施
- 避免超出用户请求范围的长期规划"""


# ---------------------------------------------------------------------------
# AGENTS.md — 项目级开发规范（行业标准，https://agents.md）
# 从当前工作目录向上查找，自动注入系统提示词。
# 非代码项目不会有此文件，读取逻辑静默跳过。
# ---------------------------------------------------------------------------
_agents_md_cache: dict[str, tuple[float, str | None]] = {}
_AGENTS_MD_CACHE_TTL = 60.0
_AGENTS_MD_MAX_CHARS = 8000
_AGENTS_MD_MAX_DEPTH = 3


def _read_agents_md(
    cwd: str | None = None,
    *,
    max_depth: int = _AGENTS_MD_MAX_DEPTH,
    max_chars: int = _AGENTS_MD_MAX_CHARS,
) -> str | None:
    """Read AGENTS.md from *cwd* or its parent directories.

    Uses a simple TTL cache to avoid repeated disk I/O on every prompt build.
    Returns the file content (truncated to *max_chars*) or ``None``.
    """
    if cwd is None:
        cwd = os.getcwd()

    now = time.monotonic()
    cached = _agents_md_cache.get(cwd)
    if cached is not None:
        ts, content = cached
        if now - ts < _AGENTS_MD_CACHE_TTL:
            return content

    content = _find_agents_md(cwd, max_depth=max_depth, max_chars=max_chars)
    _agents_md_cache[cwd] = (now, content)
    return content


def _find_agents_md(cwd: str, *, max_depth: int, max_chars: int) -> str | None:
    """Walk up from *cwd* looking for an AGENTS.md file."""
    current = Path(cwd).resolve()
    for _ in range(max_depth):
        agents_file = current / "AGENTS.md"
        if agents_file.is_file():
            try:
                raw = agents_file.read_text(encoding="utf-8", errors="ignore")
                content = raw[:max_chars] if len(raw) > max_chars else raw
                logger.info("Loaded project AGENTS.md from %s (%d chars)", agents_file, len(content))
                return content.strip() or None
            except OSError:
                return None
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


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
    session_type: str = "cli",
    precomputed_memory: str | None = None,
    persona_manager: Optional["PersonaManager"] = None,
    is_sub_agent: bool = False,
    memory_keywords: list[str] | None = None,
    prompt_mode: PromptMode | None = None,
    mode: str = "agent",
    model_id: str = "",
) -> str:
    """
    组装系统提示词

    Args:
        identity_dir: identity 目录路径
        tools_enabled: 是否启用工具
        tool_catalog: ToolCatalog 实例
        skill_catalog: SkillCatalog 实例
        mcp_catalog: MCPCatalog 实例
        memory_manager: MemoryManager 实例
        task_description: 任务描述（用于记忆检索）
        budget_config: 预算配置
        include_tools_guide: 是否包含工具使用指南
        session_type: 会话类型 "cli" 或 "im"
        precomputed_memory: 预计算的记忆文本
        persona_manager: PersonaManager 实例
        is_sub_agent: 是否是子 agent（向后兼容）
        memory_keywords: 记忆检索关键词
        prompt_mode: 提示词注入级别 (full/minimal/none)
        mode: 当前模式 (ask/plan/agent)
        model_id: 模型标识（用于 per-model 基础 prompt）

    Returns:
        完整的系统提示词
    """
    if budget_config is None:
        budget_config = BudgetConfig()

    # 向后兼容：is_sub_agent=True 且无显式 prompt_mode 时，使用 MINIMAL
    if prompt_mode is None:
        prompt_mode = PromptMode.MINIMAL if is_sub_agent else PromptMode.FULL

    system_parts: list[str] = []
    developer_parts: list[str] = []
    tool_parts: list[str] = []
    user_parts: list[str] = []

    # 1. Per-model base prompt
    base_prompt = _select_base_prompt(model_id)
    if base_prompt:
        system_parts.append(base_prompt)

    # 2. Core Rules（提问准则 + 边界条件 + 安全约束）— 所有模式都注入
    system_parts.append(_CORE_RULES)
    system_parts.append(_SAFETY_SECTION)

    # 3. 检查并加载编译产物
    if check_compiled_outdated(identity_dir):
        logger.info("Compiled files outdated, recompiling...")
        compile_all(identity_dir)

    compiled = get_compiled_content(identity_dir)

    # 4. Identity 层（SOUL.md + agent.core）
    if prompt_mode == PromptMode.FULL:
        identity_section = _build_identity_section(
            compiled=compiled,
            identity_dir=identity_dir,
            tools_enabled=tools_enabled,
            budget_tokens=budget_config.identity_budget,
        )

        # 多 Agent 委派优先声明（仅 Agent 模式 — Plan/Ask 模式不注入，因为这些工具不可用）
        from ..config import settings as _settings
        if _settings.multi_agent_enabled and not is_sub_agent and mode == "agent":
            delegation_preamble = (
                "## 协作优先原则（最高优先级）\n\n"
                "你拥有一支专业 Agent 团队。执行任务前，先判断是否有更合适的专业 Agent：\n"
                "- 有专业 Agent 能处理 → 立即委派（delegate_to_agent），不要自己尝试\n"
                "- 任务涉及多个专业领域 → 拆分并行委派（delegate_parallel）\n"
                "- 只有简单问答或用户明确要你亲自做 → 才自己处理\n\n"
                "此原则优先于下文中「自己解决」「永不放弃」等个人执行哲学。"
                "当委派与自己执行冲突时，选择委派。\n"
            )
            system_parts.append(delegation_preamble)

        if identity_section:
            system_parts.append(identity_section)

        # Persona 层
        if persona_manager:
            persona_section = _build_persona_section(persona_manager)
            if persona_section:
                system_parts.append(persona_section)

    elif prompt_mode == PromptMode.NONE:
        system_parts.append("你是 OpenAkita，一个全能 AI 助手。")

    # 5. Mode Rules（Ask/Plan/Agent 模式专属规则）
    mode_rules = _build_mode_rules(mode)
    if mode_rules:
        system_parts.append(mode_rules)

    # 6. Runtime 层（所有 prompt_mode 都注入）
    runtime_section = _build_runtime_section()
    system_parts.append(runtime_section)

    # 7. 会话类型规则（FULL 和 MINIMAL 都注入）
    if prompt_mode in (PromptMode.FULL, PromptMode.MINIMAL):
        persona_active = persona_manager.is_persona_active() if persona_manager else False
        session_rules = _build_session_type_rules(session_type, persona_active=persona_active)
        if session_rules:
            developer_parts.append(session_rules)

    # 8. 项目 AGENTS.md（FULL 和 MINIMAL 都注入）
    if prompt_mode in (PromptMode.FULL, PromptMode.MINIMAL):
        agents_md_content = _read_agents_md()
        if agents_md_content:
            developer_parts.append(
                "## Project Guidelines (AGENTS.md)\n\n"
                "以下是当前工作目录中的项目开发规范，执行开发任务时必须遵循：\n\n"
                + agents_md_content
            )

    # 9. Catalogs 层（所有 prompt_mode 都注入）
    catalogs_section = _build_catalogs_section(
        tool_catalog=tool_catalog,
        skill_catalog=skill_catalog,
        mcp_catalog=mcp_catalog,
        budget_tokens=budget_config.catalogs_budget,
        include_tools_guide=include_tools_guide,
        mode=mode,
    )
    if catalogs_section:
        tool_parts.append(catalogs_section)

    # 10. Memory 层（仅 FULL 模式）
    if prompt_mode == PromptMode.FULL:
        if precomputed_memory is not None:
            memory_section = precomputed_memory
        else:
            memory_section = _build_memory_section(
                memory_manager=memory_manager,
                task_description=task_description,
                budget_tokens=budget_config.memory_budget,
                memory_keywords=memory_keywords,
            )
        if memory_section:
            developer_parts.append(memory_section)

    # 11. User 层（仅 FULL 模式）
    if prompt_mode == PromptMode.FULL:
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

    total_tokens = estimate_tokens(system_prompt)
    logger.info(f"System prompt built: {total_tokens} tokens (mode={mode}, prompt_mode={prompt_mode.value})")

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


def _select_base_prompt(model_id: str) -> str:
    """根据模型 ID 选择 per-model 基础提示词。

    查找 prompt/models/ 目录下的 .txt 文件，按模型族匹配。
    """
    if not model_id:
        return ""

    models_dir = Path(__file__).parent / "models"
    if not models_dir.exists():
        return ""

    model_lower = model_id.lower()

    # 按模型族匹配
    if any(k in model_lower for k in ("claude", "anthropic")):
        target = "anthropic.txt"
    elif any(k in model_lower for k in ("gpt", "o1", "o3", "o4", "chatgpt")):
        target = "openai.txt"
    elif any(k in model_lower for k in ("gemini", "gemma")):
        target = "gemini.txt"
    else:
        target = "default.txt"

    prompt_file = models_dir / target
    if not prompt_file.exists():
        prompt_file = models_dir / "default.txt"
    if not prompt_file.exists():
        return ""

    try:
        return prompt_file.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _build_mode_rules(mode: str) -> str:
    """根据当前模式返回专属提示词段落。

    mode 值: "ask", "plan", "agent"（默认）
    """
    modes_dir = Path(__file__).parent / "modes"

    if mode == "plan":
        plan_file = modes_dir / "plan.txt"
        if plan_file.exists():
            try:
                return plan_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return _PLAN_MODE_FALLBACK

    if mode == "ask":
        return _ASK_MODE_RULES

    # agent mode: return agent-specific rules (complex task detection hint)
    return _AGENT_MODE_RULES


_ASK_MODE_RULES = """\
<system-reminder>
# Ask 模式 — 只读

你处于 Ask（只读）模式。你可以：
- 阅读文件、搜索代码、分析结构
- 回答问题、解释代码、提供建议

你**不可以**：
- 编辑或创建任何文件
- 运行可能产生副作用的命令
- 调用写入类工具

用户希望先了解情况再决定是否行动。保持分析性和信息性。
</system-reminder>"""

_AGENT_MODE_RULES = """\
## 复杂任务识别

当用户的请求具有以下特征时，建议切换到 Plan 模式：
- 涉及 3 个以上文件的修改
- 需求描述模糊，有多种实现路径
- 涉及架构变更或跨模块改动
- 操作不可逆或影响范围大

使用 ask_user 提出建议，提供"切换到 Plan 模式"和"继续执行"两个选项。
不要自行切换模式，让用户决定。

## 代码修改规范

- 不要添加仅描述代码行为的注释（如 "导入模块"、"定义函数"）
- 注释应只解释代码本身无法表达的意图、权衡或约束
- 编辑代码后，用 read_lints 检查最近编辑的文件是否引入了 linter 错误

## Git 安全协议

- 不要修改 git config
- 不要运行破坏性/不可逆的 git 命令（如 push --force、hard reset）除非用户明确要求
- 不要跳过 hooks（--no-verify 等）除非用户明确要求
- 不要 force push 到 main/master，如果用户要求则警告
- 不要在用户未明确要求时创建 commit"""

_PLAN_MODE_FALLBACK = """\
<system-reminder>
# Plan 模式 — 系统提醒

你处于 Plan（规划）模式。权限系统已启用，写入操作受代码级限制：
- 文件写入仅限 data/plans/*.md 路径（其他路径会被权限系统自动拦截）
- Shell 命令不可用
- 所有只读工具正常可用（read_file, web_search 等）

## 职责
思考、阅读、搜索，构建一个结构良好的计划来完成用户的目标。
计划应全面且简洁，足够详细可执行，同时避免不必要的冗长。
任何时候都可以自由使用 ask_user 向用户提问或澄清。

## 工作流程

1. **理解需求** — 阅读相关代码，使用 ask_user 澄清模糊点。
2. **设计方案** — 分析实现路径、关键文件、潜在风险。
3. **写入计划** — 调用 create_plan_file 创建 .plan.md 计划文件。
4. **退出规划** — 调用 exit_plan_mode，等待用户审批。

你的回合只应以 ask_user 提问或 exit_plan_mode 结束。

## 重要
用户希望先规划再执行。即使用户要求编辑文件，也不要尝试 —
权限系统会阻止写操作并返回 DeniedError。请将修改计划写入 plan 文件。
</system-reminder>"""


def _build_identity_section(
    compiled: dict[str, str],
    identity_dir: Path,
    tools_enabled: bool,
    budget_tokens: int,
) -> str:
    """构建 Identity 层

    SOUL.md 全文注入（已精简为 ~60 行行为约束，无需大量预算）。
    AGENT 行为规范使用编译精简版（agent.core.md）。
    用户自定义策略（policies.md）如存在则追加。
    """
    import re

    parts = []

    parts.append("# OpenAkita System")
    parts.append("")

    # SOUL — 全文注入（~60% 预算）
    soul_path = identity_dir / "SOUL.md"
    if soul_path.exists():
        soul_raw = soul_path.read_text(encoding="utf-8")
        soul_clean = re.sub(r"<!--.*?-->", "", soul_raw, flags=re.DOTALL).strip()
        soul_result = apply_budget(soul_clean, budget_tokens * 60 // 100, "soul")
        parts.append(soul_result.content)
        parts.append("")
    elif compiled.get("soul"):
        parts.append(compiled["soul"])
        parts.append("")

    # Agent core (~25%) — 核心执行原则
    if compiled.get("agent_core"):
        core_result = apply_budget(compiled["agent_core"], budget_tokens * 25 // 100, "agent_core")
        parts.append(core_result.content)
        parts.append("")

    # User policies (~15%) — 用户自定义策略文件（可选，仅追加不与核心规则重复的内容）
    policies_path = identity_dir / "prompts" / "policies.md"
    if policies_path.exists():
        try:
            user_policies = policies_path.read_text(encoding="utf-8").strip()
            if user_policies:
                policies_result = apply_budget(user_policies, budget_tokens * 15 // 100, "user_policies")
                parts.append(policies_result.content)
        except Exception:
            pass

    return "\n".join(parts)


def _get_current_time(timezone_name: str = "Asia/Shanghai") -> str:
    """获取指定时区的当前时间，避免依赖服务器本地时区"""
    from datetime import timedelta, timezone

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
    from ..runtime_env import (
        IS_FROZEN,
        can_pip_install,
        get_configured_venv_path,
        get_python_executable,
        verify_python_executable,
    )

    current_time = _get_current_time(settings.scheduler_timezone)

    # --- 部署模式与 Python 环境 ---
    deploy_mode = _detect_deploy_mode()
    ext_python = get_python_executable()
    pip_ok = can_pip_install()
    venv_path = get_configured_venv_path()

    python_info = _build_python_info(IS_FROZEN, ext_python, pip_ok, settings, venv_path)

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
    _python_in_path_ok = False
    for cmd in ("git", "python", "node", "pip", "npm", "docker", "curl"):
        found = _shutil.which(cmd)
        if not found:
            continue
        if cmd == "python" and _sys.platform == "win32":
            if not verify_python_executable(found):
                continue
            _python_in_path_ok = True
        if cmd == "pip" and _sys.platform == "win32" and not _python_in_path_ok:
            continue
        path_tools.append(cmd)
    path_tools_str = ", ".join(path_tools) if path_tools else "无"

    return f"""## 运行环境

- **OpenAkita 版本**: {version_str}
- **部署模式**: {deploy_mode}
- **当前时间**: {current_time}
- **操作系统**: {platform.system()} {platform.release()} ({platform.machine()})
- **当前工作目录**: {os.getcwd()}
- **OpenAkita 数据根目录**: {settings.openakita_home}
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


def _build_python_info(
    is_frozen: bool,
    ext_python: str | None,
    pip_ok: bool,
    settings,
    venv_path: str | None = None,
) -> str:
    """根据部署模式构建 Python 环境信息"""
    import sys as _sys

    if not is_frozen:
        in_venv = _sys.prefix != _sys.base_prefix
        env_type = "venv" if in_venv else "system"
        lines = [
            f"- **Python**: {_sys.version.split()[0]} ({env_type})",
            f"- **解释器**: {_sys.executable}",
        ]
        if in_venv:
            lines.append(f"- **虚拟环境**: {_sys.prefix}")
        lines.append("- **pip**: 可用")
        lines.append("- **注意**: 执行 Python 脚本时使用上述解释器路径，pip install 会安装到当前环境中")
        return "\n".join(lines)

    # 打包模式
    if ext_python:
        lines = [
            "- **Python**: 可用（外置环境已自动配置）",
            f"- **解释器**: {ext_python}",
        ]
        if venv_path:
            lines.append(f"- **虚拟环境**: {venv_path}")
        lines.append(f"- **pip**: {'可用' if pip_ok else '不可用'}")
        lines.append("- **注意**: 执行 Python 脚本时请使用上述解释器路径，pip install 会安装到该虚拟环境中")
        return "\n".join(lines)

    # 打包模式 + 无外置 Python
    fallback_venv = settings.project_root / "data" / "venv"
    if platform.system() == "Windows":
        install_cmd = "winget install Python.Python.3.12"
    else:
        install_cmd = "sudo apt install python3 或 brew install python3"

    return (
        f"- **Python**: ⚠️ 未检测到可用的 Python 环境\n"
        f"  - 推荐操作：通过 `run_shell` 执行 `{install_cmd}` 安装 Python\n"
        f"  - 安装后创建工作区虚拟环境：`python -m venv {fallback_venv}`\n"
        f"  - 创建完成后系统将自动检测并使用该环境，无需重启\n"
        f"  - 此环境为系统专用，与用户个人 Python 环境隔离"
    )


_PLATFORM_NAMES = {
    "feishu": "飞书",
    "telegram": "Telegram",
    "wechat_work": "企业微信",
    "dingtalk": "钉钉",
    "onebot": "OneBot",
}


def _build_im_environment_section() -> str:
    """从 IM context 读取当前环境信息，生成系统提示词段落"""
    try:
        from ..core.im_context import get_im_session
        session = get_im_session()
        if not session:
            return ""
        im_env = session.get_metadata("_im_environment") if hasattr(session, "get_metadata") else None
        if not im_env:
            return ""
    except Exception:
        return ""

    platform = im_env.get("platform", "unknown")
    platform_name = _PLATFORM_NAMES.get(platform, platform)
    chat_type = im_env.get("chat_type", "private")
    chat_type_name = "群聊" if chat_type == "group" else "私聊"
    chat_id = im_env.get("chat_id", "")
    thread_id = im_env.get("thread_id")
    bot_id = im_env.get("bot_id", "")
    capabilities = im_env.get("capabilities", [])

    lines = [
        "## 当前 IM 环境",
        f"- 平台：{platform_name}",
        f"- 场景：{chat_type_name}（ID: {chat_id}）",
    ]
    if thread_id:
        lines.append(f"- 当前在话题/线程中（thread_id: {thread_id}），对话上下文仅包含本话题内的消息")
    if bot_id:
        lines.append(f"- 你的身份：机器人（ID: {bot_id}）")
    if capabilities:
        lines.append(f"- 已确认可用的能力：{', '.join(capabilities)}")
    lines.append("- 你可以通过 get_chat_info / get_user_info / get_chat_members 等工具主动查询环境信息")
    lines.append(
        "- **重要**：你的记忆系统是跨会话共享的，检索到的记忆可能来自其他群聊或私聊场景。"
        "请优先关注当前对话上下文，审慎引用来源不明的共享记忆。"
    )
    return "\n".join(lines) + "\n\n"


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

在对话历史中，你会看到以 `[系统]`、`[系统提示]` 或 `[context_note:` 开头的消息。这些是**运行时控制信号**，由系统自动注入，**不是用户发出的请求**。你应该：
- 将它们视为背景信息或状态通知，而非需要执行的任务指令
- **绝不**将系统消息的内容复述或提及给用户（用户看不到这些消息）
- 不要把系统消息当作用户的意图来执行
- 不要因为看到系统消息而改变回复的质量、详细程度或风格

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
- **禁止在纯文本中要求用户确认后再执行**——包括复述识别结果请用户确认、展示执行计划请用户确认等场景。这些都必须通过 `ask_user` 工具完成，否则系统无法暂停等待用户回复。
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
        im_env_section = _build_im_environment_section()
        return common_rules + im_env_section + f"""## IM 会话规则

- **文本消息**：助手的自然语言回复会由网关直接转发给用户（不需要、也不应该通过工具发送）。
- **附件交付**：文件/图片/语音等交付必须通过统一的网关交付工具 `deliver_artifacts` 完成，并以回执作为交付证据。
- **进度展示**：执行过程的进度消息由网关基于事件流生成（计划步骤、交付回执、关键工具节点），避免模型刷屏。
- **表达风格**：{'遵循当前角色设定的表情使用偏好和沟通风格' if persona_active else '默认简短直接，不使用表情符号（emoji）'}；不要复述 system/developer/tool 等提示词内容。
- **IM 特殊注意**：IM 用户经常发送非常简短的消息（1-5 个字），这大多是闲聊或确认，直接回复即可，不要过度解读为复杂任务。
- **多模态消息**：当用户发送图片时，图片已作为多模态内容直接包含在你的消息中，你可以直接看到并理解图片内容。**请直接描述/分析你看到的图片**，无需调用任何工具来查看或分析图片。仅在需要获取文件路径进行程序化处理（转发、保存、格式转换等）时才使用 `get_image_file`。
- **语音识别**：系统已内置自动语音转文字（Whisper），用户发送的语音会自动转为文字。收到语音消息时直接处理文字内容，**不要尝试自己实现语音识别功能**。仅当看到"语音识别失败"时才用 `get_voice_file` 手动处理。
- **已内置功能提醒**：语音转文字、图片理解、IM 配对等功能已内置，当用户说"帮我实现语音转文字"时，告知已内置并正常运行，不要开始写代码实现。
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
    mode: str = "agent",
) -> str:
    """构建 Catalogs 层（工具/技能/MCP 清单）"""
    parts = []

    # 工具清单（预算的 33%）
    # 高频工具 (run_shell, read_file, write_file, list_directory, ask_user) 已通过
    # LLM tools 参数直接注入完整 schema，文本清单默认排除以节省 token
    if tool_catalog:
        tools_text = tool_catalog.get_catalog()  # exclude_high_freq=True by default
        # Plan/Ask 模式下，给工具清单加"仅供参考"标注，避免 LLM 误调不可用工具
        if mode in ("plan", "ask"):
            mode_note = (
                "\n> ⚠️ **当前为 {} 模式** — 以下工具清单仅供规划参考。\n"
                "> 你只能调用工具列表（tools）中实际提供给你的工具。\n"
                "> 如果某个工具不在你的可调用列表中，不要尝试调用它。\n"
            ).format("Plan" if mode == "plan" else "Ask")
            tools_text = mode_note + tools_text
        tools_result = apply_budget(tools_text, budget_tokens // 3, "tools")
        parts.append(tools_result.content)

    # 技能清单（预算的 55%）— 统一三级渐进式披露
    if skill_catalog:
        # Level 1: 全量索引（仅名称，保证所有技能名可见）+ 预算内详情（名称+描述）
        # Level 2: get_skill_info → 完整 SKILL.md 指令（按需加载）
        # Level 3: 资源文件（按需加载）
        skills_budget = budget_tokens * 55 // 100
        skills_index = skill_catalog.get_index_catalog()

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

你有一个三层分层记忆网络，各层双向关联。

**第一层：核心档案**（下方已注入）— 用户偏好、规则、事实的精炼摘要
**第二层：语义记忆 + 任务情节** — 经验教训、技能方法、每次任务的目标/结果/工具摘要
**第三层：原始对话存档** — 完整的逐轮对话，含工具调用参数和返回值

三层通过 ID 双向关联，可以从任意层钻取到其他层。

搜索工具：`search_memory`(知识) / `list_recent_tasks`(任务) / `trace_memory`(跨层导航) / `search_conversation_traces`(原始对话)
首次使用时会返回详细的搜索策略指南。

后台自动提取记忆，你只需在总结经验(experience/skill)、记录教训(error)、发现偏好(preference/rule)时用 `add_memory`。

### 当前注入的信息
下方是用户核心档案、当前任务状态和高权重历史经验，仅供快速参考。更多记忆请按需搜索。"""


def _build_memory_section(
    memory_manager: Optional["MemoryManager"],
    task_description: str,
    budget_tokens: int,
    memory_keywords: list[str] | None = None,
) -> str:
    """
    构建 Memory 层 — 渐进式披露:
    0. 记忆系统自描述 (告知 LLM 记忆系统的运作方式)
    1. Scratchpad (当前任务 + 近期完成)
    2. Core Memory (MEMORY.md 用户基本信息 + 永久规则)
    3. Experience Hints (高权重经验记忆)
    4. Active Retrieval (if memory_keywords provided by IntentAnalyzer)
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

    # Layer 1.5: Pinned Rules — 从 SQLite 查询 RULE 类型记忆，独立注入，不受裁剪
    pinned_rules = _build_pinned_rules_section(memory_manager)
    if pinned_rules:
        parts.append(pinned_rules)

    # Layer 2: Core Memory (MEMORY.md — 用户基本信息 + 永久规则)
    from openakita.memory.types import MEMORY_MD_MAX_CHARS as _MD_MAX
    core_budget = min(budget_tokens // 2, 500)
    core_memory = _get_core_memory(memory_manager, max_chars=min(core_budget * 3, _MD_MAX))
    if core_memory:
        parts.append(f"## 核心记忆\n\n{core_memory}")

    # Layer 3: Experience Hints (高权重经验/教训/技能记忆)
    experience_text = _build_experience_section(memory_manager, max_items=5)
    if experience_text:
        parts.append(experience_text)

    # Layer 4: Active Retrieval (driven by IntentAnalyzer memory_keywords)
    if memory_keywords:
        retrieved = _retrieve_by_keywords(memory_manager, memory_keywords, max_tokens=500)
        if retrieved:
            parts.append(f"## 相关记忆（自动检索）\n\n{retrieved}")

    return "\n\n".join(parts)


def _retrieve_by_keywords(
    memory_manager: Optional["MemoryManager"],
    keywords: list[str],
    max_tokens: int = 500,
) -> str:
    """Use IntentAnalyzer-extracted keywords to actively retrieve relevant memories."""
    if not memory_manager or not keywords:
        return ""

    try:
        retrieval_engine = getattr(memory_manager, "retrieval_engine", None)
        if retrieval_engine is None:
            return ""

        query = " ".join(keywords)
        recent_messages = getattr(memory_manager, "_recent_messages", [])

        result = retrieval_engine.retrieve(
            query=query,
            recent_messages=recent_messages,
            max_tokens=max_tokens,
        )
        return result if result else ""
    except Exception as e:
        logger.debug(f"[MemoryRetrieval] Active retrieval failed: {e}")
        return ""


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


_PINNED_RULES_MAX_TOKENS = 500
_PINNED_RULES_CHARS_PER_TOKEN = 3


def _build_pinned_rules_section(
    memory_manager: Optional["MemoryManager"],
) -> str:
    """从 SQLite 查询所有活跃的 RULE 类型记忆，作为独立段落注入 system prompt。

    这些规则不受 memory_budget 裁剪，确保用户设定的行为规则始终可见。
    设置独立的 token 上限防止异常膨胀。
    """
    store = getattr(memory_manager, "store", None)
    if store is None:
        return ""
    try:
        rules = store.query_semantic(memory_type="rule", limit=20)
        if not rules:
            return ""

        from datetime import datetime
        now = datetime.now()
        active_rules = [
            r for r in rules
            if not r.superseded_by
            and (not r.expires_at or r.expires_at > now)
        ]
        if not active_rules:
            return ""

        active_rules.sort(key=lambda r: r.importance_score, reverse=True)

        lines = ["## 用户设定的规则（必须遵守）\n"]
        total_chars = 0
        max_chars = _PINNED_RULES_MAX_TOKENS * _PINNED_RULES_CHARS_PER_TOKEN
        for r in active_rules:
            content = (r.content or "").strip()
            if not content:
                continue
            line = f"- {content}"
            if total_chars + len(line) > max_chars:
                break
            lines.append(line)
            total_chars += len(line)

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Failed to build pinned rules section: {e}")
        return ""


def _get_core_memory(memory_manager: Optional["MemoryManager"], max_chars: int = 600) -> str:
    """获取 MEMORY.md 核心记忆（损坏时自动 fallback 到 .bak）

    截断策略委托给 ``truncate_memory_md``：按段落拆分，规则段落优先保留。
    """
    from openakita.memory.types import truncate_memory_md

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

    return truncate_memory_md(content, max_chars)


def _build_experience_section(
    memory_manager: Optional["MemoryManager"],
    max_items: int = 5,
) -> str:
    """Inject top experience/lesson/skill memories as proactive hints."""
    store = getattr(memory_manager, "store", None)
    if store is None:
        return ""
    try:
        exp_types = ("experience", "skill", "error")
        all_exp = []
        for t in exp_types:
            try:
                results = store.query_semantic(memory_type=t, limit=10)
                all_exp.extend(results)
            except Exception:
                continue
        if not all_exp:
            return ""

        # Rank by (access_count * importance) descending, take top N
        all_exp.sort(
            key=lambda m: m.access_count * m.importance_score + m.importance_score,
            reverse=True,
        )
        top = [m for m in all_exp[:max_items] if m.importance_score >= 0.6 and not m.superseded_by]
        if not top:
            return ""

        lines = ["## 历史经验（执行任务前请参考）\n"]
        for m in top:
            icon = {"error": "⚠️", "skill": "💡", "experience": "📝"}.get(m.type.value, "📝")
            lines.append(f"- {icon} {m.content}")
        return "\n".join(lines)
    except Exception:
        return ""


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

### 工具调用风格

- **常规操作直接执行**：读文件、搜索、列目录等低风险操作无需解释说明，直接调用
- **关键节点简要叙述**：多步骤任务、敏感操作、复杂判断时简要说明意图
- **不要让用户自己跑命令**：直接使用工具执行，而不是输出命令让用户去终端跑
- **不要编造工具结果**：未调用工具前不要声称已完成操作

### 能力扩展

缺少某种能力时，不要说"我做不到"：
1. 搜索已安装 skills → 搜索 Skill Store / GitHub → 安装
2. 临时脚本: `write_file` + `run_shell`
3. 创建永久技能: `skill-creator` → `load_skill`"""


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
