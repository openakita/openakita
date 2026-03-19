"""
IntentAnalyzer — Unified intent analysis via LLM.

Replaces the separate _compile_prompt() + _should_compile_prompt() with a single
LLM call that outputs structured intent, task definition, tool hints, and memory
keywords. All messages go through the LLM — no rule-based shortcut layer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .brain import Brain

logger = logging.getLogger(__name__)


class IntentType(Enum):
    CHAT = "chat"
    QUERY = "query"
    TASK = "task"
    FOLLOW_UP = "follow_up"
    COMMAND = "command"


@dataclass
class ComplexitySignal:
    """复杂任务信号，用于判断是否建议切换到 Plan 模式"""
    multi_file_change: bool = False
    cross_module: bool = False
    ambiguous_scope: bool = False
    destructive_potential: bool = False
    multi_step_required: bool = False

    @property
    def score(self) -> int:
        return sum([
            self.multi_file_change,
            self.cross_module,
            self.ambiguous_scope * 2,
            self.destructive_potential * 2,
            self.multi_step_required,
        ])

    @property
    def should_suggest_plan(self) -> bool:
        return self.score >= 3


@dataclass
class IntentResult:
    intent: IntentType
    confidence: float = 1.0
    task_definition: str = ""
    task_type: str = "other"
    tool_hints: list[str] = field(default_factory=list)
    memory_keywords: list[str] = field(default_factory=list)
    force_tool: bool = False
    todo_required: bool = False
    suggest_plan: bool = False
    complexity: ComplexitySignal = field(default_factory=ComplexitySignal)
    raw_output: str = ""


# Default fallback: behaves identically to the pre-optimization flow
_DEFAULT_RESULT = IntentResult(
    intent=IntentType.TASK,
    confidence=0.0,
    force_tool=True,
)

INTENT_ANALYZER_SYSTEM = """【角色】
你是 Intent Analyzer，负责分析用户消息的意图并生成结构化任务定义。

【输入】
用户的原始请求。

【目标】
1. 判断用户意图类型
2. 将请求转化为结构化任务定义
3. 推荐可能需要的工具分类
4. 提取记忆检索关键词

【输出结构】
请用以下 YAML 格式输出：

```yaml
intent: [意图类型: chat/query/task/follow_up/command]
task_type: [任务类型: question/action/creation/analysis/reminder/compound/other]
goal: [一句话描述任务目标]
inputs:
  given: [已提供的信息列表]
  missing: [缺失但可能需要的信息列表，如果没有则为空]
constraints: [约束条件列表，如果没有则为空]
output_requirements: [输出要求列表]
risks_or_ambiguities: [风险或歧义点列表，如果没有则为空]
tool_hints: [可能需要的工具分类列表，从以下选择: File System, Browser, Web Search, IM Channel, Scheduled, Desktop, Agent, Agent Hub, Agent Package, Organization, Profile, Persona, Config。注意：System/Memory/Plan/Skills/Skill Store/MCP 类工具始终可用，无需列出。空列表表示仅使用始终可用的基础工具]
memory_keywords: [用于检索历史记忆的关键词列表。空列表表示不需要检索记忆]
```

【意图类型说明】
- chat: 闲聊、寒暄、感谢、告别、简短确认（如"好的""收到""你好"）
- query: 信息查询，可能不需要工具就能回答（如"Python的GIL是什么"）
- task: 需要通过工具执行的任务（如"帮我写个脚本""搜索一下""创建文件"）
- follow_up: 对上一轮结果的追问或修改要求（如"改成UTF-8编码""再加一个功能"）
- command: 系统指令（以 / 开头的命令，如 /stop /plan）

【规则】
- 不要解决任务，不要给建议，只输出 YAML
- 极短消息（如"嗯""好""谢谢"）→ intent: chat
- 涉及"之前""上次""我说过"的消息 → memory_keywords 应包含相关主题词
- task_type: compound 表示多步骤任务，需要制定计划
- 保持简洁，每项不超过一句话

【示例1 — 闲聊】
用户: "你好呀"

```yaml
intent: chat
task_type: other
goal: 用户打招呼
inputs:
  given: [问候]
  missing: []
constraints: []
output_requirements: [友好回应]
risks_or_ambiguities: []
tool_hints: []
memory_keywords: []
```

【示例2 — 任务】
用户: "帮我写一个Python脚本，读取CSV文件并统计每列的平均值"

```yaml
intent: task
task_type: creation
goal: 创建一个读取CSV文件并计算各列平均值的Python脚本
inputs:
  given:
    - 需要处理的文件格式是CSV
    - 需要统计的是平均值
    - 使用Python语言
  missing:
    - CSV文件的路径或示例
    - 是否需要处理非数值列
constraints: []
output_requirements:
  - 可执行的Python脚本
  - 能够读取CSV文件
  - 输出每列的平均值
risks_or_ambiguities:
  - 未指定如何处理包含非数值数据的列
tool_hints: [File System]
memory_keywords: [CSV, Python脚本, 数据统计]
```"""


def _strip_thinking_tags(text: str) -> str:
    return re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()


class IntentAnalyzer:
    """LLM-based intent analyzer. All messages go through LLM analysis."""

    def __init__(self, brain: Brain):
        self.brain = brain

    async def analyze(
        self,
        message: str,
        session_context: Any = None,
    ) -> IntentResult:
        """Analyze user message intent via LLM. No rule-based shortcuts."""
        try:
            response = await self.brain.compiler_think(
                prompt=message,
                system=INTENT_ANALYZER_SYSTEM,
            )

            raw_output = (
                _strip_thinking_tags(response.content).strip()
                if response.content
                else ""
            )
            if not raw_output:
                logger.warning("[IntentAnalyzer] Empty LLM response, using default")
                return _make_default(message)

            logger.info(f"[IntentAnalyzer] Raw output: {raw_output[:200]}")
            return _parse_intent_output(raw_output, message)

        except Exception as e:
            logger.warning(f"[IntentAnalyzer] LLM analysis failed: {e}, using default")
            return _make_default(message)


def _make_default(message: str) -> IntentResult:
    """Fallback: behaves like the old flow (TASK + full tools + ForceToolCall)."""
    return IntentResult(
        intent=IntentType.TASK,
        confidence=0.0,
        task_definition=message[:600],
        task_type="action",
        tool_hints=[],
        memory_keywords=[],
        force_tool=True,
        todo_required=False,
        raw_output="",
    )


def _parse_intent_output(raw_output: str, message: str) -> IntentResult:
    """Parse YAML output from IntentAnalyzer LLM into IntentResult."""
    lines = raw_output.splitlines()

    extracted: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue

        kv_match = re.match(r"^(\w[\w_]*):\s*(.*)", stripped)
        if kv_match and kv_match.group(1) in (
            "intent", "task_type", "goal", "tool_hints", "memory_keywords",
            "constraints", "inputs", "output_requirements", "risks_or_ambiguities",
        ):
            if current_key:
                extracted[current_key] = "\n".join(current_lines).strip()
            current_key = kv_match.group(1)
            current_lines = [kv_match.group(2).strip()]
        elif current_key:
            current_lines.append(stripped)

    if current_key:
        extracted[current_key] = "\n".join(current_lines).strip()

    intent_str = extracted.get("intent", "task").lower().strip()
    intent_map = {
        "chat": IntentType.CHAT,
        "query": IntentType.QUERY,
        "task": IntentType.TASK,
        "follow_up": IntentType.FOLLOW_UP,
        "command": IntentType.COMMAND,
    }
    intent = intent_map.get(intent_str, IntentType.TASK)

    task_type = extracted.get("task_type", "other").strip()

    goal = extracted.get("goal", "").strip()
    task_definition = _build_task_definition(extracted, max_chars=600)

    tool_hints = _parse_list(extracted.get("tool_hints", ""))
    memory_keywords = _parse_list(extracted.get("memory_keywords", ""))

    force_tool = intent in (IntentType.TASK,) and task_type not in ("question", "other")
    todo_required = task_type == "compound"

    result = IntentResult(
        intent=intent,
        confidence=1.0,
        task_definition=task_definition or goal or message[:200],
        task_type=task_type,
        tool_hints=tool_hints,
        memory_keywords=memory_keywords,
        force_tool=force_tool,
        todo_required=todo_required,
        raw_output=raw_output,
    )

    # Complexity analysis for plan mode suggestion
    if intent in (IntentType.TASK,):
        result.complexity = _analyze_complexity(message, result)
        result.suggest_plan = result.complexity.should_suggest_plan
        if result.suggest_plan:
            logger.info(
                f"[IntentAnalyzer] Complex task detected (score={result.complexity.score}), "
                f"suggesting Plan mode"
            )

    return result


def _build_task_definition(extracted: dict[str, str], max_chars: int = 600) -> str:
    """Build a compact task definition string from extracted YAML fields."""
    parts: list[str] = []
    for key in ("goal", "task_type", "constraints", "output_requirements"):
        val = extracted.get(key, "").strip()
        if val and val not in ("[]", ""):
            parts.append(f"{key}: {val}")
        if sum(len(p) + 3 for p in parts) >= max_chars:
            break
    summary = " | ".join(parts)
    return summary[:max_chars] if len(summary) > max_chars else summary


def _parse_list(value: str) -> list[str]:
    """Parse a YAML list value into a Python list of strings."""
    value = value.strip()
    if not value or value == "[]":
        return []

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]

    items = []
    for line in value.split("\n"):
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip().strip("'\""))
        elif line and line not in ("[]",):
            items.append(line.strip("'\""))
    return items


# ---------------------------------------------------------------------------
# Complex task detection
# ---------------------------------------------------------------------------

_REFACTOR_KEYWORDS = [
    "重构", "refactor", "redesign", "改造", "迁移", "migration", "migrate",
    "重写", "rewrite",
]
_GLOBAL_KEYWORDS = [
    "全部", "所有", "整个项目", "across the codebase", "entire", "all files",
    "批量", "全局",
]
_ARCHITECTURE_KEYWORDS = [
    "架构", "设计方案", "技术选型", "architecture", "design",
    "系统设计", "system design",
]
_RESEARCH_KEYWORDS = [
    "调研", "分析", "对比", "evaluate", "compare", "research", "review",
    "评估", "综合分析",
]
_MULTI_FILE_KEYWORDS = [
    "多个文件", "multiple files", "所有文件", "每个文件",
    "across files", "跨文件",
]


def _analyze_complexity(message: str, intent_result: "IntentResult") -> ComplexitySignal:
    """Analyze message complexity to determine if Plan mode should be suggested."""
    msg = message.lower()
    signal = ComplexitySignal()

    # Multi-file change detection
    if any(kw in msg for kw in _MULTI_FILE_KEYWORDS):
        signal.multi_file_change = True
    elif any(kw in msg for kw in _GLOBAL_KEYWORDS):
        signal.multi_file_change = True

    # Cross-module detection
    if any(kw in msg for kw in _ARCHITECTURE_KEYWORDS):
        signal.cross_module = True

    # Ambiguous scope detection
    if any(kw in msg for kw in _REFACTOR_KEYWORDS):
        signal.ambiguous_scope = True
    if any(kw in msg for kw in _RESEARCH_KEYWORDS):
        signal.ambiguous_scope = True

    # Destructive potential
    destructive_words = ["删除", "清空", "重置", "drop", "delete all", "remove all", "清除"]
    if any(kw in msg for kw in destructive_words):
        signal.destructive_potential = True

    # Multi-step required (from intent analysis)
    if intent_result.task_type == "compound":
        signal.multi_step_required = True
    elif len(message) > 200:
        signal.multi_step_required = True

    return signal
