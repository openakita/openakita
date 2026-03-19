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
- AGENT.md -> agent.core.md (<=300 tokens)
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
    # SOUL.md 不再编译 — 全文直接注入 system prompt，保留哲学基调
    "agent_core": {
        "target": "agent_core",
        "system": "你是一个文本精简专家。",
        "user": """将以下 AI 行为规范文档精简为核心执行原则。

要求:
- 保留 Ralph Wiggum 核心循环逻辑和三条铁律
- 保留任务执行流程（理解→检查→获取→执行→验证→更新）
- 保留主动行为框架（每轮自检、成长循环、自我修复协议）
- 保留禁止行为清单
- 删除配置示例、命令列表、架构说明、代码块等参考信息
- 输出纯 Markdown，不超过 {max_tokens} tokens

原文:
{content}""",
        "max_tokens": 250,
    },
    "user": {
        "target": "user",
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
        "target": "persona_custom",
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
    # SOUL.md 不再编译 — 全文注入
    "agent_core": "AGENT.md",
    "user": "USER.md",
    "persona_custom": "personas/user_custom.md",
}

_OUTPUT_MAP: dict[str, str] = {
    # soul.summary.md 不再生成 — SOUL.md 全文注入
    "agent_core": "agent.core.md",
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
        runtime_dir = identity_dir / "runtime"
        runtime_dir.mkdir(exist_ok=True)
        results: dict[str, Path] = {}

        for target, config in _COMPILE_PROMPTS.items():
            source_path = identity_dir / _SOURCE_MAP[target]
            if not source_path.exists():
                logger.debug(f"[Compiler] Source not found: {source_path}")
                continue

            output_path = runtime_dir / _OUTPUT_MAP[target]

            if _is_up_to_date(source_path, output_path):
                results[target] = output_path
                continue

            source_content = source_path.read_text(encoding="utf-8")
            compiled = await self._compile_with_llm(source_content, config)

            if compiled and compiled.strip():
                output_path.write_text(compiled, encoding="utf-8")
                results[target] = output_path
                logger.info(f"[Compiler] LLM compiled {_SOURCE_MAP[target]} -> {_OUTPUT_MAP[target]}")

        (runtime_dir / ".compiled_at").write_text(
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
    runtime_dir = identity_dir / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    results: dict[str, Path] = {}

    for target in _COMPILE_PROMPTS:
        source_path = identity_dir / _SOURCE_MAP[target]
        if not source_path.exists():
            continue

        output_path = runtime_dir / _OUTPUT_MAP[target]

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

    (runtime_dir / ".compiled_at").write_text(
        datetime.now().isoformat(), encoding="utf-8"
    )
    return results


# =========================================================================
# Rule-based Compilation (fallback)
# =========================================================================

_RELEVANCE_KEYWORDS: dict[str, list[str]] = {
    "agent_core": [
        "ralph", "wiggum", "铁律", "永不放弃", "任务执行", "执行流程",
        "self-check", "prohibited", "禁止", "proactive", "主动",
        "self-healing", "自修复", "成长循环", "growth", "每轮自检",
    ],
    "user": ["基本", "技术", "偏好", "profile", "习惯", "工作"],
    "persona_custom": ["性格", "风格", "沟通", "偏好", "特质"],
}

# Sections to explicitly exclude per target (avoid cross-contamination)
_EXCLUDE_SECTIONS: dict[str, list[str]] = {
    "agent_core": [
        "tool priority", "工具选择", "工具使用", "临时脚本", "没有工具",
        "environment", "环境", "build", "running", "multi-agent",
        "orchestration", "codebase", "code style", "skill definition",
        "operational notes", "learned patterns", "common issues",
    ],
}


def _compile_with_rules(content: str, config: dict) -> str:
    """Rule-based compilation with HTML cleanup and code block skipping.

    Falls back to static templates if extraction produces poor results.
    """
    target = config.get("target", "")

    # Try static fallback first — guaranteed quality
    if target in _STATIC_FALLBACKS:
        return _STATIC_FALLBACKS[target]

    # Otherwise do rule-based extraction
    content = _clean_html(content)
    lines = content.split("\n")

    extracted: list[str] = []
    current_section = ""
    in_relevant = False
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Skip code blocks entirely
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if not stripped:
            continue

        if stripped.startswith("##"):
            current_section = stripped.lower()
            in_relevant = _is_relevant_section(current_section, target)
            continue

        if stripped.startswith("#"):
            continue

        # Skip table rows and separator lines
        if stripped.startswith("|") or stripped.startswith("---"):
            continue

        if in_relevant:
            if stripped.startswith(("-", "*")) or re.match(r"^\d+\.", stripped):
                if len(stripped) < 150:
                    extracted.append(stripped)
            elif len(stripped) < 100:
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


def _is_relevant_section(section: str, target: str) -> bool:
    """Check if a section heading is relevant for a specific compilation target."""
    # Check exclusions first
    excludes = _EXCLUDE_SECTIONS.get(target, [])
    if any(ex in section for ex in excludes):
        return False

    keywords = _RELEVANCE_KEYWORDS.get(target, [])
    return any(kw in section for kw in keywords)


# =========================================================================
# Static Fallback Templates (hand-crafted, high quality)
# =========================================================================

_STATIC_FALLBACKS: dict[str, str] = {
    # SOUL.md 不再需要 fallback — 全文直接注入

    "agent_core": """\
## 核心执行原则

### 任务执行流程
1. 理解用户意图，分解为子任务
2. 检查所需技能是否已有
3. 缺少技能则搜索安装或自己编写
4. Ralph 循环执行：执行 → 验证 → 失败则换方法重试
5. 更新 MEMORY.md 记录进度和经验

### 每轮自检
1. 用户真正想要什么？
2. 有没有用户可能没想到的问题/机会？
3. 这个任务有没有更好的方式？
4. 之前有没有处理过类似的事？

### 成长循环
- 模式识别：同一操作第 3 次出现 → 主动提议封装为技能
- 经验沉淀：失败教训/高效方法/用户纠正 → 立即记录到记忆
- 能力扩展：缺少能力 → 搜索/安装/创建 → 继续任务

### 自我修复
- 诊断错误 → 自修复（配置/依赖/权限）→ 验证 → 记录
- 只有尝试修复失败后才向用户说明

### 禁止行为
- 删除用户数据（除非明确要求）
- 放弃任务（除非用户明确取消）
- 只回复文字而不调用工具（任务场景下）
- 说"做不到" — 应该搜索/安装/创建能力""",
}


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
    runtime_dir = identity_dir / "runtime"
    timestamp_file = runtime_dir / ".compiled_at"
    if not timestamp_file.exists():
        return True
    try:
        compiled_at = datetime.fromisoformat(
            timestamp_file.read_text(encoding="utf-8").strip()
        )
        age = datetime.now() - compiled_at
        if age.total_seconds() > max_age_hours * 3600:
            return True
    except Exception:
        return True

    # Source file mtime check: recompile if any source changed after last compilation
    for target, source_file in _SOURCE_MAP.items():
        source_path = identity_dir / source_file
        output_path = runtime_dir / _OUTPUT_MAP[target]
        if source_path.exists() and not _is_up_to_date(source_path, output_path):
            return True

    return False


def get_compiled_content(identity_dir: Path) -> dict[str, str]:
    runtime_dir = identity_dir / "runtime"
    results: dict[str, str] = {}
    for key, filename in _OUTPUT_MAP.items():
        filepath = runtime_dir / filename
        if filepath.exists():
            results[key] = filepath.read_text(encoding="utf-8")
        else:
            results[key] = ""
    return results


# Legacy function names (backward compat)
def compile_soul(content: str) -> str:
    """Deprecated: SOUL.md is now injected as full text, no compilation needed."""
    import re
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    return content.strip()

def compile_agent_core(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["agent_core"])

def compile_user(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["user"])

def compile_persona(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["persona_custom"])
