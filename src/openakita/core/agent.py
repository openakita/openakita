"""
Agent main class - coordinates all modules

This is the core of OpenAkita, responsible for:
- Receiving user input
- Coordinating all modules
- Executing tool calls
- Running the Ralph loop
- Managing conversation and memory
- Self-evolution (skill search, install, generate)

The Skills system follows the Agent Skills spec (agentskills.io)
The MCP system follows the Model Context Protocol spec (modelcontextprotocol.io)
"""

import asyncio
import base64
import contextlib
import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..sessions import Session

from ..config import settings

# Memory system
from ..memory import MemoryManager

# Prompt compilation pipeline (v2)
# Skill system (SKILL.md spec)
from ..skills import SkillCatalog, SkillLoader, SkillRegistry

# System tool catalog (progressive disclosure)
from ..tools.catalog import ToolCatalog

# System tool definitions (imported from tools/definitions)
from ..tools.definitions import BASE_TOOLS
from ..tools.file import FileTool

# Handler Registry (modular tool execution)
from ..tools.handlers import SystemHandlerRegistry
from ..tools.handlers.agent import create_handler as create_agent_tool_handler
from ..tools.handlers.agent_hub import create_handler as create_agent_hub_handler
from ..tools.handlers.agent_package import create_handler as create_agent_package_handler
from ..tools.handlers.browser import create_handler as create_browser_handler
from ..tools.handlers.cli_anything import create_handler as create_cli_anything_handler
from ..tools.handlers.cli_anything import is_available as cli_anything_available
from ..tools.handlers.code_quality import create_handler as create_code_quality_handler
from ..tools.handlers.config import create_handler as create_config_handler
from ..tools.handlers.desktop import create_handler as create_desktop_handler
from ..tools.handlers.filesystem import create_handler as create_filesystem_handler
from ..tools.handlers.im_channel import create_handler as create_im_channel_handler
from ..tools.handlers.lsp import create_handler as create_lsp_handler
from ..tools.handlers.mcp import create_handler as create_mcp_handler
from ..tools.handlers.memory import create_handler as create_memory_handler
from ..tools.handlers.mode import create_handler as create_mode_handler
from ..tools.handlers.notebook import create_handler as create_notebook_handler
from ..tools.handlers.opencli import create_handler as create_opencli_handler
from ..tools.handlers.opencli import is_available as opencli_available
from ..tools.handlers.persona import create_handler as create_persona_handler
from ..tools.handlers.plan import create_todo_handler
from ..tools.handlers.plugins import create_handler as create_plugins_handler
from ..tools.handlers.powershell import create_handler as create_powershell_handler
from ..tools.handlers.profile import create_handler as create_profile_handler
from ..tools.handlers.scheduled import create_handler as create_scheduled_handler
from ..tools.handlers.search import create_handler as create_search_handler
from ..tools.handlers.skill_store import create_handler as create_skill_store_handler
from ..tools.handlers.skills import create_handler as create_skills_handler
from ..tools.handlers.sleep import create_handler as create_sleep_handler
from ..tools.handlers.sticker import create_handler as create_sticker_handler
from ..tools.handlers.structured_output import create_handler as create_structured_output_handler
from ..tools.handlers.system import create_handler as create_system_handler
from ..tools.handlers.tool_search import create_handler as create_tool_search_handler
from ..tools.handlers.web_fetch import create_handler as create_web_fetch_handler
from ..tools.handlers.web_search import create_handler as create_web_search_handler
from ..tools.handlers.worktree import create_handler as create_worktree_handler

# MCP system
from ..tools.mcp import mcp_client
from ..tools.mcp_catalog import mcp_catalog as _shared_mcp_catalog
from ..tools.shell import ShellTool
from ..tools.web import WebTool
from .agent_state import AgentState
from .brain import Brain, Context
from .context_manager import ContextManager
from .context_utils import get_max_context_tokens as _shared_get_max_context_tokens
from .context_utils import get_raw_context_window as _shared_get_raw_context_window
from .errors import UserCancelledError
from .identity import Identity
from .prompt_assembler import PromptAssembler
from .ralph import RalphLoop, Task, TaskResult
from .reasoning_engine import ReasoningEngine
from .response_handler import (
    ResponseHandler,
    clean_llm_response,
    parse_intent_tag,
    strip_thinking_tags,
)
from .skill_manager import SkillManager
from .task_monitor import RETROSPECT_PROMPT, TaskMonitor
from .token_tracking import (
    TokenTrackingContext,
    init_token_tracking,
    reset_tracking_context,
    set_tracking_context,
)
from .tool_executor import ToolExecutor
from .user_profile import get_profile_manager

_DESKTOP_AVAILABLE: bool | None = None  # None = not yet checked
_desktop_tool_handler = None


def _ensure_desktop():
    """Lazy-load the desktop-automation module.

    pyautogui can init extremely slowly or even hang on some Windows environments;
    set OPENAKITA_SKIP_DESKTOP=1 to skip it entirely.
    """
    global _DESKTOP_AVAILABLE, _desktop_tool_handler
    if _DESKTOP_AVAILABLE is not None:
        return _DESKTOP_AVAILABLE
    if sys.platform != "win32" or os.environ.get("OPENAKITA_SKIP_DESKTOP", ""):
        _DESKTOP_AVAILABLE = False
        return False
    try:
        from ..tools.desktop import DESKTOP_TOOLS, DesktopToolHandler  # noqa: F401, F811

        _desktop_tool_handler = DesktopToolHandler()
        _DESKTOP_AVAILABLE = True
    except ImportError:
        _DESKTOP_AVAILABLE = False
    return _DESKTOP_AVAILABLE


logger = logging.getLogger(__name__)

# Context-management constants (some moved to context_manager.py; compression-related ones still defined here)
from .context_manager import CHARS_PER_TOKEN, CHUNK_MAX_TOKENS

COMPRESSION_RATIO = 0.15
LARGE_TOOL_RESULT_THRESHOLD = 5000
MIN_RECENT_TURNS = 4

# Core tool whitelist for small-context-window models (only the most basic execution capability)
SMALL_CTX_CORE_TOOLS = {
    "run_shell",
    "read_file",
    "write_file",
    "edit_file",
    "list_directory",
    "grep",
    "ask_user",
    "get_tool_info",
}
# Additional tools included for medium-context-window models
MEDIUM_CTX_EXTRA_TOOLS = {
    "add_memory",
    "search_memory",
    "get_memory_stats",
    "list_skills",
    "get_skill_info",
    "run_skill_script",
    "web_search",
    "browser_navigate",
    "call_mcp_tool",
    "list_mcp_servers",
    "enable_thinking",
    "glob",
    "delete_file",
}

# Prompt Compiler system prompt (first stage of two-stage prompting)
PROMPT_COMPILER_SYSTEM = """[Role]
You are the Prompt Compiler, not a solver model.

[Input]
The user's raw request.

[Goal]
Turn the request into a structured, explicit, executable task definition.

[Output structure]
Output in the following YAML format:

```yaml
task_type: [task type: question/action/creation/analysis/reminder/other]
goal: [one-sentence task goal]
inputs:
  given: [list of given information]
  missing: [list of missing but potentially needed information, empty if none]
constraints: [list of constraints, empty if none]
output_requirements: [list of output requirements]
risks_or_ambiguities: [list of risks or ambiguities, empty if none]
```

[Rules]
- Do not solve the task
- Do not give advice
- Do not output the final answer
- Do not fabricate capabilities: do not invent facts like 'tools execute on the user's local machine'; however, if the task involves **effects only observable on the user's local machine** (local GUI, local installation, in-game overlays, etc.), you MUST record deployment boundaries like 'executes only on the OpenAkita host by default, possibly a different domain from the user's chat device' truthfully in `constraints` or `risks_or_ambiguities` -- this is a factual constraint, different from 'assumed capability limits'
- Output only the structured task definition in YAML
- Keep it concise, one sentence per item at most

[Example]
User: "Write a Python script that reads a CSV file and computes the average of each column"

Output:
```yaml
task_type: creation
goal: Create a Python script that reads a CSV file and computes the average of each column
inputs:
  given:
    - The file format to process is CSV
    - The statistic to compute is the average
    - Use the Python language
  missing:
    - Path to the CSV file or an example
    - Whether non-numeric columns should be handled
output_requirements:
  - An executable Python script
  - Capable of reading a CSV file
  - Outputs the average of each column
constraints: []
risks_or_ambiguities:
  - How to handle columns with non-numeric data is unspecified
  - Output format is unspecified (print to console or save to file)
```"""


class Agent:
    """
    OpenAkita main class

    An all-in-one, self-evolving AI assistant that never gives up, based on the Ralph Wiggum pattern.
    """

    # Base tool definitions (Claude API tool-use format)
    # BASE_TOOLS has been moved to the tools/definitions/ directory
    # Import via `from ..tools.definitions import BASE_TOOLS`

    # Note: historically this used class variables to store IM context, with a risk of concurrent cross-talk.
    # We now use contextvars from `openakita.core.im_context` (coroutine-isolated).
    _current_im_session = None  # legacy: kept to avoid crashing external references (no longer used)
    _current_im_gateway = None  # legacy: kept to avoid crashing external references (no longer used)

    # Stop-task command list (when the user sends one of these, the current task stops immediately)
    STOP_COMMANDS = {
        "停止",
        "停",
        "stop",
        "停止执行",
        "取消",
        "取消任务",
        "算了",
        "不用了",
        "别做了",
        "停下",
        "暂停",
        "cancel",
        "abort",
        "quit",
        "停止当前任务",
        "中止",
        "终止",
        "不要了",
        "/stop",
        "/停止",
        "/取消",
        "/cancel",
        "/abort",
        "kill",
        "kill all",
    }

    SKIP_COMMANDS = {
        "跳过",
        "skip",
        "下一步",
        "next",
        "跳过这步",
        "跳过当前",
        "skip this",
        "换个方法",
        "太慢了",
        "/skip",
        "/跳过",
    }

    # ---- Task-local properties ----
    # These are backed by per-instance dicts keyed by asyncio.current_task() id,
    # so concurrent chat_with_session calls on the same Agent instance don't
    # overwrite each other's session context.
    #
    # A ContextVar propagates the parent task's key to child tasks created via
    # asyncio.create_task() (e.g. tool execution in reason_stream's
    # cancel/skip racing).  Without this, child tasks get a new task id and
    # cannot find the session stored by the parent.
    _inherited_task_key: contextvars.ContextVar[int] = contextvars.ContextVar(
        "_inherited_task_key",
        default=0,
    )

    @staticmethod
    def _task_key() -> int:
        inherited = Agent._inherited_task_key.get(0)
        if inherited:
            return inherited
        task = asyncio.current_task()
        return id(task) if task else 0

    @property
    def _current_session(self):
        return self.__dict__.get("_tls_session", {}).get(self._task_key())

    @_current_session.setter
    def _current_session(self, value):
        tls = self.__dict__.setdefault("_tls_session", {})
        key = self._task_key()
        if value is None:
            tls.pop(key, None)
        else:
            tls[key] = value
            Agent._inherited_task_key.set(key)

    @property
    def _current_session_id(self):
        return self.__dict__.get("_tls_session_id", {}).get(self._task_key())

    @_current_session_id.setter
    def _current_session_id(self, value):
        tls = self.__dict__.setdefault("_tls_session_id", {})
        key = self._task_key()
        if value is None:
            tls.pop(key, None)
        else:
            tls[key] = value

    @property
    def _current_conversation_id(self):
        return self.__dict__.get("_tls_conversation_id", {}).get(self._task_key())

    @_current_conversation_id.setter
    def _current_conversation_id(self, value):
        tls = self.__dict__.setdefault("_tls_conversation_id", {})
        key = self._task_key()
        if value is None:
            tls.pop(key, None)
        else:
            tls[key] = value

    def __init__(
        self,
        name: str | None = None,
        api_key: str | None = None,
        brain: Brain | None = None,
    ):
        self.name = name or settings.agent_name

        # Initialize core components
        self.identity = Identity()
        self.brain = brain or Brain(api_key=api_key)
        self.ralph = RalphLoop(
            max_iterations=settings.max_iterations,
            on_iteration=self._on_iteration,
            on_error=self._on_error,
        )

        # Initialize base tools
        self.shell_tool = ShellTool()
        self.file_tool = FileTool()
        self.web_tool = WebTool()

        # Initialize the skill system (SKILL.md spec)
        self.skill_registry = SkillRegistry()
        self.skill_loader = SkillLoader(self.skill_registry)

        # F6/F9: usage tracker + watcher (created early, wired after load)
        from ..skills.usage import SkillUsageTracker

        self._skill_usage_tracker = SkillUsageTracker(
            settings.project_root / "data" / "skill_usage.json"
        )
        self.skill_catalog = SkillCatalog(
            self.skill_registry,
            usage_tracker=self._skill_usage_tracker,
        )

        # F8: conditional activation manager
        from ..skills.activation import SkillActivationManager

        self._skill_activation = SkillActivationManager()

        # F9: skill file watcher (started after skills are loaded)
        self._skill_watcher = None

        # Lazy-import the self-evolution system (to avoid circular imports)
        from ..evolution.generator import SkillGenerator

        self.skill_generator = SkillGenerator(
            brain=self.brain,
            skills_dir=settings.skills_path,
            skill_registry=self.skill_registry,
        )

        # MCP system (globally shared: mcp_client and mcp_catalog are module-level singletons,
        # so all Agent instances including pool agents share a single server config and connection state)
        self.mcp_client = mcp_client
        self.mcp_catalog = _shared_mcp_catalog
        self.browser_manager = None  # Started in _start_builtin_mcp_servers
        self.pw_tools = None
        self._builtin_mcp_count = 0

        # Restore runtime state (must run before the tool catalog is built, otherwise multi_agent_enabled, etc., may still hold old values)
        from ..config import runtime_state

        runtime_state.load()

        # System tool catalog (progressive disclosure)
        _all_tools = list(BASE_TOOLS)
        if _ensure_desktop():
            from ..tools.desktop import DESKTOP_TOOLS as _DT

            _all_tools.extend(_DT)
        from ..tools.definitions.agent import AGENT_TOOLS
        from ..tools.definitions.org_setup import ORG_SETUP_TOOLS

        _all_tools.extend(AGENT_TOOLS)
        _all_tools.extend(ORG_SETUP_TOOLS)
        if opencli_available():
            from ..tools.definitions.opencli import OPENCLI_TOOLS as _OC

            _all_tools.extend(_OC)
        if cli_anything_available():
            from ..tools.definitions.cli_anything import CLI_ANYTHING_TOOLS as _CA

            _all_tools.extend(_CA)
        self.tool_catalog = ToolCatalog(_all_tools)

        # Scheduled-task scheduler
        self.task_scheduler = None  # Started in initialize()

        # Memory system
        self.memory_manager = MemoryManager(
            data_dir=settings.project_root / "data" / "memory",
            memory_md_path=settings.memory_path,
            brain=self.brain,
            embedding_model=settings.embedding_model,
            embedding_device=settings.embedding_device,
            model_download_source=settings.model_download_source,
            search_backend=settings.search_backend,
            embedding_api_provider=settings.embedding_api_provider,
            embedding_api_key=settings.embedding_api_key,
            embedding_api_model=settings.embedding_api_model,
            agent_id=self.name,
        )

        # User-profile manager
        self.profile_manager = get_profile_manager()

        # ==================== Persona system + liveness + stickers ====================
        from ..tools.sticker import StickerEngine
        from .persona import PersonaManager
        from .proactive import ProactiveConfig, ProactiveEngine
        from .trait_miner import TraitMiner

        # Persona manager
        self.persona_manager = PersonaManager(
            personas_dir=settings.personas_path,
            active_preset=settings.persona_name,
        )

        # Preference-mining engine (pass brain; the LLM analyzes preferences instead of using keyword matching)
        self.trait_miner = TraitMiner(persona_manager=self.persona_manager, brain=self.brain)

        # Liveness engine
        proactive_config = ProactiveConfig(
            enabled=settings.proactive_enabled,
            max_daily_messages=settings.proactive_max_daily_messages,
            min_interval_minutes=settings.proactive_min_interval_minutes,
            quiet_hours_start=settings.proactive_quiet_hours_start,
            quiet_hours_end=settings.proactive_quiet_hours_end,
            idle_threshold_hours=settings.proactive_idle_threshold_hours,
        )
        self.proactive_engine = ProactiveEngine(
            config=proactive_config,
            feedback_file=settings.project_root / "data" / "proactive_feedback.json",
            persona_manager=self.persona_manager,
            memory_manager=self.memory_manager,
        )

        # Sticker engine
        self.sticker_engine = (
            StickerEngine(
                data_dir=settings.sticker_data_path,
                mirrors=settings.sticker_mirrors or None,
            )
            if settings.sticker_enabled
            else None
        )

        # Dynamic tool list (base tools + skill tools)
        self._tools = list(BASE_TOOLS)
        self._skill_tool_names: set[str] = set()

        # Add desktop tools on Windows (lazy load to avoid slow pyautogui init)
        if _ensure_desktop():
            from ..tools.desktop import DESKTOP_TOOLS as _DT2

            self._tools.extend(_DT2)
            logger.info(f"Desktop automation tools enabled ({len(_DT2)} tools)")

        # OpenCLI tools (only when opencli is installed)
        if opencli_available():
            from ..tools.definitions.opencli import OPENCLI_TOOLS

            self._tools.extend(OPENCLI_TOOLS)
            logger.info(f"OpenCLI tools enabled ({len(OPENCLI_TOOLS)} tools)")

        # CLI-Anything tools (only when cli-anything-* are installed)
        if cli_anything_available():
            from ..tools.definitions.cli_anything import CLI_ANYTHING_TOOLS

            self._tools.extend(CLI_ANYTHING_TOOLS)
            logger.info(f"CLI-Anything tools enabled ({len(CLI_ANYTHING_TOOLS)} tools)")

        from ..tools.definitions.agent import AGENT_TOOLS
        from ..tools.definitions.org_setup import ORG_SETUP_TOOLS

        self._tools.extend(AGENT_TOOLS)
        self._tools.extend(ORG_SETUP_TOOLS)
        logger.info(
            f"Multi-agent tools enabled ({len(AGENT_TOOLS) + len(ORG_SETUP_TOOLS)} tools)"
        )

        # Platform hub tools (Agent Hub + Skill Store, only when enabled)
        if settings.hub_enabled:
            from ..tools.definitions import HUB_TOOLS

            self._tools.extend(HUB_TOOLS)
            logger.info(f"Platform hub tools enabled ({len(HUB_TOOLS)} tools)")

        self._update_shell_tool_description()

        # Conversation context
        self._context = Context()
        self._conversation_history: list[dict] = []

        # Message-interrupt mechanism
        self._current_session = None  # Reference to the current session
        self._interrupt_enabled = True  # Whether interrupt checking is enabled

        # Task-cancellation mechanism -- uniformly use TaskState.cancelled / agent_state.is_task_cancelled
        # (the old self._task_cancelled is deprecated; cancellation state is bound to the TaskState instance to avoid global races)

        # Discovered tools — populated by tool_search handler; tools in this set
        # are promoted from deferred to full-schema in _effective_tools.
        self._discovered_tools: set[str] = set()

        # Sub-agent call flag: set by orchestrator._call_agent()
        self._is_sub_agent_call = False
        # Agent tool names to exclude when running as sub-agent
        self._agent_tool_names = frozenset(
            {"delegate_to_agent", "delegate_parallel", "create_agent", "spawn_agent"}
        )

        # Current task monitor (only set during IM task execution; used by system tools to dynamically adjust timeout policy)
        self._current_task_monitor = None

        # State
        self._initialized = False
        self._running = False

        self._last_finalized_trace: list[dict] = []

        # Agent profile and custom prompt (set by AgentFactory)
        self._agent_profile = None
        self._custom_prompt_suffix: str = ""
        self._preferred_endpoint: str | None = None

        # Plan mode exit pending — keyed by conversation_id
        # Set by exit_plan_mode tool, consumed by chat_with_session_stream
        self._plan_exit_pending: dict[str, dict] = {}

        # Handler Registry (modular tool execution)
        self.handler_registry = SystemHandlerRegistry()
        self._init_handlers()
        self._core_tool_names: set[str] = set(self.handler_registry.list_tools())

        # === Tool-parallel execution infrastructure (parallelism is off by default, tool_max_parallel=1)===
        # Parallel execution only affects the tool-batching phase when the model returns multiple tool_use/tool_calls in a single turn.
        # Note: stateful tools like browser/desktop/mcp are mutually exclusive by default to prevent concurrent state clobbering.
        self._tool_semaphore = asyncio.Semaphore(max(1, settings.tool_max_parallel))
        self._tool_handler_locks: dict[str, asyncio.Lock] = {}
        for hn in ("browser", "desktop", "mcp"):
            self._tool_handler_locks[hn] = asyncio.Lock()
        self._task_monitor_lock = asyncio.Lock()

        # ==================== Phase 2: new submodules ====================
        # Structured state management
        self.agent_state = AgentState()
        self._pending_cancels: dict[str, str] = {}  # session_id → reason

        # Tool-execution engine (delegates from _execute_tool / _execute_tool_calls_batch)
        self.tool_executor = ToolExecutor(
            handler_registry=self.handler_registry,
            max_parallel=max(1, settings.tool_max_parallel),
        )
        self.tool_executor._agent_ref = self

        # Context manager (delegates from _compress_context, etc.)
        self.context_manager = ContextManager(brain=self.brain)

        # Response handler (see ResponseHandler.verify_task_completion for task-completion checks; called by ReasoningEngine)
        self.response_handler = ResponseHandler(
            brain=self.brain,
            memory_manager=self.memory_manager,
        )

        # Skill manager (only responsible for Git/URL downloads + the initial loader.load_skill).
        # Catalog-cache refresh, Pool notification, and SkillEvent broadcast are all handled by ``propagate_skill_change``,
        # so no callback is passed here anymore, avoiding a half-configured refresh path.
        self.skill_manager = SkillManager(
            skill_registry=self.skill_registry,
            skill_loader=self.skill_loader,
            skill_catalog=self.skill_catalog,
            shell_tool=self.shell_tool,
        )

        # Plugin catalog (set in _load_plugins)
        self.plugin_catalog = None

        # Prompt assembler (delegates from _build_system_prompt, etc.)
        self.prompt_assembler = PromptAssembler(
            tool_catalog=self.tool_catalog,
            skill_catalog=self.skill_catalog,
            mcp_catalog=self.mcp_catalog,
            memory_manager=self.memory_manager,
            profile_manager=self.profile_manager,
            brain=self.brain,
            persona_manager=self.persona_manager,
        )

        # Reasoning engine (replaces _chat_with_tools_and_context)
        self.reasoning_engine = ReasoningEngine(
            brain=self.brain,
            tool_executor=self.tool_executor,
            context_manager=self.context_manager,
            response_handler=self.response_handler,
            agent_state=self.agent_state,
            memory_manager=self.memory_manager,
            plan_exit_pending=self._plan_exit_pending,
        )

        logger.info(f"Agent '{self.name}' created (with refactored sub-modules)")

    @property
    def _effective_tools(self) -> list[dict]:
        """Tools available for the current call context.

        Filtering layers (applied in order):
        0. Sanity: drop entries without a valid name
        1. Sub-agent restriction: remove delegation tools
        2. Defer marking: use defer_config.should_defer() as single source of truth
           - Intent hints can un-defer specific categories
           - IM sessions auto-include IM Channel category
           - User settings.always_load_tools / always_load_categories override defer
           - _discovered_tools (from tool_search) override defer
           - Deferred tools stay in list but marked _deferred=True (schema omitted by Brain)
        3. Context window: reduce set for small models
        """
        from ..tools.defer_config import should_defer as _should_defer

        tools = [t for t in self._tools if t.get("name")]
        dropped = len(self._tools) - len(tools)
        if dropped:
            logger.warning(
                "[Agent] _effective_tools: dropped %d tool(s) without a valid name "
                "(total=%d, valid=%d)",
                dropped,
                len(self._tools),
                len(tools),
            )
        if self._is_sub_agent_call:
            tools = [t for t in tools if t.get("name") not in self._agent_tool_names]

        cron_disabled = getattr(self, "_cron_disabled_tools", None)
        if cron_disabled:
            tools = [t for t in tools if t.get("name") not in cron_disabled]

        intent = getattr(self, "_current_intent", None)
        intent_hints = set(intent.tool_hints) if intent and intent.tool_hints else set()

        session_type = getattr(self, "_current_session_type", "cli")
        if session_type == "im":
            intent_hints.add("IM Channel")

        user_always_tools = frozenset(settings.always_load_tools)
        user_always_cats = frozenset(settings.always_load_categories)
        discovered = getattr(self, "_discovered_tools", set())

        hint_names: set[str] = set()
        if intent_hints and hasattr(self, "tool_catalog"):
            tool_groups = self.tool_catalog.get_tool_groups()
            for hint in intent_hints:
                hint_names |= tool_groups.get(hint, set())

        deferred_count = 0
        for tool in tools:
            name = tool.get("name", "")
            cat = tool.get("category", "")

            tool.pop("_deferred", None)

            if name in discovered:
                continue
            if name in user_always_tools:
                continue
            if cat and cat in user_always_cats:
                continue
            if intent_hints and hasattr(self, "tool_catalog") and name in hint_names:
                continue

            if _should_defer(name, cat) or tool.get("should_defer", False):
                tool["_deferred"] = True
                deferred_count += 1

        if hasattr(self, "tool_catalog"):
            deferred_names = {t.get("name", "") for t in tools if t.get("_deferred")}
            self.tool_catalog.set_deferred_tools(deferred_names)

        if deferred_count:
            logger.info(
                "[Agent] tiered loading: deferred %d tools "
                "(discovered=%d, user_always_tools=%d, user_always_cats=%s, "
                "intent_hints=%s)",
                deferred_count,
                len(discovered),
                len(user_always_tools),
                sorted(user_always_cats) if user_always_cats else "[]",
                sorted(intent_hints) if intent_hints else "[]",
            )

        ctx = self._get_raw_context_window()
        if 0 < ctx < 8000:
            tools = [t for t in tools if t.get("name") in SMALL_CTX_CORE_TOOLS]
        elif 0 < ctx < 32000:
            allowed_ctx = SMALL_CTX_CORE_TOOLS | MEDIUM_CTX_EXTRA_TOOLS
            tools = [t for t in tools if t.get("name") in allowed_ctx]

        return tools

    def _derive_tool_hints_from_profile(self) -> list[str]:
        """Derive tool category hints from the agent profile's skills list.

        Maps profile skill names to tool names via normalization (hyphens to
        underscores, strip source prefix), then uses infer_category() to resolve
        built-in tool categories.  Only produces hints for skills that correspond
        to built-in categories (e.g. browser-click -> Browser).  External skills
        (openakita/skills@xxx) that don't match any category are silently skipped.

        Returns empty list when no profile or no category-mapped skills — this
        causes _effective_tools to skip intent filtering, keeping all tools.
        """
        profile = getattr(self, "_agent_profile", None)
        if not profile or not profile.skills:
            return []

        from ..tools.definitions.base import infer_category

        categories: set[str] = set()
        for skill_name in profile.skills:
            short = skill_name.split("@", 1)[1] if "@" in skill_name else skill_name
            tool_name = short.replace("-", "_")
            cat = infer_category(tool_name)
            if cat:
                categories.add(cat)

        return sorted(categories)

    def _get_tool_handler_name(self, tool_name: str) -> str | None:
        """Get the handler name for a tool (used for mutex/concurrency policy)"""
        try:
            return self.handler_registry.get_handler_name_for_tool(tool_name)
        except Exception:
            return None

    async def _execute_tool_calls_batch(
        self,
        tool_calls: list[dict],
        *,
        task_monitor=None,
        allow_interrupt_checks: bool = True,
        capture_delivery_receipts: bool = False,
    ) -> tuple[list[dict], list[str], list | None]:
        """
        [DEPRECATED] Please use self.tool_executor.execute_batch() instead.

        This method bypasses PolicyEngine safety checks and is kept only as a temporary compatibility shim.
        All new code paths have been migrated to ToolExecutor.execute_batch().
        """
        import warnings

        warnings.warn(
            "_execute_tool_calls_batch is deprecated, use self.tool_executor.execute_batch()",
            DeprecationWarning,
            stacklevel=2,
        )
        executed_tool_names: list[str] = []
        delivery_receipts: list | None = None

        if not tool_calls:
            return [], executed_tool_names, delivery_receipts

        # Parallel execution lowers the granularity of inter-tool interrupt checks (no natural gaps between tools when parallel)
        # Default: if interrupt checks are enabled -> serial; parallelism can be enabled explicitly via configuration.
        allow_parallel_with_interrupts = bool(
            getattr(settings, "allow_parallel_tools_with_interrupt_checks", False)
        )
        parallel_enabled = settings.tool_max_parallel > 1 and (
            (not allow_interrupt_checks) or allow_parallel_with_interrupts
        )

        # Get cancel_event / skip_event for race-cancel/skip during tool execution
        _tool_cancel_event = (
            self.agent_state.current_task.cancel_event
            if self.agent_state and self.agent_state.current_task
            else asyncio.Event()
        )
        _tool_skip_event = (
            self.agent_state.current_task.skip_event
            if self.agent_state and self.agent_state.current_task
            else asyncio.Event()
        )

        async def _run_one(tc: dict, idx: int) -> tuple[int, dict, str | None, list | None]:
            tool_name = tc.get("name", "")
            tool_input = tc.get("input") or {}
            tool_use_id = tc.get("id", "")

            if self._task_cancelled:
                return (
                    idx,
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "[Task stopped by user]",
                        "is_error": True,
                    },
                    None,
                    None,
                )

            handler_name = self._get_tool_handler_name(tool_name)
            handler_lock = self._tool_handler_locks.get(handler_name) if handler_name else None

            t0 = time.time()
            success = True
            result_str = ""
            receipts: list | None = None

            use_parallel_safe_monitor = (
                parallel_enabled
                and task_monitor is not None
                and hasattr(task_monitor, "record_tool_call")
            )
            if (not parallel_enabled) and task_monitor:
                task_monitor.begin_tool_call(tool_name, tool_input)

            try:

                async def _do_exec():
                    async with self._tool_semaphore:
                        if handler_lock:
                            async with handler_lock:
                                return await self._execute_tool(tool_name, tool_input)
                        else:
                            return await self._execute_tool(tool_name, tool_input)

                # Race tool execution against cancel_event / skip_event (three-way)
                # Note: do not clear_skip() here; let any already-arrived skip signal be consumed naturally by the race
                tool_task = asyncio.create_task(_do_exec())
                cancel_waiter = asyncio.create_task(_tool_cancel_event.wait())
                skip_waiter = asyncio.create_task(_tool_skip_event.wait())

                done_set, pending_set = await asyncio.wait(
                    {tool_task, cancel_waiter, skip_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for t in pending_set:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                if cancel_waiter in done_set and tool_task not in done_set:
                    # cancel_event fired first; the tool is interrupted (terminates the whole task)
                    logger.info(f"[StopTask] Tool {tool_name} interrupted by user cancel")
                    success = False
                    result_str = f"[Tool {tool_name} interrupted by user]"
                    return (
                        idx,
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": result_str,
                            "is_error": True,
                        },
                        None,
                        None,
                    )

                if skip_waiter in done_set and tool_task not in done_set:
                    # skip_event fired first; only the current tool is skipped (task is not terminated)
                    _skip_reason = (
                        self.agent_state.current_task.skip_reason
                        if self.agent_state and self.agent_state.current_task
                        else "user requested skip"
                    )
                    if self.agent_state and self.agent_state.current_task:
                        self.agent_state.current_task.clear_skip()
                    logger.info(f"[SkipStep] Tool {tool_name} skipped by user: {_skip_reason}")
                    success = True
                    result_str = f"[User skipped this step: {_skip_reason}]"
                    return (
                        idx,
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": result_str,
                            "is_error": False,
                        },
                        tool_name,
                        None,
                    )

                result = tool_task.result()

                # Support multimodal tool results: handlers may return a list (text + image)
                if isinstance(result, list):
                    result_content = result
                    # Extract plain text for logging/monitoring
                    result_str = (
                        "\n".join(
                            p.get("text", "")
                            for p in result
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                        or "(multimodal content)"
                    )
                else:
                    result_str = str(result) if result is not None else "Operation completed"
                    result_content = result_str

                logger.info(f"[Tool] {tool_name} → {result_str}")

                # Aligned with tool_executor: deliver_artifacts is direct delivery,
                # org_accept_deliverable is 'relay delivery' (the parent accepts the child's file-bearing
                # artifacts; receipts.status == "relayed"). Both cases let
                # TaskVerify see real delivery evidence.
                if (
                    capture_delivery_receipts
                    and tool_name in ("deliver_artifacts", "org_accept_deliverable")
                    and result_str
                ):
                    try:
                        import json as _json

                        parsed = _json.loads(result_str)
                        rs = parsed.get("receipts") if isinstance(parsed, dict) else None
                        if isinstance(rs, list) and rs:
                            receipts = rs
                    except Exception:
                        receipts = None

                out = {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_content,
                }
                return idx, out, tool_name, receipts
            except Exception as e:
                success = False
                result_str = str(e)
                logger.info(f"[Tool] {tool_name} ERROR: {result_str}")
                out = {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"Tool execution error: {result_str}",
                    "is_error": True,
                }
                return idx, out, None, None
            finally:
                dt_ms = int((time.time() - t0) * 1000)
                if task_monitor:
                    if use_parallel_safe_monitor:
                        async with self._task_monitor_lock:
                            task_monitor.record_tool_call(
                                tool_name,
                                tool_input,
                                result_str,
                                success=success,
                                duration_ms=dt_ms,
                            )
                    else:
                        task_monitor.end_tool_call(result_str, success=success)

        if not parallel_enabled:
            tool_results: list[dict] = []
            for tc in tool_calls:
                idx = len(tool_results)
                _, out, executed_name, receipts = await _run_one(tc, idx)
                tool_results.append(out)
                if executed_name:
                    executed_tool_names.append(executed_name)
                if receipts:
                    delivery_receipts = receipts
            return tool_results, executed_tool_names, delivery_receipts

        tasks = [_run_one(tc, idx) for idx, tc in enumerate(tool_calls)]
        done = await asyncio.gather(*tasks, return_exceptions=False)
        done.sort(key=lambda x: x[0])
        tool_results = [out for _, out, _, _ in done]
        for _, _, executed_name, receipts in done:
            if executed_name:
                executed_tool_names.append(executed_name)
            if receipts:
                delivery_receipts = receipts
        return tool_results, executed_tool_names, delivery_receipts

    async def initialize(self, start_scheduler: bool = True, lightweight: bool = False) -> None:
        """
        Initialize the Agent

        Args:
            start_scheduler: whether to start the scheduler (should be False when executing a scheduled task)
            lightweight: lightweight mode (sub-agent); skip warm-up, stickers, persona traits, and other non-essential init
        """
        if self._initialized:
            return

        # Initialize token-usage tracking
        init_token_tracking(str(settings.db_full_path))

        # Auto-generate/load the device ID (used for platform authentication)
        if not settings.hub_device_id:
            from openakita.hub.device import get_or_create_device_id

            data_dir = Path(settings.project_root) / "data"
            settings.hub_device_id = get_or_create_device_id(data_dir)

        # Load the identity document
        self.identity.load()

        # Load installed skills
        await self._load_installed_skills()

        # Load MCP configuration
        if not lightweight:
            await self._load_mcp_servers()
        else:
            await self._start_builtin_mcp_servers()

        # === Load plugins ===
        try:
            await self._load_plugins()
        except Exception as e:
            logger.error(f"Plugin system failed to initialize: {e}")

        if hasattr(self, "_plugin_manager") and self._plugin_manager:
            try:
                await self._plugin_manager.hook_registry.dispatch("on_init", agent=self)
            except Exception as e:
                logger.debug(f"on_init hook dispatch error: {e}")

        # Start the memory session
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8]
        self.memory_manager.start_session(session_id)
        self._current_session_id = session_id
        if hasattr(self, "_memory_handler"):
            self._memory_handler.reset_guide()

        # Start the scheduler (skip when executing a scheduled task to avoid recursion)
        if start_scheduler:
            await self._start_scheduler()

        # Set the system prompt (including skill list, MCP list, and related memories)
        self._context.system = self._build_system_prompt()

        if lightweight:
            self._initialized = True
            return

        # === Startup warm-up (hoist expensive-but-reusable init to the startup phase) ===
        # Goal: avoid deferring embedding / vector-store loading and list generation to the first user message, which would significantly delay the first IM response.
        try:
            # 1) Warm up list caches (so build_system_prompt doesn't regenerate every time)
            # Note: these methods already cache internally; calling them once ensures the caches are populated.
            with contextlib.suppress(Exception):
                self.tool_catalog.get_catalog()
            with contextlib.suppress(Exception):
                self.skill_catalog.get_catalog()
            with contextlib.suppress(Exception):
                self.mcp_catalog.get_catalog()

            # 2) Warm up the vector store (embedding model + ChromaDB)
            # Run in a thread to avoid blocking the event loop; subsequent searches will be noticeably faster once init completes.
            if self.memory_manager.vector_store is not None:
                await asyncio.to_thread(lambda: bool(self.memory_manager.vector_store.enabled))
        except Exception as e:
            # Warm-up failure must not affect startup (e.g. if chromadb isn't installed, it will be auto-disabled)
            logger.debug(f"[Prewarm] skipped/failed: {e}")

        # === Sticker-engine initialization ===
        if self.sticker_engine:
            try:
                await self.sticker_engine.initialize()
            except Exception as e:
                logger.debug(f"[Sticker] initialization skipped/failed: {e}")

        # === Load PERSONA_TRAIT from the memory system ===
        try:
            persona_memories = [
                m.to_dict()
                for m in self.memory_manager._memories.values()
                if m.type.value == "persona_trait"
            ]
            if persona_memories:
                self.persona_manager.load_traits_from_memories(persona_memories)
                logger.info(f"Loaded {len(persona_memories)} persona traits from memory")
        except Exception as e:
            logger.debug(f"[Persona] trait loading skipped: {e}")

        # --- Todo state recovery + debounced save loop ---
        try:
            from ..tools.handlers.plan import register_active_todo, register_plan_handler

            plan_handle_fn = self.handler_registry.get_handler("plan")
            plan_handler = getattr(plan_handle_fn, "__self__", None) if plan_handle_fn else None
            if plan_handler and hasattr(plan_handler, "_store"):
                restored = plan_handler._store.load()
                for conv_id, plan_data in restored.items():
                    if plan_data.get("status") == "in_progress":
                        plan_handler._todos_by_session[conv_id] = plan_data
                        register_active_todo(
                            conv_id, plan_data.get("id", plan_data.get("plan_id", ""))
                        )
                        register_plan_handler(conv_id, plan_handler)
                        logger.info(
                            f"[TodoStore] Restored plan {plan_data.get('id')} for {conv_id}"
                        )
                self._todo_save_task = asyncio.create_task(plan_handler._store.start_save_loop())
        except Exception as e:
            logger.debug(f"[TodoStore] Restore/save-loop failed: {e}")

        self._initialized = True
        total_mcp = self.mcp_catalog.server_count + self._builtin_mcp_count
        logger.info(
            f"Agent '{self.name}' initialized with "
            f"{self.skill_registry.count} skills, "
            f"{total_mcp} MCP servers"
            f"{f' (builtin: {self._builtin_mcp_count})' if self._builtin_mcp_count else ''}"
        )

    async def _load_plugins(self) -> None:
        """Load plugins from data/plugins/ directory."""
        from ..plugins.manager import PluginManager

        plugins_dir = Path(settings.project_root) / "data" / "plugins"
        state_path = Path(settings.project_root) / "data" / "plugin_state.json"

        memory_backends: dict = {}
        search_backends: dict = {}

        if self.memory_manager:
            self.memory_manager.set_plugin_backends(memory_backends)

        host_refs: dict = {
            "brain": self.brain,
            "memory_manager": self.memory_manager,
            "tool_registry": self.handler_registry,
            "tool_definitions": self._tools,
            "tool_catalog": self.tool_catalog,
            "gateway": None,
            "skill_loader": getattr(self, "skill_loader", None),
            "skill_catalog": getattr(self, "skill_catalog", None),
            "mcp_client": getattr(self, "mcp_client", None),
            "memory_backends": memory_backends,
            "search_backends": search_backends,
            "external_retrieval_sources": (
                self.memory_manager.retrieval_engine._external_sources
                if self.memory_manager and hasattr(self.memory_manager, "retrieval_engine")
                else []
            ),
        }

        try:
            from ..channels.registry import register_adapter

            host_refs["channel_registry"] = register_adapter
        except ImportError:
            pass

        self._plugin_manager = PluginManager(
            plugins_dir=plugins_dir,
            state_path=state_path,
            host_refs=host_refs,
        )

        await self._plugin_manager.load_all()

        try:
            from ..prompt.builder import set_prompt_hook_registry

            set_prompt_hook_registry(self._plugin_manager.hook_registry)
        except Exception as e:
            logger.debug(f"Could not wire prompt hook registry: {e}")

        if self.memory_manager and hasattr(self.memory_manager, "retrieval_engine"):
            self.memory_manager.retrieval_engine._plugin_hooks = self._plugin_manager.hook_registry

        if hasattr(self, "reasoning_engine") and self.reasoning_engine:
            self.reasoning_engine._plugin_hooks = self._plugin_manager.hook_registry
        if hasattr(self, "tool_executor") and self.tool_executor:
            self.tool_executor._plugin_hooks = self._plugin_manager.hook_registry

        from ..plugins.catalog import PluginCatalog

        self.plugin_catalog = PluginCatalog(self._plugin_manager)
        self.prompt_assembler._plugin_catalog = self.plugin_catalog

        loaded = self._plugin_manager.loaded_count
        failed = self._plugin_manager.failed_count
        if failed > 0:
            logger.warning(f"Plugins: {loaded} loaded, {failed} failed (see plugin logs)")
        elif loaded > 0:
            logger.info(f"Plugins: {loaded} loaded successfully")

    def _init_handlers(self) -> None:
        """
        Initialize system tool handlers

        Register each module's handlers with handler_registry
        """
        # Filesystem
        self.handler_registry.register("filesystem", create_filesystem_handler(self))

        # Memory system
        self.handler_registry.register("memory", create_memory_handler(self))

        # Browser
        self.handler_registry.register("browser", create_browser_handler(self))

        # Scheduled tasks
        self.handler_registry.register("scheduled", create_scheduled_handler(self))

        # MCP
        self.handler_registry.register("mcp", create_mcp_handler(self))

        # User profile
        self.handler_registry.register("profile", create_profile_handler(self))

        # Plan mode
        self.handler_registry.register("plan", create_todo_handler(self))

        # System tools
        self.handler_registry.register("system", create_system_handler(self))

        # IM channel
        self.handler_registry.register("im_channel", create_im_channel_handler(self))

        # Skill management
        self.handler_registry.register("skills", create_skills_handler(self))

        # Web search
        self.handler_registry.register("web_search", create_web_search_handler(self))

        # Web Fetch (lightweight URL-content fetcher)
        self.handler_registry.register("web_fetch", create_web_fetch_handler(self))

        # Code Quality (linter diagnostics)
        self.handler_registry.register("code_quality", create_code_quality_handler(self))

        # Semantic Search
        self.handler_registry.register("search", create_search_handler(self))

        # Mode Switch
        self.handler_registry.register("mode", create_mode_handler(self))

        # Notebook
        self.handler_registry.register("notebook", create_notebook_handler(self))

        # Persona system
        self.handler_registry.register("persona", create_persona_handler(self))

        # Stickers
        self.handler_registry.register("sticker", create_sticker_handler(self))

        # System configuration
        self.handler_registry.register("config", create_config_handler(self))

        # Plugin query
        self.handler_registry.register("plugins", create_plugins_handler(self))

        # Agent package (import/export)
        self.handler_registry.register("agent_package", create_agent_package_handler(self))

        # LSP (code intelligence)
        self.handler_registry.register("lsp", create_lsp_handler(self))

        # Sleep (interruptible wait)
        self.handler_registry.register("sleep", create_sleep_handler(self))

        # Structured Output
        self.handler_registry.register("structured_output", create_structured_output_handler(self))

        # Tool Search
        self.handler_registry.register("tool_search", create_tool_search_handler(self))

        # Worktree (Git worktrees)
        self.handler_registry.register("worktree", create_worktree_handler(self))

        # Agent Hub + Skill Store (platform interaction; registered only when hub_enabled)
        if settings.hub_enabled:
            self.handler_registry.register("agent_hub", create_agent_hub_handler(self))
            self.handler_registry.register("skill_store", create_skill_store_handler(self))

        # PowerShell (registered only on Windows)
        import platform

        if platform.system() == "Windows":
            self.handler_registry.register("powershell", create_powershell_handler(self))

        # Desktop tools (registered only on Windows when deps are available; stays in sync with _tools/ToolCatalog)
        if _ensure_desktop():
            self.handler_registry.register("desktop", create_desktop_handler(self))

        # OpenCLI (website operations; registered only when opencli is installed)
        if opencli_available():
            self.handler_registry.register("opencli", create_opencli_handler(self))
            logger.info("OpenCLI handler registered (opencli detected on PATH)")

        # CLI-Anything (desktop-software control; registered only when cli-anything-* tools are present)
        if cli_anything_available():
            self.handler_registry.register("cli_anything", create_cli_anything_handler(self))
            logger.info("CLI-Anything handler registered (cli-anything-* tools detected)")

        self.handler_registry.register("agent", create_agent_tool_handler(self))
        from ..tools.handlers.org_setup import create_handler as create_org_setup_handler

        self.handler_registry.register("org_setup", create_org_setup_handler(self))

        logger.info(
            f"Initialized {len(self.handler_registry._handlers)} handlers with {len(self.handler_registry._tool_to_handler)} tools"
        )

    async def _load_installed_skills(self) -> None:
        """
        Load installed skills (following the Agent Skills spec)

        Skills are loaded from the following directories:
        - skills/ (project-level)
        - .cursor/skills/ (Cursor compatibility)
        """
        await self.skill_manager.load_installed_skills()
        self._skill_catalog_text = self.skill_manager.catalog_text

        # Update the tool list with skill tools
        self._update_skill_tools()

        # F8: register conditional skills
        for skill in self.skill_registry.list_enabled():
            if skill.paths or skill.fallback_for_toolsets:
                self._skill_activation.register_conditional(skill)
        self._sync_available_toolsets()

        # F9: start skill file watcher
        self._start_skill_watcher()

        # Notify that the first load has completed so the API/WS layer can sync
        try:
            from ..skills.events import SkillEvent, notify_skills_changed

            notify_skills_changed(SkillEvent.LOAD)
        except Exception:
            pass

    def _sync_available_toolsets(self) -> None:
        """Collect tool category names from the active tool list and push them
        into the activation manager so ``fallback_for_toolsets`` skills can
        react to the current tool availability."""
        categories: set[str] = set()
        for tool_def in self._tools:
            cat = tool_def.get("category") or ""
            if cat:
                categories.add(cat.lower())
        self._skill_activation.update_available_toolsets(categories)

    def _start_skill_watcher(self) -> None:
        """F9: Start watching skill directories for hot-reload."""
        try:
            from ..skills.watcher import SkillWatcher

            watch_dirs = [
                settings.skills_path,
                settings.project_root / ".cursor" / "skills",
            ]
            self._skill_watcher = SkillWatcher(
                directories=watch_dirs,
                on_change=self._on_skills_dir_changed,
            )
            self._skill_watcher.start()
        except Exception as e:
            logger.debug("Failed to start skill watcher: %s", e)

    def _on_skills_dir_changed(self) -> None:
        """F9: watchdog callback (runs in the watcher's dedicated Timer thread).

        All refresh logic is funneled into ``propagate_skill_change``; this callback only handles cross-thread dispatch.
        """
        try:
            from ..skills.events import SkillEvent

            self.propagate_skill_change(SkillEvent.HOT_RELOAD)
        except Exception as e:
            logger.warning("Skill hot-reload failed: %s", e)

    def _cleanup_skill_resources(self) -> None:
        """F9: Release all skill-related resources on shutdown."""
        if self._skill_watcher:
            self._skill_watcher.stop()
            self._skill_watcher = None
        if hasattr(self, "_skill_activation"):
            self._skill_activation.clear()
        try:
            from .policy import get_policy_engine

            get_policy_engine().clear_skill_allowlists()
        except Exception:
            pass

    def _update_shell_tool_description(self) -> None:
        """Append current OS info to the end of the run_shell description (without overwriting the original)"""
        import platform

        if os.name == "nt":
            os_info = f"Windows {platform.release()} (PowerShell/cmd)"
        else:
            os_info = f"{platform.system()} (bash)"

        os_hint = f"\n\nCurrent OS: {os_info}"

        for tool in self._tools:
            if tool.get("name") == "run_shell":
                desc = tool.get("description", "")
                if "Current OS:" not in desc:
                    tool["description"] = desc + os_hint
                break

    def _update_skill_tools(self) -> None:
        """Sync system skills' tool_name -> handler mapping into handler_registry.

        After skill loading, system skills (system: true) may define tool_name and handler fields.
        Those mappings must be synced into handler_registry, or the LLM will see "Tool not found" when calling the tool.

        This method performs two-way sync:
        1. Add mappings defined by newly-loaded skills (without overwriting built-in _init_handlers mappings)
        2. Clean up stale mappings that are no longer in skill_registry (only those dynamically added by skills)
        """
        current_skill_tools: set[str] = set()

        for skill in self.skill_registry.list_system_skills():
            tool_name = skill.tool_name
            handler_name = skill.handler
            if not tool_name or not handler_name:
                continue
            current_skill_tools.add(tool_name)
            if self.handler_registry.has_tool(tool_name):
                continue
            if not self.handler_registry.has_handler(handler_name):
                logger.debug(
                    f"Skipping skill tool mapping {tool_name} -> {handler_name}: "
                    f"handler '{handler_name}' not registered"
                )
                continue
            self.handler_registry.map_tool_to_handler(tool_name, handler_name)
            logger.info(f"Mapped skill tool: {tool_name} -> {handler_name}")

        stale = self._skill_tool_names - current_skill_tools - self._core_tool_names
        for tool_name in stale:
            if self.handler_registry.unmap_tool(tool_name):
                logger.info(f"Unmapped stale skill tool: {tool_name}")

        self._skill_tool_names = current_skill_tools

    @staticmethod
    def notify_pools_skills_changed() -> None:
        """Notify all global Agent instance pools that skills have changed.

        Older pooled Agents will be lazily rebuilt on the next get_or_create.
        """
        try:
            from openakita.main import _desktop_pool, _orchestrator

            for src in (_desktop_pool, _orchestrator):
                if src is None:
                    continue
                pool = getattr(src, "_pool", src)
                if hasattr(pool, "notify_skills_changed"):
                    pool.notify_skills_changed()
        except (ImportError, AttributeError):
            pass

    def propagate_skill_change(
        self,
        action: "Any" = None,
        *,
        rescan: bool = True,
    ) -> None:
        """The single entry point for refreshing on skill-state changes.

        Callers (API routes, tool handlers, config endpoints, watchdog callbacks, SkillManager install)
        must and must only use this method to refresh after any operation that affects skill visibility / content,
        ensuring that:
          1. Parser / Loader internal caches are cleared so disk changes are visible on the next ``load_all``;
          2. The external_allowlist defined in ``data/skills.json`` is re-applied;
          3. ``SkillCatalog`` and ``_skill_catalog_text`` stay in sync with the registry;
          4. The tool -> handler mapping for system skills is synced into handler_registry;
          5. The F8 conditional-activation registry is refreshed;
          6. The ``_context.system`` cache used by the long-lived CLI path is invalidated and rebuilt;
          7. The global Agent-pool version is bumped so the next Desktop Chat request gets a fresh Agent;
          8. Cross-layer ``notify_skills_changed`` fires only here (API HTTP cache + WebSocket broadcast).

        Args:
            action: a ``SkillEvent`` enum or string, used only for broadcast and logging; it does not affect the refresh path.
            rescan: when False, skip ``loader.load_all`` and run only the allowlist->catalog->pool
                refresh chain (common for start/stop and configuration-panel scenarios to avoid repeated scans).
        """
        from ..skills.events import SkillEvent, notify_skills_changed
        from ..skills.watcher import clear_all_skill_caches

        clear_all_skill_caches()

        if rescan:
            try:
                self.skill_loader.load_all(settings.project_root)
            except Exception as e:
                logger.warning("propagate_skill_change: load_all failed: %s", e)

        try:
            from ..skills.allowlist_io import read_allowlist
            from ..skills.preset_utils import collect_preset_referenced_skills

            _, external_allowlist = read_allowlist()
            effective = self.skill_loader.compute_effective_allowlist(external_allowlist)
            agent_skills = collect_preset_referenced_skills()
            self.skill_loader.prune_external_by_allowlist(
                effective, agent_referenced_skills=agent_skills
            )
        except Exception as e:
            logger.warning("propagate_skill_change: allowlist apply failed: %s", e)

        try:
            self.skill_catalog.invalidate_cache()
            self._skill_catalog_text = self.skill_catalog.generate_catalog()
        except Exception as e:
            logger.warning("propagate_skill_change: catalog rebuild failed: %s", e)

        try:
            self._update_skill_tools()
        except Exception as e:
            logger.warning("propagate_skill_change: tool mapping update failed: %s", e)

        try:
            if hasattr(self, "_skill_activation"):
                self._skill_activation.clear()
                for skill in self.skill_registry.list_enabled():
                    if skill.paths or skill.fallback_for_toolsets:
                        self._skill_activation.register_conditional(skill)
                self._sync_available_toolsets()
        except Exception as e:
            logger.warning("propagate_skill_change: activation refresh failed: %s", e)

        try:
            if getattr(self, "_initialized", False):
                ctx = getattr(self, "_context", None)
                if ctx is not None and getattr(ctx, "system", None):
                    ctx.system = self._build_system_prompt()
        except Exception as e:
            logger.warning("propagate_skill_change: system prompt rebuild failed: %s", e)

        try:
            Agent.notify_pools_skills_changed()
        except Exception as e:
            logger.warning("propagate_skill_change: pool notify failed: %s", e)

        try:
            action_value: str
            if isinstance(action, SkillEvent):
                action_value = action.value
            elif isinstance(action, str) and action:
                action_value = action
            else:
                action_value = SkillEvent.RELOAD.value
            notify_skills_changed(action_value)
        except Exception as e:
            logger.debug("propagate_skill_change: notify_skills_changed failed: %s", e)

    async def _install_skill(
        self,
        source: str,
        name: str | None = None,
        subdir: str | None = None,
        extra_files: list[str] | None = None,
    ) -> str:
        """Install a skill -- delegates to SkillManager."""
        return await self.skill_manager.install_skill(source, name, subdir, extra_files)

    async def _load_mcp_servers(self) -> None:
        """
        Load MCP server configuration

        Load only project-local MCP, not Cursor's (since we can't actually invoke them)
        """
        if not settings.mcp_enabled:
            logger.info("MCP disabled via MCP_ENABLED=false")
            await self._start_builtin_mcp_servers()
            return

        # Scan MCP config directories: built-in (read-only) + workspace (writable)
        # Built-in: mcps/ (shipped with the project), .mcp/ (compat)
        # Workspace: data/mcp/servers/ (added by AI or user; writable in packaged mode)
        possible_dirs = [
            settings.mcp_builtin_path,
            settings.project_root / ".mcp",
            settings.mcp_config_path,
        ]

        total_count = 0

        for dir_path in possible_dirs:
            if dir_path.exists():
                count = self.mcp_catalog.scan_mcp_directory(dir_path)
                if count > 0:
                    total_count += count
                    logger.info(f"Loaded {count} MCP servers from {dir_path}")

        # Sync discovered MCP servers into MCPClient (otherwise they'd be 'visible in the catalog but not callable')
        # The catalog (mcp_catalog) handles discovery and prompt disclosure; the client (mcp_client) handles real connections and calls.
        try:
            from ..tools.mcp import MCPServerConfig

            for server in self.mcp_catalog.servers:
                if not server.identifier:
                    continue
                if not server.enabled:
                    logger.debug("Skipping disabled MCP server: %s", server.identifier)
                    continue
                transport = server.transport or "stdio"
                if transport == "stdio" and not server.command:
                    continue
                if transport in ("streamable_http", "sse") and not server.url:
                    continue
                self.mcp_client.add_server(
                    MCPServerConfig(
                        name=server.identifier,
                        command=server.command or "",
                        args=list(server.args or []),
                        env=dict(server.env or {}),
                        description=server.name or "",
                        transport=transport,
                        url=server.url or "",
                        cwd=server.config_dir or "",
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to register MCP servers into MCPClient: {e}")

        # Start the built-in browser service
        await self._start_builtin_mcp_servers()

        # Warm up the catalog cache (list even when a server has no tools, to help the AI discover and connect)
        self.mcp_catalog.generate_catalog()
        if total_count > 0:
            logger.info(f"Total MCP servers: {total_count}")
        else:
            logger.info("No MCP servers configured")

        # Auto-connect: global switch -> connect all; otherwise -> honor per-server autoConnect flags
        all_server_names = set(self.mcp_client.list_servers())
        if settings.mcp_auto_connect:
            auto_connect_ids = all_server_names
        else:
            auto_connect_ids = {
                s.identifier for s in self.mcp_catalog.servers if s.auto_connect
            } & all_server_names

        if auto_connect_ids:
            from ..tools.mcp_workspace import prepare_chrome_devtools_args

            synced_any = False
            for server_name in auto_connect_ids:
                try:
                    await prepare_chrome_devtools_args(self.mcp_client, server_name)
                    result = await self.mcp_client.connect(server_name)
                    if result.success:
                        logger.info(
                            f"Auto-connected MCP server: {server_name} ({result.tool_count} tools)"
                        )
                        runtime_tools = self.mcp_client.list_tools(server_name)
                        if runtime_tools:
                            tool_dicts = [
                                {
                                    "name": t.name,
                                    "description": t.description,
                                    "input_schema": t.input_schema,
                                }
                                for t in runtime_tools
                            ]
                            count = self.mcp_catalog.sync_tools_from_client(
                                server_name,
                                tool_dicts,
                                force=True,
                            )
                            if count > 0:
                                synced_any = True
                    else:
                        logger.warning(
                            f"Auto-connect to MCP server {server_name} failed: {result.error}"
                        )
                except Exception as e:
                    logger.warning(f"Auto-connect to MCP server {server_name} failed: {e}")

            if synced_any:
                logger.info("MCP catalog refreshed after auto-connect tool discovery")

    async def _start_builtin_mcp_servers(self) -> None:
        """Start the built-in browser service (Playwright, independent of the MCP system)"""
        self._builtin_mcp_count = 0

        try:
            from ..tools._import_helper import import_or_hint

            pw_hint = import_or_hint("playwright")
            if pw_hint:
                logger.warning(f"Browser automation unavailable: {pw_hint}")
            else:
                from ..tools.browser import BrowserManager, PlaywrightTools

                self.browser_manager = BrowserManager()
                self.pw_tools = PlaywrightTools(self.browser_manager)
                logger.info("Initialized browser service (Playwright)")
        except Exception as e:
            logger.warning(f"Failed to start browser service: {e}")

    async def _start_scheduler(self) -> None:
        """Start the scheduled-task scheduler"""
        try:
            from ..scheduler import TaskScheduler
            from ..scheduler.executor import TaskExecutor

            # Create the executor (gateway is set later via set_scheduler_gateway)
            self._task_executor = TaskExecutor(timeout_seconds=settings.scheduler_task_timeout)
            # Pre-populate persona/memory/proactive references for system tasks like the liveness heartbeat
            self._task_executor.persona_manager = getattr(self, "persona_manager", None)
            self._task_executor.memory_manager = getattr(self, "memory_manager", None)
            self._task_executor.proactive_engine = getattr(self, "proactive_engine", None)

            # Create the scheduler
            self.task_scheduler = TaskScheduler(
                storage_path=settings.project_root / "data" / "scheduler",
                executor=self._task_executor.execute,
            )

            # Register the auto-disable notification callback
            executor_ref = self._task_executor

            async def _on_auto_disabled(task):
                if task.channel_id and task.chat_id and executor_ref.gateway:
                    try:
                        await executor_ref.gateway.send(
                            channel=task.channel_id,
                            chat_id=task.chat_id,
                            text=(
                                f"WARNING: Task '{task.name}' has been automatically paused\n\n"
                                f"Reason: {task.fail_count} consecutive failures\n"
                                f"To resume, tell me 'resume task {task.id}'"
                            ),
                        )
                    except Exception as e:
                        logger.debug(f"Auto-disable notification failed: {e}")

            self.task_scheduler.on_task_auto_disabled = _on_auto_disabled

            async def _on_missed_tasks(missed_list):
                if not missed_list or not executor_ref.gateway:
                    return
                targets = executor_ref._find_all_im_targets()
                if not targets:
                    return
                lines = []
                for t in missed_list[:10]:
                    missed_at = t.metadata.get("missed_at") or t.metadata.get("last_missed_at", "")
                    lines.append(f"  · {t.name}（scheduled for {missed_at[:16]}）")
                if len(missed_list) > 10:
                    lines.append(f"  ...total {len(missed_list)}")
                msg = (
                    f"⚠️ While I was asleep, {len(missed_list)} tasks/reminders missed their scheduled time:\n"
                    + "\n".join(lines)
                    + "\n\nRecurring tasks have been rescheduled to their next run time; one-shot tasks have been marked as missed."
                )
                ch, cid = targets[0]
                try:
                    await executor_ref.gateway.send(channel=ch, chat_id=cid, text=msg)
                except Exception as e:
                    logger.debug(f"Missed tasks notification failed: {e}")

            self.task_scheduler.on_missed_tasks_summary = _on_missed_tasks

            if hasattr(self, "_plugin_manager") and self._plugin_manager:
                self.task_scheduler._plugin_hooks = self._plugin_manager.hook_registry

            # Start the scheduler
            await self.task_scheduler.start()

            # Register built-in system tasks (daily memory consolidation + daily self-check)
            await self._register_system_tasks()

            # Publish as a global singleton so pool agents share it in multi-Agent mode
            from ..scheduler import set_active_scheduler

            set_active_scheduler(self.task_scheduler, self._task_executor)

            stats = self.task_scheduler.get_stats()
            logger.info(f"TaskScheduler started with {stats['total_tasks']} tasks")

        except Exception as e:
            logger.warning(f"Failed to start scheduler: {e}")
            self.task_scheduler = None

    async def _register_system_tasks(self) -> None:
        """
        Register built-in system tasks

        Including:
        - Memory consolidation (3:00 AM; during the adaptive period, every N hours)
        - System self-check (4:00 AM)
        - Liveness heartbeat (every 30 minutes)
        """
        from ..config import settings
        from ..scheduler import ScheduledTask, TriggerType
        from ..scheduler.consolidation_tracker import ConsolidationTracker
        from ..scheduler.task import TaskType

        if not self.task_scheduler:
            return

        # Initialize the consolidation-time tracker
        tracker = ConsolidationTracker(settings.project_root / "data" / "scheduler")
        is_onboarding = tracker.is_onboarding(settings.memory_consolidation_onboarding_days)

        if is_onboarding:
            elapsed_days = tracker.get_onboarding_elapsed_days()
            interval_h = settings.memory_consolidation_onboarding_interval_hours
            logger.info(
                f"Onboarding mode: day {elapsed_days:.1f}/{settings.memory_consolidation_onboarding_days}, "
                f"memory consolidation every {interval_h}h"
            )

        existing_tasks = self.task_scheduler.list_tasks()
        existing_ids = {t.id for t in existing_tasks}

        # Task 1: memory consolidation
        # Adaptive period: use interval mode (every N hours)
        # Normal period: cron mode (3:00 AM)
        memory_task_id = "system_daily_memory"
        existing_memory_task = self.task_scheduler.get_task(memory_task_id)

        if is_onboarding:
            interval_h = settings.memory_consolidation_onboarding_interval_hours
            desired_trigger = TriggerType.INTERVAL
            desired_config = {"interval_minutes": interval_h * 60}
            desired_desc = f"Adaptive-period memory consolidation (every {interval_h} hours)"
        else:
            desired_trigger = TriggerType.CRON
            desired_config = {"cron": "0 3 * * *"}
            desired_desc = "Consolidate conversation history, extract memories, refresh MEMORY.md"

        if memory_task_id not in existing_ids:
            memory_task = ScheduledTask(
                id=memory_task_id,
                name="Memory consolidation",
                trigger_type=desired_trigger,
                trigger_config=desired_config,
                action="system:daily_memory",
                prompt="Run memory consolidation: tidy conversation history, extract key memories, refresh MEMORY.md",
                description=desired_desc,
                task_type=TaskType.TASK,
                enabled=True,
                deletable=False,
            )
            await self.task_scheduler.add_task(memory_task)
            logger.info(f"Registered system task: daily_memory ({desired_desc})")
        else:
            changed = False
            if existing_memory_task:
                if existing_memory_task.deletable:
                    existing_memory_task.deletable = False
                    changed = True
                if not getattr(existing_memory_task, "action", None):
                    existing_memory_task.action = "system:daily_memory"
                    changed = True
                # Update the trigger on adaptive-period <-> normal-period transitions
                if existing_memory_task.trigger_type != desired_trigger:
                    await self.task_scheduler.update_task(
                        memory_task_id,
                        {
                            "trigger_type": desired_trigger,
                            "trigger_config": desired_config,
                            "description": desired_desc,
                        },
                    )
                    changed = False  # update_task already persisted it
                    logger.info(
                        f"Switched memory task trigger to {desired_trigger.value}: {desired_desc}"
                    )
                if changed:
                    await self.task_scheduler.save()

        # Task 2: system self-check (4:00 AM)
        if "system_daily_selfcheck" not in existing_ids:
            selfcheck_task = ScheduledTask(
                id="system_daily_selfcheck",
                name="System self-check",
                trigger_type=TriggerType.CRON,
                trigger_config={"cron": "0 4 * * *"},
                action="system:daily_selfcheck",
                prompt="Run system self-check: analyze ERROR logs, attempt to fix tool issues, generate a report",
                description="Analyze ERROR logs, attempt to fix tool issues, generate a report",
                task_type=TaskType.TASK,
                enabled=True,
                deletable=False,
            )
            await self.task_scheduler.add_task(selfcheck_task)
            logger.info("Registered system task: daily_selfcheck (04:00)")
        else:
            existing_task = self.task_scheduler.get_task("system_daily_selfcheck")
            if existing_task:
                changed = False
                if existing_task.deletable:
                    existing_task.deletable = False
                    changed = True
                if not getattr(existing_task, "action", None):
                    existing_task.action = "system:daily_selfcheck"
                    changed = True
                if changed:
                    await self.task_scheduler.save()

        # Task 3: liveness heartbeat (fires every 30 minutes)
        try:
            if "system_proactive_heartbeat" not in existing_ids:
                heartbeat_task = ScheduledTask(
                    id="system_proactive_heartbeat",
                    name="Liveness heartbeat",
                    trigger_type=TriggerType.INTERVAL,
                    trigger_config={"interval_minutes": 30},
                    action="system:proactive_heartbeat",
                    prompt="Check whether a proactive message (greeting/reminder/follow-up) should be sent",
                    description="Periodically check and send proactive messages",
                    task_type=TaskType.TASK,
                    enabled=True,
                    deletable=False,
                    metadata={"notify_on_start": False, "notify_on_complete": False},
                )
                await self.task_scheduler.add_task(heartbeat_task)
                logger.info("Registered system task: proactive_heartbeat (every 30 min)")
        except Exception as e:
            logger.warning(f"Failed to register proactive_heartbeat task: {e}")

        # Task 4: memory review (Memory Nudge)
        try:
            nudge_task_id = "system_memory_nudge"
            if settings.memory_nudge_enabled and settings.memory_nudge_interval > 0:
                interval_min = max(5, settings.memory_nudge_interval * 3)
                if nudge_task_id not in existing_ids:
                    nudge_task = ScheduledTask(
                        id=nudge_task_id,
                        name="Memory review",
                        trigger_type=TriggerType.INTERVAL,
                        trigger_config={"interval_minutes": interval_min},
                        action="system:memory_nudge_review",
                        prompt="Review recent conversation and extract missed important memories",
                        description=f"Every {interval_min} minutes review recent conversation to extract missed memories",
                        task_type=TaskType.TASK,
                        enabled=True,
                        deletable=False,
                        metadata={"notify_on_start": False, "notify_on_complete": False},
                    )
                    await self.task_scheduler.add_task(nudge_task)
                    logger.info(f"Registered system task: memory_nudge (every {interval_min} min)")
            else:
                existing_nudge = self.task_scheduler.get_task(nudge_task_id)
                if existing_nudge and existing_nudge.enabled:
                    await self.task_scheduler.disable_task(nudge_task_id)
                    logger.info("Disabled memory_nudge task (feature disabled in settings)")
        except Exception as e:
            logger.warning(f"Failed to register memory_nudge task: {e}")

        # Task 5: periodic workspace backup (controlled by user settings)
        try:
            from ..workspace.backup import read_backup_settings

            bs = read_backup_settings(settings.project_root)
            backup_enabled = bs.get("enabled", False) and bool(bs.get("backup_path"))
            backup_task_id = "system_workspace_backup"

            if backup_task_id not in existing_ids:
                if backup_enabled:
                    cron = bs.get("cron", "0 2 * * *")
                    backup_task = ScheduledTask(
                        id=backup_task_id,
                        name="Workspace backup",
                        trigger_type=TriggerType.CRON,
                        trigger_config={"cron": cron},
                        action="system:workspace_backup",
                        prompt="Run workspace data backup",
                        description="Periodically back up workspace configuration and user data",
                        task_type=TaskType.TASK,
                        enabled=True,
                        deletable=False,
                        metadata={"notify_on_start": False, "notify_on_complete": False},
                    )
                    await self.task_scheduler.add_task(backup_task)
                    logger.info(f"Registered system task: workspace_backup (cron={cron})")
            else:
                existing_bt = self.task_scheduler.get_task(backup_task_id)
                if existing_bt and existing_bt.enabled != backup_enabled:
                    if backup_enabled:
                        await self.task_scheduler.enable_task(backup_task_id)
                    else:
                        await self.task_scheduler.disable_task(backup_task_id)
        except Exception as e:
            logger.warning(f"Failed to register workspace_backup task: {e}")

    def _build_system_prompt(
        self,
        task_description: str = "",
        session_type: str = "cli",
    ) -> str:
        """Build the system prompt (uniformly using the compilation pipeline v2)."""
        return self._build_system_prompt_compiled_sync(task_description, session_type=session_type)

    def _build_system_prompt_compiled_sync(
        self, task_description: str = "", session_type: str = "cli"
    ) -> str:
        """Synchronous version: build the initial system prompt at startup (the event loop may not yet be ready)"""
        if getattr(self, "_org_context", None):
            ctx = getattr(self, "_context", None)
            if ctx and hasattr(ctx, "system") and ctx.system:
                return ctx.system

        ctx_window = self._get_raw_context_window()
        prompt = self.prompt_assembler._build_compiled_sync(
            task_description,
            session_type=session_type,
            context_window=ctx_window,
            is_sub_agent=self._is_sub_agent_call,
        )
        if self._custom_prompt_suffix:
            prompt += f"\n\n{self._custom_prompt_suffix}"
        prompt += self._build_multi_agent_prompt_section()
        return prompt

    async def _build_system_prompt_compiled(
        self,
        task_description: str = "",
        session_type: str = "cli",
        tools_enabled: bool = True,
        session: "Session | None" = None,
    ) -> str:
        """
        Build the system prompt using the compilation pipeline (v2)

        Token usage is reduced by ~55%, from ~6300 to ~2800.
        Async version: perform vector search asynchronously up front to avoid blocking the event loop.

        Args:
            task_description: Task description (used to retrieve relevant memories)
            session_type: Session type, "cli" or "im"
            tools_enabled: Whether tools are enabled (pass False on the lightweight CHAT path)
            session: Current Session instance (used to extract metadata)

        Returns:
            The compiled system prompt
        """
        if getattr(self, "_org_context", None):
            ctx = getattr(self, "_context", None)
            if ctx and hasattr(ctx, "system") and ctx.system:
                return ctx.system

        ctx_window = self._get_raw_context_window()
        intent = getattr(self, "_current_intent", None)
        _mem_keywords = intent.memory_keywords if intent else None

        model_display = ""
        try:
            conv_id = session.id if session else None
            model_info = self.brain.get_current_model_info(conversation_id=conv_id)
            if isinstance(model_info, dict) and "model" in model_info:
                model_display = model_info["model"]
        except Exception:
            pass

        session_context = None
        if session:
            try:
                sub_records = getattr(session.context, "sub_agent_records", None) or []
                session_config = getattr(session, "config", None)
                session_context = {
                    "session_id": session.id,
                    "channel": getattr(session, "channel", "unknown"),
                    "chat_type": getattr(session, "chat_type", "private"),
                    "message_count": len(session.context.messages) if session.context else 0,
                    "has_sub_agents": bool(sub_records),
                    "sub_agent_count": len(sub_records),
                    "language": getattr(session_config, "language", "zh")
                    if session_config
                    else "zh",
                }
            except Exception:
                pass

        _effective_mode = getattr(self.tool_executor, "_current_mode", "agent")
        _model_id = getattr(self.brain, "model", "")
        _skip_catalogs = False
        if intent:
            from .intent_analyzer import IntentType

            if intent.intent == IntentType.CHAT:
                _effective_mode = "ask"
                _skip_catalogs = True
            elif intent.intent == IntentType.QUERY:
                _skip_catalogs = True

        from ..prompt.budget import estimate_tokens
        from ..prompt.builder import PromptProfile, PromptTier, resolve_tier

        _user_input_tokens = estimate_tokens(task_description) if task_description else 0
        _prompt_profile = self._resolve_prompt_profile(intent, session_type)
        _prompt_tier = resolve_tier(ctx_window)

        prompt = await self.prompt_assembler.build_system_prompt_compiled(
            task_description,
            session_type=session_type,
            context_window=ctx_window,
            is_sub_agent=self._is_sub_agent_call,
            tools_enabled=tools_enabled,
            memory_keywords=_mem_keywords,
            model_display_name=model_display,
            session_context=session_context,
            mode=_effective_mode,
            model_id=_model_id,
            skip_catalogs=_skip_catalogs,
            user_input_tokens=_user_input_tokens,
            prompt_profile=_prompt_profile,
            prompt_tier=_prompt_tier,
        )
        if self._custom_prompt_suffix:
            prompt += f"\n\n{self._custom_prompt_suffix}"
        prompt += self._build_multi_agent_prompt_section()
        return prompt

    def _resolve_prompt_profile(self, intent: Any, session_type: str) -> "PromptProfile":
        """Determine PromptProfile from intent and session type."""
        from ..prompt.builder import PromptProfile

        if session_type == "im":
            return PromptProfile.IM_ASSISTANT
        if intent:
            from .intent_analyzer import IntentType

            if intent.intent in (IntentType.CHAT, IntentType.QUERY):
                return PromptProfile.CONSUMER_CHAT
        return PromptProfile.LOCAL_AGENT

    def _build_multi_agent_prompt_section(self) -> str:
        """Generate a system prompt section describing the multi-agent system.

        Always called (multi-agent mode is always on).
        Tells the LLM: identity, roster, delegation rules with strict priority:
        delegate > spawn > create.

        Sub-agents are NOT given delegation capabilities to prevent
        recursive delegation chains (sub-agent spawning sub-sub-agents).
        """
        if getattr(self, "_org_context", None):
            return ""

        from ..agents.presets import SYSTEM_PRESETS
        if self._is_sub_agent_call:
            return (
                "\n\n---\n"
                "## Sub-Agent work mode\n"
                "You are currently a **sub-Agent** delegated by a main Agent; focus on the task you were assigned.\n"
                "**Do NOT** use delegate_to_agent, delegate_parallel, create_agent, "
                "spawn_agent, or other delegation tools. Do not create or delegate to other Agents.\n"
                "Use your own specialized tools (such as web_search, browser, read_file, etc.) to finish the task.\n"
                "\n"
                "### Zero-fabrication rule for data conclusions (MUST follow)\n"
                "- If the task requires numeric/statistical/simulation/computation results, you must obtain them by running Python via run_shell "
                "or by calling the corresponding tool; never estimate from memory.\n"
                "- Any number, percentage, mean, standard deviation, or probability without tool output as evidence is considered a violation.\n"
                "- When real data cannot be obtained, explicitly return: \"Cannot execute: <specific reason>, suggest <alternative>\","
                "fabricating placeholder data is prohibited.\n"
            )

        profile = self._agent_profile
        if profile:
            identity_section = f"You are '{profile.name}' ({profile.icon}), {profile.description}."
            my_id = profile.id
        else:
            identity_section = "You are the default general assistant."
            my_id = "default"

        # Roster — compact format (no skill lists to save tokens)
        agents_lines = []
        for p in SYSTEM_PRESETS:
            if p.id == my_id:
                continue
            agents_lines.append(f"  - {p.icon} **{p.name}** (`{p.id}`) — {p.description}")

        try:
            store_dir = settings.data_dir / "agents"
            if store_dir.exists():
                from ..agents.profile import get_profile_store

                store = get_profile_store()
                preset_ids = {sp.id for sp in SYSTEM_PRESETS}
                for p in store.list_all(include_ephemeral=False):
                    if p.id == my_id or p.id in preset_ids:
                        continue
                    agents_lines.append(f"  - {p.icon} **{p.name}** (`{p.id}`) — {p.description}")
        except Exception:
            pass

        roster = "\n".join(agents_lines) if agents_lines else "  (no other Agents available)"

        # Skills list omitted from prompt to save tokens; use list_skills tool to discover

        return f"""

## Multi-agent collaboration

{identity_section}
You have a team of Agents; delegate to specialized Agents first and handle only simple generic Q&A yourself.

### Agent team

{roster}

### Delegation priority (highest to lowest)

1. `delegate_to_agent(agent_id, message, reason)` -- preferred, direct delegation
2. `spawn_agent(inherit_from, message, ...)` -- when customization or parallel copies are needed
3. `delegate_parallel(tasks=[...])` -- multiple independent tasks in parallel
4. `create_agent(...)` -- last resort, only when no relevant Agent exists

### Rules

- Match specialization: documents -> office-doc, code -> code-assistant, browsing -> browser-agent, data -> data-analyst
- Use `delegate_parallel` for independent tasks; serialize dependent ones
- The message must contain enough context for the target Agent to work independently
- After results come back, merge them and reply in your own voice
- Delegation depth cap is 5 levels; up to 5 dynamic Agents per session
- [Sub-Agent work summary] and [Execution summary] in the conversation history are completed facts; do not re-execute"""

    def _generate_tools_text(self) -> str:
        """
        .. deprecated::
            The tool list is now generated automatically by the prompt.builder compilation pipeline; this method is no longer used.
        """
        return ""

    def _get_max_context_tokens(self) -> int:
        """Dynamically obtain the current model's available context token count."""
        return _shared_get_max_context_tokens(self.brain)

    def _get_raw_context_window(self) -> int:
        """Get the raw context_window value configured for the current endpoint (used for the budgeting system)."""
        return _shared_get_raw_context_window(self.brain)

    # NOTE: _estimate_tokens / _group_messages have been moved to context_utils / context_manager
    # Below are v1.25.x compatibility methods that delegate to the shared implementation
    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate the token count of text

        Uses a bilingual-aware algorithm: ~1.5 chars/token for Chinese, ~4 chars/token for English.
        Kept consistent with prompt.budget.estimate_tokens() to avoid estimation drift across the codebase.
        """
        if not text:
            return 0
        # Count Chinese characters
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        total_chars = len(text)
        english_chars = total_chars - chinese_chars
        # ~1.5 chars/token for Chinese, ~4 chars/token for English
        chinese_tokens = chinese_chars / 1.5
        english_tokens = english_chars / 4
        return max(int(chinese_tokens + english_tokens), 1)

    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        """Estimate the token count of a message list (delegates to context_manager's unified algorithm)"""
        return self.context_manager.estimate_messages_tokens(messages)

    @staticmethod
    def _group_messages(messages: list[dict]) -> list[list[dict]]:
        """
        Group the message list into 'tool interaction groups' so that tool_calls/tool pairs are not split

        Grouping rules:
        - If an assistant message contains tool_calls (i.e. content has type=tool_use),
          then group that assistant message with all immediately-following role=user messages that contain only tool_result
        - Other messages each form their own group
        - System-injected plain-text user messages (e.g. LoopGuard hints) form their own group

        Returns:
            A list of groups where each element is a list[dict] of messages
        """
        if not messages:
            return []

        groups: list[list[dict]] = []
        i = 0

        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Detect whether an assistant message contains tool_use
            has_tool_calls = False
            if role == "assistant" and isinstance(content, list):
                has_tool_calls = any(
                    isinstance(item, dict) and item.get("type") == "tool_use" for item in content
                )

            if has_tool_calls:
                # Start a tool-interaction group: assistant(tool_calls) + subsequent tool_result messages
                group = [msg]
                i += 1
                while i < len(messages):
                    next_msg = messages[i]
                    next_role = next_msg.get("role", "")
                    next_content = next_msg.get("content", "")

                    # user message containing only tool_result -> belongs to this tool group
                    if next_role == "user" and isinstance(next_content, list):
                        all_tool_results = all(
                            isinstance(item, dict) and item.get("type") == "tool_result"
                            for item in next_content
                            if isinstance(item, dict)
                        )
                        if all_tool_results and next_content:
                            group.append(next_msg)
                            i += 1
                            continue

                    # tool-role message (OpenAI format) -> also belongs to this tool group
                    if next_role == "tool":
                        group.append(next_msg)
                        i += 1
                        continue

                    # Any other message type -> end the tool group
                    break

                groups.append(group)
            else:
                # Plain messages form their own group
                groups.append([msg])
                i += 1

        return groups

    # ==================== Attachment Memory Helpers ====================

    def _record_inbound_attachments(
        self,
        session_id: str,
        pending_images: list | None,
        pending_videos: list | None,
        pending_audio: list | None,
        pending_files: list | None,
        desktop_attachments: list | None,
    ) -> None:
        """Record media/files sent by the user this turn into the memory system"""
        if not self.memory_manager:
            return

        if pending_images:
            for img in pending_images:
                src = img.get("source") or {}
                img_url = img.get("image_url")
                self.memory_manager.record_attachment(
                    filename=img.get("filename", src.get("media_type", "image")),
                    mime_type=src.get("media_type", "image/jpeg"),
                    local_path=img.get("local_path", ""),
                    url=img_url.get("url", "") if isinstance(img_url, dict) else "",
                    description=img.get("description", ""),
                    direction="inbound",
                    file_size=img.get("file_size", 0),
                )

        if pending_videos:
            for vid in pending_videos:
                src = vid.get("source") or {}
                vid_url = vid.get("video_url")
                self.memory_manager.record_attachment(
                    filename=vid.get("filename", "video"),
                    mime_type=src.get("media_type", "video/mp4"),
                    local_path=vid.get("local_path", ""),
                    url=vid_url.get("url", "") if isinstance(vid_url, dict) else "",
                    description=vid.get("description", ""),
                    direction="inbound",
                    file_size=vid.get("file_size", 0),
                )

        if pending_audio:
            for aud in pending_audio:
                self.memory_manager.record_attachment(
                    filename=aud.get("filename", "audio"),
                    mime_type=aud.get("mime_type", "audio/wav"),
                    local_path=aud.get("local_path", ""),
                    transcription=aud.get("transcription", ""),
                    direction="inbound",
                    file_size=aud.get("file_size", 0),
                )

        if pending_files:
            for fdata in pending_files:
                self.memory_manager.record_attachment(
                    filename=fdata.get("filename", "file"),
                    mime_type=fdata.get("mime_type", "application/octet-stream"),
                    local_path=fdata.get("local_path", ""),
                    extracted_text=fdata.get("extracted_text", ""),
                    direction="inbound",
                    file_size=fdata.get("file_size", 0),
                )

        if desktop_attachments:
            for att in desktop_attachments:
                att_type = getattr(att, "type", None) or ""
                att_name = getattr(att, "name", None) or "file"
                att_url = getattr(att, "url", None) or ""
                att_mime = getattr(att, "mime_type", None) or att_type
                self.memory_manager.record_attachment(
                    filename=att_name,
                    mime_type=att_mime,
                    url=att_url,
                    direction="inbound",
                )

    @staticmethod
    def _extract_outbound_attachments(
        tool_calls: list[dict],
        tool_results: list[dict],
    ) -> list[dict]:
        """Extract generated files from the assistant's tool calls"""
        attachments: list[dict] = []
        _FILE_TOOLS = {"write_file", "save_file", "create_file", "download_file"}
        _MEDIA_EXTENSIONS = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".svg",
            ".mp4",
            ".webm",
            ".mov",
            ".avi",
            ".mp3",
            ".wav",
            ".ogg",
            ".flac",
            ".pdf",
            ".docx",
            ".xlsx",
            ".pptx",
            ".csv",
        }
        import mimetypes as _mt

        for tc in tool_calls:
            name = tc.get("name", tc.get("function", {}).get("name", ""))
            args = tc.get("arguments", tc.get("function", {}).get("arguments", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            if name in _FILE_TOOLS:
                path = args.get("path", args.get("file_path", ""))
                if path:
                    mime = _mt.guess_type(path)[0] or "application/octet-stream"
                    attachments.append(
                        {
                            "filename": Path(path).name,
                            "local_path": path,
                            "mime_type": mime,
                            "direction": "outbound",
                        }
                    )

        for tr in tool_results:
            result_str = str(tr.get("result", tr.get("content", "")))
            for token in result_str.split():
                p = Path(token)
                if p.suffix.lower() in _MEDIA_EXTENSIONS and len(token) < 500:
                    mime = _mt.guess_type(token)[0] or "application/octet-stream"
                    attachments.append(
                        {
                            "filename": p.name,
                            "local_path": token,
                            "mime_type": mime,
                            "direction": "outbound",
                        }
                    )

        seen = set()
        unique = []
        for a in attachments:
            key = a.get("local_path") or a.get("filename", "")
            if key and key not in seen:
                seen.add(key)
                unique.append(a)
        return unique

    async def _compress_context(
        self,
        messages: list[dict],
        max_tokens: int = None,
        system_prompt: str = None,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """Delegates to the unified context_manager.compress_if_needed()."""
        _sp = system_prompt or getattr(self._context, "system", "")
        _tools = getattr(self, "_tools", None)
        _conv_id = conversation_id or getattr(self, "_current_session_id", None)
        _msg_count_before = len(messages)
        result = await self.context_manager.compress_if_needed(
            messages,
            system_prompt=_sp,
            tools=_tools,
            max_tokens=max_tokens,
            memory_manager=self.memory_manager,
            conversation_id=_conv_id,
        )
        if len(result) != _msg_count_before:
            logger.info(
                f"[Compress] Delegated: {_msg_count_before} → {len(result)} msgs "
                f"(system_prompt={'custom' if system_prompt else 'default'}, "
                f"tools={len(_tools) if _tools else 0})"
            )
        return result

    async def _compress_large_tool_results(
        self, messages: list[dict], threshold: int = LARGE_TOOL_RESULT_THRESHOLD
    ) -> list[dict]:
        """Compress oversized tool_result / tool_use.input via LLM summarization.

        Scan one by one; for any tool_result with tokens > threshold, compress to a terse summary via LLM,
        preserving structure (role/type remain unchanged).
        """
        from .tool_executor import OVERFLOW_MARKER

        result = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                new_content = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        raw_content = item.get("content", "")
                        if isinstance(raw_content, list):
                            text_parts = [
                                p.get("text", "")
                                for p in raw_content
                                if isinstance(p, dict) and p.get("type") == "text"
                            ]
                            result_text = "\n".join(text_parts)
                        else:
                            result_text = str(raw_content)
                        if OVERFLOW_MARKER in result_text:
                            new_content.append(item)
                            continue
                        result_tokens = self._estimate_tokens(result_text)
                        if result_tokens > threshold:
                            target_tokens = max(int(result_tokens * COMPRESSION_RATIO), 100)
                            compressed_text = await self._llm_compress_text(
                                result_text, target_tokens, context_type="tool_result"
                            )
                            new_item = dict(item)
                            new_item["content"] = compressed_text
                            new_content.append(new_item)
                            logger.info(
                                f"Compressed tool_result from {result_tokens} to "
                                f"~{self._estimate_tokens(compressed_text)} tokens"
                            )
                        else:
                            new_content.append(item)
                    elif isinstance(item, dict) and item.get("type") == "tool_use":
                        input_text = json.dumps(item.get("input", {}), ensure_ascii=False)
                        input_tokens = self._estimate_tokens(input_text)
                        if input_tokens > threshold:
                            target_tokens = max(int(input_tokens * COMPRESSION_RATIO), 100)
                            compressed_input = await self._llm_compress_text(
                                input_text, target_tokens, context_type="tool_input"
                            )
                            new_item = dict(item)
                            new_item["input"] = {"compressed_summary": compressed_input}
                            new_content.append(new_item)
                            logger.info(
                                f"Compressed tool_use input from {input_tokens} to "
                                f"~{self._estimate_tokens(compressed_input)} tokens"
                            )
                        else:
                            new_content.append(item)
                    else:
                        new_content.append(item)
                result.append({**msg, "content": new_content})
            else:
                result.append(msg)
        return result

    async def _cancellable_await(self, coro, cancel_event: asyncio.Event | None = None):
        """Wrap an arbitrary coroutine so it can be interrupted immediately by cancel_event.

        If cancel_event fires before coro completes, raise UserCancelledError.
        If cancel_event is None or the task has no active asyncio.Task, just await coro directly.
        """
        if cancel_event is None:
            if self.agent_state and self.agent_state.current_task:
                cancel_event = self.agent_state.current_task.cancel_event
            else:
                return await coro

        task = asyncio.create_task(coro) if not isinstance(coro, asyncio.Task) else coro
        cancel_waiter = asyncio.create_task(cancel_event.wait())

        done, pending = await asyncio.wait(
            {task, cancel_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        if task in done:
            return task.result()
        raise UserCancelledError(
            reason=self._cancel_reason or "user requested stop",
            source="cancellable_await",
        )

    async def _llm_compress_text(
        self, text: str, target_tokens: int, context_type: str = "general"
    ) -> str:
        """
        Use the LLM to compress a block of text to the target token count

        Args:
            text: Text to compress
            target_tokens: Target token count
            context_type: Context type (tool_result/tool_input/conversation)

        Returns:
            Compressed text
        """
        # If the text itself exceeds the LLM's context budget, hard-truncate it first
        max_input = CHUNK_MAX_TOKENS * CHARS_PER_TOKEN
        if len(text) > max_input:
            # Keep the head and tail; truncate the middle
            head_size = int(max_input * 0.6)
            tail_size = int(max_input * 0.3)
            text = text[:head_size] + "\n...(middle content too long, omitted)...\n" + text[-tail_size:]

        target_chars = target_tokens * CHARS_PER_TOKEN

        if context_type == "tool_result":
            system_prompt = (
                "You are an information-compression assistant. Compress the following tool execution result into a concise summary, "
                "keep key data, status codes, error messages, and important output; drop redundant detail."
            )
        elif context_type == "tool_input":
            system_prompt = (
                "You are an information-compression assistant. Compress the following tool-call arguments into a concise summary, "
                "keep key parameter names and values; drop redundant content."
            )
        else:
            system_prompt = (
                "You are a conversation-compression assistant. Compress the following conversation into a concise summary, "
                "keep user intent, key decisions, execution results, and current state."
            )

        _tt = set_tracking_context(
            TokenTrackingContext(
                operation_type="context_compress",
                operation_detail=context_type,
            )
        )
        try:
            response = await self._cancellable_await(
                self.brain.messages_create_async(
                    model=self.brain.model,
                    max_tokens=target_tokens,
                    system=system_prompt,
                    messages=[
                        {
                            "role": "user",
                            "content": f"Compress the following to within {target_chars} characters:\n\n{text}",
                        }
                    ],
                    use_thinking=False,
                )
            )

            summary = ""
            for block in response.content:
                if block.type == "text":
                    summary += block.text
                elif block.type == "thinking" and hasattr(block, "thinking"):
                    # thinking-block fallback: when the model puts the summary inside thinking
                    if not summary:
                        summary = (
                            block.thinking
                            if isinstance(block.thinking, str)
                            else str(block.thinking)
                        )

            # If it's still empty, log a warning and fall back to hard truncation
            if not summary.strip():
                logger.warning(
                    f"[Compress] LLM returned empty summary (tokens_out={response.usage.output_tokens}), "
                    f"falling back to hard truncation"
                )
                if len(text) > target_chars:
                    head = int(target_chars * 0.7)
                    tail = int(target_chars * 0.2)
                    return text[:head] + "\n...(compression failed, truncated)...\n" + text[-tail:]
                return text

            return summary.strip()

        except UserCancelledError:
            raise
        except Exception as e:
            logger.warning(f"LLM compression failed: {e}")
            if len(text) > target_chars:
                head = int(target_chars * 0.7)
                tail = int(target_chars * 0.2)
                return text[:head] + "\n...(compression failed, truncated)...\n" + text[-tail:]
            return text
        finally:
            reset_tracking_context(_tt)

    def _extract_message_text(self, msg: dict) -> str:
        """
        Extract text content from a message (including tool_use/tool_result structured info)

        Args:
            msg: Message dict

        Returns:
            Extracted text content
        """
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg.get("content", "")

        if isinstance(content, str):
            return f"{role}: {content}\n"

        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        texts.append(item.get("text", ""))
                    elif item.get("type") == "tool_use":
                        from .tool_executor import smart_truncate as _st

                        name = item.get("name", "unknown")
                        input_data = item.get("input", {})
                        input_summary = json.dumps(input_data, ensure_ascii=False)
                        input_summary, _ = _st(
                            input_summary, 3000, save_full=False, label="compress_input"
                        )
                        texts.append(f"[Tool call: {name}, args: {input_summary}]")
                    elif item.get("type") == "tool_result":
                        from .tool_executor import smart_truncate as _st

                        raw_content = item.get("content", "")
                        if isinstance(raw_content, list):
                            text_parts = [
                                p.get("text", "")
                                for p in raw_content
                                if isinstance(p, dict) and p.get("type") == "text"
                            ]
                            result_text = "\n".join(text_parts)
                        else:
                            result_text = str(raw_content)
                        result_text, _ = _st(
                            result_text, 10000, save_full=False, label="compress_result"
                        )
                        is_error = item.get("is_error", False)
                        status = "error" if is_error else "ok"
                        texts.append(f"[Tool result({status}): {result_text}]")
            if texts:
                return f"{role}: {' '.join(texts)}\n"

        return ""

    async def _summarize_messages_chunked(self, messages: list[dict], target_tokens: int) -> str:
        """
        Chunked LLM summarization for message lists

        Split messages into chunks of CHUNK_MAX_TOKENS and compress each via an independent LLM call,
        then concatenate the summaries. If the combined summary is still long, do one more aggregate-compression pass.

        Args:
            messages: List of messages to summarize
            target_tokens: Final target token count

        Returns:
            Summary text
        """
        if not messages:
            return ""

        # Convert messages to text and split into chunks
        chunks: list[str] = []
        current_chunk = ""
        current_chunk_tokens = 0

        for msg in messages:
            msg_text = self._extract_message_text(msg)
            msg_tokens = self._estimate_tokens(msg_text)

            if current_chunk_tokens + msg_tokens > CHUNK_MAX_TOKENS and current_chunk:
                chunks.append(current_chunk)
                current_chunk = msg_text
                current_chunk_tokens = msg_tokens
            else:
                current_chunk += msg_text
                current_chunk_tokens += msg_tokens

        if current_chunk:
            chunks.append(current_chunk)

        if not chunks:
            return ""

        logger.info(f"Splitting {len(messages)} messages into {len(chunks)} chunks for compression")

        # Compress each chunk independently
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            chunk_tokens = self._estimate_tokens(chunk)
            # Target per chunk = total target / number of chunks (even split)
            chunk_target = max(int(target_tokens / len(chunks)), 100)

            _tt2 = set_tracking_context(
                TokenTrackingContext(
                    operation_type="context_compress",
                    operation_detail=f"chunk_{i}",
                )
            )
            try:
                response = await self._cancellable_await(
                    self.brain.messages_create_async(
                        model=self.brain.model,
                        max_tokens=chunk_target,
                        system=(
                            "You are a conversation-compression assistant. Compress the following conversation segment into a concise summary.\n"
                            "Requirements:\n"
                            "1. Preserve the user's original intent and key instructions\n"
                            "2. Preserve tool-call names, key parameters, and results (success/failure/key output)\n"
                            "3. Preserve important state changes and decisions\n"
                            "4. Remove duplicates, redundant output, and intermediate details\n"
                            "5. Use concise prose; original formatting need not be preserved"
                        ),
                        messages=[
                            {
                                "role": "user",
                                "content": (
                                    f"Compress the following conversation segment (chunk {i + 1}/{len(chunks)}, "
                                    f"approx {chunk_tokens} tokens) to within {chunk_target * CHARS_PER_TOKEN} characters:\n\n"
                                    f"{chunk}"
                                ),
                            }
                        ],
                        use_thinking=False,
                    )
                )

                summary = ""
                for block in response.content:
                    if block.type == "text":
                        summary += block.text
                    elif block.type == "thinking" and hasattr(block, "thinking"):
                        # thinking-block fallback: when the model puts the summary inside thinking
                        if not summary:
                            summary = (
                                block.thinking
                                if isinstance(block.thinking, str)
                                else str(block.thinking)
                            )

                if not summary.strip():
                    # Summary is empty; fall back to hard truncation
                    logger.warning(
                        f"[Compress] Chunk {i + 1} returned empty summary, using hard truncation"
                    )
                    max_chars = chunk_target * CHARS_PER_TOKEN
                    if len(chunk) > max_chars:
                        chunk_summaries.append(
                            chunk[: max_chars // 2] + "\n...(summarization failed, truncated)...\n"
                        )
                    else:
                        chunk_summaries.append(chunk)
                else:
                    chunk_summaries.append(summary.strip())
                    logger.info(
                        f"Chunk {i + 1}/{len(chunks)}: {chunk_tokens} -> "
                        f"~{self._estimate_tokens(summary)} tokens"
                    )

            except UserCancelledError:
                raise
            except Exception as e:
                logger.warning(f"Failed to summarize chunk {i + 1}: {e}")
                max_chars = chunk_target * CHARS_PER_TOKEN
                if len(chunk) > max_chars:
                    chunk_summaries.append(chunk[: max_chars // 2] + "\n...(summarization failed, truncated)...\n")
                else:
                    chunk_summaries.append(chunk)
            finally:
                reset_tracking_context(_tt2)

        # Concatenate all chunk summaries
        combined = "\n---\n".join(chunk_summaries)
        combined_tokens = self._estimate_tokens(combined)

        # If the concatenation still exceeds 2x the target, run another aggregate pass
        if combined_tokens > target_tokens * 2 and len(chunks) > 1:
            logger.info(
                f"Combined summary still large ({combined_tokens} tokens), "
                f"doing final consolidation..."
            )
            combined = await self._llm_compress_text(
                combined, target_tokens, context_type="conversation"
            )

        return combined

    async def _compress_further(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """
        Recursive compression: reduce the number of recent groups kept and continue compressing (preserving tool-call/result pairing)

        Args:
            messages: Current message list
            max_tokens: Target token upper bound

        Returns:
            Compressed message list
        """
        current_tokens = self._estimate_messages_tokens(messages)

        if current_tokens <= max_tokens:
            return messages

        # Split at group boundaries, keeping the last 2 groups (fewer than _compress_context's MIN_RECENT_TURNS)
        groups = self._group_messages(messages)
        recent_group_count = min(2, len(groups))

        if len(groups) <= recent_group_count:
            # Only a few recent groups remain; do one last tool_result compression pass
            logger.warning("Cannot compress further, attempting final tool_result compression")
            return await self._compress_large_tool_results(messages, threshold=1000)

        early_groups = groups[:-recent_group_count]
        recent_groups = groups[-recent_group_count:]

        early_messages = [msg for group in early_groups for msg in group]
        recent_messages = [msg for group in recent_groups for msg in group]

        # Compress earlier messages via LLM
        early_tokens = self._estimate_messages_tokens(early_messages)
        target = max(int(early_tokens * COMPRESSION_RATIO), 100)
        summary = await self._summarize_messages_chunked(early_messages, target)

        compressed = ContextManager._inject_summary_into_recent(summary, recent_messages)

        compressed_tokens = self._estimate_messages_tokens(compressed)
        logger.info(
            f"Further compressed context from {current_tokens} to {compressed_tokens} tokens"
        )
        return compressed

    def _hard_truncate_if_needed(self, messages: list[dict], hard_limit: int) -> list[dict]:
        """
        Hard floor: when LLM compression still exceeds hard_limit, hard-truncate so we can submit to the API

        Strategy:
        1. Drop from the oldest messages first, keeping the most recent
        2. Enqueue the dropped messages onto the extraction queue to avoid losing them forever
        3. Character-truncate any remaining single message whose content is still too large
        4. Add a truncation notice so the model knows the context is incomplete
        """
        current_tokens = self._estimate_messages_tokens(messages)
        if current_tokens <= hard_limit:
            return messages

        logger.error(
            f"[HardTruncate] LLM compression insufficient! "
            f"Still {current_tokens} tokens > hard_limit {hard_limit}. "
            f"Applying hard truncation to guarantee API submission."
        )

        truncated = list(messages)
        dropped_messages: list[dict] = []
        while len(truncated) > 2 and self._estimate_messages_tokens(truncated) > hard_limit:
            removed = truncated.pop(0)
            dropped_messages.append(removed)
            removed_role = removed.get("role", "?")
            logger.warning(f"[HardTruncate] Dropped earliest message (role={removed_role})")

        if dropped_messages:
            from .context_manager import ContextManager

            ContextManager._enqueue_dropped_for_extraction(dropped_messages, self.memory_manager)

        # Strategy 2: if even 2 messages remain over-limit, character-truncate individual message content
        if self._estimate_messages_tokens(truncated) > hard_limit:
            max_chars_per_msg = (hard_limit * CHARS_PER_TOKEN) // max(len(truncated), 1)
            for i, msg in enumerate(truncated):
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > max_chars_per_msg:
                    keep_head = int(max_chars_per_msg * 0.7)
                    keep_tail = int(max_chars_per_msg * 0.2)
                    truncated[i] = {
                        **msg,
                        "content": (
                            content[:keep_head]
                            + "\n\n...[content too long, hard-truncated]...\n\n"
                            + content[-keep_tail:]
                        ),
                    }
                elif isinstance(content, list):
                    # For list-type content, truncate any oversized text block within
                    new_content = []
                    for item in content:
                        if isinstance(item, dict):
                            for key in ("text", "content"):
                                val = item.get(key, "")
                                if isinstance(val, str) and len(val) > max_chars_per_msg:
                                    keep_h = int(max_chars_per_msg * 0.7)
                                    keep_t = int(max_chars_per_msg * 0.2)
                                    item = dict(item)
                                    item[key] = val[:keep_h] + "\n...[hard truncation]...\n" + val[-keep_t:]
                        new_content.append(item)
                    truncated[i] = {**msg, "content": new_content}

        truncated.insert(
            0,
            {
                "role": "user",
                "content": (
                    "[context_note: earlier conversation has been auto-consolidated] Reply normally, keeping the same detail level and output quality."
                ),
            },
        )

        final_tokens = self._estimate_messages_tokens(truncated)
        logger.warning(
            f"[HardTruncate] Final: {final_tokens} tokens "
            f"(hard_limit={hard_limit}, messages={len(truncated)})"
        )
        return truncated

    async def chat(self, message: str, session_id: str | None = None) -> str:
        """
        Chat interface - delegates to chat_with_session() to reuse the full pipeline

        Internally creates/reuses a persistent CLI Session so the CLI gains the same capabilities as the IM channel:
        Prompt Compiler, advanced loop detection, Task Monitor, memory retrieval, context compression, etc.

        Args:
            message: user message
            session_id: optional session identifier (used for logging)

        Returns:
            Agent response
        """
        if not self._initialized:
            await self.initialize()

        # Lazily initialize the CLI Session (persistent for the Agent's lifetime)
        if not hasattr(self, "_cli_session") or self._cli_session is None:
            from ..sessions.session import Session

            self._cli_session = Session.create(channel="cli", chat_id="cli", user_id="user")
            self._cli_session.set_metadata("_memory_manager", self.memory_manager)

        # Mimic the Gateway's message-management flow: record the user message into the Session first
        self._cli_session.add_message("user", message)
        session_messages = self._cli_session.context.get_messages()

        # Delegate to the unified chat_with_session
        response = await self.chat_with_session(
            message=message,
            session_messages=session_messages,
            session_id=session_id or self._cli_session.id,
            session=self._cli_session,
            gateway=None,  # CLI has no Gateway
        )

        # Record the assistant response into the Session (tool-execution summary as a separate field)
        _cli_meta: dict = {}
        try:
            _cli_tool_summary = self.build_tool_trace_summary()
            if _cli_tool_summary:
                _cli_meta["tool_summary"] = _cli_tool_summary
        except Exception:
            pass
        self._cli_session.add_message("assistant", response, **_cli_meta)

        # Also update legacy attributes (backward compat: conversation_history, /status command, etc. depend on them)
        self._conversation_history.append(
            {"role": "user", "content": message, "timestamp": datetime.now().isoformat()}
        )
        self._conversation_history.append(
            {"role": "assistant", "content": response, "timestamp": datetime.now().isoformat()}
        )
        # Prevent memory leaks: cap _conversation_history size (keep the most recent 200 entries)
        _max_cli_history = 200
        if len(self._conversation_history) > _max_cli_history:
            self._conversation_history = self._conversation_history[-_max_cli_history:]

        return response

    # ==================== Session pipeline: shared prepare / finalize / entry ====================

    async def _prepare_session_context(
        self,
        message: str,
        session_messages: list[dict],
        session_id: str,
        session: Any,
        gateway: Any,
        conversation_id: str,
        *,
        attachments: list | None = None,
        mode: str = "agent",
    ) -> tuple[list[dict], str, "TaskMonitor", str, Any]:
        """
        Session pipeline - shared prepare stage.

        chat_with_session() and chat_with_session_stream() share this method,
        ensuring IM/Desktop take identical prepare logic.

        Steps:
        1. Memory session align
        2. IM context setup
        3. Agent state / log session setup
        4. Proactive engine update
        5. User turn memory record
        6. Trait mining
        7. Prompt Compiler (first stage of two-stage prompting)
        8. Plan-mode auto detection
        9. Task definition setup
        10. Message history build (including context boundary markers and multimodal/attachments)
        11. Context compression
        12. TaskMonitor creation

        Args:
            message: user message
            session_messages: Session's conversation history
            session_id: session ID (used for logging)
            session: Session object
            gateway: MessageGateway object
            conversation_id: stable conversation-thread ID
            attachments: Desktop Chat attachment list (optional)

        Returns:
            (messages, session_type, task_monitor, conversation_id, im_tokens)
        """
        # 1. Align the MemoryManager session
        # memory safe_id is derived uniformly from session.session_key, matching the im_channel fallback
        # and the query logic used by sessions/manager backfill.
        try:
            _memory_key = (
                session.session_key
                if session and hasattr(session, "session_key")
                else conversation_id
            )
            conversation_safe_id = _memory_key.replace(":", "__")
            conversation_safe_id = re.sub(r'[/\\+=%?*<>|"\x00-\x1f]', "_", conversation_safe_id)
            if getattr(self.memory_manager, "_current_session_id", None) != conversation_safe_id:
                self.memory_manager.start_session(conversation_safe_id)
                if hasattr(self, "_memory_handler"):
                    self._memory_handler.reset_guide()
                # 1.5 On a new session, clear the Scratchpad working memory to avoid cross-session leakage
                try:
                    store = getattr(self.memory_manager, "store", None)
                    if store and hasattr(store, "save_scratchpad"):
                        from ..memory.types import Scratchpad as _SpClear

                        store.save_scratchpad(_SpClear(user_id="default"))
                        logger.debug(
                            f"[Session] Cleared scratchpad for new conversation {conversation_id}"
                        )
                except Exception as _e:
                    logger.debug(f"[Session] Scratchpad clear failed (non-critical): {_e}")
        except Exception as e:
            logger.warning(f"[Memory] Failed to align memory session: {e}")

        # 2. IM context setup (coroutine-isolated)
        from .im_context import set_im_context

        im_tokens = set_im_context(
            session=session if gateway else None,
            gateway=gateway,
        )

        # 2.5 Inject memory_manager into session metadata (so extraction can be enqueued when the session is truncated)
        if session is not None:
            session.set_metadata("_memory_manager", self.memory_manager)

        # 3. Agent state / log session
        self._current_session = session
        self.agent_state.current_session = session

        from ..logging import get_session_log_buffer

        get_session_log_buffer().set_current_session(conversation_id)

        logger.info(f"[Session:{session_id}] User: {message}")

        # 4. Proactive engine: record user interaction time
        if hasattr(self, "proactive_engine") and self.proactive_engine:
            self.proactive_engine.update_user_interaction()

        # 5. User turn memory record
        self.memory_manager.record_turn("user", message)

        # 6. Trait mining
        if hasattr(self, "trait_miner") and self.trait_miner and self.trait_miner.brain:
            try:
                mined_traits = await asyncio.wait_for(
                    self.trait_miner.mine_from_message(message, role="user"),
                    timeout=10,
                )
                for trait in mined_traits:
                    store = getattr(self.memory_manager, "store", None)
                    if store:
                        existing = store.query_semantic(memory_type="persona_trait", limit=50)
                        found = False
                        for old in existing:
                            if old.content.startswith(f"{trait.dimension}="):
                                store.update_semantic(
                                    old.id,
                                    {
                                        "content": f"{trait.dimension}={trait.preference}",
                                        "importance_score": max(
                                            old.importance_score, trait.confidence
                                        ),
                                    },
                                )
                                found = True
                                break
                        if found:
                            continue
                    from ..memory.types import Memory, MemoryPriority, MemoryType

                    mem = Memory(
                        type=MemoryType.PERSONA_TRAIT,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"{trait.dimension}={trait.preference}",
                        source=trait.source,
                        tags=[f"dimension:{trait.dimension}", f"preference:{trait.preference}"],
                        importance_score=trait.confidence,
                    )
                    self.memory_manager.add_memory(mem)
                if mined_traits:
                    logger.debug(f"[TraitMiner] Mined {len(mined_traits)} traits from user message")
            except Exception as e:
                logger.debug(f"[TraitMiner] Mining failed (non-critical): {e}")

        # 7. IntentAnalyzer (unified intent analysis — all messages go through LLM)
        #    Sub-agents skip IntentAnalyzer: they receive structured task instructions
        #    from the parent, always TASK intent, always need tools.
        from .intent_analyzer import IntentAnalyzer, IntentResult, IntentType

        if self._is_sub_agent_call:
            _profile_hints = self._derive_tool_hints_from_profile()
            intent_result = IntentResult(
                intent=IntentType.TASK,
                confidence=1.0,
                task_definition=message[:600],
                task_type="action",
                tool_hints=_profile_hints,
                memory_keywords=[],
                force_tool=True,
                todo_required=False,
            )
            logger.info(
                f"[Session:{session_id}] Sub-agent: skipping IntentAnalyzer, "
                f"forced TASK intent, profile_tool_hints={_profile_hints}"
            )
        else:
            if not hasattr(self, "_intent_analyzer"):
                self._intent_analyzer = IntentAnalyzer(self.brain)

            # session_messages includes the current user message as the last entry,
            # so history exists if there are more than 1 message
            _has_history = len(session_messages) > 1

            try:
                intent_result = await asyncio.wait_for(
                    self._intent_analyzer.analyze(
                        message, session_context=None, has_history=_has_history
                    ),
                    timeout=30,
                )
            except (TimeoutError, Exception) as e:
                logger.warning(f"[Session:{session_id}] Intent analysis failed/timed out: {e}")
                from .intent_analyzer import _make_default

                intent_result = _make_default(message)

        self._current_intent = intent_result
        compiler_summary = intent_result.task_definition
        compiled_message = message
        logger.info(
            f"[Session:{session_id}] Intent: {intent_result.intent.value}, "
            f"task_type: {intent_result.task_type}, "
            f"tool_hints: {intent_result.tool_hints}, "
            f"memory_keywords: {intent_result.memory_keywords}"
        )

        # 8. Plan mode detection (Agent mode only -- Plan/Ask modes are controlled by the prompt and tool filtering)
        if mode in ("plan", "ask"):
            from ..tools.handlers.plan import require_todo_for_session

            require_todo_for_session(conversation_id, False)
        elif mode == "agent":
            from ..tools.handlers.plan import require_todo_for_session, should_require_todo

            has_multi_actions = should_require_todo(message)
            if intent_result.todo_required or has_multi_actions:
                require_todo_for_session(conversation_id, True)
                logger.info(f"[Session:{session_id}] Multi-step task detected, Plan required")

        # 9. Task definition setup
        self._current_task_definition = compiler_summary
        self._current_task_query = compiler_summary or message

        # 9.5 Topic-switch detection -- IM channels only (telegram/wechat/feishu, etc.)
        # Desktop/CLI skip topic detection, keeping the full conversation history and letting the LLM handle context itself.
        # Defensive shallow copy: _detect_topic_change may insert() boundary markers,
        # If we operated on the live reference to session.context.messages, boundary messages would accumulate indefinitely and cause
        # consecutive user-role messages -> API error / model confusion / repeated tool execution
        session_messages = list(session_messages)
        topic_changed = False
        _channel = getattr(session, "channel", None) if session else None
        _is_im = _channel and _channel not in ("cli", "desktop")
        if _is_im and session and len(session_messages) >= 4:
            try:
                topic_changed = await asyncio.wait_for(
                    self._detect_topic_change(session_messages, message, session),
                    timeout=10,
                )
            except (TimeoutError, Exception) as e:
                logger.warning(
                    f"[Session:{session_id}] Topic change detection failed/timed out: {e}"
                )
            if topic_changed:
                _boundary_msg = {
                    "role": "user",
                    "content": "[Context boundary]",
                    "timestamp": datetime.now().isoformat(),
                }
                # Insert the boundary marker at the second-to-last position of session_messages (before the current message)
                if session_messages and session_messages[-1].get("role") == "user":
                    session_messages.insert(-1, _boundary_msg)
                else:
                    session_messages.append(_boundary_msg)
                # Sync the topic-boundary index on the Session model
                if hasattr(session.context, "mark_topic_boundary"):
                    session.context.mark_topic_boundary()
                logger.info(
                    f"[Session:{session_id}] Topic change detected, inserted context boundary"
                )
                # Fire-and-forget: schedule extraction in background, never block the response path
                try:
                    import asyncio as _aio

                    _loop = _aio.get_running_loop()
                    _extraction_task = _loop.create_task(
                        self.memory_manager.extract_on_topic_change()
                    )
                    _extraction_task.add_done_callback(
                        lambda t: (
                            logger.info(
                                f"[Session:{session_id}] Topic-change extraction: {t.result()} memories"
                            )
                            if not t.cancelled() and t.exception() is None and t.result()
                            else (
                                logger.debug(
                                    f"[Session:{session_id}] Topic-change extraction failed: {t.exception()}"
                                )
                                if not t.cancelled() and t.exception()
                                else None
                            )
                        )
                    )
                    logger.info(
                        f"[Session:{session_id}] Topic-change extraction scheduled (background)"
                    )
                except Exception as _tc_err:
                    logger.debug(
                        f"[Session:{session_id}] Topic-change extraction scheduling failed: {_tc_err}"
                    )

        # 9.7 Sync the current task in Scratchpad (skip for CHAT intent to avoid overwriting task focus)
        _new_task = compiler_summary or message[:200]
        if _new_task and intent_result.intent != IntentType.CHAT:
            try:
                _sp_store = getattr(self.memory_manager, "store", None)
                if _sp_store:
                    from ..memory.types import Scratchpad as _Sp

                    _pad = _sp_store.get_scratchpad() or _Sp()
                    _old_focus = _pad.current_focus
                    if topic_changed and _old_focus:
                        _pad.active_projects = (
                            [f"[{datetime.now().strftime('%m-%d %H:%M')}] {_old_focus}"]
                            + _pad.active_projects
                        )[:5]
                    _pad.current_focus = _new_task
                    _pad.content = _pad.to_markdown()
                    _pad.updated_at = datetime.now()
                    _sp_store.save_scratchpad(_pad)
            except Exception as _sp_err:
                logger.debug(f"[Scratchpad] sync failed: {_sp_err}")

        # 10. Message history build
        # session_messages already contains the current turn's user message (add_message was called before gateway),
        # the current turn is appended separately via compiled_message below, so exclude the last entry to avoid duplication.
        history_messages = session_messages
        if history_messages and history_messages[-1].get("role") == "user":
            history_messages = history_messages[:-1]

        # Dedup: remove near-duplicate messages within a sliding window.
        # A pure global dedup would incorrectly remove legitimate repeated
        # short messages (e.g. user saying "ok" twice in different contexts).
        # Window-based dedup only catches retry/reconnection artifacts.
        _DEDUP_WINDOW = 6
        if len(history_messages) >= 2:
            import hashlib as _hl

            def _fp(m: dict) -> str:
                return _hl.md5(
                    f"{m.get('role', '')}:{(m.get('content', '') or '')[:200]}".encode(
                        errors="replace"
                    )
                ).hexdigest()

            deduped: list[dict] = []
            deduped_fps: list[str] = []
            for hm in history_messages:
                fp = _fp(hm)
                window_start = max(0, len(deduped_fps) - _DEDUP_WINDOW)
                if fp in deduped_fps[window_start:]:
                    continue
                deduped.append(hm)
                deduped_fps.append(fp)
            if len(deduped) < len(history_messages):
                logger.warning(
                    f"[Session:{session_id}] Removed {len(history_messages) - len(deduped)} "
                    f"near-duplicate messages from history (window={_DEDUP_WINDOW})"
                )
            history_messages = deduped

        _STRIP_MARKERS = ["\n\n[Sub-Agent work summary]", "\n\n[Execution summary]"]
        _RE_TIME_PREFIX = re.compile(r"^\[\d{1,2}:\d{2}\]\s")

        messages: list[dict] = []
        for msg in history_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            if role == "assistant":
                for _marker in _STRIP_MARKERS:
                    while _marker in content:
                        idx = content.index(_marker)
                        before = content[:idx]
                        after = content[idx + len(_marker) :]
                        next_section = -1
                        for sep in ("\n\n[", "\n\n##", "\n\n---"):
                            pos = after.find(sep)
                            if pos != -1 and (next_section == -1 or pos < next_section):
                                next_section = pos
                        content = before + after[next_section:] if next_section != -1 else before
                if content.startswith("[Execution summary]") or content.startswith("[Sub-Agent work summary]"):
                    content = ""
                # Restore tool_summary from metadata (cross-turn tool-context recovery)
                _tool_summary = msg.get("tool_summary")
                if _tool_summary and isinstance(_tool_summary, str) and content:
                    content = content.rstrip() + "\n\n" + _tool_summary
            if role in ("user", "assistant") and content:
                if ts and isinstance(content, str):
                    try:
                        t = datetime.fromisoformat(ts)
                        time_prefix = f"[{t.strftime('%H:%M')}] "
                        if not _RE_TIME_PREFIX.match(content):
                            content = time_prefix + content
                    except Exception:
                        pass
                if messages and messages[-1]["role"] == role:
                    messages[-1]["content"] += "\n" + content
                else:
                    messages.append({"role": role, "content": content})

        # 10.5 Inject sub-Agent delegation-result summary into the last assistant message
        if session and hasattr(session, "context"):
            sub_records = getattr(session.context, "sub_agent_records", None)
            if sub_records and messages:
                summary_parts = []
                for r in sub_records:
                    name = r.get("agent_name", "unknown")
                    preview = r.get("result_preview", "")
                    if preview:
                        summary_parts.append(f"- {name}: {preview[:500]}")
                if summary_parts:
                    delegation_summary = "\n\n[Delegated-task execution record]\n" + "\n".join(summary_parts)
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i]["role"] == "assistant":
                            messages[i]["content"] += delegation_summary
                            break

        # Context-continuation marker (merged as a prefix on the current user message to avoid fake assistant replies breaking conversation coherence)
        _has_history = bool(messages)
        logger.debug(
            f"[Session:{session_id}] _prepare_session_context: "
            f"{len(messages)} history msgs, has_history={_has_history}"
        )

        # Current user message (multimodal-capable)
        pending_images = session.get_metadata("pending_images") if session else None
        pending_videos = session.get_metadata("pending_videos") if session else None
        pending_audio = session.get_metadata("pending_audio") if session else None
        pending_files = session.get_metadata("pending_files") if session else None

        # Handle PDF/document files -- build a DocumentBlock if the LLM supports PDF, otherwise fall back to text extraction
        document_blocks = []
        if pending_files:
            llm_client_for_pdf = getattr(self.brain, "_llm_client", None)
            has_pdf_cap = (
                llm_client_for_pdf and llm_client_for_pdf.has_any_endpoint_with_capability("pdf")
            )
            for fdata in pending_files:
                if has_pdf_cap and fdata.get("type") == "document":
                    document_blocks.append(fdata)
                    logger.info(f"[Session:{session_id}] PDF → native DocumentBlock")
                else:
                    # Fallback: extract text content from the PDF
                    fname = fdata.get("filename", "unknown")
                    local_path = fdata.get("local_path", "")
                    extracted = ""
                    if local_path and Path(local_path).exists():
                        try:
                            from openakita.channels.media.handler import MediaHandler

                            _handler = MediaHandler()
                            extracted = await _handler._extract_pdf(Path(local_path))
                        except Exception as _ext_err:
                            logger.warning(
                                f"[Session:{session_id}] PDF text extraction failed: {_ext_err}"
                            )
                    if extracted and extracted.strip():
                        _PDF_TEXT_LIMIT = 80_000
                        if len(extracted) > _PDF_TEXT_LIMIT:
                            extracted = extracted[:_PDF_TEXT_LIMIT] + "\n...(document too long, truncated)"
                        compiled_message += (
                            f"\n\n--- PDF file: {fname} ---\n{extracted}\n--- End of file ---"
                        )
                        logger.info(
                            f"[Session:{session_id}] PDF → text fallback ({len(extracted)} chars)"
                        )
                    else:
                        compiled_message += f"\n[Document attachment: {fname}, local path: {local_path}]"
                        logger.warning(
                            f"[Session:{session_id}] PDF text extraction empty, path provided"
                        )

        # Three-tier audio decision: LLM-native audio > online STT > local Whisper
        audio_blocks = []
        if pending_audio:
            llm_client = getattr(self.brain, "_llm_client", None)
            has_audio_cap = llm_client and llm_client.has_any_endpoint_with_capability("audio")

            if has_audio_cap:
                # Tier 1: LLM-native audio input
                for aud in pending_audio:
                    local_path = aud.get("local_path", "")
                    if local_path and Path(local_path).exists():
                        try:
                            from ..channels.media.audio_utils import ensure_llm_compatible

                            compat_path = ensure_llm_compatible(local_path)
                            audio_blocks.append(
                                {
                                    "type": "audio",
                                    "source": {
                                        "type": "base64",
                                        "media_type": aud.get("mime_type", "audio/wav"),
                                        "data": base64.b64encode(
                                            Path(compat_path).read_bytes()
                                        ).decode("utf-8"),
                                        "format": Path(compat_path).suffix.lstrip(".") or "wav",
                                    },
                                }
                            )
                            logger.info(f"[Session:{session_id}] Audio → native AudioBlock")
                        except Exception as e:
                            logger.error(f"[Session:{session_id}] Failed to build AudioBlock: {e}")
            else:
                # Tier 2: online STT (if available)
                stt_client = None
                im_gateway = gateway or (session.get_metadata("_gateway") if session else None)
                if im_gateway and hasattr(im_gateway, "stt_client"):
                    stt_client = im_gateway.stt_client

                if stt_client and stt_client.is_available:
                    for aud in pending_audio:
                        local_path = aud.get("local_path", "")
                        existing_transcription = aud.get("transcription")
                        if existing_transcription:
                            continue  # Whisper result already present; do not call again
                        if local_path and Path(local_path).exists():
                            try:
                                stt_result = await stt_client.transcribe(local_path)
                                if stt_result:
                                    if not compiled_message.strip() or "[语音:" in compiled_message:
                                        compiled_message = stt_result
                                    else:
                                        compiled_message = f"{compiled_message}\n\n[Voice content (online STT): {stt_result}]"
                                    media_ref = aud.get("_media_ref")
                                    if media_ref is not None:
                                        media_ref.transcription = stt_result
                                    logger.info(
                                        f"[Session:{session_id}] Audio → online STT: {stt_result[:50]}..."
                                    )
                            except Exception as e:
                                logger.warning(f"[Session:{session_id}] Online STT failed: {e}")
                # Tier 3: local Whisper (already handled by Gateway; transcription is already in input_text)
                # No additional action needed

        if _has_history and compiled_message and isinstance(compiled_message, str):
            compiled_message = f"[Latest message]\n{compiled_message}"

        # === Role-alternation protection ===
        # If the end of history is a user message (typically produced by a context boundary marker),
        # merge its text as a prefix on the current message to avoid consecutive same-role messages causing API errors or model confusion
        if messages and messages[-1]["role"] == "user":
            _trailing_user = messages.pop()
            _trailing_text = _trailing_user.get("content", "")
            if isinstance(_trailing_text, str) and _trailing_text:
                compiled_message = _trailing_text + "\n" + compiled_message
            elif _trailing_text:
                # Non-string content (e.g. multimodal list) cannot be merged; restore it in place
                messages.append(_trailing_user)

        # Desktop Chat attachment handling (aligned with IM's pending_images)
        if attachments and not pending_images:
            _desk_llm_client = getattr(self.brain, "_llm_client", None)
            _desk_has_vision = (
                _desk_llm_client and _desk_llm_client.has_any_endpoint_with_capability("vision")
            )
            _desk_has_video = (
                _desk_llm_client and _desk_llm_client.has_any_endpoint_with_capability("video")
            )

            content_blocks: list[dict] = []
            _degraded_notices: list[str] = []
            if compiled_message:
                content_blocks.append({"type": "text", "text": compiled_message})
            for att in attachments:
                att_type = getattr(att, "type", None) or ""
                att_url = getattr(att, "url", None) or ""
                att_name = getattr(att, "name", None) or "file"
                att_mime = getattr(att, "mime_type", None) or att_type

                is_image = (
                    att_type == "image"
                    or (att_mime or "").startswith("image/")
                    or (att_url or "").startswith("data:image/")
                )
                is_video = (
                    att_type == "video"
                    or (att_mime or "").startswith("video/")
                    or (att_url or "").startswith("data:video/")
                )

                if is_image and att_url:
                    if _desk_has_vision:
                        content_blocks.append({"type": "image_url", "image_url": {"url": att_url}})
                    else:
                        _degraded_notices.append(
                            f"[User sent image {att_name}; the current model does not support image input]"
                        )
                elif is_video and att_url:
                    if _desk_has_video:
                        content_blocks.append({"type": "video_url", "video_url": {"url": att_url}})
                    else:
                        _degraded_notices.append(
                            f"[User sent video {att_name}; the current model does not support video input]"
                        )
                elif att_type == "document" and att_url:
                    content_blocks.append(
                        {
                            "type": "text",
                            "text": f"[Document: {att_name} ({att_mime})] URL: {att_url}",
                        }
                    )
                elif att_url:
                    content_blocks.append(
                        {
                            "type": "text",
                            "text": f"[Attachment: {att_name} ({att_mime})] URL: {att_url}",
                        }
                    )

            if _degraded_notices:
                content_blocks.append(
                    {
                        "type": "text",
                        "text": "\n".join(_degraded_notices),
                    }
                )
                logger.info(
                    "[Session:%s] Desktop attachments degraded: vision=%s video=%s, %d notice(s)",
                    session_id,
                    _desk_has_vision,
                    _desk_has_video,
                    len(_degraded_notices),
                )

            if content_blocks:
                messages.append({"role": "user", "content": content_blocks})
            elif compiled_message:
                messages.append({"role": "user", "content": compiled_message})
        elif pending_images or pending_videos or audio_blocks or document_blocks:
            # IM path: multimodal (images + videos + audio + documents)
            # Align with the audio/PDF pattern: check capability first, fall back to text when unavailable
            content_parts: list[dict] = []
            _text_for_llm = compiled_message.strip()

            llm_client = getattr(self.brain, "_llm_client", None)
            has_vision = llm_client and llm_client.has_any_endpoint_with_capability("vision")
            has_video = llm_client and llm_client.has_any_endpoint_with_capability("video")

            embed_images = pending_images if has_vision else None
            embed_videos = pending_videos if has_video else None

            # Image placeholder substitution (only switch to 'please view directly' when actually embedded)
            _is_img_placeholder = _text_for_llm and re.fullmatch(
                r"(\[图片: [^\]]+\]\s*)+", _text_for_llm
            )
            if pending_images and _is_img_placeholder:
                if embed_images:
                    _text_for_llm = (
                        f"User sent {len(pending_images)} image(s)"
                        "(attached to the message; please view directly)."
                        "Please describe or respond to what you see in the images."
                    )
                else:
                    _text_for_llm = ""

            # Video placeholder substitution
            _is_vid_placeholder = _text_for_llm and re.fullmatch(
                r"(\[视频: [^\]]+\]\s*)+", _text_for_llm
            )
            if pending_videos and _is_vid_placeholder:
                if embed_videos:
                    _text_for_llm = (
                        f"User sent {len(pending_videos)} video(s)"
                        "(attached to the message; please view directly)."
                        "Please describe or respond to what you see in the videos."
                    )
                else:
                    _text_for_llm = ""

            # Image fallback notice
            if pending_images and not has_vision:
                img_paths = [
                    img.get("local_path", "") for img in pending_images if img.get("local_path")
                ]
                notice = f"[User sent {len(pending_images)} image(s); the current model does not support image input"
                if img_paths:
                    notice += (
                        f". File path: {'; '.join(img_paths)}"
                        f". To view image content, use the view_image tool"
                    )
                notice += "]"
                _text_for_llm = f"{_text_for_llm}\n\n{notice}" if _text_for_llm else notice
                logger.info(
                    f"[Session:{session_id}] No vision endpoint, "
                    f"degrading {len(pending_images)} images to text notice"
                )

            # Video fallback notice
            if pending_videos and not has_video:
                vid_paths = [v.get("local_path", "") for v in pending_videos if v.get("local_path")]
                notice = f"[User sent {len(pending_videos)} video(s); the current model does not support video input"
                if vid_paths:
                    notice += f". File path: {'; '.join(vid_paths)}"
                notice += "]"
                _text_for_llm = f"{_text_for_llm}\n\n{notice}" if _text_for_llm else notice
                logger.info(
                    f"[Session:{session_id}] No video endpoint, "
                    f"degrading {len(pending_videos)} videos to text notice"
                )

            # Assemble content_parts
            if _text_for_llm:
                content_parts.append({"type": "text", "text": _text_for_llm})
            if embed_images:
                content_parts.extend(embed_images)
            if embed_videos:
                content_parts.extend(embed_videos)
            if audio_blocks:
                content_parts.extend(audio_blocks)
            if document_blocks:
                content_parts.extend(document_blocks)

            # If all media was demoted to text, send a plain-text message instead of a multimodal list
            has_media = embed_images or embed_videos or audio_blocks or document_blocks
            if has_media:
                messages.append({"role": "user", "content": content_parts})
            else:
                plain = _text_for_llm or compiled_message
                messages.append({"role": "user", "content": plain})

            media_info = []
            if embed_images:
                media_info.append(f"{len(embed_images)} images")
            if embed_videos:
                media_info.append(f"{len(embed_videos)} videos")
            if audio_blocks:
                media_info.append(f"{len(audio_blocks)} audio")
            if document_blocks:
                media_info.append(f"{len(document_blocks)} documents")
            if media_info:
                logger.info(
                    f"[Session:{session_id}] Multimodal message with {', '.join(media_info)}"
                )
        else:
            # Plain text message
            messages.append({"role": "user", "content": compiled_message})

        # 10.5. Record incoming attachments (images/videos/files) to memory
        self._record_inbound_attachments(
            session_id,
            pending_images,
            pending_videos,
            pending_audio,
            pending_files,
            attachments,
        )

        # 11. Context compression
        messages = await self._compress_context(messages)

        # 12. TaskMonitor creation
        task_monitor = TaskMonitor(
            task_id=f"{session_id}_{datetime.now().strftime('%H%M%S')}",
            description=message,
            session_id=session_id,
            timeout_seconds=settings.progress_timeout_seconds,
            hard_timeout_seconds=settings.hard_timeout_seconds,
            retrospect_threshold=180,
            fallback_model=self.brain.get_fallback_model(session_id),
        )
        task_monitor.start(self.brain.model)
        self._current_task_monitor = task_monitor

        # session_type detection
        # The desktop chat panel and the CLI are both local interactions, so ForceToolCall acceptance should be enabled
        # Only actual IM channels (telegram/wechat/feishu, etc.) use im mode
        _channel = getattr(session, "channel", None) if session else None
        session_type = "im" if _channel and _channel not in ("cli", "desktop") else "cli"
        self._current_session_type = session_type

        return messages, session_type, task_monitor, conversation_id, im_tokens

    async def _finalize_session(
        self,
        response_text: str,
        session: Any,
        session_id: str,
        task_monitor: "TaskMonitor",
    ) -> None:
        """
        Session pipeline - shared finalize stage.

        chat_with_session() and chat_with_session_stream() share this method.

        Steps:
        1. Write the react_trace summary to session metadata (used by IM)
        2. Finalize TaskMonitor + background retrospective
        3. Record assistant responses to memory
        4. Clean up transient state
        """
        # 0. Snapshot the current trace (prevents concurrent sessions from overwriting _last_react_trace)
        _trace_snapshot = list(getattr(self.reasoning_engine, "_last_react_trace", None) or [])
        self._last_finalized_trace = _trace_snapshot

        # 0b. Extract a lightweight token-usage summary (readable by SSE/API after cleanup)
        self._last_usage_summary = self._extract_usage_summary(_trace_snapshot)

        # 1. Reasoning-chain summary -> session metadata
        if session:
            try:
                chain_summary = self._build_chain_summary(_trace_snapshot)
                if chain_summary:
                    session.set_metadata("_last_chain_summary", chain_summary)
            except Exception as e:
                logger.debug(f"[ChainSummary] Failed to build chain summary: {e}")

        # 2. TaskMonitor complete + retrospect
        metrics = task_monitor.complete(success=True, response=response_text)
        if metrics.retrospect_needed:
            asyncio.create_task(self._do_task_retrospect_background(task_monitor, session_id))
            logger.info(f"[Session:{session_id}] Task retrospect scheduled (background)")

        # 3. Memory: record the assistant response (including tool-call data)
        _trace = _trace_snapshot
        _all_tool_calls: list[dict] = []
        _all_tool_results: list[dict] = []
        for _it in _trace:
            _all_tool_calls.extend(_it.get("tool_calls", []))
            _all_tool_results.extend(_it.get("tool_results", []))
        logger.debug(
            f"[Session:{session_id}] record_turn: "
            f"text={len(response_text)} chars, "
            f"tool_calls={len(_all_tool_calls)}, tool_results={len(_all_tool_results)}, "
            f"trace_iterations={len(_trace)}"
        )
        outbound_attachments = self._extract_outbound_attachments(
            _all_tool_calls, _all_tool_results
        )
        self.memory_manager.record_turn(
            "assistant",
            response_text,
            tool_calls=_all_tool_calls,
            tool_results=_all_tool_results,
            attachments=outbound_attachments or None,
        )
        try:
            logger.info(f"[Session:{session_id}] Agent: {response_text}")
        except (UnicodeEncodeError, OSError):
            logger.info(
                f"[Session:{session_id}] Agent: (response logged, {len(response_text)} chars)"
            )

        # 4. Auto-close any unfinished Plan
        # Fallback in case the LLM did not explicitly call complete_todo:
        # - Mark remaining step statuses (in_progress -> completed, pending -> skipped)
        # - Save and unregister the Plan
        # Note: do not close the Plan on ask_user exit (execution resumes after the user replies)
        # Note: do not close the Plan on sub-Agent calls (the Plan belongs to the parent Agent)
        exit_reason = getattr(self.reasoning_engine, "_last_exit_reason", "normal")
        is_sub_agent = getattr(self, "_is_sub_agent_call", False)
        if exit_reason != "ask_user" and not is_sub_agent:
            conversation_id = getattr(self, "_current_conversation_id", "") or session_id
            try:
                from ..tools.handlers.plan import auto_close_todo

                if auto_close_todo(conversation_id):
                    logger.info(f"[Session:{session_id}] Todo auto-closed at finalize")
            except Exception as e:
                logger.debug(f"[Todo] auto_close_todo failed: {e}")

            # End the memory session promptly to trigger memory extraction
            try:
                task_desc = (getattr(self, "_current_task_query", "") or "").strip()[:200]
                self.memory_manager.end_session(task_desc, success=True)
                logger.debug(f"[Session:{session_id}] memory_manager.end_session() called")
            except Exception as e:
                logger.debug(f"[Session:{session_id}] memory end_session failed: {e}")

        # 5. Cleanup (always runs; placed in finally by the caller)
        # Note: this method does not perform cleanup; cleanup is centralized in _cleanup_session_state()

    def _cleanup_session_state(self, im_tokens: Any) -> None:
        """
        Session pipeline - state cleanup (always invoked from finally).

        im_tokens may be None (when _prepare_session_context throws before/after step 2),
        any leftover contextvar is overwritten by the next set_im_context, so it's fine to skip the reset here.
        """
        self._current_task_definition = ""
        self._current_task_query = ""
        self._current_session_type = "cli"
        if im_tokens is not None:
            with contextlib.suppress(Exception):
                from .im_context import reset_im_context

                reset_im_context(im_tokens)
        self._current_session = None
        self.agent_state.current_session = None
        self._current_task_monitor = None
        # Reset task state so that cancelled/completed tasks don't leak into the next session
        _sid = self._current_session_id
        _conv_id = self._current_conversation_id
        _cleaned = set()
        for _key in (_sid, _conv_id):
            if not _key or _key in _cleaned:
                continue
            _task = self.agent_state.get_task_for_session(_key) if self.agent_state else None
            if _task and not _task.is_active:
                self.agent_state.reset_task(session_id=_key)
                _cleaned.add(_key)
        if not _cleaned and self.agent_state:
            _ct = self.agent_state.current_task
            if _ct and not _ct.is_active:
                _ct_key = _ct.session_id or _ct.task_id
                self.agent_state.reset_task(session_id=_ct_key)

        # P1-7: clean up PolicyEngine session state + ToolExecutor pending-approval cache
        try:
            from .policy import get_policy_engine

            _pe = get_policy_engine()
            for _clean_id in (_sid, _conv_id):
                if _clean_id:
                    _pe.cleanup_session(_clean_id)
        except Exception:
            pass
        if hasattr(self, "tool_executor") and hasattr(self.tool_executor, "_pending_confirms"):
            self.tool_executor._pending_confirms.clear()

        # Clean up task-local session references to prevent dict growth
        if _sid:
            self._pending_cancels.pop(_sid, None)
        if self._current_conversation_id:
            self._pending_cancels.pop(self._current_conversation_id, None)
        self._current_session_id = None
        self._current_conversation_id = None

        # Clean up Plan/Todo module-level state to prevent handler memory leaks
        for _clean_id in (_sid, _conv_id):
            if _clean_id:
                try:
                    from ..tools.handlers.plan import clear_session_todo_state

                    clear_session_todo_state(_clean_id)
                except Exception:
                    pass

        # Release large residual objects from the reasoning engine (working_messages / checkpoints),
        # working_messages can hold tens of MB of tool results (base64 screenshots, web content, etc.)
        # Note: do not clean _last_finalized_trace; it's read by the orchestrator/SSE
        # and will be overwritten naturally on the next _finalize_session
        if hasattr(self, "reasoning_engine"):
            self.reasoning_engine.release_large_buffers()

    async def chat_with_session(
        self,
        message: str,
        session_messages: list[dict],
        session_id: str = "",
        session: Any = None,
        gateway: Any = None,
        *,
        mode: str = "agent",
        endpoint_override: str | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
    ) -> str:
        """
        Chat using externally-provided Session history (for IM / CLI channels).

        Runs the full Agent pipeline: Prompt Compiler -> context build -> ReasoningEngine.run().
        Shares _prepare_session_context / _finalize_session with chat_with_session_stream().

        Args:
            message: user message
            session_messages: Session's conversation history
            session_id: session ID
            session: Session object
            gateway: MessageGateway object
            mode: interaction mode (ask/plan/agent), defaults to agent
            endpoint_override: endpoint override (uses _preferred_endpoint when None)
            thinking_mode: thinking-mode override ('auto'/'on'/'off'/None)
            thinking_depth: thinking depth ('low'/'medium'/'high'/None)

        Returns:
            Agent response
        """
        if not self._initialized:
            await self.initialize()

        endpoint_override = endpoint_override or self._preferred_endpoint

        # === Stop-command detection ===
        message_lower = message.strip().lower()
        if message_lower in self.STOP_COMMANDS or message.strip() in self.STOP_COMMANDS:
            self.cancel_current_task(f"User sent stop command: {message}", session_id=session_id)
            logger.info(f"[StopTask] User requested to stop (session={session_id}): {message}")
            return "✅ Got it, current task stopped. Anything else I can help with?"

        # Resolve conversation_id (done early so cleanup uses the right key)
        self._current_session_id = session_id
        conversation_id = self._resolve_conversation_id(session, session_id)
        self._current_conversation_id = conversation_id

        # Clean up leftover task state from the previous turn (session-scoped)
        _prev_task = None
        _reset_key = session_id
        if self.agent_state:
            _prev_task = self.agent_state.get_task_for_session(session_id)
        if not _prev_task and self.agent_state:
            _prev_task = self.agent_state.current_task
            if _prev_task:
                _reset_key = _prev_task.session_id or _prev_task.task_id
        if _prev_task:
            if _prev_task.cancelled or not _prev_task.is_active:
                logger.info(
                    f"[Session:{session_id}] Resetting stale task "
                    f"(cancelled={_prev_task.cancelled}, status={_prev_task.status.value}, "
                    f"reset_key={_reset_key!r})"
                )
                self.agent_state.reset_task(session_id=_reset_key)
            else:
                _prev_task.clear_skip()
                await _prev_task.drain_user_inserts()

        # Clear leftover pending_cancels from the previous turn (a disconnect watcher may write them after cleanup)
        self._pending_cancels.pop(session_id, None) if session_id else None

        # User proactively sends a new message -> unconditionally clear all endpoint cooldowns so last turn's errors don't block this one
        llm_client = getattr(self.brain, "_llm_client", None)
        if llm_client:
            llm_client.reset_all_cooldowns(force_all=True)

        im_tokens = None
        try:
            # Pre-prepare check: only catches cancel signals that arrive right before prepare begins
            if self._is_session_cancelled(session_id):
                self._consume_pending_cancel(session_id)
                logger.info(
                    f"[Session:{session_id}] Cancelled before prepare, returning immediately"
                )
                return "✅ Got it, current task stopped."

            # === Shared prepare ===
            (
                messages,
                session_type,
                task_monitor,
                conversation_id,
                im_tokens,
            ) = await self._prepare_session_context(
                message=message,
                session_messages=session_messages,
                session_id=session_id,
                session=session,
                gateway=gateway,
                conversation_id=conversation_id,
            )

            # Post-prepare check (including pending cancel)
            _conv_cancel_id = conversation_id or session_id
            if self._is_session_cancelled(session_id) or self._is_session_cancelled(
                _conv_cancel_id
            ):
                self._consume_pending_cancel(session_id)
                self._consume_pending_cancel(_conv_cancel_id)
                logger.info(
                    f"[Session:{session_id}] Cancelled during prepare, returning immediately"
                )
                return "✅ Got it, current task stopped."

            # === Read thinking preference from session metadata (used by the IM channel) ===
            _thinking_mode = thinking_mode
            _thinking_depth = thinking_depth
            if session and (_thinking_mode is None or _thinking_depth is None):
                try:
                    if _thinking_mode is None:
                        _thinking_mode = session.get_metadata("thinking_mode")
                    if _thinking_depth is None:
                        _thinking_depth = session.get_metadata("thinking_depth")
                except Exception:
                    pass

            # === Build the IM reasoning-chain progress callback ===
            # Controlled by the im_chain_push switch: off by default to reduce noise; does not affect internal trace saving
            _progress_cb = None
            if gateway and session:
                _chain_push = session.get_metadata("chain_push")
                if _chain_push is None:
                    _chain_push = settings.im_chain_push
                if _chain_push:

                    async def _im_chain_progress(text: str) -> None:
                        try:
                            await gateway.emit_progress_event(session, text)
                        except Exception:
                            pass

                    _progress_cb = _im_chain_progress

            # === Intent-driven routing ===
            from .intent_analyzer import IntentType as _IT

            _intent = getattr(self, "_current_intent", None)
            _fast_usage = None
            _fast_handled = False

            if _intent and _intent.intent == _IT.CHAT and getattr(_intent, "fast_reply", False):
                # Ultra-fast path: rule-based greeting only, use lightweight model
                try:
                    _identity_snippet = ""
                    if hasattr(self, "identity") and hasattr(self.identity, "get_system_prompt"):
                        _identity_snippet = (
                            self.identity.get_system_prompt(include_active_task=False) or ""
                        )[:500]

                    _fast_system = (
                        f"{_identity_snippet}\n\n"
                        "The user sent a short greeting/confirmation message. Reply briefly in your persona's style; "
                        "do not use any tools; do not over-explain. Keep it light and natural, 1-3 sentences is enough."
                    ).strip()

                    _fast_resp = await self.brain.think_lightweight(
                        prompt=message,
                        system=_fast_system,
                    )
                    _fast_usage = _fast_resp.usage
                    response_text = (
                        clean_llm_response(_fast_resp.content if _fast_resp.content else "")
                        or "Hello! How can I help you?"
                    )
                    _fast_handled = True
                except Exception as e:
                    logger.error(f"[FastReply] Failed: {e}")
                    response_text = "Hello! How can I help you?"
                    _fast_handled = True

            elif _intent and _intent.intent == _IT.QUERY and getattr(_intent, "fast_reply", False):
                # Fast-path for simple factual queries (math, date, definitions)
                # No tools passed → LLM answers directly
                try:
                    _runtime_info = ""
                    try:
                        from ..prompt.builder import _build_runtime_section

                        _runtime_info = _build_runtime_section() or ""
                    except Exception:
                        pass

                    _identity_snippet = ""
                    if hasattr(self, "identity") and hasattr(self.identity, "get_system_prompt"):
                        _identity_snippet = (
                            self.identity.get_system_prompt(include_active_task=False) or ""
                        )[:500]

                    _fast_system = (
                        f"{_identity_snippet}\n\n"
                        f"{_runtime_info}\n\n"
                        "The user asked a simple knowledge/math/date question."
                        "Give an accurate and concise answer directly. Do not use any tools."
                        "If it involves date/time, answer based on the runtime environment info above."
                    ).strip()

                    logger.info(f"[FastQuery] Answering '{message}' without tools")
                    _fast_resp = await self.brain.think_lightweight(
                        prompt=message,
                        system=_fast_system,
                    )
                    _fast_usage = _fast_resp.usage
                    response_text = clean_llm_response(
                        _fast_resp.content if _fast_resp.content else ""
                    )
                    if response_text:
                        _fast_handled = True
                    else:
                        logger.warning("[FastQuery] Empty response, falling back to full agent")
                except Exception as e:
                    logger.warning(f"[FastQuery] Failed ({e}), falling back to full agent")

            if not _fast_handled:
                # All non-fast paths, or fast_reply fallback → ReasoningEngine
                response_text = await self._chat_with_tools_and_context(
                    messages,
                    task_monitor=task_monitor,
                    session_type=session_type,
                    thinking_mode=_thinking_mode,
                    thinking_depth=_thinking_depth,
                    progress_callback=_progress_cb,
                    session=session,
                    endpoint_override=endpoint_override,
                    intent_result=_intent,
                )

            # === Flush any remaining IM progress messages so the reasoning chain arrives before the answer ===
            if gateway and session:
                try:
                    await gateway.flush_progress(session)
                except Exception:
                    pass

            # === Shared finalize ===
            await self._finalize_session(
                response_text=response_text,
                session=session,
                session_id=session_id,
                task_monitor=task_monitor,
            )

            # fast_reply bypasses ReasoningEngine, leaving trace empty so _last_usage_summary = {}.
            # Fill in from Response.usage.
            if _fast_handled and not self._last_usage_summary and isinstance(_fast_usage, dict):
                self._last_usage_summary = {
                    "input_tokens": _fast_usage.get("input_tokens", 0),
                    "output_tokens": _fast_usage.get("output_tokens", 0),
                    "total_tokens": _fast_usage.get("input_tokens", 0) + _fast_usage.get("output_tokens", 0),
                }

            return response_text
        finally:
            self._cleanup_session_state(im_tokens)

    async def chat_with_session_stream(
        self,
        message: str,
        session_messages: list[dict],
        session_id: str = "",
        session: Any = None,
        gateway: Any = None,
        *,
        plan_mode: bool = False,
        mode: str = "agent",
        endpoint_override: str | None = None,
        attachments: list | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
    ):
        """
        Streaming version of chat_with_session; yields SSE event dicts.

        Runs the exact same Agent pipeline as chat_with_session() (shared prepare/finalize),
        with the middle reasoning portion using reasoning_engine.reason_stream() for streaming output.

        Used for the SSE channel of the Desktop Chat API (/api/chat).

        Args:
            message: user message
            session_messages: Session's conversation history
            session_id: session ID
            session: Session object
            gateway: MessageGateway object
            plan_mode: whether to enable Plan mode (deprecated, use mode)
            mode: interaction mode (ask/plan/agent)
            endpoint_override: endpoint override
            attachments: Desktop Chat attachment list
            thinking_mode: thinking-mode override ('auto'/'on'/'off'/None)
            thinking_depth: thinking depth ('low'/'medium'/'high'/None)

        Yields:
            SSE event dict {"type": "...", ...}
        """
        if not self._initialized:
            await self.initialize()

        endpoint_override = endpoint_override or self._preferred_endpoint

        # === Stop-command detection ===
        message_lower = message.strip().lower()
        if message_lower in self.STOP_COMMANDS or message.strip() in self.STOP_COMMANDS:
            self.cancel_current_task(f"User sent stop command: {message}", session_id=session_id)
            logger.info(f"[StopTask] User requested to stop (session={session_id}): {message}")
            yield {"type": "todo_cancelled"}
            yield {
                "type": "text_delta",
                "content": "✅ Got it, current task stopped. Anything else I can help with?",
            }
            yield {"type": "done"}
            return

        # Resolve conversation_id (done early so cleanup uses the right key)
        self._current_session_id = session_id
        conversation_id = self._resolve_conversation_id(session, session_id)
        self._current_conversation_id = conversation_id

        # Clean up leftover task state from the previous turn (session-scoped)
        _prev_task = None
        _reset_key = session_id
        if self.agent_state:
            _prev_task = self.agent_state.get_task_for_session(session_id)
        if not _prev_task and self.agent_state:
            _prev_task = self.agent_state.current_task
            if _prev_task:
                _reset_key = _prev_task.session_id or _prev_task.task_id
        if _prev_task:
            if _prev_task.cancelled or not _prev_task.is_active:
                logger.info(
                    f"[Session:{session_id}] Resetting stale task "
                    f"(cancelled={_prev_task.cancelled}, status={_prev_task.status.value}, "
                    f"reset_key={_reset_key!r})"
                )
                self.agent_state.reset_task(session_id=_reset_key)
            else:
                _prev_task.clear_skip()
                await _prev_task.drain_user_inserts()

        # Clear leftover pending_cancels from the previous turn (a disconnect watcher may write them after cleanup)
        self._pending_cancels.pop(session_id, None) if session_id else None

        # User proactively sends a new message -> unconditionally clear all endpoint cooldowns
        llm_client = getattr(self.brain, "_llm_client", None)
        if llm_client:
            llm_client.reset_all_cooldowns(force_all=True)

        im_tokens = None
        _reply_text = ""
        try:
            # Send a heartbeat immediately so the frontend knows the request is received (the prepare stage can contain multiple LLM calls)
            yield {"type": "heartbeat"}

            # Pre-prepare check: if the session has a pending cancel signal, exit immediately
            if self._is_session_cancelled(session_id):
                self._consume_pending_cancel(session_id)
                logger.info(
                    f"[Session:{session_id}] Cancelled before prepare, returning immediately"
                )
                yield {"type": "text_delta", "content": "✅ Got it, current task stopped."}
                yield {"type": "done"}
                return

            # === Shared prepare ===
            (
                messages,
                session_type,
                task_monitor,
                conversation_id,
                im_tokens,
            ) = await self._prepare_session_context(
                message=message,
                session_messages=session_messages,
                session_id=session_id,
                session=session,
                gateway=gateway,
                conversation_id=conversation_id,
                attachments=attachments,
                mode=mode,
            )

            yield {"type": "heartbeat"}

            # Post-prepare check: if a cancel signal arrived during prepare (including pending cancel)
            _conv_cancel_id = conversation_id or session_id
            if self._is_session_cancelled(session_id) or self._is_session_cancelled(
                _conv_cancel_id
            ):
                self._consume_pending_cancel(session_id)
                self._consume_pending_cancel(_conv_cancel_id)
                logger.info(
                    f"[Session:{session_id}] Cancelled during prepare, returning immediately"
                )
                yield {"type": "text_delta", "content": "✅ Got it, current task stopped."}
                yield {"type": "done"}
                return

            # === Build the System Prompt (matching _chat_with_tools_and_context) ===
            # Pre-compute _effective_tools so the catalog's deferred annotations
            # are up-to-date before the system prompt is built.
            _ = self._effective_tools

            task_description = (getattr(self, "_current_task_query", "") or "").strip()
            if not task_description:
                task_description = self._get_last_user_request(messages).strip()

            system_prompt = await self._build_system_prompt_compiled(
                task_description=task_description,
                session_type=session_type,
                session=session,
            )

            # Inject TaskDefinition
            task_def = (getattr(self, "_current_task_definition", "") or "").strip()
            if task_def:
                system_prompt += f"\n\n## Developer: TaskDefinition\n{task_def}\n"

            base_system_prompt = system_prompt

            # === Plan mode handoff: consume _plan_exit_pending ===
            system_prompt, mode = self._handle_plan_exit_pending(
                system_prompt,
                mode,
                conversation_id,
                message,
            )
            # Update plan_mode flag to match potentially changed mode
            plan_mode = mode == "plan"

            # === Read thinking preference from session metadata (used by the IM channel) ===
            _thinking_mode = thinking_mode
            _thinking_depth = thinking_depth
            if session and (_thinking_mode is None or _thinking_depth is None):
                try:
                    if _thinking_mode is None:
                        _thinking_mode = session.get_metadata("thinking_mode")
                    if _thinking_depth is None:
                        _thinking_depth = session.get_metadata("thinking_depth")
                except Exception:
                    pass

            # === Intent-driven routing (streaming) ===
            from .intent_analyzer import IntentType as _IT

            _intent = getattr(self, "_current_intent", None)

            # Intent-driven ForceToolCall for streaming path
            _force_tool_retries = None
            if _intent:
                if _intent.intent in (_IT.CHAT, _IT.QUERY):
                    _force_tool_retries = 0
                elif _intent.force_tool:
                    pass
                else:
                    _force_tool_retries = max(
                        0, getattr(settings, "force_tool_call_max_retries", 2) - 1
                    )

            _agent_profile_id = "default"
            if session and hasattr(session, "context"):
                _agent_profile_id = (
                    getattr(session.context, "agent_profile_id", "default") or "default"
                )

            _fast_usage = None

            if _intent and _intent.intent == _IT.CHAT and getattr(_intent, "fast_reply", False):
                # Ultra-fast path: rule-based greeting only, use lightweight model
                try:
                    _identity_snippet = ""
                    if hasattr(self, "identity") and hasattr(self.identity, "get_system_prompt"):
                        _identity_snippet = (
                            self.identity.get_system_prompt(include_active_task=False) or ""
                        )[:500]

                    _fast_system = (
                        f"{_identity_snippet}\n\n"
                        "The user sent a short greeting/confirmation message. Reply briefly in your persona's style; "
                        "do not use any tools; do not over-explain. Keep it light and natural, 1-3 sentences is enough."
                    ).strip()

                    _fast_response = await self.brain.think_lightweight(
                        prompt=message,
                        system=_fast_system,
                    )
                    _fast_usage = _fast_response.usage
                    _reply_text = clean_llm_response(
                        _fast_response.content if _fast_response.content else ""
                    )
                    if _reply_text:
                        yield {"type": "text_delta", "content": _reply_text}
                    else:
                        yield {"type": "text_delta", "content": "Hello! How can I help you?"}
                        _reply_text = "Hello! How can I help you?"
                except Exception as e:
                    logger.error(f"[FastReply] Failed: {e}")
                    yield {"type": "text_delta", "content": "Hello! How can I help you?"}
                    _reply_text = "Hello! How can I help you?"
                yield {"type": "done"}

                await self._finalize_session(
                    response_text=_reply_text,
                    session=session,
                    session_id=session_id,
                    task_monitor=task_monitor,
                )
                if not self._last_usage_summary and isinstance(_fast_usage, dict):
                    self._last_usage_summary = {
                        "input_tokens": _fast_usage.get("input_tokens", 0),
                        "output_tokens": _fast_usage.get("output_tokens", 0),
                        "total_tokens": _fast_usage.get("input_tokens", 0) + _fast_usage.get("output_tokens", 0),
                    }
                return

            if _intent and _intent.intent == _IT.QUERY and getattr(_intent, "fast_reply", False):
                # Fast-path for simple factual queries (math, date, definitions)
                # No tools passed → LLM answers directly; empty response falls through
                # to full agent path below.
                _query_ok = False
                try:
                    _runtime_info = ""
                    try:
                        from ..prompt.builder import _build_runtime_section

                        _runtime_info = _build_runtime_section() or ""
                    except Exception:
                        pass

                    _identity_snippet = ""
                    if hasattr(self, "identity") and hasattr(self.identity, "get_system_prompt"):
                        _identity_snippet = (
                            self.identity.get_system_prompt(include_active_task=False) or ""
                        )[:500]

                    _fast_system = (
                        f"{_identity_snippet}\n\n"
                        f"{_runtime_info}\n\n"
                        "The user asked a simple knowledge/math/date question."
                        "Give an accurate and concise answer directly. Do not use any tools."
                        "If it involves date/time, answer based on the runtime environment info above."
                    ).strip()

                    logger.info(f"[FastQuery-Stream] Answering '{message}' without tools")
                    _fast_response = await self.brain.think_lightweight(
                        prompt=message,
                        system=_fast_system,
                    )
                    _fast_usage = _fast_response.usage
                    _reply_text = clean_llm_response(
                        _fast_response.content if _fast_response.content else ""
                    )
                    if _reply_text:
                        yield {"type": "text_delta", "content": _reply_text}
                        _query_ok = True
                    else:
                        logger.warning("[FastQuery-Stream] Empty response, falling back to full agent")
                except Exception as e:
                    logger.warning(f"[FastQuery-Stream] Failed ({e}), falling back to full agent")

                if _query_ok:
                    yield {"type": "done"}
                    await self._finalize_session(
                        response_text=_reply_text,
                        session=session,
                        session_id=session_id,
                        task_monitor=task_monitor,
                    )
                    if not self._last_usage_summary and isinstance(_fast_usage, dict):
                        self._last_usage_summary = {
                            "input_tokens": _fast_usage.get("input_tokens", 0),
                            "output_tokens": _fast_usage.get("output_tokens", 0),
                            "total_tokens": _fast_usage.get("input_tokens", 0) + _fast_usage.get("output_tokens", 0),
                        }
                    return

            # LLM-classified CHAT (non-fast_reply) falls through to reason_stream
            # with force_tool_retries=0, so tools are available but not forced.

            # Complexity detection: soft suggestion instead of hard interruption
            # suppress_plan=True means the intent analyzer explicitly decided
            # this task is too simple for plan mode — skip the suggestion.
            if (
                mode == "agent"
                and hasattr(self, "_current_intent")
                and self._current_intent
                and getattr(self._current_intent, "suggest_plan", False)
                and not getattr(self._current_intent, "suppress_plan", False)
            ):
                _score = getattr(getattr(self._current_intent, "complexity", None), "score", 0)
                logger.info(
                    f"[ComplexityDetection] Complex task detected (score={_score}), "
                    "adding soft plan suggestion to context"
                )
                soft_hint = (
                    "\n\n[System notice: this task is fairly complex; please give a brief plan in your reply before executing."
                    "You do not need to pause for user confirmation, proceed directly.]"
                )
                if messages and isinstance(messages[-1], dict):
                    messages = list(messages)
                    last = dict(messages[-1])
                    last["content"] = (last.get("content") or "") + soft_hint
                    messages[-1] = last

            async for event in self.reasoning_engine.reason_stream(
                messages=messages,
                tools=self._effective_tools,
                system_prompt=system_prompt,
                base_system_prompt=base_system_prompt,
                task_description=task_description,
                task_monitor=task_monitor,
                session_type=session_type,
                plan_mode=plan_mode,
                mode=mode,
                endpoint_override=endpoint_override,
                conversation_id=conversation_id,
                thinking_mode=_thinking_mode,
                thinking_depth=_thinking_depth,
                agent_profile_id=_agent_profile_id,
                session=session,
                force_tool_retries=_force_tool_retries,
                is_sub_agent=getattr(self, "_is_sub_agent_call", False),
            ):
                # Collect reply text (used for session persistence & memory)
                if event.get("type") == "text_delta":
                    _reply_text += event.get("content", "")
                elif event.get("type") == "ask_user" and not _reply_text:
                    _reply_text = event.get("question", "")
                yield event

            # === Shared finalize (always runs; record memory/trace even if reply text is empty) ===
            await self._finalize_session(
                response_text=_reply_text,
                session=session,
                session_id=session_id,
                task_monitor=task_monitor,
            )

        except Exception as e:
            logger.error(f"chat_with_session_stream error: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)[:500]}
            yield {"type": "done"}
        finally:
            self._cleanup_session_state(im_tokens)

    def _handle_plan_exit_pending(
        self,
        system_prompt: str,
        mode: str,
        conversation_id: str,
        user_message: str,
    ) -> tuple[str, str]:
        """Handle Plan mode exit pending state when user sends the next message.

        Flow:
        - Plan mode → LLM calls create_plan_file → exit_plan_mode → pending flag set
        - User sends next message:
          a) mode="agent" → user approved the plan → inject plan content, switch to Agent
          b) mode="plan" → user wants refinements → inject plan awareness, stay in Plan
          c) No pending → pass through unchanged

        Returns:
            (updated_system_prompt, effective_mode)
        """
        pending_map = getattr(self, "_plan_exit_pending", {})
        if not isinstance(pending_map, dict) or not pending_map:
            return system_prompt, mode

        pending = pending_map.pop(conversation_id, None)
        if not pending:
            return system_prompt, mode

        plan_file = pending.get("plan_file", "")
        plan_summary = pending.get("summary", "")
        plan_content = ""

        if plan_file:
            try:
                plan_content = Path(plan_file).read_text(encoding="utf-8")
            except Exception:
                logger.warning(f"[Plan] Could not read plan file: {plan_file}")

        if mode == "agent":
            # User approved → switch to Agent mode with plan context
            logger.info(
                f"[Plan→Agent] User approved plan, injecting plan content "
                f"(conv={conversation_id}, file={plan_file})"
            )
            if plan_content:
                system_prompt += (
                    "\n\n## Plan to Execute\n\n"
                    "The user has reviewed and approved this plan from Plan mode. "
                    "Execute the steps described below. Use create_todo to track "
                    "progress, then execute each step.\n\n"
                    f"Plan file: {plan_file}\n\n"
                    f"{plan_content}\n"
                )
            elif plan_summary:
                system_prompt += (
                    f"\n\n## Plan to Execute\n\n"
                    f"The user approved a plan: {plan_summary}\n"
                    f"Plan file: {plan_file}\n"
                    f"Read the plan file and execute the steps.\n"
                )
        elif mode == "plan":
            # User wants refinements → stay in Plan mode
            logger.info(
                f"[Plan] User wants refinements, keeping Plan mode "
                f"(conv={conversation_id}, file={plan_file})"
            )
            if plan_file:
                system_prompt += (
                    "\n\n## Existing Plan (Needs Refinement)\n\n"
                    f"A plan file was already created at: {plan_file}\n"
                    "The user wants to refine it. Read the current plan file "
                    "and modify it based on the user's feedback.\n"
                    "Use write_file to update the plan file (only data/plans/*.md "
                    "paths are allowed in Plan mode).\n"
                    "After updating, call exit_plan_mode again to present the "
                    "revised plan for approval.\n"
                )

        return system_prompt, mode

    def _resolve_conversation_id(self, session: Any, session_id: str) -> str:
        """Return the caller-supplied session_id directly as the canonical conversation_id.

        Desktop path: session_id = raw chat_id (passed in as conversation_id from the frontend)
        IM path:      session_id = session.id (passed in by orchestrator._call_agent)
        CLI path:     session_id = "cli_<uuid>"

        We no longer read session.session_key to avoid the task key drifting away from the pool key.
        """
        return session_id

    def _extract_usage_summary(self, trace: list[dict]) -> dict:
        """Extract a lightweight token-usage summary from react_trace.

        Called from _finalize_session to cache the result up front.
        After cleanup frees the large objects, chat.py can still read this summary without needing the full trace.
        """
        if not trace:
            return {}
        total_in = sum(t.get("tokens", {}).get("input", 0) for t in trace)
        total_out = sum(t.get("tokens", {}).get("output", 0) for t in trace)
        summary = {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
        }
        # Estimate the context token count
        try:
            re = self.reasoning_engine
            ctx_mgr = getattr(self, "context_manager", None) or getattr(
                re, "_context_manager", None
            )
            if ctx_mgr and hasattr(ctx_mgr, "get_max_context_tokens"):
                msgs = getattr(re, "_last_working_messages", None) or []
                summary["context_tokens"] = ctx_mgr.estimate_messages_tokens(msgs) if msgs else 0
                summary["context_limit"] = ctx_mgr.get_max_context_tokens()
        except Exception:
            pass
        return summary

    _DELEGATION_TOOLS = frozenset(
        {
            "delegate_to_agent",
            "delegate_parallel",
            "spawn_agent",
        }
    )

    def build_tool_trace_summary(self) -> str:
        """
        Generate tool-execution summary text from the latest react_trace.

        Return format:

          [Sub-Agent work summary]      (only present when multi-Agent delegation occurs)
          1. [web-scout] task: ... | status: done | delivered files: ...
          2. [doc-helper] task: ... | status: done | delivered files: ...

          [Execution summary]
          - tool_name({key: val}) → result_hint...

        Callers must store the return value in the message's ``tool_summary`` metadata field (do not concatenate into content).
        An empty string indicates there were no tool calls.
        """
        from .tool_executor import save_overflow, smart_truncate

        trace = (
            getattr(self, "_last_finalized_trace", None)
            or getattr(self.reasoning_engine, "_last_react_trace", None)
            or []
        )
        if not trace:
            return ""

        TOTAL_RESULT_BUDGET = 4000
        num_tools = sum(len(it.get("tool_calls", [])) for it in trace)
        per_tool_budget = max(150, min(600, TOTAL_RESULT_BUDGET // max(num_tools, 1)))

        lines: list[str] = []
        has_delegation = False
        truncated_full_results: list[str] = []

        for it in trace:
            for tc in it.get("tool_calls", []):
                name = tc.get("name", "")
                if not name:
                    continue
                if name in self._DELEGATION_TOOLS:
                    has_delegation = True
                tc_input = tc.get("input", {})
                param_hint = ""
                if isinstance(tc_input, dict):
                    items = list(tc_input.items())[:6]
                    param_budget = max(80, per_tool_budget // 2 // max(len(items), 1))
                    kv = {}
                    for k, v in items:
                        val_str = str(v)
                        val_truncated, _ = smart_truncate(
                            val_str, param_budget, save_full=False, label="param"
                        )
                        kv[k] = val_truncated
                    param_hint = str(kv) if kv else ""

                result_hint = ""
                is_error = False
                for tr in it.get("tool_results", []):
                    if tr.get("tool_use_id") == tc.get("id", ""):
                        raw = str(tr.get("result_content", tr.get("result_preview", "")))
                        is_error = tr.get("is_error", False)
                        max_len = 800 if name in self._DELEGATION_TOOLS else per_tool_budget
                        if len(raw) > max_len:
                            result_hint = raw[:max_len].replace("\n", " ") + "..."
                            truncated_full_results.append(
                                f"=== {name} (id={tc.get('id', '')}) ===\n{raw}"
                            )
                        else:
                            result_hint = raw.replace("\n", " ")
                        break

                status_mark = "❌ " if is_error else ""
                line = f"- {status_mark}{name}"
                if param_hint:
                    line += f"({param_hint})"
                if result_hint:
                    line += f" → {result_hint}"
                lines.append(line)
        if not lines:
            return ""

        if truncated_full_results:
            overflow_content = "\n\n".join(truncated_full_results)
            overflow_path = save_overflow("trace_summary", overflow_content)
            lines.append(f"[Some tool results were truncated; full content: {overflow_path}, readable via read_file]")

        parts: list[str] = []

        if has_delegation:
            ws_section = self._build_work_summary_section()
            if ws_section:
                parts.append(ws_section)

        parts.append("\n\n[Execution summary]\n" + "\n".join(lines))

        return "".join(parts)

    def _build_work_summary_section(self) -> str:
        """Build [Sub-Agent work summary] section from sub_agent_records.

        Placed BEFORE [Execution summary] so that high-level task summaries appear
        before low-level tool call details, improving readability and
        ContextManager summarization quality.
        """
        session = self._current_session
        if not session:
            return ""
        records = getattr(getattr(session, "context", None), "sub_agent_records", None)
        if not records:
            return ""
        summaries = [r.get("work_summary", "") for r in records if r.get("work_summary")]
        if not summaries:
            return ""
        lines = ["\n\n[Sub-Agent work summary]"]
        for i, ws in enumerate(summaries, 1):
            lines.append(f"{i}. {ws}")
        return "\n".join(lines)

    def _build_chain_summary(self, react_trace: list[dict]) -> list[dict] | None:
        """
        Build a reasoning-chain summary from the ReAct trace (used for IM message metadata).

        Produce one summary entry per iteration, including a thinking preview and the list of tool calls.
        """
        if not react_trace:
            return None
        summaries = []
        for t in react_trace:
            results_by_id: dict[str, str] = {}
            for tr in t.get("tool_results", []):
                tid = tr.get("tool_use_id", "")
                if tid:
                    results_by_id[tid] = str(tr.get("result_content", ""))[:120]
            tools = []
            for tc in t.get("tool_calls", []):
                tool_entry: dict = {
                    "name": tc.get("name", ""),
                    "input_preview": str(tc.get("input", tc.get("input_preview", "")))[:80],
                }
                tc_id = tc.get("id", "")
                if tc_id and tc_id in results_by_id:
                    tool_entry["result_preview"] = results_by_id[tc_id]
                tools.append(tool_entry)
            item: dict = {
                "iteration": t.get("iteration", 0),
                "thinking_preview": (t.get("thinking") or "")[:150],
                "thinking_duration_ms": t.get("thinking_duration_ms", 0),
                "tools": tools,
            }
            if t.get("context_compressed"):
                item["context_compressed"] = t["context_compressed"]
            summaries.append(item)
        return summaries

    async def _compile_prompt(self, user_message: str) -> tuple[str, str]:
        """
        First stage of two-stage prompting: Prompt Compiler

        Turn the user's raw request into a structured task definition.
        Uses an isolated context; does not enter the core conversation history.

        Args:
            user_message: raw user message

        Returns:
            (compiled_prompt, raw_compiler_output)
            - compiled_prompt: the compiled prompt (defaults to the raw user message to avoid polluting the main messages)
            - raw_compiler_output: raw output of the Prompt Compiler (used for logging)
        """
        try:
            # Invoke Brain's Compiler-specific method (separate fast model, thinking disabled, falls back to the main model on failure)
            response = await self.brain.compiler_think(
                prompt=user_message,
                system=PROMPT_COMPILER_SYSTEM,
            )

            # Strip thinking tags (the fallback-to-main-model path may produce them)
            compiler_output = (
                strip_thinking_tags(response.content).strip() if response.content else ""
            )
            logger.info(f"Prompt compiled: {compiler_output}")

            # Key strategy: do not stuff compiler_output back into the user message (to avoid polluting the main model's messages)
            # A short summary is injected into the system/developer segment later, and reused as the memory-retrieval query
            return user_message, compiler_output

        except Exception as e:
            logger.warning(f"Prompt compilation failed: {e}, using original message")
            # On compile failure, just use the raw message
            return user_message, ""

    def _summarize_compiler_output(self, compiler_output: str, max_chars: int = 600) -> str:
        """
        Condense the Prompt Compiler's YAML output into a short summary (used for system/developer injection and as the memory query).

        Goals: stable, short, reusable; does not pollute the main messages.
        """
        if not compiler_output:
            return ""

        lines = [ln.strip() for ln in compiler_output.splitlines() if ln.strip()]
        if not lines:
            return ""

        picked: list[str] = []
        keys = ("goal:", "task_summary:", "constraints:", "missing:", "deliverables:", "task_type:")
        for ln in lines:
            lower = ln.lower()
            if any(lower.startswith(k) for k in keys):
                picked.append(ln)
            if sum(len(x) + 1 for x in picked) >= max_chars:
                break

        if not picked:
            picked = lines[:10]

        summary = " | ".join(picked)
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "…"
        return summary

    async def _do_task_retrospect(self, task_monitor: TaskMonitor) -> str:
        """
        Run task retrospective analysis

        When a task takes too long, have the LLM analyze causes and suggest improvements.

        Args:
            task_monitor: task monitor

        Returns:
            Retrospective analysis result
        """
        try:
            context = task_monitor.get_retrospect_context()
            prompt = RETROSPECT_PROMPT.format(context=context)

            # Use think_lightweight for the retrospective (disable reasoning chain to save tokens)
            response = await self.brain.think_lightweight(
                prompt=prompt,
                system="You are a task-execution analysis expert. Concisely analyze task execution, identify causes of slowness, and suggest improvements.",
                max_tokens=512,
            )

            result = strip_thinking_tags(response.content).strip() if response.content else ""

            # Save the retrospective result to the monitor
            task_monitor.metrics.retrospect_result = result

            # If an obvious repeated-error pattern is detected, record it to memory
            if "重复" in result or "无效" in result or "弯路" in result:
                try:
                    from ..memory.types import Memory, MemoryPriority, MemoryType

                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"Task execution retrospective found an issue: {result}",
                        source="retrospect",
                        importance_score=0.7,
                    )
                    self.memory_manager.add_memory(memory)
                except Exception as e:
                    logger.warning(f"Failed to save retrospect to memory: {e}")

            return result

        except Exception as e:
            logger.warning(f"Task retrospect failed: {e}")
            return ""

    async def _do_task_retrospect_background(
        self, task_monitor: TaskMonitor, session_id: str
    ) -> None:
        """
        Run task retrospective analysis in the background

        This method runs asynchronously in the background without blocking the main response.
        Retrospective results are saved to files for the daily self-check system to aggregate.

        Args:
            task_monitor: task monitor
            session_id: session ID
        """
        try:
            # Run the retrospective analysis
            retrospect_result = await self._do_task_retrospect(task_monitor)

            if not retrospect_result:
                return

            # Save to the retrospective store
            from .task_monitor import RetrospectRecord, get_retrospect_storage

            record = RetrospectRecord(
                task_id=task_monitor.metrics.task_id,
                session_id=session_id,
                description=task_monitor.metrics.description,
                duration_seconds=task_monitor.metrics.total_duration_seconds,
                iterations=task_monitor.metrics.total_iterations,
                model_switched=task_monitor.metrics.model_switched,
                initial_model=task_monitor.metrics.initial_model,
                final_model=task_monitor.metrics.final_model,
                retrospect_result=retrospect_result,
            )

            storage = get_retrospect_storage()
            storage.save(record)

            logger.info(f"[Session:{session_id}] Retrospect saved: {task_monitor.metrics.task_id}")

        except Exception as e:
            logger.error(f"[Session:{session_id}] Background retrospect failed: {e}")

    def _should_compile_prompt(self, message: str) -> bool:
        """
        Decide whether prompt compilation is needed

        Based on length only: very short messages carry too little information to produce a meaningful TaskDefinition,
        so compilation is pure overhead. Message classification (chit-chat/Q&A/task) is left to the LLM itself,
        we do not do keyword/regex matching here.
        """
        # Very short messages do not need compilation (too little info to produce a meaningful structured TaskDefinition)
        if len(message.strip()) < 20:
            return False

        # Pure image/voice messages do not need compilation (the Compiler cannot see multimodal content and would produce misleading task definitions)
        stripped = message.strip()
        if re.fullmatch(r"(\[图片: [^\]]+\]\s*)+", stripped):
            return False
        if re.fullmatch(r"(\[语音转文字: [^\]]+\]\s*)+", stripped):
            return False

        # In all other cases, compile
        return True

    async def _detect_topic_change(
        self, session_messages: list[dict], new_message: str, session: Any = None
    ) -> bool:
        """Detect whether the current message is a new topic (unrelated to the recent conversation).

        Combines multiple layers of context (current task, conversation summary, recent messages) and lets the LLM judge holistically.
        Only invoked on the IM channel.

        Returns:
            True indicates a topic switch was detected
        """
        if not new_message or len(new_message.strip()) < 5:
            return False
        if not session_messages:
            return False

        _new = new_message.strip()

        # ---- Build multi-layer context ----

        context_parts: list[str] = []

        # Layer 1: current task/topic (if any)
        if session:
            task_desc = (
                session.context.get_variable("task_description")
                if hasattr(session, "context")
                else None
            )
            if task_desc:
                context_parts.append(f"Current task: {task_desc}")
            summary = (
                getattr(session.context, "summary", None) if hasattr(session, "context") else None
            )
            if summary:
                from .tool_executor import smart_truncate as _st

                summary_trunc, _ = _st(summary, 600, save_full=False, label="topic_summary")
                context_parts.append(f"Conversation summary: {summary_trunc}")

        from .tool_executor import smart_truncate as _st

        recent = session_messages[-6:]
        dialog_lines: list[str] = []
        for msg in recent:
            role = "User" if msg.get("role") == "user" else "Assistant"
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                preview, _ = _st(content, 500, save_full=False, label="topic_content")
                preview = preview.replace("\n", " ")
                dialog_lines.append(f"{role}: {preview}")
        if dialog_lines:
            context_parts.append("Recent conversation:\n" + "\n".join(dialog_lines))

        if not context_parts:
            return False

        full_context = "\n\n".join(context_parts)

        new_trunc, _ = _st(_new, 800, save_full=False, label="topic_new")
        try:
            response = await self.brain.compiler_think(
                prompt=(
                    f"{full_context}\n\n"
                    f"New message: {new_trunc}\n\n"
                    "Judge: does the new message continue the current topic (CONTINUE) or start a new one (NEW)?\n"
                    "Output only one word: CONTINUE or NEW"
                ),
                system=(
                    "You are a topic-switch detector. Based on the current task and recent conversation context,"
                    "decide whether the new message belongs to the same topic.\n"
                    "CONTINUE: the new message follows up on, supplements, confirms, or asks further about the current topic,"
                    "or is a follow-up action related to the current task.\n"
                    "NEW: the new message introduces a new topic or task unrelated to the current conversation.\n"
                    "Output only one word."
                ),
            )
            result = (response.content or "").strip().upper()
            is_new = "NEW" in result and "CONTINUE" not in result
            if is_new:
                logger.info(f"[TopicDetect] LLM detected topic change: {_new[:60]}")
            return is_new
        except Exception as e:
            logger.debug(f"[TopicDetect] LLM check failed (non-critical): {e}")
            return False

    def _get_last_user_request(self, messages: list[dict]) -> str:
        """Get the last user request (same source as TaskVerify; delegates to ResponseHandler)."""
        return ResponseHandler.get_last_user_request(messages)

    @staticmethod
    def _build_tool_fallback_summary(
        executed_tool_names: list[str],
        delivery_receipts: list[dict],
    ) -> str | None:
        """When the LLM repeatedly fails to return visible text, build a fallback summary from the tool-execution records."""
        parts: list[str] = []

        if delivery_receipts:
            for r in delivery_receipts:
                desc = r.get("description") or r.get("summary") or r.get("title") or ""
                if desc:
                    parts.append(f"• {desc}")
            if parts:
                return "Completed the following operations:\n" + "\n".join(parts)

        if executed_tool_names:
            unique = list(dict.fromkeys(executed_tool_names))
            tool_summary = "、".join(unique[:10])
            if len(unique) > 10:
                tool_summary += f", {len(unique)} items in total"
            return f"Task execution finished (tools used: {tool_summary}), but the model produced no text summary. Please re-ask if you need details."

        return None

    async def _cancellable_llm_call(self, cancel_event: asyncio.Event, **kwargs) -> Any:
        """Wrap the LLM call as a cancellable asyncio.Task, racing it against cancel_event.

        When cancel_event is set() before the LLM returns, raise UserCancelledError.
        """
        logger.info(
            f"[CancellableLLM] dispatching cancellable LLM call, cancel_event.is_set={cancel_event.is_set()}"
        )
        _tt = set_tracking_context(
            TokenTrackingContext(
                operation_type="chat",
                session_id=kwargs.get("conversation_id", ""),
                channel="cli",
            )
        )
        try:
            llm_task = asyncio.create_task(
                self.brain.messages_create_async(cancel_event=cancel_event, **kwargs)
            )
            cancel_waiter = asyncio.create_task(cancel_event.wait())

            done, pending = await asyncio.wait(
                {llm_task, cancel_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            if llm_task in done:
                logger.info("[CancellableLLM] LLM call completed first; returning normally")
                return llm_task.result()
            else:
                reason = self._cancel_reason or "user requested stop"
                logger.info(
                    f"[CancellableLLM] cancel_event fired first; raising UserCancelledError: {reason!r}"
                )
                raise UserCancelledError(
                    reason=reason,
                    source="llm_call",
                )
        finally:
            reset_tracking_context(_tt)

    async def _handle_cancel_farewell(
        self,
        working_messages: list[dict],
        system_prompt: str,
        current_model: str,
    ) -> str:
        """Return default text immediately after cancellation; dispatch LLM farewell in the background.

        Args:
            working_messages: current working message list
            system_prompt: current system prompt
            current_model: currently used model

        Returns:
            Fixed cancellation text (does not wait for the LLM)
        """
        cancel_reason = self._cancel_reason or "user requested stop"
        default_farewell = "✅ Got it, current task stopped."

        logger.info(
            f"[StopTask][CancelFarewell] returning default text immediately; dispatching LLM farewell in background: "
            f"cancel_reason={cancel_reason!r}, model={current_model}"
        )

        asyncio.create_task(
            self._background_cancel_farewell(
                list(working_messages), system_prompt, current_model, cancel_reason
            )
        )

        return default_farewell

    async def _background_cancel_farewell(
        self,
        working_messages: list[dict],
        system_prompt: str,
        current_model: str,
        cancel_reason: str,
    ) -> None:
        """Run the LLM farewell call in the background and persist the result to the context (non-blocking for the user)."""
        farewell_text = "✅ Got it, current task stopped."
        try:
            cancel_msg = (
                f"[System notice] User sent stop command '{cancel_reason}';"
                "Please stop the current operation immediately and briefly tell the user it has stopped along with current progress (1-2 sentences)."
                "Do not call any tools."
            )
            working_messages.append({"role": "user", "content": cancel_msg})

            _tt = set_tracking_context(
                TokenTrackingContext(
                    operation_type="farewell",
                    channel="api",
                )
            )
            try:
                response = await asyncio.wait_for(
                    self.brain.messages_create_async(
                        model=current_model,
                        max_tokens=200,
                        system=system_prompt,
                        tools=[],
                        messages=working_messages,
                    ),
                    timeout=5.0,
                )
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        farewell_text = block.text.strip()
                        break
                logger.info(f"[StopTask][BgFarewell] LLM farewell complete: {farewell_text[:100]}")
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("[StopTask][BgFarewell] LLM farewell timed out (5s)")
            except Exception as e:
                logger.warning(f"[StopTask][BgFarewell] LLM farewell failed: {e}")
            finally:
                reset_tracking_context(_tt)
        except Exception as e:
            logger.warning(f"[StopTask][BgFarewell] background farewell exception: {e}")

        self._persist_cancel_to_context(cancel_reason, farewell_text)

    def _persist_cancel_to_context(self, cancel_reason: str, farewell_text: str) -> None:
        """Persist the interrupt event into the _context.messages conversation history.

        Ensures the LLM sees prior interrupt history in later turns.
        """
        try:
            ctx = getattr(self, "_context", None)
            if ctx and hasattr(ctx, "messages"):
                ctx.messages.append(
                    {
                        "role": "user",
                        "content": f"[User interrupted the previous task: {cancel_reason}]",
                    }
                )
                ctx.messages.append(
                    {
                        "role": "assistant",
                        "content": farewell_text,
                    }
                )
                logger.debug(
                    f"[StopTask] Cancel event persisted to context (reason={cancel_reason})"
                )
        except Exception as e:
            logger.warning(f"[StopTask] Failed to persist cancel to context: {e}")

    _LIGHTWEIGHT_EMPTY_MAX_RETRIES = 2

    async def _chat_lightweight(
        self,
        messages: list[dict],
        session_type: str = "cli",
        endpoint_override: str | None = None,
    ) -> str:
        """Lightweight path for CHAT intent: no tools, slim system prompt.

        Retries up to _LIGHTWEIGHT_EMPTY_MAX_RETRIES times if the LLM returns
        an empty content array (a known model-level glitch).
        """
        system_prompt = await self._build_system_prompt_compiled(
            task_description="",
            session_type=session_type,
            tools_enabled=False,
            session=self._current_session,
        )

        for attempt in range(1 + self._LIGHTWEIGHT_EMPTY_MAX_RETRIES):
            try:
                response = await self.brain.messages_create_async(
                    system=system_prompt,
                    messages=messages,
                    tools=[],
                    max_tokens=self.brain.max_tokens,
                    endpoint_override=endpoint_override,
                )

                content = getattr(response, "content", None)
                _has_tool_use = False
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if hasattr(block, "text"):
                            text_parts.append(block.text)
                        elif isinstance(block, dict) and "text" in block:
                            text_parts.append(block["text"])
                        block_type = getattr(block, "type", None) or (
                            block.get("type") if isinstance(block, dict) else None
                        )
                        if block_type == "tool_use":
                            _has_tool_use = True
                    raw = "\n".join(text_parts) or ""
                else:
                    raw = str(content or "")

                cleaned = clean_llm_response(raw)
                if cleaned:
                    return cleaned

                if _has_tool_use:
                    return "Got it, message received."

                if attempt < self._LIGHTWEIGHT_EMPTY_MAX_RETRIES:
                    logger.warning(
                        f"[ChatLightweight] Empty content from LLM "
                        f"(attempt {attempt + 1}), retrying..."
                    )
                    continue
                return cleaned or "Sorry, the model can't generate a reply right now. Please try again later."
            except Exception as e:
                logger.error(f"[ChatLightweight] LLM call failed: {e}")
                return "Sorry, unable to reply right now. Please try again later."
        return "Sorry, the model can't generate a reply right now. Please try again later."

    async def _chat_with_tools_and_context(
        self,
        messages: list[dict],
        use_session_prompt: bool = True,
        task_monitor: TaskMonitor | None = None,
        session_type: str = "cli",
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
        progress_callback: Any = None,
        session: Any = None,
        endpoint_override: str | None = None,
        intent_result: Any = None,
    ) -> str:
        """
        Chat using the specified message context (delegates to ReasoningEngine)

        Phase 2 refactor: retain the system-prompt / task_description construction logic,
        and delegate the core reasoning loop to self.reasoning_engine.run().

        Args:
            messages: conversation message list
            use_session_prompt: whether to use the Session-specific System Prompt
            task_monitor: task monitor
            session_type: session type ("cli" or "im")
            thinking_mode: thinking-mode override ('auto'/'on'/'off'/None)
            thinking_depth: thinking depth ('low'/'medium'/'high'/None)
            progress_callback: progress callback async fn(str) -> None, for the IM real-time reasoning chain
            endpoint_override: endpoint override
            intent_result: IntentResult from IntentAnalyzer (drives ForceToolCall policy)

        Returns:
            Final response text
        """
        # === Build System Prompt ===
        task_description = self._get_last_user_request(messages).strip()
        if not task_description:
            task_description = (getattr(self, "_current_task_query", "") or "").strip()

        if use_session_prompt:
            system_prompt = await self._build_system_prompt_compiled(
                task_description=task_description,
                session_type=session_type,
                session=session or self._current_session,
            )
        else:
            system_prompt = self._context.system

        # Inject TaskDefinition
        task_def = (getattr(self, "_current_task_definition", "") or "").strip()
        if task_def:
            system_prompt += f"\n\n## Developer: TaskDefinition\n{task_def}\n"

        base_system_prompt = system_prompt
        conversation_id = getattr(self, "_current_conversation_id", None) or getattr(
            self, "_current_session_id", None
        )
        _agent_profile_id = "default"
        if session and hasattr(session, "context"):
            _agent_profile_id = getattr(session.context, "agent_profile_id", "default") or "default"

        # === Intent-driven ForceToolCall policy ===
        force_tool_retries = None
        if intent_result:
            from .intent_analyzer import IntentType as _IT

            if intent_result.intent in (_IT.CHAT, _IT.QUERY):
                force_tool_retries = 0
            elif intent_result.force_tool:
                pass  # None = use default from settings
            else:
                force_tool_retries = max(0, getattr(settings, "force_tool_call_max_retries", 2) - 1)

        # === Delegate to ReasoningEngine ===
        return await self.reasoning_engine.run(
            messages,
            tools=self._effective_tools,
            system_prompt=system_prompt,
            base_system_prompt=base_system_prompt,
            task_description=task_description,
            task_monitor=task_monitor,
            session_type=session_type,
            conversation_id=conversation_id,
            thinking_mode=thinking_mode,
            thinking_depth=thinking_depth,
            progress_callback=progress_callback,
            agent_profile_id=_agent_profile_id,
            endpoint_override=endpoint_override,
            force_tool_retries=force_tool_retries,
            is_sub_agent=getattr(self, "_is_sub_agent_call", False),
        )

    # ==================== Cancellation-state proxy properties ====================

    @property
    def _task_cancelled(self) -> bool:
        """Unified cancellation-state query (delegates to TaskState; kept for legacy references)"""
        return (
            hasattr(self, "agent_state")
            and self.agent_state is not None
            and self.agent_state.is_task_cancelled
        )

    @property
    def _cancel_reason(self) -> str:
        """Unified cancellation-reason query (delegates to TaskState; kept for legacy references)"""
        if hasattr(self, "agent_state") and self.agent_state:
            return self.agent_state.task_cancel_reason
        return ""

    def set_interrupt_enabled(self, enabled: bool) -> None:
        """
        Set whether interrupt checking is enabled

        Args:
            enabled: whether to enable
        """
        self._interrupt_enabled = enabled
        logger.info(f"Interrupt check {'enabled' if enabled else 'disabled'}")

    def cancel_current_task(
        self, reason: str = "user requested stop", session_id: str | None = None
    ) -> None:
        """
        Cancel the currently running task.

        If session_id is given, only cancel that session's task and plan; otherwise cancel all.
        When the session has no active task (e.g. in the prepare stage), store the cancel in _pending_cancels,
        to be consumed by a later checkpoint.

        Args:
            reason: cancellation reason
            session_id: optional session ID to provide per-channel isolation
        """
        has_state = hasattr(self, "agent_state") and self.agent_state

        if session_id and has_state:
            task = self.agent_state.get_task_for_session(session_id)
            _effective_sid = session_id
            # task key = session_id (raw chat_id); an exact match usually hits.
            # If still not found, fall back to _current_conversation_id / _current_session_id.
            if not task:
                for _alt_key in (self._current_conversation_id, self._current_session_id):
                    if _alt_key and _alt_key != session_id:
                        task = self.agent_state.get_task_for_session(_alt_key)
                        if task:
                            _effective_sid = _alt_key
                            break
            task_status = task.status.value if task else "N/A"
            logger.info(
                f"[StopTask] cancel_current_task invoked: reason={reason!r}, "
                f"session_id={session_id}, effective_sid={_effective_sid!r}, "
                f"task_status={task_status}"
            )
            if task:
                self.agent_state.cancel_task(reason, session_id=_effective_sid)
            else:
                logger.warning(
                    f"[StopTask] No task found for session {session_id}, storing as pending cancel"
                )
                self._pending_cancels[session_id] = reason
        elif has_state:
            has_task = self.agent_state.current_task is not None
            task_status = self.agent_state.current_task.status.value if has_task else "N/A"
            logger.info(
                f"[StopTask] cancel_current_task invoked: reason={reason!r}, "
                f"has_state={has_state}, has_task={has_task}, task_status={task_status}"
            )
            self.agent_state.cancel_task(reason)

        try:
            from ..tools.handlers.plan import cancel_todo

            if session_id:
                if cancel_todo(session_id):
                    logger.info(f"[StopTask] Cancelled active todo for session {session_id}")
            else:
                from ..tools.handlers.plan import iter_active_todo_sessions

                for sid in list(iter_active_todo_sessions().keys()):
                    if cancel_todo(sid):
                        logger.info(f"[StopTask] Cancelled active todo for session {sid}")
        except Exception as e:
            logger.warning(f"[StopTask] Failed to cancel todo: {e}")

        logger.info(f"[StopTask] Task cancellation completed: {reason}")

    def _is_session_cancelled(self, session_id: str | None = None) -> bool:
        """Check whether the given session has a pending cancel signal written during the prepare stage.

        Only checks _pending_cancels (written by cancel_current_task when no task exists).
        Does not check task.cancelled, since that may be a stale task from the previous turn and could mislead.
        Real-time cancellation is handled internally by the cancel_event mechanism in reason_stream/run.
        """
        return bool(session_id and session_id in self._pending_cancels)

    def _consume_pending_cancel(self, session_id: str | None = None) -> str | None:
        """Consume and return the pending cancel reason, or None if there is none."""
        if session_id:
            return self._pending_cancels.pop(session_id, None)
        return None

    def is_stop_command(self, message: str) -> bool:
        """
        Check whether the message is a stop command

        Args:
            message: user message

        Returns:
            Whether it is a stop command
        """
        msg_lower = message.strip().lower()
        return msg_lower in self.STOP_COMMANDS or message.strip() in self.STOP_COMMANDS

    def is_skip_command(self, message: str) -> bool:
        """
        Check whether the message is a skip-current-step command

        Args:
            message: user message

        Returns:
            Whether it is a skip command
        """
        msg_lower = message.strip().lower()
        return msg_lower in self.SKIP_COMMANDS or message.strip() in self.SKIP_COMMANDS

    def classify_interrupt(self, message: str) -> str:
        """
        Classify the interrupt message type

        Args:
            message: user message

        Returns:
            "stop" / "skip" / "insert"
        """
        if self.is_stop_command(message):
            return "stop"
        elif self.is_skip_command(message):
            return "skip"
        else:
            return "insert"

    def skip_current_step(
        self, reason: str = "user requested to skip current step", session_id: str | None = None
    ) -> bool:
        """
        Skip the currently-executing tool/step (without terminating the entire task)

        Args:
            reason: skip reason
            session_id: optional session ID to provide per-channel isolation

        Returns:
            Whether skip was set successfully (False means no active task)
        """
        has_state = hasattr(self, "agent_state") and self.agent_state
        if not has_state:
            logger.warning(f"[SkipStep] No agent_state to skip: {reason}")
            return False

        _effective_sid = session_id or getattr(self, "_current_session_id", None)
        task = self.agent_state.get_task_for_session(_effective_sid) if _effective_sid else None
        if not task and _effective_sid:
            for _alt_key in (self._current_conversation_id, self._current_session_id):
                if _alt_key and _alt_key != _effective_sid:
                    task = self.agent_state.get_task_for_session(_alt_key)
                    if task:
                        _effective_sid = _alt_key
                        break
        if not task:
            task = self.agent_state.current_task
            if task:
                _effective_sid = task.session_id or task.task_id

        if task:
            self.agent_state.skip_current_step(reason, session_id=_effective_sid)
            logger.info(
                f"[SkipStep] Step skip requested: {reason} "
                f"(session_id={session_id}, effective_sid={_effective_sid!r})"
            )
            return True
        logger.warning(f"[SkipStep] No active task to skip: {reason} (session_id={session_id})")
        return False

    async def insert_user_message(self, text: str, session_id: str | None = None) -> bool:
        """
        Inject a user message into the current task (non-command messages during task execution)

        Args:
            text: user message text
            session_id: optional session ID to provide per-channel isolation

        Returns:
            Whether enqueue succeeded (False means no active task; the message was dropped)
        """
        has_state = hasattr(self, "agent_state") and self.agent_state
        if not has_state:
            logger.warning(f"[UserInsert] No agent_state, message dropped: {text[:50]}...")
            return False

        _effective_sid = session_id or getattr(self, "_current_session_id", None)
        task = self.agent_state.get_task_for_session(_effective_sid) if _effective_sid else None
        if not task and _effective_sid:
            for _alt_key in (self._current_conversation_id, self._current_session_id):
                if _alt_key and _alt_key != _effective_sid:
                    task = self.agent_state.get_task_for_session(_alt_key)
                    if task:
                        _effective_sid = _alt_key
                        break
        if not task:
            task = self.agent_state.current_task
            if task:
                _effective_sid = task.session_id or task.task_id

        if task:
            await self.agent_state.insert_user_message(text, session_id=_effective_sid)
            logger.info(
                f"[UserInsert] User message queued: {text[:50]}... (effective_sid={_effective_sid!r})"
            )
            return True
        logger.warning(f"[UserInsert] No active task, message dropped: {text[:50]}...")
        return False

    async def _chat_with_tools(self, message: str) -> str:
        """
        DEPRECATED: this method is deprecated; chat() now delegates to chat_with_session() + _chat_with_tools_and_context().
        Kept solely for backward compatibility; will be removed in a future release.

        Chat handling with tool-call support

        Let the LLM decide whether tools are needed; no hard-coded logic

        Args:
            message: user message

        Returns:
            Final response text
        """
        # Use the full conversation history (already includes the current user message)
        # Make a copy so that intermediate tool-call messages don't pollute the original context
        messages = list(self._context.messages)

        # Check and compress the context (if near the limit)
        messages = await self._compress_context(messages)

        max_iterations = settings.max_iterations  # Ralph Wiggum mode: never give up

        # === Plan persistence: save the base prompt without Plan, append active Plan dynamically inside the loop ===
        _base_system_prompt_cli = self._context.system

        def _build_effective_system_prompt_cli() -> str:
            """Append the active Plan section to the base prompt dynamically (CLI path)"""
            from ..tools.handlers.plan import get_active_todo_prompt

            _cid = getattr(self, "_current_conversation_id", None) or getattr(
                self, "_current_session_id", None
            )
            prompt = _base_system_prompt_cli
            if _cid:
                plan_section = get_active_todo_prompt(_cid)
                if plan_section:
                    prompt += f"\n\n{plan_section}\n"
            return prompt

        # Loop-detection guard
        recent_tool_calls: list[str] = []
        max_repeated_calls = 3

        # Get cancel_event (used to race-cancel the LLM call)
        _cancel_event = (
            self.agent_state.current_task.cancel_event
            if self.agent_state and self.agent_state.current_task
            else asyncio.Event()
        )

        for iteration in range(max_iterations):
            # C8: check cancellation on each iteration
            if self._task_cancelled:
                logger.info(f"[StopTask] Task cancelled in _chat_with_tools: {self._cancel_reason}")
                return "✅ Task stopped."

            try:
                # Before each iteration, check context size (tool calls may produce large output)
                if iteration > 0:
                    messages = await self._compress_context(
                        messages, system_prompt=_build_effective_system_prompt_cli()
                    )

                # Call Brain (can be interrupted by cancel_event)
                response = await self._cancellable_llm_call(
                    _cancel_event,
                    model=self.brain.model,
                    max_tokens=self.brain.max_tokens,
                    system=_build_effective_system_prompt_cli(),
                    tools=self._effective_tools,
                    messages=messages,
                )
            except UserCancelledError:
                logger.info("[StopTask] LLM call interrupted by user cancel in _chat_with_tools")
                return await self._handle_cancel_farewell(
                    messages, _build_effective_system_prompt_cli(), self.brain.model
                )

            # Detect max_tokens truncation
            _cli_stop = getattr(response, "stop_reason", "")
            if str(_cli_stop) == "max_tokens":
                logger.warning(
                    f"[CLI] ⚠️ LLM output truncated (stop_reason=max_tokens, limit={self.brain.max_tokens})"
                )

            # Handle the response
            tool_calls = []
            text_content = ""

            for block in response.content:
                if block.type == "text":
                    text_content += block.text
                elif block.type == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            # If there were no tool calls, return the text directly
            if not tool_calls:
                _cleaned = strip_thinking_tags(text_content)
                _, _cleaned = parse_intent_tag(_cleaned)
                return _cleaned

            # Loop detection
            call_signature = "|".join(
                [f"{tc['name']}:{sorted(tc['input'].items())}" for tc in tool_calls]
            )
            recent_tool_calls.append(call_signature)
            if len(recent_tool_calls) > max_repeated_calls:
                recent_tool_calls = recent_tool_calls[-max_repeated_calls:]

            if len(recent_tool_calls) >= max_repeated_calls and len(set(recent_tool_calls)) == 1:
                logger.warning(
                    f"[Loop Detection] Same tool call repeated {max_repeated_calls} times, ending chat"
                )
                return "Repeated operation detected; automatically terminated."

            # Tool calls present; execute them
            logger.info(f"Chat iteration {iteration + 1}, {len(tool_calls)} tool calls")

            # Build the assistant message
            # MiniMax M2.1 interleaved-thinking support:
            # Thinking blocks must be fully preserved to maintain reasoning-chain continuity
            assistant_content = []
            for block in response.content:
                if block.type == "thinking":
                    # Preserve thinking blocks (required by MiniMax M2.1)
                    assistant_content.append(
                        {
                            "type": "thinking",
                            "thinking": block.thinking
                            if hasattr(block, "thinking")
                            else str(block),
                        }
                    )
                elif block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            messages.append({"role": "assistant", "content": assistant_content})

            # P0-1: unified path via ToolExecutor (with PolicyEngine checks + skip/cancel race)
            tool_results, _, _ = await self.tool_executor.execute_batch(
                tool_calls,
                state=self.agent_state.current_task if self.agent_state else None,
                task_monitor=None,
                allow_interrupt_checks=self._interrupt_enabled,
                capture_delivery_receipts=False,
            )

            messages.append({"role": "user", "content": tool_results})

            # === Unified handling of skip-reflection + user-inserted messages ===
            if self.agent_state and self.agent_state.current_task:
                await self.agent_state.current_task.process_post_tool_signals(messages)

            # Check termination
            if response.stop_reason == "end_turn":
                break

        # Return the last text response (with thinking tags + intent markers filtered)
        _final = strip_thinking_tags(text_content)
        _, _final = parse_intent_tag(_final)
        return _final or "Operation complete"

    async def execute_task_from_message(self, message: str) -> TaskResult:
        """Create and execute a task from a message"""
        task = Task(
            id=str(uuid.uuid4())[:8],
            description=message,
            session_id=getattr(self, "_current_session_id", None),  # associate with the current session
            priority=1,
        )
        return await self.execute_task(task)

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        [DEPRECATED] Please use self.tool_executor.execute_tool() instead.

        This method bypasses PolicyEngine safety checks and is kept only as a temporary compatibility shim.
        """
        import warnings

        warnings.warn(
            "_execute_tool is deprecated, use self.tool_executor.execute_tool()",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.info(f"Executing tool: {tool_name} with {tool_input}")

        # ============================================
        # Todo enforcement check (Agent mode only; skipped in plan/ask mode)
        # ============================================
        _effective_mode = getattr(self.tool_executor, "_current_mode", "agent")
        if _effective_mode not in ("plan", "ask"):
            _todo_exempt = (
                "create_todo",
                "create_plan_file",
                "exit_plan_mode",
                "get_todo_status",
                "ask_user",
            )
            if tool_name not in _todo_exempt:
                from ..tools.handlers.plan import has_active_todo, is_todo_required

                session_id = getattr(self, "_current_session_id", None)
                if session_id and is_todo_required(session_id) and not has_active_todo(session_id):
                    return (
                        "⚠️ **This looks like a multi-step task; consider creating a Todo first!**\n\n"
                        "Please call the `create_todo` tool to create a task plan before running concrete operations.\n\n"
                        "Example:\n"
                        "```\n"
                        "create_todo(\n"
                        "  task_summary='write a script to get and display the time',\n"
                        "  steps=[\n"
                        "    {id: 'step1', description: 'create a Python script', tool: 'write_file'},\n"
                        "    {id: 'step2', description: 'run the script', tool: 'run_shell'},\n"
                        "    {id: 'step3', description: 'read the result', tool: 'read_file'}\n"
                        "  ]\n"
                        ")\n"
                        "```"
                    )

        # Import the log cache
        from ..logging import get_session_log_buffer

        log_buffer = get_session_log_buffer()

        # Record log count before execution
        logs_before = log_buffer.get_logs(count=500)
        logs_before_count = len(logs_before)

        try:
            # Prefer executing via handler_registry
            if self.handler_registry.has_tool(tool_name):
                result = await self.handler_registry.execute_by_tool(tool_name, tool_input)
            else:
                all_tools = self.handler_registry.list_tools()
                name_lower = tool_name.lower()
                similar = [
                    t
                    for t in all_tools
                    if name_lower in t.lower()
                    or t.lower() in name_lower
                    or set(name_lower.split("_")) & set(t.lower().split("_"))
                ][:5]
                hint = (
                    f" Did you mean: {', '.join(similar)}?"
                    if similar
                    else " Please check that the tool name is correct."
                )
                return f"❌ Unknown tool: {tool_name}. {hint}"

            # Collect new logs produced during execution (WARNING/ERROR/CRITICAL)
            all_logs = log_buffer.get_logs(count=500)
            new_logs = [
                log
                for log in all_logs[logs_before_count:]
                if log["level"] in ("WARNING", "ERROR", "CRITICAL")
            ]

            # If there are warning/error logs, append them to the result
            if new_logs:
                result += "\n\n[Execution log]:\n"
                for log in new_logs[-10:]:  # Show at most 10 entries
                    result += f"[{log['level']}] {log['module']}: {log['message']}\n"

            # Generic truncation guard (matches ToolExecutor._guard_truncate)
            result = ToolExecutor._guard_truncate(tool_name, result)

            return result

        except Exception as e:
            logger.error(f"Tool execution error: {e}", exc_info=True)
            return f"Tool execution error: {str(e)}"

    async def execute_task(self, task: Task) -> TaskResult:
        """
        Execute a task (with tool calls)

        Safe model-switching strategy:
        1. On timeout or error, retry up to 3 times first
        2. Only switch to the fallback model after retries are exhausted
        3. On switch, discard the tool-call history and restart from the original task description

        Args:
            task: task object

        Returns:
            TaskResult
        """
        import time

        start_time = time.time()

        if not self._initialized:
            await self.initialize()

        logger.info(f"Executing task: {task.description}")

        # === Create task monitor ===
        task_monitor = TaskMonitor(
            task_id=task.id,
            description=task.description,
            session_id=task.session_id,
            timeout_seconds=settings.progress_timeout_seconds,
            hard_timeout_seconds=settings.hard_timeout_seconds,
            retrospect_threshold=180,  # retrospective threshold: 180 seconds
            fallback_model=self.brain.get_fallback_model(task.session_id),  # dynamically fetch the fallback model
            retry_before_switch=3,  # retry 3 times before switching
        )
        task_monitor.start(self.brain.model)

        # Use the already-built system prompt (including the skill catalog)
        # The skill catalog has already been injected into _context.system during initialization
        system_prompt = (
            self._context.system
            + """

## Task Execution Strategy

Use tools to actually execute the task:

1. **Check skill catalog above** - the skill catalog is listed above; use descriptions to decide if a matching skill exists
2. **If skill matches**: Use `get_skill_info(skill_name)` to load full instructions
3. **Run script**: Use `run_skill_script(skill_name, script_name, args)`
4. **If no skill matches**: Use `skill-creator` skill to create one, then `load_skill` to load it

Never give up until the task is complete!"""
        )

        # === Plan persistence: save the base prompt without Plan, append active Plan dynamically inside the loop ===
        _base_system_prompt_task = system_prompt
        _task_conversation_id = task.session_id or f"task:{task.id}"

        def _build_effective_system_prompt_task() -> str:
            """Append the active Plan section to the base prompt dynamically (Task path)"""
            from ..tools.handlers.plan import get_active_todo_prompt

            prompt = _base_system_prompt_task
            plan_section = get_active_todo_prompt(_task_conversation_id)
            if plan_section:
                prompt += f"\n\n{plan_section}\n"
            return prompt

        # === Critical: save the original task description so we can reset context on model switch ===
        original_task_message = {"role": "user", "content": task.description}
        messages = [original_task_message.copy()]

        max_tool_iterations = settings.max_iterations  # Ralph Wiggum mode: never give up
        iteration = 0
        final_response = ""
        current_model = self.brain.model
        conversation_id = task.session_id or f"task:{task.id}"

        def _resolve_endpoint_name(model_or_endpoint: str) -> str | None:
            """Resolve 'endpoint_name' or 'model' to an endpoint_name (task-loop specific, minimal compat)."""
            try:
                llm_client = getattr(self.brain, "_llm_client", None)
                if not llm_client:
                    return None
                available = [m.name for m in llm_client.list_available_models()]
                if model_or_endpoint in available:
                    return model_or_endpoint
                for m in llm_client.list_available_models():
                    if m.model == model_or_endpoint:
                        return m.name
                return None
            except Exception:
                return None

        # Loop-detection guard
        recent_tool_calls: list[str] = []  # record of recent tool calls
        max_repeated_calls = 3  # force-terminate when the same call repeats more than this many times

        MAX_TASK_MODEL_SWITCHES = 2
        _task_switch_count = 0
        _total_llm_retries = 0
        MAX_TOTAL_LLM_RETRIES = 3

        # Follow-up counter: when the LLM does not call tools, how many times to re-prompt
        no_tool_call_count = 0
        max_no_tool_retries = max(0, int(getattr(settings, "force_tool_call_max_retries", 2)))

        # Get cancel_event (used to race-cancel the LLM call)
        _cancel_event = (
            self.agent_state.current_task.cancel_event
            if self.agent_state and self.agent_state.current_task
            else asyncio.Event()
        )

        try:
            while iteration < max_tool_iterations:
                # C8: check at iteration start whether the task has been cancelled
                if self._task_cancelled:
                    logger.info(f"[StopTask] Task cancelled in execute_task: {self._cancel_reason}")
                    return "✅ Task stopped."

                iteration += 1
                logger.info(f"Task iteration {iteration}")

                # Task monitoring: iteration start
                task_monitor.begin_iteration(iteration, current_model)

                # === Safe model-switch check ===
                # Check whether we timed out and retries are exhausted
                if task_monitor.should_switch_model:
                    # Circuit-breaker check: prevent infinite model-switching loops
                    _task_switch_count += 1
                    if _task_switch_count > MAX_TASK_MODEL_SWITCHES:
                        logger.error(
                            f"[Task:{task.id}] Exceeded max model switches "
                            f"({MAX_TASK_MODEL_SWITCHES}), aborting task"
                        )
                        return (
                            "Task execution failed; already tried multiple models but could not recover.\n"
                            "You can simply resend to retry."
                        )

                    new_model = task_monitor.fallback_model
                    if not new_model:
                        logger.warning(
                            "[ModelSwitch] No fallback model available for sub-agent timeout"
                        )
                        return "Task failed: all model endpoints are unavailable; please check your network connection."
                    task_monitor.switch_model(
                        new_model,
                        f"Task exceeded {task_monitor.timeout_seconds} seconds; switching after {task_monitor.retry_count} retries",
                        reset_context=True,
                    )

                    endpoint_name = _resolve_endpoint_name(new_model)
                    if endpoint_name:
                        ok, msg = self.brain.switch_model(
                            endpoint_name=endpoint_name,
                            hours=0.05,
                            reason=f"task_timeout:{task.id}",
                            conversation_id=conversation_id,
                        )
                        if not ok:
                            logger.error(
                                f"[ModelSwitch] switch_model failed: {msg}. "
                                f"Aborting task (no healthy endpoint)."
                            )
                            return (
                                f"Task failed: model switch failed ({msg}); cannot continue.\n"
                                "Suggestion: check your network connection, or open the Setup Center and confirm at least one model is correctly configured."
                            )
                    else:
                        logger.warning(f"[ModelSwitch] Cannot resolve endpoint for '{new_model}'")

                    current_model = new_model

                    # === Critical: reset context and discard tool-call history ===
                    logger.warning(
                        f"[ModelSwitch] Task {task.id}: Switching to {new_model}, resetting context. "
                        f"Discarding {len(messages) - 1} tool-related messages"
                    )
                    messages = [original_task_message.copy()]

                    # Add a model-switch notice + tool-state revalidation barrier
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[System notice] A model switch occurred: previous tool_use/tool_result history has been cleared; all tool state must now be treated as unknown.\n"
                                "Before invoking any stateful tool, re-check state first: browser -> browser_open; MCP -> list_mcp_servers; desktop -> desktop_window/desktop_inspect.\n"
                                "Please process the task request above from scratch."
                            ),
                        }
                    )

                    # Reset loop detection
                    recent_tool_calls.clear()

                try:
                    # Check and compress context (task execution may produce large tool output)
                    if iteration > 1:
                        messages = await self._compress_context(
                            messages, system_prompt=_build_effective_system_prompt_task()
                        )

                    # Call Brain (can be interrupted by cancel_event)
                    response = await self._cancellable_llm_call(
                        _cancel_event,
                        max_tokens=self.brain.max_tokens,
                        system=_build_effective_system_prompt_task(),
                        tools=self._effective_tools,
                        messages=messages,
                        conversation_id=conversation_id,
                    )

                    # Successful call; reset retry counter
                    task_monitor.reset_retry_count()

                except UserCancelledError:
                    logger.info(
                        f"[StopTask] LLM call interrupted by user cancel in execute_task {task.id}"
                    )
                    return await self._handle_cancel_farewell(
                        messages, _build_effective_system_prompt_task(), current_model
                    )

                except Exception as e:
                    logger.error(f"[LLM] Brain call failed in task {task.id}: {e}")

                    # -- global retry counter --
                    _total_llm_retries += 1
                    if _total_llm_retries > MAX_TOTAL_LLM_RETRIES:
                        logger.error(
                            f"[Task:{task.id}] Global retry limit reached "
                            f"({_total_llm_retries}/{MAX_TOTAL_LLM_RETRIES}), aborting"
                        )
                        return (
                            f"Task execution failed; retried {MAX_TOTAL_LLM_RETRIES} times without recovery.\n"
                            f"Error: {str(e)[:200]}\n"
                            "You can simply resend to retry."
                        )

                    # -- structural errors: fast circuit-break --
                    from ..llm.types import AllEndpointsFailedError as _Aefe
                    from .reasoning_engine import ReasoningEngine

                    if isinstance(e, _Aefe) and e.is_structural:
                        _already = getattr(self, "_task_structural_stripped", False)
                        if not _already:
                            stripped, did_strip = ReasoningEngine._strip_heavy_content(messages)
                            if did_strip:
                                logger.warning(
                                    f"[Task:{task.id}] Structural error: stripping heavy content, retrying once"
                                )
                                self._task_structural_stripped = True
                                messages.clear()
                                messages.extend(stripped)
                                llm_client = getattr(self.brain, "_llm_client", None)
                                if llm_client:
                                    llm_client.reset_all_cooldowns(include_structural=True)
                                continue
                        logger.error(f"[Task:{task.id}] Structural error, aborting: {str(e)[:200]}")
                        return (
                            f"API request format error; cannot recover.\n"
                            f"Error: {str(e)[:200]}\n"
                            "You can simply resend to retry."
                        )

                    # Record the error and decide whether to retry
                    should_retry = task_monitor.record_error(str(e))

                    if should_retry:
                        logger.info(
                            f"[LLM] Will retry (attempt {task_monitor.retry_count}, "
                            f"global {_total_llm_retries}/{MAX_TOTAL_LLM_RETRIES})"
                        )
                        try:
                            await self._cancellable_await(asyncio.sleep(2), _cancel_event)
                        except UserCancelledError:
                            return await self._handle_cancel_farewell(
                                messages, _build_effective_system_prompt_task(), current_model
                            )
                        continue
                    else:
                        _task_switch_count += 1
                        if _task_switch_count > MAX_TASK_MODEL_SWITCHES:
                            logger.error(
                                f"[Task:{task.id}] Exceeded max model switches "
                                f"({MAX_TASK_MODEL_SWITCHES}), aborting task"
                            )
                            return (
                                f"Task execution failed; already tried multiple models but could not recover.\n"
                                f"Error: {str(e)[:200]}\n"
                                "You can simply resend to retry."
                            )

                        new_model = task_monitor.fallback_model
                        if not new_model:
                            logger.warning(
                                "[ModelSwitch] No fallback model available for sub-agent error"
                            )
                            return "Task failed: all model endpoints are unavailable; please check your network connection."
                        task_monitor.switch_model(
                            new_model,
                            f"LLM call failed; switching after {task_monitor.retry_count} retries: {e}",
                            reset_context=True,
                        )
                        endpoint_name = _resolve_endpoint_name(new_model)
                        if endpoint_name:
                            ok, msg = self.brain.switch_model(
                                endpoint_name=endpoint_name,
                                hours=0.05,
                                reason=f"task_error:{task.id}",
                                conversation_id=conversation_id,
                            )
                            if not ok:
                                logger.warning(
                                    f"[ModelSwitch] switch_model failed: {msg}. "
                                    f"Not resetting retry_count."
                                )
                                # switch_model failed (target is in cooldown); do not reset retry_count
                                # break directly to avoid infinite retries
                                return (
                                    f"Task failed: model switch failed ({msg}); cannot continue.\n"
                                    "Suggestion: check your network connection, or open the Setup Center and confirm at least one model is correctly configured."
                                )
                        else:
                            logger.warning(
                                f"[ModelSwitch] Cannot resolve endpoint for '{new_model}'"
                            )
                        current_model = new_model

                        # Reset context + barrier
                        logger.warning(
                            f"[ModelSwitch] Task {task.id}: Switching to {new_model} due to errors, resetting context"
                        )
                        messages = [original_task_message.copy()]
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "[System notice] A model switch occurred: previous tool_use/tool_result history has been cleared; all tool state must now be treated as unknown.\n"
                                    "Before invoking any stateful tool, re-check state first: browser -> browser_open; MCP -> list_mcp_servers; desktop -> desktop_window/desktop_inspect.\n"
                                    "Please process the task request above from scratch."
                                ),
                            }
                        )
                        recent_tool_calls.clear()
                        continue

                # Detect max_tokens truncation
                _task_stop = getattr(response, "stop_reason", "")
                if str(_task_stop) == "max_tokens":
                    logger.warning(
                        f"[Task:{task.id}] ⚠️ LLM output truncated (stop_reason=max_tokens, limit={self.brain.max_tokens})"
                    )

                # Handle the response
                tool_calls = []
                text_content = ""

                for block in response.content:
                    if block.type == "text":
                        text_content += block.text
                    elif block.type == "tool_use":
                        tool_calls.append(
                            {
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )

                # Task monitoring: iteration end
                task_monitor.end_iteration(text_content if text_content else "")

                # If a text response is present, save it (filtering thinking tags and simulated tool-call text)
                if text_content:
                    cleaned_text = clean_llm_response(text_content)
                    # Only save the text as the final response when there are no tool calls
                    # When tool calls are present, this text may just be the LLM's thinking process
                    if not tool_calls and cleaned_text:
                        final_response = cleaned_text

                # If there were no tool calls, check whether we should force one
                if not tool_calls:
                    no_tool_call_count += 1

                    # If follow-up attempts remain, force a tool call
                    if no_tool_call_count <= max_no_tool_retries:
                        logger.warning(
                            f"[ForceToolCall] Task LLM returned text without tool calls (attempt {no_tool_call_count}/{max_no_tool_retries})"
                        )

                        # Append the LLM's response to history
                        if text_content:
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": text_content}],
                                }
                            )

                        # Append a message forcing a tool call
                        messages.append(
                            {
                                "role": "user",
                                "content": "[System] If a tool is truly needed, call the corresponding tool; if not (pure chat/Q&A), answer directly without reciting system rules.",
                            }
                        )
                        continue  # loop again so the LLM calls a tool

                    # Follow-up attempts exhausted; task complete
                    break

                # Loop detection: record tool-call signatures
                call_signature = "|".join(
                    [f"{tc['name']}:{sorted(tc['input'].items())}" for tc in tool_calls]
                )
                recent_tool_calls.append(call_signature)

                # Keep only the most recent call records
                if len(recent_tool_calls) > max_repeated_calls:
                    recent_tool_calls = recent_tool_calls[-max_repeated_calls:]

                # Detect consecutive duplicate calls
                if len(recent_tool_calls) >= max_repeated_calls:
                    if len(set(recent_tool_calls)) == 1:
                        logger.warning(
                            f"[Loop Detection] Same tool call repeated {max_repeated_calls} times, forcing task end"
                        )
                        final_response = (
                            "Repeated operation detected during task execution; automatically terminated. To continue, please restate the task."
                        )
                        break

                # Execute tool calls
                # MiniMax M2.1 interleaved-thinking support:
                # Thinking blocks must be preserved intact to maintain reasoning-chain continuity
                assistant_content = []
                for block in response.content:
                    if block.type == "thinking":
                        # Preserve thinking blocks (required by MiniMax M2.1)
                        assistant_content.append(
                            {
                                "type": "thinking",
                                "thinking": block.thinking
                                if hasattr(block, "thinking")
                                else str(block),
                            }
                        )
                    elif block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )

                messages.append({"role": "assistant", "content": assistant_content})

                # Execute each tool and collect results
                # execute_task() does not strictly require inter-tool interrupt checks, so parallelism can be enabled per config
                tool_results, executed_names, _ = await self.tool_executor.execute_batch(
                    tool_calls,
                    state=self.agent_state.current_task if self.agent_state else None,
                    task_monitor=task_monitor,
                    allow_interrupt_checks=False,
                    capture_delivery_receipts=False,
                )

                messages.append({"role": "user", "content": tool_results})

                # === Unified handling of skip-reflection + user-inserted messages ===
                if self.agent_state and self.agent_state.current_task:
                    await self.agent_state.current_task.process_post_tool_signals(messages)

                # Note: do not check stop_reason after tool execution; let the loop continue to fetch the LLM's final summary
            # After the loop exits, if final_response is empty, ask the LLM to produce a summary
            if not final_response or len(final_response.strip()) < 10:
                logger.info("Task completed but no final response, requesting summary...")
                try:
                    # Ask the LLM to generate a task-completion summary
                    messages.append(
                        {
                            "role": "user",
                            "content": "Task execution is complete. Please briefly summarize the results and completion status.",
                        }
                    )
                    _tt_sum = set_tracking_context(
                        TokenTrackingContext(
                            operation_type="task_summary",
                            session_id=conversation_id or "",
                            channel="scheduler",
                        )
                    )
                    try:
                        summary_response = await self._cancellable_await(
                            self.brain.messages_create_async(
                                max_tokens=1000,
                                system=_build_effective_system_prompt_task(),
                                messages=messages,
                                conversation_id=conversation_id,
                            ),
                            _cancel_event,
                        )
                    finally:
                        reset_tracking_context(_tt_sum)
                    for block in summary_response.content:
                        if block.type == "text":
                            final_response = clean_llm_response(block.text)
                            break
                except UserCancelledError:
                    final_response = "✅ Task stopped."
                except Exception as e:
                    logger.warning(f"Failed to get summary: {e}")
                    final_response = "Task execution completed."
        finally:
            # Clean up per-conversation overrides to avoid affecting later tasks/sessions
            with contextlib.suppress(Exception):
                self.brain.restore_default_model(conversation_id=conversation_id)

        # === Finalize task monitoring ===
        metrics = task_monitor.complete(
            success=True,
            response=final_response,
        )

        # === Background retrospective analysis (if the task took too long; does not block the response) ===
        if metrics.retrospect_needed:
            # Create a background task to run the retrospective without awaiting the result
            asyncio.create_task(
                self._do_task_retrospect_background(task_monitor, task.session_id or task.id)
            )
            logger.info(f"[Task:{task.id}] Retrospect scheduled (background)")

        task.mark_completed(final_response)

        duration = time.time() - start_time

        # === Desktop notifications (local channels only: cli/desktop; IM channels have their own notification mechanism) ===
        if settings.desktop_notify_enabled:
            _session = getattr(self, "_current_session", None)
            _channel = getattr(_session, "channel", "cli") if _session else "cli"
            if _channel in ("cli", "desktop"):
                from .desktop_notify import notify_task_completed_async

                asyncio.ensure_future(
                    notify_task_completed_async(
                        task.description[:80],
                        success=True,
                        duration_seconds=duration,
                        sound=settings.desktop_notify_sound,
                    )
                )

        return TaskResult(
            success=True,
            data=final_response,
            iterations=iteration,
            duration_seconds=duration,
        )

    def _format_task_result(self, result: TaskResult) -> str:
        """Format the task result"""
        if result.success:
            return f"""Task completed

{result.data}

---
Iterations: {result.iterations}
Elapsed: {result.duration_seconds:.2f}s"""
        else:
            return f"""Task did not complete

Error: {result.error}

---
Attempts: {result.iterations}
Elapsed: {result.duration_seconds:.2f}s

I will continue trying other approaches..."""

    async def self_check(self) -> dict[str, Any]:
        """
        Self-check

        Returns:
            Self-check result
        """
        logger.info("Running self-check...")

        results = {
            "timestamp": datetime.now().isoformat(),
            "status": "healthy",
            "checks": {},
        }

        # Check Brain
        try:
            response = await self.brain.think("Hello, this is a test. Please reply with 'OK'.")
            results["checks"]["brain"] = {
                "status": "ok"
                if "OK" in response.content or "ok" in response.content.lower()
                else "warning",
                "message": "Brain is responsive",
            }
        except Exception as e:
            results["checks"]["brain"] = {
                "status": "error",
                "message": str(e),
            }
            results["status"] = "unhealthy"

        # Check Identity
        try:
            soul = self.identity.soul
            agent = self.identity.agent
            results["checks"]["identity"] = {
                "status": "ok" if soul and agent else "warning",
                "message": f"SOUL.md: {len(soul)} chars, AGENT.md: {len(agent)} chars",
            }
        except Exception as e:
            results["checks"]["identity"] = {
                "status": "error",
                "message": str(e),
            }

        # Check configuration
        results["checks"]["config"] = {
            "status": "ok" if settings.anthropic_api_key else "error",
            "message": "API key configured" if settings.anthropic_api_key else "API key missing",
        }

        # Check the skill system (SKILL.md spec)
        skill_count = self.skill_registry.count
        results["checks"]["skills"] = {
            "status": "ok",
            "message": f"{skill_count} skills installed (Agent Skills spec)",
            "count": skill_count,
            "skills": [s.name for s in self.skill_registry.list_all()],
        }

        # Check the skill catalog
        skills_path = settings.skills_path
        results["checks"]["skills_dir"] = {
            "status": "ok" if skills_path.exists() else "warning",
            "message": str(skills_path),
        }

        # Check the MCP client
        mcp_servers = self.mcp_client.list_servers()
        mcp_connected = self.mcp_client.list_connected()
        results["checks"]["mcp"] = {
            "status": "ok",
            "message": f"configured {len(mcp_servers)} servers; {len(mcp_connected)} connected",
            "servers": mcp_servers,
            "connected": mcp_connected,
        }

        logger.info(f"Self-check complete: {results['status']}")

        return results

    def _on_iteration(self, iteration: int, task: Task) -> None:
        """Ralph-loop iteration callback"""
        logger.debug(f"Ralph iteration {iteration} for task {task.id}")

    def _on_error(self, error: str, task: Task) -> None:
        """Ralph-loop error callback"""
        logger.warning(f"Ralph error for task {task.id}: {error}")

    @property
    def is_initialized(self) -> bool:
        """Whether initialization is complete"""
        return self._initialized

    @property
    def conversation_history(self) -> list[dict]:
        """Conversation history"""
        return self._conversation_history.copy()

    # ==================== Memory-system methods ====================

    def set_scheduler_gateway(self, gateway: Any) -> None:
        """
        Set the message gateway for the scheduled-task scheduler

        Used to send notifications to IM channels after scheduled tasks run

        Args:
            gateway: MessageGateway instance
        """
        if hasattr(self, "_task_executor") and self._task_executor:
            self._task_executor.gateway = gateway
            # Also pass persona/memory/proactive references for system tasks like the liveness heartbeat
            self._task_executor.persona_manager = getattr(self, "persona_manager", None)
            self._task_executor.memory_manager = getattr(self, "memory_manager", None)
            self._task_executor.proactive_engine = getattr(self, "proactive_engine", None)
            logger.info("Scheduler gateway configured")

    async def shutdown(
        self, task_description: str = "", success: bool = True, errors: list = None
    ) -> None:
        """
        Shut down the Agent and persist memory

        Args:
            task_description: main task description of the session
            success: whether the task succeeded
            errors: list of encountered errors
        """
        logger.info("Shutting down agent...")

        # Plugin-system cleanup: dispatch on_shutdown -> unload -> clear global map
        pm = getattr(self, "_plugin_manager", None)
        if pm is not None:
            try:
                await pm.hook_registry.dispatch("on_shutdown", agent=self)
            except Exception as e:
                logger.debug(f"on_shutdown hook dispatch error: {e}")
            for pid in list(pm.loaded_plugins.keys()):
                try:
                    await pm.unload_plugin(pid)
                except Exception as e:
                    logger.warning(f"Plugin '{pid}' unload error during shutdown: {e}")
            try:
                from ..plugins import PLUGIN_PROVIDER_MAP, PLUGIN_REGISTRY_MAP

                PLUGIN_PROVIDER_MAP.clear()
                PLUGIN_REGISTRY_MAP.clear()
            except Exception:
                pass
            try:
                from ..prompt.builder import set_prompt_hook_registry

                set_prompt_hook_registry(None)
            except Exception:
                pass

        # F9: clean up skill-related resources
        self._cleanup_skill_resources()

        # Close SkillStoreClient (if any)
        skill_store_client = getattr(self, "_skill_store_client", None)
        if skill_store_client and hasattr(skill_store_client, "close"):
            try:
                await skill_store_client.close()
            except Exception:
                pass

        # End the memory session
        self.memory_manager.end_session(
            task_description=task_description,
            success=success,
            errors=errors or [],
        )

        # Wait for pending async tasks in the memory system (e.g. episode generation)
        try:
            await self.memory_manager.await_pending_tasks(timeout=15.0)
        except Exception as e:
            logger.warning(f"Failed to await memory pending tasks: {e}")

        # Flush TodoStore and stop the debounce loop
        try:
            todo_save_task = getattr(self, "_todo_save_task", None)
            if todo_save_task and not todo_save_task.done():
                todo_save_task.cancel()
                try:
                    await todo_save_task
                except asyncio.CancelledError:
                    pass
            plan_handle_fn = self.handler_registry.get_handler("plan")
            plan_handler = getattr(plan_handle_fn, "__self__", None) if plan_handle_fn else None
            if plan_handler and hasattr(plan_handler, "_store"):
                await plan_handler._store.flush()
        except Exception as e:
            logger.debug(f"[TodoStore] Shutdown flush failed: {e}")

        self._running = False
        logger.info("Agent shutdown complete")

    async def consolidate_memories(self) -> dict:
        """
        Consolidate memory (batch-process unprocessed sessions)

        Suitable for invocation by a cron job during idle hours (e.g. overnight)

        Returns:
            Consolidation result statistics
        """
        logger.info("Starting memory consolidation...")
        return await self.memory_manager.consolidate_daily()

    def get_memory_stats(self) -> dict:
        """Get memory statistics"""
        return self.memory_manager.get_stats()
