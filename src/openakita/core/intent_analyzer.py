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
        llm_flag = getattr(self, "_llm_suggest_plan", False)
        return llm_flag or self.score >= 3


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
    suppress_plan: bool = False
    complexity: ComplexitySignal = field(default_factory=ComplexitySignal)
    raw_output: str = ""
    fast_reply: bool = False


# Default fallback: behaves identically to the pre-optimization flow
_DEFAULT_RESULT = IntentResult(
    intent=IntentType.TASK,
    confidence=0.0,
    force_tool=True,
)

INTENT_ANALYZER_SYSTEM = """\
你是 Intent Analyzer。根据用户消息判断意图，只输出 YAML，不要解释。

意图类型：
- task: 需要执行操作（写文件、搜索、查看目录、创建、发送消息、运行命令等）
- query: 知识问答，不需要工具就能回答
- chat: 纯闲聊、寒暄、感谢、告别
- follow_up: 追问或修改上一轮结果
- command: 以 / 开头的系统指令

task_type 可选值: question/action/creation/analysis/reminder/compound/other

【目标】
1. 判断用户意图类型
2. 将请求转化为结构化任务定义
3. 推荐可能需要的工具分类
4. 提取记忆检索关键词
5. 评估任务复杂度和风险

tool_hints 可选值: File System, Browser, Web Search, IM Channel, Desktop, Agent, Organization, Config（空列表=仅基础工具）

输出格式（严格遵循，不要添加多余字段）：
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
complexity:
  destructive: [true/false]
  scope: [local/broad]
  suggest_plan: [true/false]
```

示例：
用户: "帮我查看项目里有哪些Python文件" → intent: task, task_type: action, goal: 列出项目中的Python文件, tool_hints: [File System]
用户: "搜索一下最新的AI新闻" → intent: task, task_type: action, goal: 搜索AI新闻, tool_hints: [Web Search]
用户: "Python的GIL是什么" → intent: query, task_type: question, goal: 解释Python GIL机制, tool_hints: []
用户: "推荐3本产品经理的书" → intent: query, task_type: question, goal: 推荐产品经理书籍, tool_hints: []
用户: "你好" → intent: chat, task_type: other, goal: 用户打招呼, tool_hints: []
用户: "改成UTF-8编码" → intent: follow_up, task_type: action, goal: 修改编码为UTF-8, tool_hints: [File System]

【complexity 判断标准】
- destructive: true — 请求涉及不可逆操作：删除数据/文件、清空内容、覆盖未备份的内容、重置配置、终止进程、发送不可撤回的消息等
- scope: broad — 请求涉及多文件修改、跨模块调整、全局重构、大范围迁移、整个项目级别的变更
- suggest_plan: true — 当 destructive=true，或 scope=broad，或任务需要多步骤协调时
- 非 task 类型的意图（chat/query/command）→ destructive: false, scope: local, suggest_plan: false

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
complexity:
  destructive: false
  scope: local
  suggest_plan: false
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
complexity:
  destructive: false
  scope: local
  suggest_plan: false
```

【示例3 — 破坏性任务】
用户: "帮我删除 temp 目录下所有超过30天的文件"

```yaml
intent: task
task_type: action
goal: 删除 temp 目录下超过30天的旧文件
inputs:
  given:
    - 目标目录: temp
    - 删除条件: 修改时间超过30天
  missing:
    - temp 目录的完整路径
constraints: []
output_requirements:
  - 确认删除的文件列表和数量
risks_or_ambiguities:
  - 删除操作不可逆，需确认目标目录正确
tool_hints: [File System]
memory_keywords: []
complexity:
  destructive: true
  scope: local
  suggest_plan: true
```

重要：你必须分析用户的实际消息内容来判断意图，不要复制上面的示例。"""


def _strip_thinking_tags(text: str) -> str:
    return re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()


# ---------------------------------------------------------------------------
# Rule-based fast-path for obvious chat messages
# ---------------------------------------------------------------------------

_GREETING_PATTERNS: set[str] = {
    # Chinese greetings / confirmations / farewells
    "你好", "您好", "你好呀", "你好啊", "嗨", "哈喽", "hello", "hi", "hey",
    "嗯", "嗯嗯", "好", "好的", "行", "ok", "可以", "收到", "了解",
    "谢谢", "谢了", "感谢", "thanks", "thank you", "thx",
    "再见", "拜拜", "bye", "晚安", "早安", "早", "早上好", "下午好", "晚上好",
    "在吗", "在不在", "你在吗",
    "哈哈", "哈哈哈", "笑死", "666", "牛", "厉害",
    "?", "？", "!", "！",
}

# When conversation history exists, only these unambiguous strings use the fast-path;
# punctuation and short confirmations are analyzed by the LLM (may be follow-ups).
_SAFE_WITH_HISTORY: frozenset[str] = frozenset({
    "你好", "您好", "你好呀", "你好啊", "嗨", "哈喽", "hello", "hi", "hey",
    "谢谢", "谢了", "感谢", "thanks", "thank you", "thx",
    "再见", "拜拜", "bye", "晚安", "早安", "早", "早上好", "下午好", "晚上好",
})

_FAST_CHAT_MAX_LEN = 12


def _try_fast_chat_shortcut(message: str, has_history: bool = False) -> IntentResult | None:
    """Rule-based shortcut: if message is an obvious greeting/confirmation,
    return CHAT intent immediately without LLM call.

    Returns None if the message doesn't match (should go through normal LLM analysis).
    """
    stripped = message.strip()

    if len(stripped) > _FAST_CHAT_MAX_LEN:
        return None

    normalized = stripped.lower().rstrip("~～。.!！?？、,，")

    # If there's conversation history, only match unambiguous greetings,
    # NOT punctuation or short confirmations that could be follow-ups
    if has_history:
        # With history, only pure greetings are safe to fast-path
        # Things like "？", "!", "好的", "嗯" could be follow-ups
        if normalized not in _SAFE_WITH_HISTORY:
            return None  # Ambiguous with history → go through LLM

    if normalized in _GREETING_PATTERNS:
        logger.info(f"[IntentAnalyzer] Fast-path: '{stripped}' matched as CHAT (rule-based)")
        return IntentResult(
            intent=IntentType.CHAT,
            confidence=1.0,
            task_definition="",
            task_type="other",
            tool_hints=[],
            memory_keywords=[],
            force_tool=False,
            todo_required=False,
            raw_output="[fast-chat-shortcut]",
            fast_reply=True,
        )

    if not has_history and len(stripped) <= 6 and all(
        not c.isalnum() or c in "0123456789" for c in stripped
    ):
        logger.info(f"[IntentAnalyzer] Fast-path: '{stripped}' is pure punctuation/emoji → CHAT")
        return IntentResult(
            intent=IntentType.CHAT,
            confidence=0.9,
            task_definition="",
            task_type="other",
            tool_hints=[],
            memory_keywords=[],
            force_tool=False,
            todo_required=False,
            raw_output="[fast-chat-shortcut-punctuation]",
            fast_reply=True,
        )

    return None


class IntentAnalyzer:
    """LLM-based intent analyzer. All messages go through LLM analysis."""

    def __init__(self, brain: Brain):
        self.brain = brain

    async def analyze(
        self,
        message: str,
        session_context: Any = None,
        has_history: bool = False,
    ) -> IntentResult:
        """Analyze user message intent. Rule-based shortcut for obvious greetings,
        LLM analysis for everything else."""
        # fast_reply 快捷路径已禁用，所有消息统一走 LLM 意图分析
        # fast_result = _try_fast_chat_shortcut(message, has_history=has_history)
        # if fast_result is not None:
        #     return fast_result

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
    """Fallback when LLM is unavailable.

    Not paranoid — the system prompt's risk assessment rules (Phase 1a) are the
    primary defense against destructive operations. The intent analyzer is an
    additional layer, not the only one.
    """
    return IntentResult(
        intent=IntentType.TASK,
        confidence=0.0,
        task_definition=message[:600],
        task_type="action",
        tool_hints=[],
        memory_keywords=[],
        force_tool=True,
        todo_required=False,
        suggest_plan=False,
        complexity=ComplexitySignal(),
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
            "complexity",
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

    result.complexity = _parse_complexity(extracted)
    result.suggest_plan = result.complexity.should_suggest_plan
    if result.complexity.score < 2:
        result.todo_required = False
        result.suppress_plan = True
    logger.info(
        f"[IntentAnalyzer] Complexity: destructive={result.complexity.destructive_potential}, "
        f"score={result.complexity.score}, suggest_plan={result.suggest_plan}"
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


def _parse_complexity(extracted: dict[str, str]) -> ComplexitySignal:
    """Parse complexity block from LLM output into ComplexitySignal."""
    raw = extracted.get("complexity", "")
    if not raw:
        return ComplexitySignal()

    fields: dict[str, str] = {}
    for line in raw.split("\n"):
        line = line.strip()
        m = re.match(r"^(\w+):\s*(.*)", line)
        if m:
            fields[m.group(1)] = m.group(2).strip().lower()

    destructive = fields.get("destructive", "false") == "true"
    scope_broad = fields.get("scope", "local") == "broad"
    llm_suggest_plan = fields.get("suggest_plan", "false") == "true"

    signal = ComplexitySignal(
        destructive_potential=destructive,
        multi_file_change=scope_broad,
        cross_module=scope_broad,
        multi_step_required=llm_suggest_plan and not destructive and not scope_broad,
    )
    signal._llm_suggest_plan = llm_suggest_plan  # type: ignore[attr-defined]
    return signal
