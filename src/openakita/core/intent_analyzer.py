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
    """Complexity signal used to decide whether to suggest switching to Plan mode."""

    multi_file_change: bool = False
    cross_module: bool = False
    ambiguous_scope: bool = False
    destructive_potential: bool = False
    multi_step_required: bool = False

    @property
    def score(self) -> int:
        return sum(
            [
                self.multi_file_change,
                self.cross_module,
                self.ambiguous_scope,
                self.destructive_potential * 2,
                self.multi_step_required,
            ]
        )

    @property
    def should_suggest_plan(self) -> bool:
        from ..config import settings

        threshold = getattr(settings, "plan_suggest_threshold", 5)
        llm_flag = getattr(self, "_llm_suggest_plan", False)
        if llm_flag and self.score >= max(threshold - 2, 2):
            return True
        return self.score >= threshold


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
You are the Intent Analyzer. Based on the user's message, determine the intent and complexity. Output YAML only, no explanations.

Intent types:
- task: requires performing an operation (write file, search, list directory, create, send message, run command, etc.)
- query: knowledge question that can be answered without tools
- chat: pure small talk, greetings, thanks, farewells
- follow_up: follow-up question or modification of the previous result
- command: system instruction starting with /

task_type options: question/action/creation/analysis/reminder/compound/other

tool_hints options: File System, Browser, Web Search, IM Channel, Desktop, Agent, Organization, Config (empty list = only basic tools)

complexity fields (only fill in when intent=task; may be omitted for other intents):
- destructive: whether the operation is irreversible or affects critical system resources. Typical true cases: deleting files/data, formatting disks, DROP TABLE, force push, modifying system config files (e.g. hosts), killing processes, overwriting important data, etc.
- scope: narrow=affects only a single file or local area; broad=affects multiple files/modules/globally/the whole project
- suggest_plan: whether to suggest planning before execution. Usually true when destructive=true or scope=broad

Output format (strictly follow):
```yaml
intent: <type>
task_type: <type>
goal: <one-sentence description>
tool_hints: [<tool category>]
memory_keywords: [<memory keywords>]
destructive: <true/false>
scope: <narrow/broad>
suggest_plan: <true/false>
```

Examples:
User: "Show me which Python files are in the project" -> intent: task, task_type: action, goal: list Python files in the project, tool_hints: [File System], destructive: false, scope: narrow, suggest_plan: false
User: "Delete all .bak files for me" -> intent: task, task_type: action, goal: delete all .bak files, tool_hints: [File System], destructive: true, scope: broad, suggest_plan: true
User: "Modify /etc/hosts for me" -> intent: task, task_type: action, goal: modify the hosts file, tool_hints: [File System], destructive: true, scope: narrow, suggest_plan: true
User: "git push --force origin main" -> intent: task, task_type: action, goal: force push to main branch, tool_hints: [File System], destructive: true, scope: broad, suggest_plan: true
User: "What is Python's GIL" -> intent: query, task_type: question, goal: explain Python GIL mechanism, tool_hints: []
User: "Hello" -> intent: chat, task_type: other, goal: user greeting, tool_hints: []
User: "Change it to UTF-8 encoding" -> intent: follow_up, task_type: action, goal: change encoding to UTF-8, tool_hints: [File System], destructive: false, scope: narrow, suggest_plan: false

Key principles:
- Math calculations, date/time, concept explanations, general-knowledge questions -> always query, not task
- Only requests that require **actually operating on external systems** (read/write files, run commands, search the web, send messages) are task
- When unsure, if it can be answered without tools, choose query
- The destructive judgment should be based on semantic analysis of the operation's real consequences, not simple keyword matching

Important: you must analyze the user's actual message content to judge intent; do not copy the examples above."""


def _strip_thinking_tags(text: str) -> str:
    return re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()


# ---------------------------------------------------------------------------
# Rule-based fast-path for obvious chat messages
# ---------------------------------------------------------------------------

_GREETING_PATTERNS: set[str] = {
    # Chinese greetings / confirmations / farewells
    "你好",
    "您好",
    "你好呀",
    "你好啊",
    "嗨",
    "哈喽",
    "hello",
    "hi",
    "hey",
    "嗯",
    "嗯嗯",
    "好",
    "好的",
    "行",
    "ok",
    "可以",
    "收到",
    "了解",
    "谢谢",
    "谢了",
    "感谢",
    "thanks",
    "thank you",
    "thx",
    "再见",
    "拜拜",
    "bye",
    "晚安",
    "早安",
    "早",
    "早上好",
    "下午好",
    "晚上好",
    "在吗",
    "在不在",
    "你在吗",
    "哈哈",
    "哈哈哈",
    "笑死",
    "666",
    "牛",
    "厉害",
    "?",
    "？",
    "!",
    "！",
}

# When conversation history exists, only these unambiguous strings use the fast-path;
# punctuation and short confirmations are analyzed by the LLM (may be follow-ups).
_SAFE_WITH_HISTORY: frozenset[str] = frozenset(
    {
        "你好",
        "您好",
        "你好呀",
        "你好啊",
        "嗨",
        "哈喽",
        "hello",
        "hi",
        "hey",
        "谢谢",
        "谢了",
        "感谢",
        "thanks",
        "thank you",
        "thx",
        "再见",
        "拜拜",
        "bye",
        "晚安",
        "早安",
        "早",
        "早上好",
        "下午好",
        "晚上好",
    }
)

_FAST_CHAT_MAX_LEN = 12

# Rule-based patterns for QUERY intent (no tools needed).
# IMPORTANT: Chinese text has no whitespace, so \S+ greedily matches
# entire strings.  All patterns must be tightly bounded to avoid
# false-positives on context-dependent questions like
# "说回我的情况，我的猫是什么品种？".
_QUERY_PATTERNS = re.compile(
    r"^(?:"
    r"\d+\s*[+\-*/×÷]\s*\d+"  # math: 1+1, 3*4
    r"|\S{1,12}等于[几多少什么]"  # X等于几 (bounded prefix)
    r"|今天几[号日]"  # 今天几号
    r"|现在几[点时]"  # 现在几点
    r"|(?:什么|啥)(?:时间|日期|时候)"  # 什么时间
    r"|几月几[号日]"  # 几月几号
    r"|今天(?:是|星期|周)[几什么]"  # 今天星期几
    r"|什么是\S{1,10}"  # 什么是X (short term only)
    r"|\S{1,10}是什么"  # X是什么 (short term only)
    r")$",
    re.IGNORECASE,
)

# Context-dependent markers: when present the user is referencing prior
# conversation turns, so the fast (history-free) path MUST be skipped.
_CONTEXT_DEPENDENT_RE = re.compile(
    r"(?:说回|回到|刚才|之前|前面|上面|你说的|我说的|"
    r"我们讨论的|你提到的|我告诉你的|你记得|还记得|"
    r"来着|我的.{0,6}叫什么|"
    r"^[我你他她它](?:的|们的))"
)


def _try_fast_query_shortcut(message: str) -> IntentResult | None:
    """Rule-based shortcut for obvious query messages (math, date, definitions).
    Returns QUERY intent immediately without LLM call."""
    stripped = message.strip().rstrip("？?。.!！")
    if len(stripped) > 50:
        return None
    if _CONTEXT_DEPENDENT_RE.search(stripped):
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

    if (
        not has_history
        and len(stripped) <= 6
        and all(not c.isalnum() or c in "0123456789" for c in stripped)
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

            raw_output = _strip_thinking_tags(response.content).strip() if response.content else ""
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
            "intent",
            "task_type",
            "goal",
            "tool_hints",
            "memory_keywords",
            "constraints",
            "inputs",
            "output_requirements",
            "risks_or_ambiguities",
            "destructive",
            "scope",
            "suggest_plan",
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

    # Complexity analysis — purely from LLM output, no keyword matching
    if result.intent in (IntentType.TASK,):
        signal = ComplexitySignal()
        signal.destructive_potential = extracted.get("destructive", "").strip().lower() == "true"
        signal.cross_module = extracted.get("scope", "").strip().lower() == "broad"
        if extracted.get("suggest_plan", "").strip().lower() == "true":
            signal._llm_suggest_plan = True  # type: ignore[attr-defined]
        signal.multi_step_required = task_type == "compound"
        result.complexity = signal

        logger.info(
            f"[IntentAnalyzer] Complexity: destructive={signal.destructive_potential}, "
            f"score={signal.score}, suggest_plan={signal.should_suggest_plan}"
        )

        result.suggest_plan = signal.should_suggest_plan
        if signal.score < 2:
            result.todo_required = False
            result.suppress_plan = True
        if result.suggest_plan:
            logger.info(
                f"[IntentAnalyzer] Complex task detected (score={signal.score}), "
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
