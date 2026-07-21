"""
Prompt Compiler (v2) — LLM 辅助编译 + 缓存 + 规则降级

编译流程:
1. 检查源文件是否变更 (mtime 比较)
2. 如果未变更, 跳过 (使用缓存)
3. 如果变更, 用 LLM 生成高质量摘要
4. LLM 不可用时回退到规则编译 (清理 HTML 残留)
5. 写入 compiled/ 目录

编译目标:
- SOUL.md -> identity.core.md (<=600 tokens，仅身份/使命/气质)
- AGENT.md -> agent.behavior.md (<=450 tokens，仅平台规则之外的行为增量)
- USER.md -> user.profile.core.md (<=300 tokens)
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

COMPILED_SCHEMA_VERSION = "9"
_COMPILER_VERSION_FILE = ".compiler_version"


# =========================================================================
# LLM Compilation Prompts
# =========================================================================

_COMPILE_PROMPTS: dict[str, dict] = {
    "identity_core": {
        "target": "identity_core",
        "system": "你是身份文件编译器，只提取人格身份，不复述平台行为规则。",
        "user": (
            "将以下 SOUL.md 编译为不超过 600 tokens 的身份核心。只保留自称、使命、"
            "独特价值取向、交流气质和人格特征。不要包含安全边界、诚实/来源标签、"
            "人类监督、权限、工具、任务执行、验证、记忆、自修复或多 Agent 规则；"
            "这些由平台提示统一提供。\n\n原文:\n{content}"
        ),
        "max_tokens": 600,
    },
    "agent_behavior": {
        "target": "agent_behavior",
        "system": "你是 Agent 行为增量编译器，只保留平台通用规则之外的项目特质。",
        "user": (
            "将以下 AGENT.md 编译为不超过 450 tokens 的行为增量。只保留 OpenAkita "
            "特有的主动洞察、成长循环、能力扩展和自修复倾向。不要包含安全、权限、"
            "工具选择、任务分解、执行、验证、错误报告、记忆操作、构建命令、"
            "多 Agent 编排或代码规范；这些由平台提示统一提供。\n\n原文:\n{content}"
        ),
        "max_tokens": 450,
    },
    "user_profile_core": {
        "target": "user_profile_core",
        "system": "你是用户档案编译器，只保留长期稳定且需要每轮遵守的信息。",
        "user": "从以下 USER.md 提取 pinned 用户偏好和稳定事实，跳过占位、过期、空内容，不超过 300 tokens。\n\n原文:\n{content}",
        "max_tokens": 300,
    },
}

_SOURCE_MAP: dict[str, str] = {
    "identity_core": "SOUL.md",
    "agent_behavior": "AGENT.md",
    "user_profile_core": "USER.md",
}

_OUTPUT_MAP: dict[str, str] = {
    "identity_core": "identity.core.md",
    "agent_behavior": "agent.behavior.md",
    "user_profile_core": "user.profile.core.md",
}

_ORPHAN_FILES = [
    "soul.summary.md",
    "user.summary.md",
    "agent.core.md",
    "agent.tooling.md",
    "identity.longform.index.md",
    "persona.custom.md",
]


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
        force_recompile = not _compiler_schema_is_current(runtime_dir)

        for target, config in _COMPILE_PROMPTS.items():
            source_path = identity_dir / _SOURCE_MAP[target]
            if not source_path.exists():
                logger.debug(f"[Compiler] Source not found: {source_path}")
                continue

            output_path = runtime_dir / _OUTPUT_MAP[target]

            if not force_recompile and _is_up_to_date(source_path, output_path):
                results[target] = output_path
                continue

            source_content = source_path.read_text(encoding="utf-8")
            compiled = await self._compile_with_llm(source_content, config)
            compiled = _enforce_token_limit(compiled.strip(), config.get("max_tokens", 500))
            output_path.write_text(compiled, encoding="utf-8", newline="\n")
            logger.info(f"[Compiler] LLM compiled {_SOURCE_MAP[target]} -> {_OUTPUT_MAP[target]}")
            results[target] = output_path

        _cleanup_orphan_files(runtime_dir)
        _write_compiled_timestamp(identity_dir, runtime_dir)
        return results

    async def _compile_with_llm(self, content: str, config: dict) -> str:
        """Try LLM compilation, fall back to rules if unavailable."""
        if self.brain:
            try:
                prompt = config["user"].format(content=content, max_tokens=config["max_tokens"])
                if hasattr(self.brain, "think_lightweight"):
                    response = await self.brain.think_lightweight(prompt, system=config["system"])
                else:
                    response = await self.brain.think(prompt, system=config["system"])
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
    force_recompile = not _compiler_schema_is_current(runtime_dir)

    for target in _COMPILE_PROMPTS:
        source_path = identity_dir / _SOURCE_MAP[target]
        if not source_path.exists():
            continue

        output_path = runtime_dir / _OUTPUT_MAP[target]

        if not force_recompile and _is_up_to_date(source_path, output_path):
            results[target] = output_path
            continue

        source_content = source_path.read_text(encoding="utf-8")
        config = _COMPILE_PROMPTS[target]
        compiled = _compile_with_rules(source_content, config)
        compiled = _enforce_token_limit(compiled.strip(), config.get("max_tokens", 500))
        output_path.write_text(compiled, encoding="utf-8", newline="\n")
        logger.info(f"[Compiler] Rule compiled {_SOURCE_MAP[target]} -> {_OUTPUT_MAP[target]}")
        results[target] = output_path

    _cleanup_orphan_files(runtime_dir)

    _write_compiled_timestamp(identity_dir, runtime_dir)
    return results


def _source_paths(identity_dir: Path) -> list[Path]:
    return [
        identity_dir / source_file
        for source_file in set(_SOURCE_MAP.values())
        if (identity_dir / source_file).exists()
    ]


def _write_compiled_timestamp(identity_dir: Path, runtime_dir: Path) -> None:
    timestamp_file = runtime_dir / ".compiled_at"
    timestamp_file.write_text(datetime.now().isoformat(), encoding="utf-8")
    (runtime_dir / _COMPILER_VERSION_FILE).write_text(
        COMPILED_SCHEMA_VERSION,
        encoding="utf-8",
    )
    try:
        max_source_mtime_ns = max(
            (source.stat().st_mtime_ns for source in _source_paths(identity_dir)),
            default=timestamp_file.stat().st_mtime_ns,
        )
        target_ns = max(max_source_mtime_ns + 1, timestamp_file.stat().st_mtime_ns)
        os.utime(timestamp_file, ns=(target_ns, target_ns))
    except OSError:
        pass


def _cleanup_orphan_files(runtime_dir: Path) -> None:
    """清理旧版编译管线遗留的孤儿文件。"""
    for filename in _ORPHAN_FILES:
        orphan = runtime_dir / filename
        if orphan.exists():
            try:
                orphan.unlink()
                logger.info(f"[Compiler] Cleaned up orphan file: {filename}")
            except Exception:
                pass


# =========================================================================
# Rule-based Compilation (fallback)
# =========================================================================


def _compile_with_rules(content: str, config: dict) -> str:
    """Compile one of the three identity-owned targets with deterministic rules."""
    target = config["target"]
    if target not in _OUTPUT_MAP:
        raise ValueError(f"Unknown identity compilation target: {target}")
    return _compile_identity_target(content, target, config["max_tokens"])


_OWNED_SECTION_MARKERS: dict[str, tuple[tuple[str, ...], ...]] = {
    # Platform safety, honesty, permissions and execution rules live in builder.py.
    # Identity compilation owns only who the Agent is and how it relates to users.
    "identity_core": (
        ("identity", "身份认知", "核心性格", "personality"),
        ("soul overview", "soul", "使命", "overview"),
    ),
    # Generic task/tool rules also live in builder.py. AGENT.md contributes only
    # OpenAkita-specific traits that are not already enforced by the platform.
    "agent_behavior": (
        ("成长循环", "growth loops", "growth loop"),
        ("self-healing", "自我修复"),
    ),
}

_TARGET_EXCLUDED_LINE_MARKERS: dict[str, tuple[str, ...]] = {
    "identity_core": (
        "安全",
        "诚实",
        "监督",
        "不道德",
        "伤害",
        "权限",
        "工具",
        "任务执行",
        "验证",
        "记忆",
        "自修复",
        "多 agent",
        "safety",
        "honest",
        "ethical",
        "supervision",
        "harm",
        "permission",
        "tool",
        "validation",
        "memory",
    ),
    "agent_behavior": (
        "工具",
        "技能",
        "命令",
        "记忆",
        "记录",
        "任务执行",
        "验证",
        "配置",
        "依赖",
        "权限",
        "安装",
        "重启",
        "磁盘",
        "模式识别循环",
        "经验沉淀循环",
        "**自修复**",
        "多 agent",
        "tool",
        "skill",
        "command",
        "memory",
        "task execution",
        "validation",
        "config",
        "dependency",
        "permission",
        "install",
    ),
    "user_profile_core": (
        "[待学习",
        "[待统计",
        "[待补充",
        "[agent 会",
        "[其他需要记住",
        "此文件由 openakita 自动维护",
        "最后更新:",
    ),
}


def _extract_owned_sections(content: str, target: str) -> list[str]:
    """Return recognized source sections in ownership-priority order."""
    marker_groups = _OWNED_SECTION_MARKERS.get(target)
    if not marker_groups:
        return []

    ranked_blocks: list[tuple[int, int, list[str]]] = []
    active: tuple[int, int, list[str]] | None = None
    in_code_block = False

    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).casefold()
            if active is not None and level <= active[1]:
                ranked_blocks.append(active)
                active = None
            for rank, markers in enumerate(marker_groups):
                if any(marker.casefold() in title for marker in markers):
                    if active is not None:
                        ranked_blocks.append(active)
                    active = (rank, level, [])
                    break
            continue

        if active is not None and stripped and not stripped.startswith(("|", "---")):
            line = stripped if stripped.startswith(("-", "*")) else f"- {stripped}"
            active[2].append(line)

    if active is not None:
        ranked_blocks.append(active)

    ranked_blocks.sort(key=lambda block: block[0])
    return [line for _, _, lines in ranked_blocks for line in lines]


def _normalize_compiled_lines(content: str, target: str) -> list[str]:
    """Normalize identity source while omitting code, tables and duplicate lines."""
    content = _clean_html(content)
    owned_lines = _extract_owned_sections(content, target)
    if owned_lines:
        candidates = owned_lines
    else:
        candidates = []
        in_code_block = False
        for raw in content.splitlines():
            line = raw.strip()
            if line.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block or not line or line.startswith(("#", "|", "---")):
                continue
            candidates.append(line if line.startswith(("-", "*")) else f"- {line}")

    normalized: list[str] = []
    seen: set[str] = set()
    excluded_markers = _TARGET_EXCLUDED_LINE_MARKERS.get(target, ())
    for line in candidates:
        folded = line.casefold()
        if any(marker.casefold() in folded for marker in excluded_markers):
            continue
        if target == "identity_core" and re.match(r"^-\s*\d+[.)]", line):
            continue
        if len(line) > 240:
            line = line[:237] + "..."
        key = line.strip()
        if key and key not in seen:
            seen.add(key)
            normalized.append(line)
    return normalized


def _enforce_token_limit(text: str, max_tokens: int) -> str:
    """Strictly cap compiled text using the shared prompt token estimator."""
    if not text or max_tokens <= 0:
        return ""

    from .budget import estimate_tokens

    if estimate_tokens(text) <= max_tokens:
        return text

    kept: list[str] = []
    for line in text.splitlines():
        candidate = "\n".join([*kept, line])
        if estimate_tokens(candidate) <= max_tokens:
            kept.append(line)
            continue

        low, high = 0, len(line)
        while low < high:
            mid = (low + high + 1) // 2
            partial = "\n".join([*kept, line[:mid].rstrip()])
            if estimate_tokens(partial) <= max_tokens:
                low = mid
            else:
                high = mid - 1
        if low:
            kept.append(line[:low].rstrip())
        break
    return "\n".join(kept).strip()


def _compile_identity_target(content: str, target: str, max_tokens: int) -> str:
    """Compile only the content owned by an identity target."""
    result = "\n".join(_normalize_compiled_lines(content, target))

    if not result:
        result = _STATIC_FALLBACKS.get(target, "")
    return _enforce_token_limit(result, max_tokens)


def _clean_html(content: str) -> str:
    """Remove HTML comments and artifacts."""
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    content = re.sub(r"^\s*-->\s*$", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*<!--\s*$", "", content, flags=re.MULTILINE)
    return content


# =========================================================================
# Static Fallback Templates (hand-crafted, high quality)
# =========================================================================

_STATIC_FALLBACKS: dict[str, str] = {
    "identity_core": """\
## 身份核心
- 你是 {{agent_name}}，由 OpenAkita 项目驱动的全能自进化 AI 助手。
- 使命是以实质性的帮助改善用户的工作和生活，而不是只给表面答案。
- 气质专业、好奇、务实且有温度，把用户当作能够自主判断的成年人。""",
    "agent_behavior": """\
## OpenAkita 行为增量
- 完成请求时留意更深层需求、可复用机会和用户可能忽略的风险；有实际价值时再提出。
- 识别重复工作模式，适时建议沉淀为自动化或可复用能力。
- 遇到系统异常时先诊断根因并尝试自修复，确实受阻后再清楚说明。""",
    "user_profile_core": "",
}


# =========================================================================
# Utilities
# =========================================================================


def _is_up_to_date(source: Path, output: Path) -> bool:
    if not output.exists():
        return False
    try:
        return output.stat().st_mtime_ns >= source.stat().st_mtime_ns
    except OSError:
        return False


def _compiler_schema_is_current(runtime_dir: Path) -> bool:
    version_file = runtime_dir / _COMPILER_VERSION_FILE
    try:
        return version_file.read_text(encoding="utf-8").strip() == COMPILED_SCHEMA_VERSION
    except OSError:
        return False


def check_compiled_outdated(identity_dir: Path, max_age_hours: int = 24) -> bool:
    runtime_dir = identity_dir / "runtime"
    if not _compiler_schema_is_current(runtime_dir):
        return True
    timestamp_file = runtime_dir / ".compiled_at"
    if not timestamp_file.exists():
        return True
    try:
        compiled_at = datetime.fromisoformat(timestamp_file.read_text(encoding="utf-8").strip())
        age = datetime.now() - compiled_at
        if age.total_seconds() > max_age_hours * 3600:
            return True
    except Exception:
        return True

    try:
        compiled_mtime_ns = timestamp_file.stat().st_mtime_ns
    except OSError:
        return True

    # Source file mtime check: recompile if any source changed after last compilation
    for target, source_file in _SOURCE_MAP.items():
        source_path = identity_dir / source_file
        output_path = runtime_dir / _OUTPUT_MAP[target]
        if not source_path.exists():
            continue
        if source_path.stat().st_mtime_ns > compiled_mtime_ns:
            return True
        if not _is_up_to_date(source_path, output_path):
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
