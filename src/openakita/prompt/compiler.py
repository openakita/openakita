"""
Prompt Compiler (v2) — LLM 辅助编译 + 缓存 + 规则降级

编译流程:
1. 检查源文件是否变更 (mtime 比较)
2. 如果未变更, 跳过 (使用缓存)
3. 如果变更, 用 LLM 生成高质量摘要
4. LLM 不可用时回退到规则编译 (清理 HTML 残留)
5. 写入 compiled/ 目录

编译目标:
- SOUL.md -> soul.summary.md (<=150 tokens)
- AGENT.md -> agent.core.md (<=250 tokens)
- AGENT.md -> agent.tooling.md (<=200 tokens)
- USER.md -> user.summary.md (<=120 tokens)
- personas/user_custom.md -> persona.custom.md (<=150 tokens)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# =========================================================================
# LLM Compilation Prompts
# =========================================================================

_COMPILE_PROMPTS: dict[str, dict] = {
    "soul": {
        "system": "你是一个文本精简专家。",
        "user": """将以下 AI 身份文档精简为核心原则摘要。

要求:
- 保留所有核心价值观和行为原则
- 保留关键的"做/不做"清单
- 删除叙事性段落、示例和比喻
- 删除 HTML 注释和格式噪声
- 输出纯 Markdown，不超过 {max_tokens} tokens
- 使用紧凑的列表格式

原文:
{content}""",
        "max_tokens": 150,
    },
    "agent_core": {
        "system": "你是一个文本精简专家。",
        "user": """将以下 AI 行为规范文档精简为核心执行原则。

要求:
- 保留 Ralph Wiggum 核心循环逻辑
- 保留任务执行流程
- 保留禁止行为清单
- 删除配置示例、命令列表、架构说明等参考信息
- 输出纯 Markdown，不超过 {max_tokens} tokens

原文:
{content}""",
        "max_tokens": 250,
    },
    "agent_tooling": {
        "system": "你是一个文本精简专家。",
        "user": """从以下文档中提取工具使用原则。

要求:
- 保留工具选择优先级
- 保留禁止的敷衍响应模式
- 保留渐进式披露机制说明
- 删除具体工具列表（运行时通过 tools 参数注入）
- 输出纯 Markdown，不超过 {max_tokens} tokens

原文:
{content}""",
        "max_tokens": 200,
    },
    "user": {
        "system": "你是一个文本精简专家。",
        "user": """从以下用户档案中提取已知信息。

要求:
- 只保留有实际内容的字段（跳过"[待学习]"等占位符）
- 保留用户称呼、技术栈、偏好、工作习惯等已知信息
- 输出紧凑的列表格式，不超过 {max_tokens} tokens
- 如果没有任何已知信息，输出空字符串

原文:
{content}""",
        "max_tokens": 120,
    },
    "persona_custom": {
        "system": "你是一个文本精简专家。",
        "user": """从以下用户自定义人格偏好中提取已归集的信息。

要求:
- 只保留有实际内容的偏好（跳过空白占位内容）
- 保留沟通风格、情感偏好等特质
- 输出紧凑的列表格式，不超过 {max_tokens} tokens
- 如果没有有效内容，输出空字符串

原文:
{content}""",
        "max_tokens": 150,
    },
}

_SOURCE_MAP: dict[str, str] = {
    "soul": "SOUL.md",
    "agent_core": "AGENT.md",
    "agent_tooling": "AGENT.md",
    "user": "USER.md",
    "persona_custom": "personas/user_custom.md",
}

_OUTPUT_MAP: dict[str, str] = {
    "soul": "soul.summary.md",
    "agent_core": "agent.core.md",
    "agent_tooling": "agent.tooling.md",
    "user": "user.summary.md",
    "persona_custom": "persona.custom.md",
}


# =========================================================================
# Main API (async, LLM-assisted)
# =========================================================================


class PromptCompiler:
    """LLM 辅助的 Prompt 编译器"""

    def __init__(self, brain=None):
        self.brain = brain

    async def compile_all(self, identity_dir: Path) -> dict[str, Path]:
        """编译所有 identity 文件, 使用 LLM 辅助 + 缓存"""
        compiled_dir = identity_dir / "compiled"
        compiled_dir.mkdir(exist_ok=True)
        results: dict[str, Path] = {}

        for target, config in _COMPILE_PROMPTS.items():
            source_path = identity_dir / _SOURCE_MAP[target]
            if not source_path.exists():
                logger.debug(f"[Compiler] Source not found: {source_path}")
                continue

            output_path = compiled_dir / _OUTPUT_MAP[target]

            if _is_up_to_date(source_path, output_path):
                results[target] = output_path
                continue

            source_content = source_path.read_text(encoding="utf-8")
            compiled = await self._compile_with_llm(source_content, config)

            if compiled and compiled.strip():
                output_path.write_text(compiled, encoding="utf-8")
                results[target] = output_path
                logger.info(f"[Compiler] LLM compiled {_SOURCE_MAP[target]} -> {_OUTPUT_MAP[target]}")

        (compiled_dir / ".compiled_at").write_text(
            datetime.now().isoformat(), encoding="utf-8"
        )
        return results

    async def _compile_with_llm(self, content: str, config: dict) -> str:
        """Try LLM compilation, fall back to rules if unavailable."""
        if self.brain:
            try:
                prompt = config["user"].format(
                    content=content, max_tokens=config["max_tokens"]
                )
                if hasattr(self.brain, "think_lightweight"):
                    response = await self.brain.think_lightweight(
                        prompt, system=config["system"]
                    )
                else:
                    response = await self.brain.think(
                        prompt, system=config["system"]
                    )
                result = (getattr(response, "content", None) or str(response)).strip()
                if result:
                    return result
            except Exception as e:
                logger.warning(f"[Compiler] LLM compilation failed, using rules: {e}")

        return _compile_with_rules(content, config)


# =========================================================================
# Sync API (backward compatible)
# =========================================================================


def compile_all(identity_dir: Path, use_llm: bool = False) -> dict[str, Path]:
    """
    同步编译所有源文件 (向后兼容)

    如果需要 LLM 辅助, 使用 PromptCompiler.compile_all() 异步版本。
    """
    compiled_dir = identity_dir / "compiled"
    compiled_dir.mkdir(exist_ok=True)
    results: dict[str, Path] = {}

    for target in _COMPILE_PROMPTS:
        source_path = identity_dir / _SOURCE_MAP[target]
        if not source_path.exists():
            continue

        output_path = compiled_dir / _OUTPUT_MAP[target]

        if _is_up_to_date(source_path, output_path):
            results[target] = output_path
            continue

        source_content = source_path.read_text(encoding="utf-8")
        config = _COMPILE_PROMPTS[target]
        compiled = _compile_with_rules(source_content, config)

        if compiled and compiled.strip():
            output_path.write_text(compiled, encoding="utf-8")
            results[target] = output_path
            logger.info(f"[Compiler] Rule compiled {_SOURCE_MAP[target]} -> {_OUTPUT_MAP[target]}")

    (compiled_dir / ".compiled_at").write_text(
        datetime.now().isoformat(), encoding="utf-8"
    )
    return results


# =========================================================================
# Rule-based Compilation (fallback)
# =========================================================================


def _compile_with_rules(content: str, config: dict) -> str:
    """Rule-based compilation with HTML cleanup."""
    content = _clean_html(content)
    lines = content.split("\n")

    extracted: list[str] = []
    current_section = ""
    in_relevant = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("##"):
            current_section = stripped.lower()
            in_relevant = _is_relevant_section(current_section, config)
            continue

        if stripped.startswith("#"):
            continue

        if stripped.startswith(("-", "*")) or re.match(r"^\d+\.", stripped):
            if len(stripped) < 150:
                extracted.append(stripped)
        elif in_relevant and stripped and len(stripped) < 100:
            extracted.append(f"- {stripped}")
        elif not extracted and stripped and len(stripped) < 200:
            extracted.append(f"- {stripped}")

    unique = list(dict.fromkeys(extracted))
    max_items = max(config.get("max_tokens", 150) // 10, 3)
    return "\n".join(unique[:max_items])


def _clean_html(content: str) -> str:
    """Remove HTML comments and artifacts."""
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    content = re.sub(r"^\s*-->\s*$", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*<!--\s*$", "", content, flags=re.MULTILINE)
    return content


def _is_relevant_section(section: str, config: dict) -> bool:
    """Check if a section heading is relevant for this compilation target."""
    relevance_keywords = {
        150: ["原则", "核心", "principle", "core", "诚实", "校准", "价值"],
        250: ["ralph", "wigum", "核心", "core", "循环", "任务", "执行"],
        200: ["工具", "tool", "技能", "skill", "mcp", "敷衍"],
        120: ["基本", "技术", "偏好", "profile"],
    }
    max_tokens = config.get("max_tokens", 150)
    keywords = relevance_keywords.get(max_tokens, [])
    return any(kw in section for kw in keywords)


# =========================================================================
# Utilities (backward compatible)
# =========================================================================


def _is_up_to_date(source: Path, output: Path) -> bool:
    if not output.exists():
        return False
    try:
        return output.stat().st_mtime > source.stat().st_mtime
    except Exception:
        return False


def check_compiled_outdated(identity_dir: Path, max_age_hours: int = 24) -> bool:
    compiled_dir = identity_dir / "compiled"
    timestamp_file = compiled_dir / ".compiled_at"
    if not timestamp_file.exists():
        return True
    try:
        compiled_at = datetime.fromisoformat(
            timestamp_file.read_text(encoding="utf-8").strip()
        )
        age = datetime.now() - compiled_at
        return age.total_seconds() > max_age_hours * 3600
    except Exception:
        return True


def get_compiled_content(identity_dir: Path) -> dict[str, str]:
    compiled_dir = identity_dir / "compiled"
    results: dict[str, str] = {}
    for key, filename in _OUTPUT_MAP.items():
        filepath = compiled_dir / filename
        if filepath.exists():
            results[key] = filepath.read_text(encoding="utf-8")
        else:
            results[key] = ""
    return results


# Legacy function names (backward compat)
def compile_soul(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["soul"])

def compile_agent_core(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["agent_core"])

def compile_agent_tooling(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["agent_tooling"])

def compile_user(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["user"])

def compile_persona(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["persona_custom"])
