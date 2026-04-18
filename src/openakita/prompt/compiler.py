"""
Prompt Compiler (v2) — LLM-assisted compilation + caching + rule-based fallback.

Compilation flow:
1. Check whether the source file has changed (mtime comparison).
2. If unchanged, skip (use the cache).
3. If changed, use the LLM to generate a high-quality summary.
4. If the LLM is unavailable, fall back to rule-based compilation (clean up HTML residue).
5. Write to the compiled/ directory.

Compilation targets:
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
    # SOUL.md — not compiled; injected verbatim into the system prompt.
    # AGENT.md — not compiled; injected directly by builder.py (v3).
    # USER.md — not compiled; cleaned up at runtime by builder.py (v3).
    "persona_custom": {
        "target": "persona_custom",
        "system": "You are an expert at condensing text.",
        "user": """Extract the consolidated information from the following user-customized persona preferences.

Requirements:
- Keep only preferences with actual content (skip blank placeholder items).
- Preserve traits such as communication style and emotional preferences.
- Output a compact list, no more than {max_tokens} tokens.
- If there is no valid content, output an empty string.

Source:
{content}""",
        "max_tokens": 150,
    },
}

_SOURCE_MAP: dict[str, str] = {
    # SOUL.md — not compiled; injected verbatim.
    # AGENT.md — not compiled; injected verbatim (v3: switched to direct injection).
    # USER.md — not compiled; cleaned up at runtime by builder (v3: switched to runtime cleanup).
    "persona_custom": "personas/user_custom.md",
}

_OUTPUT_MAP: dict[str, str] = {
    # soul.summary.md — no longer generated.
    # agent.core.md — no longer generated (v3: AGENT.md injected directly).
    # user.summary.md — no longer generated (v3: USER.md cleaned at runtime).
    "persona_custom": "persona.custom.md",
}

_ORPHAN_FILES = ["soul.summary.md", "agent.tooling.md", "agent.core.md", "user.summary.md"]


# =========================================================================
# Main API (async, LLM-assisted)
# =========================================================================


class PromptCompiler:
    """LLM-assisted prompt compiler."""

    def __init__(self, brain=None):
        self.brain = brain

    async def compile_all(self, identity_dir: Path) -> dict[str, Path]:
        """Compile all identity files using LLM assistance with caching."""
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
                logger.info(
                    f"[Compiler] LLM compiled {_SOURCE_MAP[target]} -> {_OUTPUT_MAP[target]}"
                )
            else:
                fallback = source_content[: config.get("max_tokens", 500)]
                output_path.write_text(fallback, encoding="utf-8")
                logger.info(
                    f"[Compiler] LLM compilation empty for {target}, wrote truncated source"
                )
            results[target] = output_path

        (runtime_dir / ".compiled_at").write_text(datetime.now().isoformat(), encoding="utf-8")
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
    Synchronously compile all source files (backward compatible).

    For LLM-assisted compilation, use the async PromptCompiler.compile_all() instead.
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
            logger.info(f"[Compiler] Rule compiled {_SOURCE_MAP[target]} -> {_OUTPUT_MAP[target]}")
        else:
            fallback = source_content[: config.get("max_tokens", 500)]
            output_path.write_text(fallback, encoding="utf-8")
            logger.info(f"[Compiler] Rule extraction empty for {target}, wrote truncated source")
        results[target] = output_path

    _cleanup_orphan_files(runtime_dir)

    (runtime_dir / ".compiled_at").write_text(datetime.now().isoformat(), encoding="utf-8")
    return results


def _cleanup_orphan_files(runtime_dir: Path) -> None:
    """Clean up orphan files left over from the legacy compilation pipeline."""
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

_RELEVANCE_KEYWORDS: dict[str, list[str]] = {
    "agent_core": [
        "ralph",
        "wiggum",
        "铁律",
        "永不放弃",
        "任务执行",
        "执行流程",
        "self-check",
        "prohibited",
        "禁止",
        "proactive",
        "主动",
        "self-healing",
        "自修复",
        "成长循环",
        "growth",
        "每轮自检",
    ],
    "agent_tooling": [
        "工具",
        "tool",
        "技能",
        "skill",
        "mcp",
        "脚本",
        "script",
        "优先级",
        "priority",
        "临时脚本",
        "能力扩展",
        "capability",
        "敷衍",
        "没有工具",
    ],
    "user": ["基本", "技术", "偏好", "profile", "习惯", "工作"],
    "persona_custom": ["性格", "风格", "沟通", "偏好", "特质"],
}

# Sections to explicitly exclude per target (avoid cross-contamination)
_EXCLUDE_SECTIONS: dict[str, list[str]] = {
    "agent_core": [
        "tool priority",
        "工具选择",
        "工具使用",
        "临时脚本",
        "没有工具",
        "environment",
        "环境",
        "build",
        "running",
        "multi-agent",
        "orchestration",
        "codebase",
        "code style",
        "skill definition",
        "operational notes",
        "learned patterns",
        "common issues",
    ],
    "agent_tooling": [
        "ralph",
        "wiggum",
        "铁律",
        "永不放弃",
        "backpressure",
        "self-check",
        "environment",
        "环境",
        "build",
        "running",
        "multi-agent",
        "orchestration",
        "codebase",
        "code style",
        "skill definition",
        "operational notes",
        "validation",
    ],
}


def _compile_with_rules(content: str, config: dict) -> str:
    """Rule-based compilation with HTML cleanup and code block skipping.

    Falls back to static templates if extraction produces poor results.

    ADR (EV3): For targets listed in ``_STATIC_FALLBACKS`` (currently
    ``agent_core``), this *sync* path always returns the hand-crafted static
    template and never parses ``AGENT.md``.  This is intentional:

    * The sync path is used at import time / first prompt build when no event
      loop is available.  It must be fast and deterministic.
    * The *async* ``compile()`` path (which calls the LLM) is the canonical
      route for incorporating live ``AGENT.md`` edits.  It writes compiled
      output to ``identity/runtime/agent.core.md``.
    * On startup, ``PromptBuilder`` should call ``check_compiled_outdated``
      and, when stale, schedule an async ``compile_all`` so that the runtime
      prompt reflects the latest ``AGENT.md``.  Until that finishes, the
      static fallback provides a safe, well-tested default.
    """
    target = config.get("target", "")

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
    # NOTE: The agent_core and agent_tooling fallbacks are no longer in use.
    # (v3: AGENT.md is now injected directly by builder.py; the safety net lives in builder._BUILT_IN_DEFAULTS.)
    # Kept here only for backward compatibility; not invoked by the new code paths.
    "agent_core": """\
## Core execution principles

### Task execution flow
1. Understand the user's intent and break it into subtasks.
2. Check whether the required skills are already available.
3. If a skill is missing, search and install it, or write one yourself.
4. Ralph-loop execution: execute -> verify -> on failure, retry with a different approach.
5. Update MEMORY.md to record progress and lessons learned.

### Self-check each turn
1. What does the user actually want?
2. Are there issues/opportunities the user may not have considered?
3. Is there a better way to approach this task?
4. Have I handled something similar before?

### Growth loop
- Pattern recognition: when the same operation recurs a third time -> proactively propose packaging it as a skill.
- Experience capture: failure lessons / effective approaches / user corrections -> record them to memory immediately.
- Capability expansion: missing capability -> search / install / create -> continue the task.

### Self-healing
- Diagnose the error -> self-heal (config / dependencies / permissions) -> verify -> record.
- Only explain to the user after attempts to fix have failed.

### Forbidden behaviors
- Deleting user data (unless explicitly requested).
- Giving up on a task (unless the user explicitly cancels it).
- Replying only with text without calling tools (in task scenarios).
- Saying "can't do it" — instead, search / install / create the capability.

### Iron-rule exceptions
- Exception: in multi-agent mode, if a task is clearly better handled by a specialist agent, proactive delegation is allowed.
- Delegation is not giving up — it is a way to achieve higher quality; you remain responsible for the final result after delegating.""",
    "agent_tooling": """\
## Tool usage principles

### Core principle: tasks must be completed via tools or scripts
Not using tools/scripts = the task was not truly executed.

### Tool selection order
1. **Installed skills** — skills may come from the built-in directory, the user workspace directory, or the project directory; do not guess paths, use `list_skills` / `get_skill_info`.
2. **MCP server tools** — external tools invoked via the MCP protocol.
3. **Shell commands** — system commands and scripts.
4. **Ad-hoc scripts** — write a script with write_file and run it via run_shell.
5. **Web search + install** — search GitHub to find and install new capabilities.
6. **Write your own skill** — use skill-creator to create a permanent skill.

### Capability-expansion protocol (when a capability is missing)
1. **Search** — check installed skills first, then search the web.
2. **Install** — when a suitable skill is found -> install and load it immediately.
3. **Create** — if nothing existing fits -> create one with skill-creator.
4. **Record** — after acquiring a new capability, update experience memory.
Missing capability = needs to be acquired = acquire it = continue the task. There is no "report back to the user" step in between.

### Forbidden dismissive behaviors
- "I don't have that feature right now"
- "You need to do it yourself..."
- "I suggest you manually..."
- Replying only with text without calling any tool.
- "Let me handle that" -> invoke a tool immediately.
- "I don't have that capability yet, let me create one" -> skill-creator or an ad-hoc script.""",
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
        compiled_at = datetime.fromisoformat(timestamp_file.read_text(encoding="utf-8").strip())
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


def compile_agent_tooling(content: str) -> str:
    return _compile_with_rules(content, {"target": "agent_tooling", "max_tokens": 300})


def compile_user(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["user"])


def compile_persona(content: str) -> str:
    return _compile_with_rules(content, _COMPILE_PROMPTS["persona_custom"])
