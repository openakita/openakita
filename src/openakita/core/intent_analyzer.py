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
        return self.score >= 4


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

tool_hints 可选值: File System, Browser, Web Search, IM Channel, Desktop, Agent, Organization, Config（空列表=仅基础工具）

输出格式（严格遵循，不要添加多余字段）：
```yaml
intent: <类型>
task_type: <类型>
goal: <一句话描述>
tool_hints: [<工具分类>]
memory_keywords: [<记忆关键词>]
```

示例：
用户: "帮我查看项目里有哪些Python文件" → intent: task, task_type: action, goal: 列出项目中的Python文件, tool_hints: [File System]
用户: "搜索一下最新的AI新闻" → intent: task, task_type: action, goal: 搜索AI新闻, tool_hints: [Web Search]
用户: "Python的GIL是什么" → intent: query, task_type: question, goal: 解释Python GIL机制, tool_hints: []
用户: "推荐3本产品经理的书" → intent: query, task_type: question, goal: 推荐产品经理书籍, tool_hints: []
用户: "1+1等于几" → intent: query, task_type: question, goal: 简单数学计算, tool_hints: []
用户: "今天几号" → intent: query, task_type: question, goal: 询问当前日期, tool_hints: []
用户: "现在几点" → intent: query, task_type: question, goal: 询问当前时间, tool_hints: []
用户: "什么是微服务架构" → intent: query, task_type: question, goal: 解释微服务架构概念, tool_hints: []
用户: "你好" → intent: chat, task_type: other, goal: 用户打招呼, tool_hints: []
用户: "改成UTF-8编码" → intent: follow_up, task_type: action, goal: 修改编码为UTF-8, tool_hints: [File System]

关键判断原则：
- 数学计算、日期时间、概念解释、常识问答 → 一律是 query，不是 task
- 只有需要**实际操作外部系统**（读写文件、执行命令、搜索网络、发送消息）的请求才是 task
- 不确定时，如果不需要工具就能回答，选 query

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

# Rule-based patterns for QUERY intent (no tools needed)
_QUERY_PATTERNS = re.compile(
    r"^(?:"
    r"\d+\s*[+\-*/×÷]\s*\d+"           # math: 1+1, 3*4
    r"|.*等于[几多少什么]"                # X等于几
    r"|今天几[号日]"                      # 今天几号
    r"|现在几[点时]"                      # 现在几点
    r"|(?:什么|啥)(?:时间|日期|时候)"     # 什么时间
    r"|几月几[号日]"                      # 几月几号
    r"|今天(?:是|星期|周)[几什么]"        # 今天星期几
    r"|什么是\S+"                         # 什么是X
    r"|\S+是什么"                         # X是什么
    r").*$",
    re.IGNORECASE,
)


def _try_fast_query_shortcut(message: str) -> IntentResult | None:
    """Rule-based shortcut for obvious query messages (math, date, definitions).
    Returns QUERY intent immediately without LLM call."""
    stripped = message.strip().rstrip("？?。.!！")
    if len(stripped) > 50:
        return None
    if _QUERY_PATTERNS.match(stripped):
        logger.info(f"[IntentAnalyzer] Fast-path: '{stripped}' matched as QUERY (rule-based)")
        return IntentResult(
            intent=IntentType.QUERY,
            confidence=1.0,
            task_definition="",
            task_type="question",
            tool_hints=[],
            memory_keywords=[],
            force_tool=False,
            todo_required=False,
            raw_output="[fast-query-shortcut]",
            fast_reply=True,
        )
    return None


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
        """Analyze user message intent. Rule-based shortcut for obvious greetings
        and simple queries, LLM analysis for everything else."""
        # Rule-based fast-path for simple queries (math, date, definitions)
        query_result = _try_fast_query_shortcut(message)
        if query_result is not None:
            return query_result

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
    if result.intent in (IntentType.TASK,):
        result.complexity = _analyze_complexity(message, result)
        result.suggest_plan = result.complexity.should_suggest_plan
        if result.complexity.score < 2:
            result.todo_required = False
            result.suppress_plan = True
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


def _analyze_complexity(message: str, intent_result: IntentResult) -> ComplexitySignal:
    """Analyze message complexity to determine if Plan mode should be suggested."""
    msg = message.lower()
    signal = ComplexitySignal()

    # Multi-file change detection
    if any(kw in msg for kw in _MULTI_FILE_KEYWORDS) or any(kw in msg for kw in _GLOBAL_KEYWORDS):
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
    # Only flag truly compound tasks; message length alone is not a reliable signal
    # (users often send long context/requirements that are a single question).
    if intent_result.task_type == "compound":
        signal.multi_step_required = True

    return signal
