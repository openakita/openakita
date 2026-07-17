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
    from ._brain_legacy import Brain

logger = logging.getLogger(__name__)


class IntentType(Enum):
    CHAT = "chat"
    QUERY = "query"
    TASK = "task"
    FOLLOW_UP = "follow_up"
    COMMAND = "command"


class CapabilityScope(Enum):
    NONE = "none"
    FILES = "files"
    WEB = "web"
    BROWSER = "browser"
    PLUGIN = "plugin"
    SKILL = "skill"
    MCP = "mcp"
    IM = "im"
    DESKTOP = "desktop"
    ORG = "org"
    CODE = "code"


class PromptDepth(Enum):
    FAST = "fast"
    MINIMAL = "minimal"
    STANDARD = "standard"
    FULL = "full"


class MemoryScope(Enum):
    NONE = "none"
    PINNED_ONLY = "pinned_only"
    RELEVANT = "relevant"
    FULL = "full"


class RiskLevelHint(Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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
    capability_scope: list[CapabilityScope] = field(default_factory=list)
    prompt_depth: PromptDepth = PromptDepth.STANDARD
    memory_scope: MemoryScope = MemoryScope.RELEVANT
    catalog_scope: list[str] = field(default_factory=list)
    requires_tools: bool = False
    # P0-2 阶段 2：evidence_required 仅来自 LLM 自评（"我必须调工具才能回答"）
    # evidence_recommended 是规则启发式建议（"这种问题最好查一下，但不是必须"）
    # 二者不再 OR 等价；前者驱动重试/警告，后者驱动 prompt 软提示。
    evidence_required: bool = False
    evidence_recommended: bool = False
    requires_project_context: bool = False
    risk_level_hint: RiskLevelHint = RiskLevelHint.NONE
    compiler_source: str = ""
    compiler_fallback_reason: str = ""
    compiler_fallback_detail: str = ""


# Default fallback: behaves identically to the pre-optimization flow
_DEFAULT_RESULT = IntentResult(
    intent=IntentType.QUERY,
    confidence=0.0,
    force_tool=False,
    prompt_depth=PromptDepth.MINIMAL,
    memory_scope=MemoryScope.PINNED_ONLY,
    requires_tools=False,
    evidence_required=False,
)

INTENT_ANALYZER_MAX_TOKENS = 384

INTENT_ANALYZER_SYSTEM = """\
你是 Intent Analyzer。分析用户的实际请求，只输出紧凑 YAML（无代码块、无解释）。

intent: task=需实际操作外部系统；query=无工具知识问答；chat=闲聊；follow_up=追问/修改上轮结果；command=/指令。
task_type: question|action|creation|analysis|reminder|compound|other。
tool_hints: File System|Browser|Web Search|IM Channel|Desktop|Agent|Organization|Config。
capability_scope: none|files|web|browser|plugin|skill|mcp|im|desktop|org|code。

每次必须输出：
intent: <intent>
task_type: <task_type>
goal: <最多80字>
tool_hints: [<必需的工具类别>]
memory_keywords: [<最多5个检索词>]
capability_scope: [<所需能力>]

仅当值为 true/broad 或需要覆盖默认值时输出：
evidence_required: true
destructive: true
scope: broad
prompt_depth: fast|minimal|standard|full
memory_scope: none|pinned_only|relevant|full
catalog_scope: [tools|skills|plugins|mcp|memory|project]
requires_tools: true
requires_project_context: true
suggest_plan: true

默认：query/chat 用 minimal+pinned_only，其他用 standard+relevant；requires_tools 由 task 且存在
tool_hints/capability_scope 推导；destructive=false；scope=narrow。

原则：
- 数学/日期/概念/常识是 query；只有实际读写、执行、搜索、发送等外部操作才是 task。
- 需查 GitHub/issue/网页/日志/当前代码/配置/API/下载/验证时，evidence_required=true。
- 风险必须按实际后果判断，不得只匹配 add/remove/delete 字样。

示例（只演示格式，必须重新分析实际消息）：
问：Python的GIL是什么
intent: query
task_type: question
goal: 解释Python GIL
tool_hints: []
memory_keywords: [Python, GIL]
capability_scope: [none]

问：查福州明天温度并写query.py
intent: task
task_type: compound
goal: 查天气并写代码
tool_hints: [Web Search, File System]
memory_keywords: [福州, 明天天气, query.py]
capability_scope: [web, files, code]
evidence_required: true

问：删除项目内所有.bak
intent: task
task_type: action
goal: 删除.bak文件
tool_hints: [File System]
memory_keywords: [.bak]
capability_scope: [files, code]
destructive: true
scope: broad
"""


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

# Direct short-answer requests are still knowledge/chat style questions.  They
# should not enter the full ReAct loop just because they contain words like
# "回答" or "介绍".
_DIRECT_SHORT_ANSWER_RE = re.compile(
    r"^(?:请)?(?:只)?(?:用)?一[句段]话(?:回答|说明|解释|介绍)?[，,:：\s]*"
    r"(?:你(?:的)?(?:职责|角色)(?:是什么)?|你是谁|介绍(?:一下)?你自己|"
    r"解释\s*\S{1,30}|说明\s*\S{1,30}|介绍\s*\S{1,30})$"
    r"|^(?:你(?:的)?(?:职责|角色)(?:是什么)?|你是谁|介绍(?:一下)?你自己)$"
    r"|^(?:请)?(?:简洁|简单|直接)(?:回答|说明|解释|介绍)[，,:：\s]*"
    r"(?:你(?:的)?(?:职责|角色)(?:是什么)?|你是谁|介绍(?:一下)?你自己|"
    r"\S{1,30}(?:是什么|怎么理解))$",
    re.IGNORECASE,
)

_SHORT_EXPLANATION_RE = re.compile(
    r"^(?:请)?(?:简单|简洁|直接)?(?:解释|说明|介绍)(?:一下)?[，,:：\s]+.{1,40}$",
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

_ACTION_VERB_RE = re.compile(
    r"(?:帮我|请你|开始|继续|执行|处理|排查|查看|看看|检查|分析|修复|安装|"
    r"下载|打开|运行|创建|生成|写入|修改|改成|删除|清理|搜索)"
)

_TOOL_TARGET_RE = re.compile(
    r"(?:日志|报错|警告|错误|文件|目录|项目|代码|仓库|网页|浏览器|GitHub|issue|"
    r"skill|技能|配置|环境|数据库|截图|任务|命令|脚本|记录|待办|记忆|进度)"
)

_STRONG_EVIDENCE_RE = re.compile(
    r"(?:https?://|github\.com|GitHub|issue\s*#?\d+|日志|log|报错|警告|错误|"
    r"当前代码|代码中|本仓库|这个仓库|技能市场|SkillHub|Skill Store|skill\s*store|"
    r"技能仓库|诊断包|反馈包|下载日志|API\s*状态|接口状态)",
    re.IGNORECASE,
)

_EVIDENCE_ACTION_RE = re.compile(r"(?:分析|排查|检查|验证|复现|下载|查看|看看|定位)")

_EXECUTION_FOLLOWUP_RE = re.compile(
    r"^(?:立即|马上|现在|直接|开始|继续|接着)?\s*"
    r"(?:执行|处理|推进|继续执行|接着做)"
    r"(?:\s*(?:任务|这个|它|上面|刚才的|之前的|不要停|别停|下去|吧|。|！|!))?$"
)

_RECORD_CONTENT_RE = re.compile(
    r"^(?:(?:\d{4}-\d{1,2}-\d{1,2})|(?:\d{1,2}|[一二三四五六七八九十]+)月"
    r"(?:\d{1,2}|[一二三四五六七八九十]+)日)?"
    r".{0,12}(?:工作|日常|记录|进度|待办)[:：]",
    re.IGNORECASE,
)

_WRITE_CONFIRMATION_RE = re.compile(
    r"(?:写入|保存|记录|读取|验证|文件|内容).{0,12}"
    r"(?:成功|了吗|没有|没看到|看不到|不同|不一致|确认|确定)"
    r"|(?:还是)?没有写入成功|(?:系统中)?没看到(?:该)?文件|和你显示不同",
    re.IGNORECASE,
)

_DESKTOP_SCREENSHOT_RE = re.compile(
    r"(?:桌面|屏幕|电脑|窗口|当前(?:画面|界面)).{0,8}(?:截图|截屏|屏幕截图)"
    r"|(?:截图|截屏|屏幕截图).{0,8}(?:发我|发给我|发送|传给我|给我|桌面|屏幕|电脑|窗口)",
    re.IGNORECASE,
)


def _requires_external_evidence(message: str) -> bool:
    """Whether the answer should be backed by current external/project evidence.

    This guard intentionally does not add timeouts or hard loop limits. It only
    prevents evidence-sensitive questions from being accepted as pure memory
    answers.
    """
    stripped = message.strip()
    if not stripped:
        return False
    if _RECORD_CONTENT_RE.search(stripped) or _WRITE_CONFIRMATION_RE.search(stripped):
        return True
    if _STRONG_EVIDENCE_RE.search(stripped):
        return True
    if _EXECUTION_FOLLOWUP_RE.search(stripped):
        return True
    return bool(_EVIDENCE_ACTION_RE.search(stripped) and _TOOL_TARGET_RE.search(stripped))


def _looks_like_tool_action_request(message: str) -> bool:
    """Return True for requests that clearly require operating on external state."""
    stripped = message.strip()
    if not stripped:
        return False

    if _RECORD_CONTENT_RE.search(stripped) or _WRITE_CONFIRMATION_RE.search(stripped):
        return True

    # Continuation commands are only treated as tool actions when they explicitly
    # refer to an execution/task, avoiding ordinary acknowledgements like "继续说".
    if _EXECUTION_FOLLOWUP_RE.search(stripped):
        return True
    if re.search(r"(?:继续|执行).{0,8}(?:任务|处理|排查|操作|执行|不要停|别停)", stripped):
        return True

    return bool(_ACTION_VERB_RE.search(stripped) and _TOOL_TARGET_RE.search(stripped))


def _infer_tool_action_hints(message: str) -> tuple[list[str], bool]:
    hints: list[str] = []
    needs_project_context = False

    def add_hint(name: str) -> None:
        if name not in hints:
            hints.append(name)

    if re.search(r"(?:浏览器|网页)", message):
        add_hint("Browser")
    if _DESKTOP_SCREENSHOT_RE.search(message):
        add_hint("Desktop")
    if re.search(r"(?:GitHub|issue|网页|搜索|下载|仓库)", message, flags=re.IGNORECASE):
        add_hint("Web Search")
    if re.search(
        r"(?:日志|报错|警告|错误|文件|目录|项目|代码|skill|技能|配置|数据库|命令|脚本)", message
    ):
        add_hint("File System")
        needs_project_context = True

    if not hints:
        add_hint("File System")

    return hints, needs_project_context


def _make_tool_action_result(message: str, *, follow_up: bool = False) -> IntentResult:
    intent = IntentType.FOLLOW_UP if follow_up else IntentType.TASK
    tool_hints, requires_project_context = _infer_tool_action_hints(message)
    return IntentResult(
        intent=intent,
        confidence=0.95,
        task_definition=message[:600],
        task_type="action",
        tool_hints=tool_hints,
        memory_keywords=[],
        force_tool=True,
        todo_required=False,
        raw_output="[action-tool-guard]",
        fast_reply=False,
        prompt_depth=PromptDepth.STANDARD,
        memory_scope=MemoryScope.RELEVANT,
        requires_tools=True,
        evidence_required=True,
        evidence_recommended=True,
        requires_project_context=requires_project_context,
        risk_level_hint=RiskLevelHint.NONE,
    )


def _try_fast_query_shortcut(message: str) -> IntentResult | None:
    """Rule-based shortcut for obvious query messages (math, date, definitions).
    Returns QUERY intent immediately without LLM call."""
    stripped = message.strip().rstrip("？?。.!！")
    if len(stripped) > 50:
        return None
    if _CONTEXT_DEPENDENT_RE.search(stripped):
        return None
    if _looks_like_tool_action_request(stripped):
        # Action requests need the structured analyzer because even short text
        # can span multiple capability domains (for example web research plus
        # writing a file). The rule is only an exclusion guard for the QUERY
        # shortcut; it must not synthesize a reduced intent on the LLM's behalf.
        return None
    if (
        _QUERY_PATTERNS.match(stripped)
        or _DIRECT_SHORT_ANSWER_RE.match(stripped)
        or _SHORT_EXPLANATION_RE.match(stripped)
    ):
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
            prompt_depth=PromptDepth.FAST,
            memory_scope=MemoryScope.PINNED_ONLY,
            requires_tools=False,
            evidence_required=False,
            risk_level_hint=RiskLevelHint.NONE,
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
            prompt_depth=PromptDepth.FAST,
            memory_scope=MemoryScope.PINNED_ONLY,
            requires_tools=False,
            evidence_required=False,
            risk_level_hint=RiskLevelHint.NONE,
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
            prompt_depth=PromptDepth.FAST,
            memory_scope=MemoryScope.PINNED_ONLY,
            requires_tools=False,
            evidence_required=False,
            risk_level_hint=RiskLevelHint.NONE,
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

        # Rule-based fast-path for greetings and other unambiguous casual chat.
        # This avoids sending a full prompt/tool context to small local models for
        # messages like "你好", while still letting ambiguous follow-ups with
        # history go through the normal analyzer.
        chat_result = _try_fast_chat_shortcut(message, has_history=has_history)
        if chat_result is not None:
            return chat_result

        try:
            response = await self.brain.compiler_think(
                prompt=message,
                system=INTENT_ANALYZER_SYSTEM,
                max_tokens=INTENT_ANALYZER_MAX_TOKENS,
            )

            raw_output = _strip_thinking_tags(response.content).strip() if response.content else ""
            if not raw_output:
                logger.warning("[IntentAnalyzer] Empty LLM response, using default")
                return _make_default(message)

            logger.info(f"[IntentAnalyzer] Raw output: {raw_output[:200]}")
            result = _parse_intent_output(raw_output, message)
            result.compiler_source = getattr(response, "compiler_source", "")
            result.compiler_fallback_reason = getattr(response, "compiler_fallback_reason", "")
            result.compiler_fallback_detail = getattr(response, "compiler_fallback_detail", "")
            return result

        except Exception as e:
            logger.warning(f"[IntentAnalyzer] LLM analysis failed: {e}, using default")
            return _make_default(message)


def _make_default(message: str) -> IntentResult:
    """LLM 不可用 / 输出为空时的安全兜底。

    安全约束：当意图分析器拿不到结果，我们必须**保证用户的需求仍然能被
    工具能力服务到**，否则会出现"明明用户在让 OpenAkita 干活，却被识别成
    chitchat 然后没有任何工具被挂到上下文里"的退步。所以这里：

    * 明显的知识问答仍走轻量 ``QUERY``，避免简单解释进入 ReAct 工具循环；
    * 其余情况默认 ``TASK``；
    * confidence 设为 ``0.0`` 让上层知道这不是来自 LLM 的高置信结果；
    * 强制 ``force_tool=True`` + ``requires_tools=True`` —— 兜底必须能
      调用工具，否则就退化成纯文本助手；
    * 工具 hint 使用启发式 ``_infer_tool_action_hints``，给 reasoning_engine
      一个最小可用的工具集；
    * todo_required 仍然 False（LLM 没说要拆 todo，就别强行拆）。
    """
    fast_query = _try_fast_query_shortcut(message)
    if fast_query is not None:
        fast_query.confidence = 0.0
        fast_query.prompt_depth = PromptDepth.MINIMAL
        fast_query.fast_reply = False
        fast_query.task_definition = message[:600]
        fast_query.raw_output = ""
        return fast_query

    # P0-2 阶段 2：规则启发式降级为 evidence_recommended，不再硬等于 evidence_required
    rule_evidence = _requires_external_evidence(message)
    tool_hints, requires_project_context = _infer_tool_action_hints(message)
    return IntentResult(
        intent=IntentType.TASK,
        confidence=0.0,
        task_definition=message[:600],
        task_type="action",
        tool_hints=tool_hints,
        memory_keywords=[],
        force_tool=True,
        todo_required=False,
        raw_output="",
        prompt_depth=PromptDepth.MINIMAL,
        memory_scope=MemoryScope.PINNED_ONLY,
        capability_scope=[],
        catalog_scope=[],
        requires_tools=True,
        evidence_required=False,
        evidence_recommended=rule_evidence,
        requires_project_context=requires_project_context,
        risk_level_hint=RiskLevelHint.NONE,
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
            "capability_scope",
            "prompt_depth",
            "memory_scope",
            "catalog_scope",
            "requires_tools",
            "evidence_required",
            "requires_project_context",
            "risk_level_hint",
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
    capability_scope = _parse_enum_list(
        extracted.get("capability_scope", ""),
        CapabilityScope,
        aliases={
            "file system": CapabilityScope.FILES,
            "files": CapabilityScope.FILES,
            "web search": CapabilityScope.WEB,
            "plugin": CapabilityScope.PLUGIN,
            "plugins": CapabilityScope.PLUGIN,
            "skills": CapabilityScope.SKILL,
            "skill": CapabilityScope.SKILL,
            "organization": CapabilityScope.ORG,
            "config": CapabilityScope.CODE,
        },
    )
    prompt_depth = _parse_enum(
        extracted.get("prompt_depth", ""),
        PromptDepth,
        PromptDepth.MINIMAL
        if intent in (IntentType.CHAT, IntentType.QUERY)
        else PromptDepth.STANDARD,
    )
    memory_scope = _parse_enum(
        extracted.get("memory_scope", ""),
        MemoryScope,
        MemoryScope.PINNED_ONLY
        if intent in (IntentType.CHAT, IntentType.QUERY)
        else MemoryScope.RELEVANT,
    )
    catalog_scope = [
        item.lower().strip() for item in _parse_list(extracted.get("catalog_scope", ""))
    ]
    requires_tools = _parse_bool(
        extracted.get("requires_tools", ""),
        default=intent == IntentType.TASK and bool(tool_hints or capability_scope),
    )
    llm_evidence_required = _parse_bool(
        extracted.get("evidence_required", ""),
        default=False,
    )
    # P0-2 阶段 2（修正版）：拆开 LLM 判断和规则启发
    # - evidence_required = LLM 自评（"必须查工具"），驱动 ForceToolCall/重试逻辑
    # - evidence_recommended = LLM 自评 OR 规则启发（"建议查"），驱动 prompt 软提示
    # 这样规则误判（如把"算我33岁离60岁还几年"识别为外部证据）只影响 prompt 文案，
    # 不再触发 ForceToolCall 重试 + text_replace + OrgRuntime 误判 task_failed。
    evidence_required = llm_evidence_required
    rule_evidence = _requires_external_evidence(message)
    evidence_recommended = llm_evidence_required or rule_evidence
    if evidence_required:
        requires_tools = True
        inferred_hints, inferred_project_context = _infer_tool_action_hints(message)
        for hint in inferred_hints:
            if hint not in tool_hints:
                tool_hints.append(hint)
    elif evidence_recommended:
        # 软建议：补充 tool_hints 给 LLM 参考，但不强制 requires_tools
        inferred_hints, inferred_project_context = _infer_tool_action_hints(message)
        for hint in inferred_hints:
            if hint not in tool_hints:
                tool_hints.append(hint)
    else:
        inferred_project_context = False
    requires_project_context = _parse_bool(
        extracted.get("requires_project_context", ""),
        default=(
            inferred_project_context
            or CapabilityScope.CODE in capability_scope
            or "project" in catalog_scope
        ),
    )
    risk_level_hint = _parse_enum(
        extracted.get("risk_level_hint", ""),
        RiskLevelHint,
        RiskLevelHint.HIGH
        if extracted.get("destructive", "").strip().lower() == "true"
        else RiskLevelHint.NONE,
    )

    force_tool = intent in (IntentType.TASK,) and requires_tools
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
        capability_scope=capability_scope,
        prompt_depth=prompt_depth,
        memory_scope=memory_scope,
        catalog_scope=catalog_scope,
        requires_tools=requires_tools,
        evidence_required=evidence_required,
        evidence_recommended=evidence_recommended,
        requires_project_context=requires_project_context,
        risk_level_hint=risk_level_hint,
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

    if result.intent in (IntentType.CHAT, IntentType.QUERY) and _looks_like_tool_action_request(
        message
    ):
        logger.info(
            "[IntentAnalyzer] Coerced %s to task because message requires external action: %r",
            result.intent.value,
            message[:120],
        )
        return _make_tool_action_result(message)

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


def _parse_bool(value: str, default: bool = False) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "yes", "1", "是", "需要"}:
        return True
    if normalized in {"false", "no", "0", "否", "不需要"}:
        return False
    return default


def _parse_enum(value: str, enum_cls: type[Enum], default: Enum) -> Enum:
    normalized = str(value or "").strip().lower().strip("'\"")
    if not normalized:
        return default
    for item in enum_cls:
        if normalized in {item.value.lower(), item.name.lower(), str(item).lower()}:
            return item
    return default


def _parse_enum_list(
    value: str,
    enum_cls: type[Enum],
    aliases: dict[str, Enum] | None = None,
) -> list[Enum]:
    aliases = aliases or {}
    result: list[Enum] = []
    for raw in _parse_list(value):
        normalized = raw.strip().lower().strip("'\"")
        item = aliases.get(normalized)
        if item is None:
            item = _parse_enum(normalized, enum_cls, None)  # type: ignore[arg-type]
        if item is not None and item not in result:
            result.append(item)
    return result
