"""
Prompt Compiler - 从源 md 文件编译摘要

编译规则:
- SOUL.md -> soul.summary.md (<=120 tokens): 保留核心原则，丢弃长叙事
- AGENT.md -> agent.core.md (<=200 tokens): 保留 Ralph 循环原则
- AGENT.md -> agent.tooling.md (<=220 tokens): 保留工具使用原则
- USER.md -> user.summary.md (<=120 tokens): 仅保留已学习字段

编译可以使用 LLM 辅助，也可以手动编辑编译产物。
"""

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def compile_all(identity_dir: Path, use_llm: bool = False) -> dict[str, Path]:
    """
    编译所有源文件到 compiled/ 目录

    Args:
        identity_dir: identity 目录路径
        use_llm: 是否使用 LLM 辅助编译（默认 False，使用规则编译）

    Returns:
        编译产物路径字典
    """
    compiled_dir = identity_dir / "compiled"
    compiled_dir.mkdir(exist_ok=True)

    results = {}

    # 1. 编译 SOUL.md
    soul_path = identity_dir / "SOUL.md"
    if soul_path.exists():
        soul_content = soul_path.read_text(encoding="utf-8")
        soul_summary = compile_soul(soul_content)
        soul_out = compiled_dir / "soul.summary.md"
        soul_out.write_text(soul_summary, encoding="utf-8")
        results["soul"] = soul_out
        logger.info(f"Compiled SOUL.md -> {soul_out}")
    else:
        logger.warning("SOUL.md not found, skipping")

    # 2. 编译 AGENT.md -> agent.core.md
    agent_path = identity_dir / "AGENT.md"
    if agent_path.exists():
        agent_content = agent_path.read_text(encoding="utf-8")

        # 编译核心原则
        agent_core = compile_agent_core(agent_content)
        core_out = compiled_dir / "agent.core.md"
        core_out.write_text(agent_core, encoding="utf-8")
        results["agent_core"] = core_out
        logger.info(f"Compiled AGENT.md -> {core_out}")

        # 编译工具使用原则
        agent_tooling = compile_agent_tooling(agent_content)
        tooling_out = compiled_dir / "agent.tooling.md"
        tooling_out.write_text(agent_tooling, encoding="utf-8")
        results["agent_tooling"] = tooling_out
        logger.info(f"Compiled AGENT.md -> {tooling_out}")
    else:
        logger.warning("AGENT.md not found, skipping")

    # 3. 编译 USER.md
    user_path = identity_dir / "USER.md"
    if user_path.exists():
        user_content = user_path.read_text(encoding="utf-8")
        user_summary = compile_user(user_content)
        if user_summary.strip():  # 只有非空才写入
            user_out = compiled_dir / "user.summary.md"
            user_out.write_text(user_summary, encoding="utf-8")
            results["user"] = user_out
            logger.info(f"Compiled USER.md -> {user_out}")
        else:
            logger.info("USER.md has no learned fields, skipping")
    else:
        logger.warning("USER.md not found, skipping")

    # 4. 编译 personas/user_custom.md
    persona_custom_path = identity_dir / "personas" / "user_custom.md"
    if persona_custom_path.exists():
        persona_content = persona_custom_path.read_text(encoding="utf-8")
        persona_summary = compile_persona(persona_content)
        if persona_summary.strip():
            persona_out = compiled_dir / "persona.custom.md"
            persona_out.write_text(persona_summary, encoding="utf-8")
            results["persona_custom"] = persona_out
            logger.info(f"Compiled user_custom.md -> {persona_out}")
        else:
            logger.info("user_custom.md has no learned preferences, skipping")
    else:
        logger.debug("personas/user_custom.md not found, skipping")

    # 写入编译时间戳
    timestamp_file = compiled_dir / ".compiled_at"
    timestamp_file.write_text(datetime.now().isoformat(), encoding="utf-8")

    return results


def compile_soul(soul_content: str) -> str:
    """
    SOUL.md -> soul.summary.md

    保留核心原则，丢弃长叙事段落。
    目标: <=120 tokens (~480 字符)
    """
    # 提取关键原则
    principles = []

    # 匹配常见的原则格式
    # 1. "诚实"、"避免欺骗" 等关键词
    # 2. 带编号的列表项
    # 3. 带 ## 的标题下的内容

    lines = soul_content.split("\n")
    current_section = ""
    in_principles = False

    for line in lines:
        stripped = line.strip()

        # 检测标题
        if stripped.startswith("##"):
            current_section = stripped.lower()
            # 关键章节
            if any(
                kw in current_section
                for kw in ["原则", "核心", "principle", "core", "诚实", "校准"]
            ):
                in_principles = True
            else:
                in_principles = False
            continue

        # 提取列表项和关键句子
        if stripped.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.")):
            # 过滤掉叙事性描述（过长的句子）
            if len(stripped) < 100:
                principles.append(stripped)
        elif in_principles and stripped and len(stripped) < 80:
            principles.append(stripped)

    # 去重并限制数量
    unique_principles = []
    seen = set()
    for p in principles:
        # 简单去重：取前 20 字符作为 key
        key = p[:20].lower()
        if key not in seen:
            seen.add(key)
            unique_principles.append(p)

    # 限制在 10 条以内
    unique_principles = unique_principles[:10]

    # 构建摘要
    summary = """# Soul Summary

## 核心原则

"""
    summary += "\n".join(unique_principles)

    # 如果提取内容太少，使用默认原则
    if len(unique_principles) < 3:
        summary = """# Soul Summary

## 核心原则

- 诚实：不欺骗，不误导，承认不确定性
- 校准：对自己的判断保持适当的信心水平
- 避免恶意：不协助有害活动
- 支持监督：允许用户纠正和指导
"""

    return summary


def compile_agent_core(agent_content: str) -> str:
    """
    AGENT.md -> agent.core.md

    保留 Ralph 循环核心原则。
    目标: <=200 tokens (~800 字符)
    """
    # 提取 Ralph 相关内容
    ralph_principles = []

    # 关键词匹配
    keywords = [
        "ralph",
        "wigum",
        "永不放弃",
        "never give up",
        "任务未完成",
        "plan-act-verify",
        "循环",
        "缺能力",
        "获取能力",
        "进度",
        "memory",
    ]

    lines = agent_content.split("\n")
    current_section = ""
    in_ralph = False

    for line in lines:
        stripped = line.strip()

        # 检测标题
        if stripped.startswith("##"):
            current_section = stripped.lower()
            if any(kw in current_section for kw in ["ralph", "wigum", "核心", "core", "循环"]):
                in_ralph = True
            else:
                in_ralph = False
            continue

        # 提取 Ralph 相关内容
        line_lower = stripped.lower()
        if any(kw in line_lower for kw in keywords):
            if stripped.startswith(("-", "*", "1.", "2.", "3.")) and len(stripped) < 120:
                ralph_principles.append(stripped)
        elif in_ralph and stripped.startswith(("-", "*")) and len(stripped) < 100:
            ralph_principles.append(stripped)

    # 去重
    unique = list(dict.fromkeys(ralph_principles))[:8]

    # 构建摘要
    summary = """# Agent Core Principles

## Ralph Wiggum Mode（永不放弃模式）

"""

    if unique:
        summary += "\n".join(unique)
    else:
        # 默认原则
        summary += """- 任务未完成不退出，持续尝试直到成功
- Plan-Act-Verify 循环：规划 → 执行 → 验证
- 缺少能力？搜索/安装/创建工具获取
- 保存进度到 MEMORY.md，防止丢失
- 失败不报错，换方法再试
"""

    return summary


def compile_agent_tooling(agent_content: str) -> str:
    """
    AGENT.md -> agent.tooling.md

    保留工具使用原则。
    目标: <=220 tokens (~880 字符)
    """
    # 关键词匹配
    tooling_keywords = [
        "工具",
        "tool",
        "脚本",
        "script",
        "敷衍",
        "禁止",
        "skill",
        "技能",
        "mcp",
        "浏览器",
        "browser",
    ]

    tooling_principles = []
    lines = agent_content.split("\n")
    current_section = ""
    in_tooling = False

    for line in lines:
        stripped = line.strip()

        # 检测标题
        if stripped.startswith("##"):
            current_section = stripped.lower()
            if any(kw in current_section for kw in ["工具", "tool", "技能", "skill", "mcp"]):
                in_tooling = True
            else:
                in_tooling = False
            continue

        # 提取工具相关内容
        line_lower = stripped.lower()
        if any(kw in line_lower for kw in tooling_keywords):
            if stripped.startswith(("-", "*", "1.", "2.", "3.")) and len(stripped) < 120:
                tooling_principles.append(stripped)
        elif in_tooling and stripped.startswith(("-", "*")) and len(stripped) < 100:
            tooling_principles.append(stripped)

    # 去重
    unique = list(dict.fromkeys(tooling_principles))[:10]

    # 构建摘要
    summary = """# Agent Tooling Principles

## 工具使用原则

"""

    if unique:
        summary += "\n".join(unique)
    else:
        # 默认原则
        summary += """- 需要执行操作的请求使用工具完成；纯问答/闲聊直接文字回复
- 禁止敷衍响应：不能只说"好的"而不执行
- 工具优先级：系统工具 > Skills 技能 > MCP 外部服务
- 缺少现成工具时可组合 write_file + run_shell 或 skill-creator
- 提醒/定时任务必须调用 schedule_task 工具
- Plan 模式：超过 2 步的任务先 create_plan
- 任务完成后用简洁文字告知用户结果，不要继续调用工具
"""

    return summary


def compile_user(user_content: str) -> str:
    """
    USER.md -> user.summary.md

    仅保留已学习字段（非 [待学习]）。
    目标: <=120 tokens (~480 字符)
    """
    learned_items = []

    lines = user_content.split("\n")

    for line in lines:
        stripped = line.strip()

        # 检测标题
        if stripped.startswith("#"):
            continue

        # 跳过待学习条目
        if "[待学习]" in stripped or "[未知]" in stripped or "[待填写]" in stripped:
            continue

        # 提取有内容的条目
        if stripped.startswith(("-", "*")) and ":" in stripped:
            # 检查冒号后面是否有实际内容
            parts = stripped.split(":", 1)
            if len(parts) == 2 and parts[1].strip() and parts[1].strip() != "-":
                learned_items.append(stripped)

    # 如果没有已学习内容，返回空
    if not learned_items:
        return ""

    # 构建摘要
    summary = """# User Profile Summary

## 已了解的用户信息

"""
    summary += "\n".join(learned_items[:15])  # 限制 15 条

    return summary


def compile_persona(persona_content: str) -> str:
    """
    personas/user_custom.md -> persona.custom.md

    保留已归集的用户偏好，跳过空白占位内容。
    目标: <=150 tokens (~600 字符)
    """
    # 如果内容是占位符/空白，跳过
    if "尚未收集" in persona_content or len(persona_content.strip()) < 100:
        return ""

    # 提取有实质内容的条目
    items = []
    lines = persona_content.split("\n")

    for line in lines:
        stripped = line.strip()

        # 跳过标题和注释
        if stripped.startswith("#") or stripped.startswith("<!--") or stripped.startswith(">"):
            continue

        # 提取有内容的条目
        if stripped.startswith("-") and ":" in stripped:
            # 检查冒号后面是否有实际内容
            parts = stripped.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                items.append(stripped)

    if not items:
        return ""

    summary = "# User Persona Preferences\n\n"
    summary += "\n".join(items[:15])  # 限制 15 条

    return summary


def check_compiled_outdated(identity_dir: Path, max_age_hours: int = 24) -> bool:
    """
    检查编译产物是否过期

    Args:
        identity_dir: identity 目录路径
        max_age_hours: 最大有效时间（小时）

    Returns:
        True 如果过期或不存在
    """
    compiled_dir = identity_dir / "compiled"
    timestamp_file = compiled_dir / ".compiled_at"

    if not timestamp_file.exists():
        return True

    try:
        compiled_at = datetime.fromisoformat(timestamp_file.read_text(encoding="utf-8").strip())
        age = datetime.now() - compiled_at
        return age.total_seconds() > max_age_hours * 3600
    except Exception:
        return True


def get_compiled_content(identity_dir: Path) -> dict[str, str]:
    """
    获取所有编译产物内容

    Args:
        identity_dir: identity 目录路径

    Returns:
        编译产物内容字典
    """
    compiled_dir = identity_dir / "compiled"
    results = {}

    files = [
        ("soul", "soul.summary.md"),
        ("agent_core", "agent.core.md"),
        ("agent_tooling", "agent.tooling.md"),
        ("user", "user.summary.md"),
    ]

    for key, filename in files:
        filepath = compiled_dir / filename
        if filepath.exists():
            results[key] = filepath.read_text(encoding="utf-8")
        else:
            results[key] = ""

    return results
