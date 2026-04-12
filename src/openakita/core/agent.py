"""
Agent 主类 - 协调所有模块

这是 OpenAkita 的核心，负责:
- 接收用户输入
- 协调各个模块
- 执行工具调用
- 执行 Ralph 循环
- 管理对话和记忆
- 自我进化（技能搜索、安装、生成）

Skills 系统遵循 Agent Skills 规范 (agentskills.io)
MCP 系统遵循 Model Context Protocol 规范 (modelcontextprotocol.io)
"""

import asyncio
import base64
import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings

# 记忆系统
from ..memory import MemoryManager

# Prompt 编译管线 (v2)
# 技能系统 (SKILL.md 规范)
from ..skills import SkillCatalog, SkillLoader, SkillRegistry

# 系统工具目录（渐进式披露）
from ..tools.catalog import ToolCatalog

# 系统工具定义（从 tools/definitions 导入）
from ..tools.definitions import BASE_TOOLS
from .context_utils import DEFAULT_MAX_CONTEXT_TOKENS  # noqa: E402
from .context_utils import get_max_context_tokens as _shared_get_max_context_tokens
from .context_utils import get_raw_context_window as _shared_get_raw_context_window
from ..tools.file import FileTool

# Handler Registry（模块化工具执行）
from ..tools.handlers import SystemHandlerRegistry
from ..tools.handlers.browser import create_handler as create_browser_handler
from ..tools.handlers.config import create_handler as create_config_handler
from ..tools.handlers.desktop import create_handler as create_desktop_handler
from ..tools.handlers.filesystem import create_handler as create_filesystem_handler
from ..tools.handlers.im_channel import create_handler as create_im_channel_handler
from ..tools.handlers.mcp import create_handler as create_mcp_handler
from ..tools.handlers.memory import create_handler as create_memory_handler
from ..tools.handlers.persona import create_handler as create_persona_handler
from ..tools.handlers.plan import create_plan_handler
from ..tools.handlers.profile import create_handler as create_profile_handler
from ..tools.handlers.scheduled import create_handler as create_scheduled_handler
from ..tools.handlers.skills import create_handler as create_skills_handler
from ..tools.handlers.sticker import create_handler as create_sticker_handler
from ..tools.handlers.system import create_handler as create_system_handler
from ..tools.handlers.agent import create_handler as create_agent_tool_handler
from ..tools.handlers.agent_hub import create_handler as create_agent_hub_handler
from ..tools.handlers.agent_package import create_handler as create_agent_package_handler
from ..tools.handlers.skill_store import create_handler as create_skill_store_handler
from ..tools.handlers.web_search import create_handler as create_web_search_handler

# MCP 系统
from ..tools.mcp import mcp_client
from ..tools.mcp_catalog import mcp_catalog as _shared_mcp_catalog
from ..tools.shell import ShellTool
from ..tools.web import WebTool
from .agent_state import AgentState
from .brain import Brain, Context
from .context_manager import ContextManager
from .errors import UserCancelledError
from .identity import Identity
from .prompt_assembler import PromptAssembler
from .ralph import RalphLoop, Task, TaskResult
from .reasoning_engine import ReasoningEngine
from .response_handler import (
    ResponseHandler,
    clean_llm_response,
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
    """延迟加载桌面自动化模块。

    pyautogui 在部分 Windows 环境下初始化极慢甚至卡死，
    通过环境变量 OPENAKITA_SKIP_DESKTOP=1 可完全跳过。
    """
    global _DESKTOP_AVAILABLE, _desktop_tool_handler
    if _DESKTOP_AVAILABLE is not None:
        return _DESKTOP_AVAILABLE
    if sys.platform != "win32" or os.environ.get("OPENAKITA_SKIP_DESKTOP", ""):
        _DESKTOP_AVAILABLE = False
        return False
    try:
        from ..tools.desktop import DESKTOP_TOOLS, DesktopToolHandler  # noqa: F811
        _desktop_tool_handler = DesktopToolHandler()
        _DESKTOP_AVAILABLE = True
    except ImportError:
        _DESKTOP_AVAILABLE = False
    return _DESKTOP_AVAILABLE

logger = logging.getLogger(__name__)

# 上下文管理常量（部分迁移至 context_manager.py，压缩相关仍需就地定义）
from .context_manager import CHARS_PER_TOKEN, CHUNK_MAX_TOKENS
COMPRESSION_RATIO = 0.15
LARGE_TOOL_RESULT_THRESHOLD = 5000
MIN_RECENT_TURNS = 4

# 小上下文窗口模型的核心工具白名单（仅保留最基本的执行能力）
SMALL_CTX_CORE_TOOLS = {
    "run_shell", "read_file", "write_file", "edit_file", "list_directory",
    "grep", "ask_user", "get_tool_info",
}
# 中等上下文窗口模型额外包含的工具
MEDIUM_CTX_EXTRA_TOOLS = {
    "add_memory", "search_memory", "get_memory_stats",
    "list_skills", "get_skill_info", "run_skill_script",
    "web_search", "browser_navigate",
    "call_mcp_tool", "list_mcp_tools",
    "enable_thinking",
    "glob", "delete_file",
}

# Prompt Compiler 系统提示词（两段式 Prompt 第一阶段）
PROMPT_COMPILER_SYSTEM = """【角色】
你是 Prompt Compiler，不是解题模型。

【输入】
用户的原始请求。

【目标】
将请求转化为一个结构化、明确、可执行的任务定义。

【输出结构】
请用以下 YAML 格式输出：

```yaml
task_type: [任务类型: question/action/creation/analysis/reminder/other]
goal: [一句话描述任务目标]
inputs:
  given: [已提供的信息列表]
  missing: [缺失但可能需要的信息列表，如果没有则为空]
constraints: [约束条件列表，如果没有则为空]
output_requirements: [输出要求列表]
risks_or_ambiguities: [风险或歧义点列表，如果没有则为空]
```

【规则】
- 不要解决任务
- 不要给建议
- 不要输出最终答案
- 不要假设执行能力的限制（如"AI无法操作浏览器"等）
- 只输出 YAML 格式的结构化任务定义
- 保持简洁，每项不超过一句话

【示例】
用户: "帮我写一个Python脚本，读取CSV文件并统计每列的平均值"

输出:
```yaml
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
output_requirements:
  - 可执行的Python脚本
  - 能够读取CSV文件
  - 输出每列的平均值
constraints: []
risks_or_ambiguities:
  - 未指定如何处理包含非数值数据的列
  - 未指定输出格式（打印到控制台还是保存到文件）
```"""


def _collect_preset_referenced_skills() -> set[str]:
    """Collect all skill names referenced by system preset agents."""
    try:
        from openakita.agents.presets import SYSTEM_PRESETS
        skills: set[str] = set()
        for preset in SYSTEM_PRESETS:
            skills.update(preset.skills)
        return skills
    except Exception:
        return set()


class Agent:
    """
    OpenAkita 主类

    一个全能自进化AI助手，基于 Ralph Wiggum 模式永不放弃。
    """

    # 基础工具定义 (Claude API tool use format)
    # BASE_TOOLS 已移至 tools/definitions/ 目录
    # 通过 from ..tools.definitions import BASE_TOOLS 导入

    # 说明：历史上这里用类变量保存 IM 上下文，存在并发串台风险。
    # 现在改为使用 `openakita.core.im_context` 中的 contextvars（协程隔离）。
    _current_im_session = None  # legacy: 保留字段避免外部引用崩溃（不再使用）
    _current_im_gateway = None  # legacy: 保留字段避免外部引用崩溃（不再使用）

    # 停止任务的指令列表（用户发送这些指令时会立即停止当前任务）
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
        "/stop", "/停止", "/取消", "/cancel", "/abort",
        "kill", "kill all",
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
        "/skip", "/跳过",
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
        "_inherited_task_key", default=0,
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
    ):
        self.name = name or settings.agent_name

        # 初始化核心组件
        self.identity = Identity()
        self.brain = Brain(api_key=api_key)
        self.ralph = RalphLoop(
            max_iterations=settings.max_iterations,
            on_iteration=self._on_iteration,
            on_error=self._on_error,
        )

        # 初始化基础工具
        self.shell_tool = ShellTool(default_cwd=str(settings.project_root))
        self.file_tool = FileTool()
        self.web_tool = WebTool()

        # 初始化技能系统 (SKILL.md 规范)
        self.skill_registry = SkillRegistry()
        self.skill_loader = SkillLoader(self.skill_registry)
        self.skill_catalog = SkillCatalog(self.skill_registry)

        # 延迟导入自进化系统（避免循环导入）
        from ..evolution.generator import SkillGenerator

        self.skill_generator = SkillGenerator(
            brain=self.brain,
            skills_dir=settings.skills_path,
            skill_registry=self.skill_registry,
        )

        # MCP 系统（全局共享：mcp_client 和 mcp_catalog 为模块级单例，
        # 所有 Agent 实例（含 pool agent）共享同一份服务器配置和连接状态）
        self.mcp_client = mcp_client
        self.mcp_catalog = _shared_mcp_catalog
        self.browser_manager = None  # 在 _start_builtin_mcp_servers 中启动
        self.pw_tools = None
        self._builtin_mcp_count = 0

        # 恢复运行时状态（必须在工具目录构建之前，否则 multi_agent_enabled 等可能还是旧值）
        from ..config import runtime_state
        runtime_state.load()

        # 系统工具目录（渐进式披露）
        _all_tools = list(BASE_TOOLS)
        if _ensure_desktop():
            from ..tools.desktop import DESKTOP_TOOLS as _DT
            _all_tools.extend(_DT)
        if settings.multi_agent_enabled:
            from ..tools.definitions.agent import AGENT_TOOLS
            from ..tools.definitions.org_setup import ORG_SETUP_TOOLS
            _all_tools.extend(AGENT_TOOLS)
            _all_tools.extend(ORG_SETUP_TOOLS)
        self.tool_catalog = ToolCatalog(_all_tools)

        # 定时任务调度器
        self.task_scheduler = None  # 在 initialize() 中启动

        # 记忆系统
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
        )

        # 用户档案管理器
        self.profile_manager = get_profile_manager()

        # ==================== 人格系统 + 活人感 + 表情包 ====================
        from ..tools.sticker import StickerEngine
        from .persona import PersonaManager
        from .proactive import ProactiveConfig, ProactiveEngine
        from .trait_miner import TraitMiner

        # 人格管理器
        self.persona_manager = PersonaManager(
            personas_dir=settings.personas_path,
            active_preset=settings.persona_name,
        )

        # 偏好挖掘引擎（传入 brain，由 LLM 分析偏好而非关键词匹配）
        self.trait_miner = TraitMiner(persona_manager=self.persona_manager, brain=self.brain)

        # 活人感引擎
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

        # 表情包引擎
        self.sticker_engine = StickerEngine(
            data_dir=settings.sticker_data_path,
        ) if settings.sticker_enabled else None

        # 动态工具列表（基础工具 + 技能工具）
        self._tools = list(BASE_TOOLS)
        self._skill_tool_names: set[str] = set()

        # Add desktop tools on Windows (lazy load to avoid slow pyautogui init)
        if _ensure_desktop():
            from ..tools.desktop import DESKTOP_TOOLS as _DT2
            self._tools.extend(_DT2)
            logger.info(f"Desktop automation tools enabled ({len(_DT2)} tools)")

        # Multi-agent tools (only when enabled)
        if settings.multi_agent_enabled:
            from ..tools.definitions.agent import AGENT_TOOLS
            from ..tools.definitions.org_setup import ORG_SETUP_TOOLS
            self._tools.extend(AGENT_TOOLS)
            self._tools.extend(ORG_SETUP_TOOLS)
            logger.info(f"Multi-agent tools enabled ({len(AGENT_TOOLS) + len(ORG_SETUP_TOOLS)} tools)")

        # Platform hub tools (Agent Hub + Skill Store, only when enabled)
        if settings.hub_enabled:
            from ..tools.definitions import HUB_TOOLS
            self._tools.extend(HUB_TOOLS)
            logger.info(f"Platform hub tools enabled ({len(HUB_TOOLS)} tools)")

        self._update_shell_tool_description()

        # 对话上下文
        self._context = Context()
        self._conversation_history: list[dict] = []

        # 消息中断机制
        self._current_session = None  # 当前会话引用
        self._interrupt_enabled = True  # 是否启用中断检查

        # 任务取消机制 — 统一使用 TaskState.cancelled / agent_state.is_task_cancelled
        # (旧 self._task_cancelled 已废弃，取消状态绑定到 TaskState 实例，避免全局竞态)

        # Sub-agent call flag: set by orchestrator._call_agent()
        self._is_sub_agent_call = False
        # Agent tool names to exclude when running as sub-agent
        self._agent_tool_names = frozenset(
            {"delegate_to_agent", "delegate_parallel", "create_agent", "spawn_agent"}
        )

        # 当前任务监控器（仅在 IM 任务执行期间设置；供 system 工具动态调整超时策略）
        self._current_task_monitor = None

        # 状态
        self._initialized = False
        self._running = False

        self._last_finalized_trace: list[dict] = []

        # Agent profile and custom prompt (set by AgentFactory)
        self._agent_profile = None
        self._custom_prompt_suffix: str = ""
        self._preferred_endpoint: str | None = None

        # Handler Registry（模块化工具执行）
        self.handler_registry = SystemHandlerRegistry()
        self._init_handlers()
        self._core_tool_names: set[str] = set(self.handler_registry.list_tools())

        # === 工具并行执行基础设施（默认不开启并行，tool_max_parallel=1）===
        # 并行执行只影响“同一轮模型返回多个 tool_use/tool_calls”的工具批处理阶段。
        # 注意：browser/desktop/mcp 等状态型工具默认互斥，避免并发踩踏状态。
        self._tool_semaphore = asyncio.Semaphore(max(1, settings.tool_max_parallel))
        self._tool_handler_locks: dict[str, asyncio.Lock] = {}
        for hn in ("browser", "desktop", "mcp"):
            self._tool_handler_locks[hn] = asyncio.Lock()
        self._task_monitor_lock = asyncio.Lock()

        # ==================== Phase 2: 新增子模块 ====================
        # 结构化状态管理
        self.agent_state = AgentState()
        self._pending_cancels: dict[str, str] = {}  # session_id → reason

        # 工具执行引擎（委托自 _execute_tool / _execute_tool_calls_batch）
        self.tool_executor = ToolExecutor(
            handler_registry=self.handler_registry,
            max_parallel=max(1, settings.tool_max_parallel),
        )

        # 上下文管理器（委托自 _compress_context 等）
        self.context_manager = ContextManager(brain=self.brain)

        # 响应处理器（委托自 _verify_task_completion 等）
        self.response_handler = ResponseHandler(
            brain=self.brain,
            memory_manager=self.memory_manager,
        )

        # 技能管理器（委托自 _install_skill / _load_installed_skills 等）
        self.skill_manager = SkillManager(
            skill_registry=self.skill_registry,
            skill_loader=self.skill_loader,
            skill_catalog=self.skill_catalog,
            shell_tool=self.shell_tool,
            on_skill_loaded=self._on_skill_manager_loaded,
        )

        # 提示词组装器（委托自 _build_system_prompt 等）
        self.prompt_assembler = PromptAssembler(
            tool_catalog=self.tool_catalog,
            skill_catalog=self.skill_catalog,
            mcp_catalog=self.mcp_catalog,
            memory_manager=self.memory_manager,
            profile_manager=self.profile_manager,
            brain=self.brain,
            persona_manager=self.persona_manager,
        )

        # 推理引擎（替代 _chat_with_tools_and_context）
        self.reasoning_engine = ReasoningEngine(
            brain=self.brain,
            tool_executor=self.tool_executor,
            context_manager=self.context_manager,
            response_handler=self.response_handler,
            agent_state=self.agent_state,
            memory_manager=self.memory_manager,
        )

        logger.info(f"Agent '{self.name}' created (with refactored sub-modules)")

    @property
    def _effective_tools(self) -> list[dict]:
        """Tools available for the current call context.

        Sub-agents must not have delegation tools to prevent
        uncontrolled recursive delegation chains.

        Small context window models get a reduced tool set to save tokens.
        """
        tools = self._tools
        if self._is_sub_agent_call:
            tools = [t for t in tools if t.get("name") not in self._agent_tool_names]

        ctx = self._get_raw_context_window()
        if 0 < ctx < 8000:
            tools = [t for t in tools if t.get("name") in SMALL_CTX_CORE_TOOLS]
        elif 0 < ctx < 32000:
            allowed = SMALL_CTX_CORE_TOOLS | MEDIUM_CTX_EXTRA_TOOLS
            tools = [t for t in tools if t.get("name") in allowed]

        return tools

    def _get_tool_handler_name(self, tool_name: str) -> str | None:
        """获取工具对应的 handler 名称（用于互斥/并发策略）"""
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
        执行一批工具调用，并返回 tool_results（顺序与 tool_calls 一致）。

        并行策略：
        - 默认串行（settings.tool_max_parallel=1 或启用中断检查时）
        - 当 tool_max_parallel>1 且不需要“工具间中断检查”时，允许并行执行
        - browser/desktop/mcp handler 默认互斥锁（即使并行也不会并发执行同 handler）
        """
        executed_tool_names: list[str] = []
        delivery_receipts: list | None = None

        if not tool_calls:
            return [], executed_tool_names, delivery_receipts

        # 并行执行会降低“工具间中断检查”的插入粒度（并行时没有天然的工具间隙）
        # 默认：启用中断检查 => 串行；可通过配置显式允许并行。
        allow_parallel_with_interrupts = bool(
            getattr(settings, "allow_parallel_tools_with_interrupt_checks", False)
        )
        parallel_enabled = settings.tool_max_parallel > 1 and (
            (not allow_interrupt_checks) or allow_parallel_with_interrupts
        )

        # 获取 cancel_event / skip_event 用于工具执行竞速取消/跳过
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
                        "content": "[任务已被用户停止]",
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

            use_parallel_safe_monitor = parallel_enabled and task_monitor is not None and hasattr(
                task_monitor, "record_tool_call"
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

                # 将工具执行与 cancel_event / skip_event 三路竞速
                # 注意: 不在此处 clear_skip()，让已到达的 skip 信号自然被竞速消费
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
                    # cancel_event 先触发，工具被中断（终止整个任务）
                    logger.info(f"[StopTask] Tool {tool_name} interrupted by user cancel")
                    success = False
                    result_str = f"[工具 {tool_name} 被用户中断]"
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
                    # skip_event 先触发，仅跳过当前工具（不终止任务）
                    _skip_reason = (
                        self.agent_state.current_task.skip_reason
                        if self.agent_state and self.agent_state.current_task
                        else "用户请求跳过"
                    )
                    if self.agent_state and self.agent_state.current_task:
                        self.agent_state.current_task.clear_skip()
                    logger.info(f"[SkipStep] Tool {tool_name} skipped by user: {_skip_reason}")
                    success = True
                    result_str = f"[用户跳过了此步骤: {_skip_reason}]"
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

                # 支持多模态 tool result：处理器可返回 list（文本+图片）
                if isinstance(result, list):
                    result_content = result
                    # 提取纯文本用于日志/监控
                    result_str = "\n".join(
                        p.get("text", "") for p in result
                        if isinstance(p, dict) and p.get("type") == "text"
                    ) or "(multimodal content)"
                else:
                    result_str = str(result) if result is not None else "操作已完成"
                    result_content = result_str

                logger.info(f"[Tool] {tool_name} → {result_str}")

                if capture_delivery_receipts and tool_name == "deliver_artifacts" and result_str:
                    try:
                        import json as _json

                        parsed = _json.loads(result_str)
                        rs = parsed.get("receipts") if isinstance(parsed, dict) else None
                        if isinstance(rs, list):
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
                logger.info(f"[Tool] {tool_name} ❌ 错误: {result_str}")
                out = {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"工具执行错误: {result_str}",
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
        初始化 Agent

        Args:
            start_scheduler: 是否启动定时任务调度器（定时任务执行时应设为 False）
            lightweight: 轻量模式（sub-agent），跳过预热、表情包、人格特征等非必要初始化
        """
        if self._initialized:
            return

        # 初始化 token 用量追踪
        init_token_tracking(str(settings.db_full_path))

        # 自动生成/加载设备 ID（用于平台认证）
        if not settings.hub_device_id:
            from openakita.hub.device import get_or_create_device_id
            data_dir = Path(settings.project_root) / "data"
            settings.hub_device_id = get_or_create_device_id(data_dir)

        # 加载身份文档
        self.identity.load()

        # 加载已安装的技能
        await self._load_installed_skills()

        # 加载 MCP 配置
        if not lightweight:
            await self._load_mcp_servers()
        else:
            await self._start_builtin_mcp_servers()

        # 启动记忆会话
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8]
        self.memory_manager.start_session(session_id)
        self._current_session_id = session_id
        if hasattr(self, "_memory_handler"):
            self._memory_handler.reset_guide()

        # 启动定时任务调度器（定时任务执行时跳过，避免重复）
        if start_scheduler:
            await self._start_scheduler()

        # 设置系统提示词 (包含技能清单、MCP 清单和相关记忆)
        base_prompt = self.identity.get_system_prompt()
        self._context.system = self._build_system_prompt(base_prompt, use_compiled=True)

        if lightweight:
            self._initialized = True
            return

        # === 启动预热（把昂贵但可复用的初始化提前到启动阶段）===
        # 目标：避免首条用户消息才加载 embedding/向量库、生成清单等，导致 IM 首响应显著变慢。
        try:
            # 1) 预热清单缓存（避免每次 build_system_prompt 都重新生成）
            # 注意：这些方法内部已有缓存；这里调用一次确保缓存命中。
            with contextlib.suppress(Exception):
                self.tool_catalog.get_catalog()
            with contextlib.suppress(Exception):
                self.skill_catalog.get_catalog()
            with contextlib.suppress(Exception):
                self.mcp_catalog.get_catalog()

            # 2) 预热向量库（embedding 模型 + ChromaDB）
            # 放到线程中执行，避免阻塞事件循环；初始化完成后后续搜索会明显更快。
            if self.memory_manager.vector_store is not None:
                await asyncio.to_thread(lambda: bool(self.memory_manager.vector_store.enabled))
        except Exception as e:
            # 预热失败不应影响启动（例如 chromadb 未安装时会自动禁用）
            logger.debug(f"[Prewarm] skipped/failed: {e}")

        # === 表情包引擎初始化 ===
        if self.sticker_engine:
            try:
                await self.sticker_engine.initialize()
            except Exception as e:
                logger.debug(f"[Sticker] initialization skipped/failed: {e}")

        # === 从记忆系统加载 PERSONA_TRAIT ===
        try:
            persona_memories = [
                m.to_dict() for m in self.memory_manager._memories.values()
                if m.type.value == "persona_trait"
            ]
            if persona_memories:
                self.persona_manager.load_traits_from_memories(persona_memories)
                logger.info(f"Loaded {len(persona_memories)} persona traits from memory")
        except Exception as e:
            logger.debug(f"[Persona] trait loading skipped: {e}")

        self._initialized = True
        total_mcp = self.mcp_catalog.server_count + self._builtin_mcp_count
        logger.info(
            f"Agent '{self.name}' initialized with "
            f"{self.skill_registry.count} skills, "
            f"{total_mcp} MCP servers"
            f"{f' (builtin: {self._builtin_mcp_count})' if self._builtin_mcp_count else ''}"
        )

    def _init_handlers(self) -> None:
        """
        初始化系统工具处理器

        将各个模块的处理器注册到 handler_registry
        """
        # 文件系统
        self.handler_registry.register(
            "filesystem",
            create_filesystem_handler(self),
            ["run_shell", "write_file", "read_file", "edit_file",
             "list_directory", "grep", "glob", "delete_file"],
        )

        # 记忆系统
        self.handler_registry.register(
            "memory",
            create_memory_handler(self),
            ["consolidate_memories", "add_memory", "search_memory", "get_memory_stats",
             "list_recent_tasks", "trace_memory", "search_conversation_traces"],
        )

        # 浏览器
        self.handler_registry.register(
            "browser",
            create_browser_handler(self),
            [
                "browser_open",
                "browser_navigate",
                "browser_click",
                "browser_type",
                "browser_scroll",
                "browser_wait",
                "browser_execute_js",
                "browser_get_content",
                "browser_screenshot",
                "browser_list_tabs",
                "browser_switch_tab",
                "browser_new_tab",
                "browser_close",
                "view_image",
            ],
        )

        # 定时任务
        self.handler_registry.register(
            "scheduled",
            create_scheduled_handler(self),
            [
                "schedule_task",
                "list_scheduled_tasks",
                "cancel_scheduled_task",
                "update_scheduled_task",
                "trigger_scheduled_task",
            ],
        )

        # MCP
        self.handler_registry.register(
            "mcp",
            create_mcp_handler(self),
            [
                "list_mcp_servers",
                "get_mcp_instructions",
                "call_mcp_tool",
                "add_mcp_server",
                "remove_mcp_server",
                "connect_mcp_server",
                "disconnect_mcp_server",
                "reload_mcp_servers",
            ],
        )

        # 用户档案
        self.handler_registry.register(
            "profile",
            create_profile_handler(self),
            ["get_user_profile", "update_user_profile", "skip_profile_question"],
        )

        # Plan 模式
        self.handler_registry.register(
            "plan",
            create_plan_handler(self),
            ["create_plan", "update_plan_step", "get_plan_status", "complete_plan"],
        )

        # 系统工具
        self.handler_registry.register(
            "system",
            create_system_handler(self),
            [
                "ask_user",
                "get_tool_info",
                "get_session_logs",
                "enable_thinking",
                "set_task_timeout",
                "generate_image",
                "get_workspace_map",
            ],
        )

        # IM 渠道
        self.handler_registry.register(
            "im_channel",
            create_im_channel_handler(self),
            ["deliver_artifacts", "get_voice_file", "get_image_file", "get_chat_history",
             "get_chat_info", "get_user_info", "get_chat_members", "get_recent_messages"],
        )

        # 技能管理
        self.handler_registry.register(
            "skills",
            create_skills_handler(self),
            [
                "list_skills",
                "get_skill_info",
                "run_skill_script",
                "get_skill_reference",
                "install_skill",
                "load_skill",
                "reload_skill",
                "manage_skill_enabled",
            ],
        )

        # Web 搜索
        self.handler_registry.register(
            "web_search",
            create_web_search_handler(self),
            ["web_search", "news_search"],
        )

        # 人格系统
        self.handler_registry.register(
            "persona",
            create_persona_handler(self),
            ["switch_persona", "update_persona_trait", "toggle_proactive", "get_persona_profile"],
        )

        # 表情包
        self.handler_registry.register(
            "sticker",
            create_sticker_handler(self),
            ["send_sticker"],
        )

        # 系统配置
        self.handler_registry.register(
            "config",
            create_config_handler(self),
            ["system_config"],
        )

        # Agent 包（导入/导出）
        self.handler_registry.register(
            "agent_package",
            create_agent_package_handler(self),
            ["export_agent", "import_agent", "list_exportable_agents", "inspect_agent_package"],
        )

        # Agent Hub + Skill Store（平台交互，仅在 hub_enabled 时注册）
        if settings.hub_enabled:
            self.handler_registry.register(
                "agent_hub",
                create_agent_hub_handler(self),
                ["search_hub_agents", "install_hub_agent", "publish_agent", "get_hub_agent_detail"],
            )
            self.handler_registry.register(
                "skill_store",
                create_skill_store_handler(self),
                ["search_store_skills", "install_store_skill", "get_store_skill_detail", "submit_skill_repo"],
            )

        # 桌面工具（仅 Windows 且依赖可用时注册，与 _tools/ToolCatalog 保持一致）
        if _ensure_desktop():
            self.handler_registry.register(
                "desktop",
                create_desktop_handler(self),
                [
                    "desktop_screenshot",
                    "desktop_find_element",
                    "desktop_click",
                    "desktop_type",
                    "desktop_hotkey",
                    "desktop_scroll",
                    "desktop_window",
                    "desktop_wait",
                    "desktop_inspect",
                ],
            )

        # Multi-agent tools (only when multi_agent_enabled)
        if settings.multi_agent_enabled:
            self.handler_registry.register(
                "agent",
                create_agent_tool_handler(self),
                ["delegate_to_agent", "delegate_parallel", "spawn_agent", "create_agent"],
            )
            from ..tools.handlers.org_setup import create_handler as create_org_setup_handler
            self.handler_registry.register(
                "org_setup",
                create_org_setup_handler(self),
                ["setup_organization"],
            )

        logger.info(
            f"Initialized {len(self.handler_registry._handlers)} handlers with {len(self.handler_registry._tool_to_handler)} tools"
        )

    async def _load_installed_skills(self) -> None:
        """
        加载已安装的技能 (遵循 Agent Skills 规范)

        技能从以下目录加载:
        - skills/ (项目级别)
        - .cursor/skills/ (Cursor 兼容)
        """
        # 从所有标准目录加载
        loaded = self.skill_loader.load_all(settings.project_root)
        logger.info(f"Loaded {loaded} skills from standard directories")

        # 外部技能启用/禁用（系统技能永远启用）
        # 配置文件：<workspace>/data/skills.json
        # - 存在且有 external_allowlist => 使用用户显式选择
        # - 不存在 => 应用 DEFAULT_DISABLED_SKILLS 默认禁用列表
        try:
            cfg_path = settings.project_root / "data" / "skills.json"
            external_allowlist: set[str] | None = None
            if cfg_path.exists():
                raw = cfg_path.read_text(encoding="utf-8")
                cfg = json.loads(raw) if raw.strip() else {}
                al = cfg.get("external_allowlist", None)
                if isinstance(al, list):
                    external_allowlist = {str(x).strip() for x in al if str(x).strip()}
            effective = self.skill_loader.compute_effective_allowlist(external_allowlist)
            agent_skills = _collect_preset_referenced_skills()
            removed = self.skill_loader.prune_external_by_allowlist(
                effective, agent_referenced_skills=agent_skills,
            )
            if removed:
                logger.info(f"External skills filtered: {removed} disabled")
        except Exception as e:
            logger.warning(f"Failed to apply skills allowlist: {e}")

        # 生成技能清单 (用于系统提示)
        self._skill_catalog_text = self.skill_catalog.generate_catalog()
        logger.info(f"Generated skill catalog with {self.skill_catalog.skill_count} skills")

        # 更新工具列表，添加技能工具
        self._update_skill_tools()

    def _update_shell_tool_description(self) -> None:
        """动态更新 shell 工具描述，包含当前操作系统信息"""
        import platform

        # 获取操作系统信息
        if os.name == "nt":
            os_info = f"Windows {platform.release()} (使用 PowerShell/cmd 命令，如: dir, type, tasklist, Get-Process, findstr)"
        else:
            os_info = f"{platform.system()} (使用 bash 命令，如: ls, cat, ps aux, grep)"

        # 更新 run_shell 工具的描述
        for tool in self._tools:
            if tool.get("name") == "run_shell":
                tool["description"] = (
                    f"执行Shell命令。当前操作系统: {os_info}。"
                    "注意：请使用当前操作系统支持的命令；如果命令连续失败，请尝试不同的命令或放弃该方法。"
                )
                tool["input_schema"]["properties"]["command"]["description"] = (
                    f"要执行的Shell命令（当前系统: {os.name}）"
                )
                break

    def _update_skill_tools(self) -> None:
        """同步系统技能的 tool_name → handler 映射到 handler_registry。

        技能加载后，系统技能（system: true）可能定义了 tool_name 和 handler 字段。
        这些映射需要同步到 handler_registry，否则 LLM 调用对应工具时会返回 "Tool not found"。

        此方法执行双向同步:
        1. 添加新技能定义的映射（不覆盖 _init_handlers 内置映射）
        2. 清理已不存在于 skill_registry 中的旧映射（仅清理由技能动态添加的）
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
        """通知所有全局 Agent 实例池技能已变更。

        池中旧版本 Agent 将在下次 get_or_create 时惰性重建。
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

    def _on_skill_manager_loaded(self) -> None:
        """SkillManager 安装完技能后的回调：同步映射 + 通知池。"""
        self._update_skill_tools()
        self.notify_pools_skills_changed()

    async def _install_skill(
        self,
        source: str,
        name: str | None = None,
        subdir: str | None = None,
        extra_files: list[str] | None = None,
    ) -> str:
        """安装技能 — 委托给 SkillManager。"""
        return await self.skill_manager.install_skill(source, name, subdir, extra_files)

    async def _load_mcp_servers(self) -> None:
        """
        加载 MCP 服务器配置

        只加载项目本地的 MCP，不加载 Cursor 的（因为无法实际调用）
        """
        if not settings.mcp_enabled:
            logger.info("MCP disabled via MCP_ENABLED=false")
            await self._start_builtin_mcp_servers()
            return

        # 扫描 MCP 配置目录：内置(只读) + 工作区(可写)
        # 内置: mcps/ (随项目分发), .mcp/ (兼容)
        # 工作区: data/mcp/servers/ (AI 和用户添加的，打包模式可写)
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

        # 将扫描到的 MCP 服务器同步注册到 MCPClient（否则“目录可见但不可调用”）
        # 目录（mcp_catalog）负责发现与提示词披露；执行（mcp_client）负责真实连接与调用。
        try:
            from ..tools.mcp import MCPServerConfig

            for server in self.mcp_catalog.servers:
                if not server.identifier:
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

        # 启动内置浏览器服务
        await self._start_builtin_mcp_servers()

        # 预热 catalog 缓存（即使服务器暂无工具也应列出，方便 AI 发现并连接）
        self.mcp_catalog.generate_catalog()
        if total_count > 0:
            logger.info(f"Total MCP servers: {total_count}")
        else:
            logger.info("No MCP servers configured")

        # 自动连接：全局开关 → 连接所有；否则 → 按 per-server autoConnect 标志
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
                        logger.info(f"Auto-connected MCP server: {server_name} ({result.tool_count} tools)")
                        runtime_tools = self.mcp_client.list_tools(server_name)
                        if runtime_tools:
                            tool_dicts = [
                                {"name": t.name, "description": t.description,
                                 "input_schema": t.input_schema}
                                for t in runtime_tools
                            ]
                            count = self.mcp_catalog.sync_tools_from_client(
                                server_name, tool_dicts, force=True,
                            )
                            if count > 0:
                                synced_any = True
                    else:
                        logger.warning(f"Auto-connect to MCP server {server_name} failed: {result.error}")
                except Exception as e:
                    logger.warning(f"Auto-connect to MCP server {server_name} failed: {e}")

            if synced_any:
                logger.info("MCP catalog refreshed after auto-connect tool discovery")

    async def _start_builtin_mcp_servers(self) -> None:
        """启动内置浏览器服务 (Playwright，独立于 MCP 体系)"""
        self._builtin_mcp_count = 0

        try:
            from ..tools._import_helper import import_or_hint
            pw_hint = import_or_hint("playwright")
            if pw_hint:
                logger.warning(f"浏览器自动化不可用: {pw_hint}")
            else:
                from ..tools.browser import BrowserManager, PlaywrightTools

                self.browser_manager = BrowserManager()
                self.pw_tools = PlaywrightTools(self.browser_manager)
                logger.info("Initialized browser service (Playwright)")
        except Exception as e:
            logger.warning(f"Failed to start browser service: {e}")

    async def _start_scheduler(self) -> None:
        """启动定时任务调度器"""
        try:
            from ..scheduler import TaskScheduler
            from ..scheduler.executor import TaskExecutor

            # 创建执行器（gateway 稍后通过 set_scheduler_gateway 设置）
            self._task_executor = TaskExecutor(timeout_seconds=settings.scheduler_task_timeout)
            # 预设 persona/memory/proactive 引用，供活人感心跳等系统任务使用
            self._task_executor.persona_manager = getattr(self, "persona_manager", None)
            self._task_executor.memory_manager = getattr(self, "memory_manager", None)
            self._task_executor.proactive_engine = getattr(self, "proactive_engine", None)

            # 创建调度器
            self.task_scheduler = TaskScheduler(
                storage_path=settings.project_root / "data" / "scheduler",
                executor=self._task_executor.execute,
            )

            # 启动调度器
            await self.task_scheduler.start()

            # 注册内置系统任务（每日记忆整理 + 每日自检）
            await self._register_system_tasks()

            # 发布为全局单例，供多 Agent 模式下的 pool agent 共享
            from ..scheduler import set_active_scheduler
            set_active_scheduler(self.task_scheduler, self._task_executor)

            stats = self.task_scheduler.get_stats()
            logger.info(f"TaskScheduler started with {stats['total_tasks']} tasks")

        except Exception as e:
            logger.warning(f"Failed to start scheduler: {e}")
            self.task_scheduler = None

    async def _register_system_tasks(self) -> None:
        """
        注册内置系统任务

        包括:
        - 记忆整理（凌晨 3:00，适应期内每 N 小时一次）
        - 系统自检（凌晨 4:00）
        - 活人感心跳（每 30 分钟）
        """
        from ..config import settings
        from ..scheduler import ScheduledTask, TriggerType
        from ..scheduler.consolidation_tracker import ConsolidationTracker
        from ..scheduler.task import TaskType

        if not self.task_scheduler:
            return

        # 初始化整理时间追踪器
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

        # 任务 1: 记忆整理
        # 适应期: 改为 interval 模式（每 N 小时一次）
        # 正常期: cron 模式（凌晨 3:00）
        memory_task_id = "system_daily_memory"
        existing_memory_task = self.task_scheduler.get_task(memory_task_id)

        if is_onboarding:
            interval_h = settings.memory_consolidation_onboarding_interval_hours
            desired_trigger = TriggerType.INTERVAL
            desired_config = {"interval_minutes": interval_h * 60}
            desired_desc = f"适应期记忆整理（每 {interval_h} 小时）"
        else:
            desired_trigger = TriggerType.CRON
            desired_config = {"cron": "0 3 * * *"}
            desired_desc = "整理对话历史，提取记忆，刷新 MEMORY.md"

        if memory_task_id not in existing_ids:
            memory_task = ScheduledTask(
                id=memory_task_id,
                name="记忆整理",
                trigger_type=desired_trigger,
                trigger_config=desired_config,
                action="system:daily_memory",
                prompt="执行记忆整理：整理对话历史，提取精华记忆，刷新 MEMORY.md",
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
                # 适应期 ↔ 正常期切换时，更新触发器
                if existing_memory_task.trigger_type != desired_trigger:
                    existing_memory_task.trigger_type = desired_trigger
                    existing_memory_task.trigger_config = desired_config
                    existing_memory_task.description = desired_desc
                    changed = True
                    # 同步更新内存中的 trigger 实例
                    from ..scheduler.triggers import Trigger
                    new_trigger = Trigger.from_config(desired_trigger.value, desired_config)
                    self.task_scheduler._triggers[memory_task_id] = new_trigger
                    existing_memory_task.next_run = new_trigger.get_next_run_time()
                    logger.info(f"Switched memory task trigger to {desired_trigger.value}: {desired_desc}")
                if changed:
                    self.task_scheduler._save_tasks()

        # 任务 2: 系统自检（凌晨 4:00）
        if "system_daily_selfcheck" not in existing_ids:
            selfcheck_task = ScheduledTask(
                id="system_daily_selfcheck",
                name="系统自检",
                trigger_type=TriggerType.CRON,
                trigger_config={"cron": "0 4 * * *"},
                action="system:daily_selfcheck",
                prompt="执行系统自检：分析 ERROR 日志，尝试修复工具问题，生成报告",
                description="分析 ERROR 日志、尝试修复工具问题、生成报告",
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
                    self.task_scheduler._save_tasks()

        # 任务 3: 活人感心跳（每 30 分钟触发）
        try:
            if "system_proactive_heartbeat" not in existing_ids:
                heartbeat_task = ScheduledTask(
                    id="system_proactive_heartbeat",
                    name="活人感心跳",
                    trigger_type=TriggerType.INTERVAL,
                    trigger_config={"interval_minutes": 30},
                    action="system:proactive_heartbeat",
                    prompt="检查是否需要发送主动消息（问候/提醒/跟进）",
                    description="定时检查并发送主动消息",
                    task_type=TaskType.TASK,
                    enabled=True,
                    deletable=False,
                    metadata={"notify_on_start": False, "notify_on_complete": False},
                )
                await self.task_scheduler.add_task(heartbeat_task)
                logger.info("Registered system task: proactive_heartbeat (every 30 min)")
        except Exception as e:
            logger.warning(f"Failed to register proactive_heartbeat task: {e}")

        # 任务 4: 工作区定时备份（根据用户设置）
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
                        name="工作区备份",
                        trigger_type=TriggerType.CRON,
                        trigger_config={"cron": cron},
                        action="system:workspace_backup",
                        prompt="执行工作区数据备份",
                        description="定时备份工作区配置和用户数据",
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
                    existing_bt.enabled = backup_enabled
                    self.task_scheduler._save_tasks()
        except Exception as e:
            logger.warning(f"Failed to register workspace_backup task: {e}")

    def _build_system_prompt(
        self, base_prompt: str, task_description: str = "", use_compiled: bool = False,
        session_type: str = "cli",
    ) -> str:
        """
        构建系统提示词 (动态生成，包含技能清单、MCP 清单和相关记忆)

        遵循规范的渐进式披露:
        - Agent Skills: name + description 在系统提示中
        - MCP: server + tool name + description 在系统提示中
        - Memory: 相关记忆按需注入
        - Tools: 从 BASE_TOOLS 动态生成
        - User Profile: 首次引导或日常询问

        Args:
            base_prompt: 基础提示词 (身份信息，use_compiled=True 时忽略)
            task_description: 任务描述 (用于检索相关记忆)
            use_compiled: 是否使用编译管线 (v2)，降低约 55% token 消耗

        Returns:
            完整的系统提示词
        """
        # 使用编译管线 (v2) - 降低 token 消耗（同步版本，启动时使用）
        if use_compiled:
            return self._build_system_prompt_compiled_sync(task_description, session_type=session_type)

        # 技能清单 (Agent Skills 规范) - 每次动态生成，确保新创建的技能被包含
        skill_catalog = self.skill_catalog.generate_catalog()

        # MCP 清单（从全局共享的 MCPCatalog 获取，内部自动缓存和失效）
        mcp_catalog = self.mcp_catalog.get_catalog()

        # 相关记忆 (按任务相关性注入)
        memory_context = self.memory_manager.get_injection_context(task_description)

        # 动态生成工具列表
        tools_text = self._generate_tools_text()

        # 用户档案收集提示 (首次引导或日常询问)
        profile_prompt = ""
        if self.profile_manager.is_first_use():
            profile_prompt = self.profile_manager.get_onboarding_prompt()
        else:
            profile_prompt = self.profile_manager.get_daily_question_prompt()

        # 系统环境信息
        import os
        import platform

        system_info = f"""## 运行环境

- **操作系统**: {platform.system()} {platform.release()}
- **当前工作目录**: {os.getcwd()}
- **临时目录**:
  - Windows: 使用当前目录下的 `data/temp/` 或 `%TEMP%`
  - Linux/macOS: 使用当前目录下的 `data/temp/` 或 `/tmp`
- **建议**: 创建临时文件时优先使用 `data/temp/` 目录（相对于当前工作目录）

## ⚠️ 重要：运行时状态不持久化

**服务重启后以下状态会丢失，不能依赖会话历史记录判断当前状态：**

| 状态 | 重启后 | 正确做法 |
|------|--------|----------|
| 浏览器 | **已关闭** | 必须先调用 `browser_open` 确认状态，不能假设已打开 |
| 变量/内存数据 | **已清空** | 通过工具重新获取，不能依赖历史 |
| 临时文件 | **可能清除** | 重新检查文件是否存在 |
| 网络连接 | **已断开** | 需要重新建立连接 |

**⚠️ 会话历史中的"成功打开浏览器"等记录只是历史，不代表当前状态！每次执行任务必须通过工具调用获取实时状态。**
"""

        # 工具使用指南
        tools_guide = """
## 工具体系说明

你有三类工具可以使用，**它们都是工具，都可以调用**：

### 1. 系统工具（渐进式披露）

系统内置的核心工具，采用渐进式披露：

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "Available System Tools" 清单 | 了解有哪些工具可用 |
| 2 | `get_tool_info(tool_name)` | 获取工具的完整参数定义 |
| 3 | 直接调用工具 | 如 `read_file(path="...")` |

**工具类别**：文件系统、浏览器、记忆、定时任务、用户档案等

### 2. Skills 技能（渐进式披露）

可扩展的能力模块，采用渐进式披露：

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "Available Skills" 清单 | 了解有哪些技能可用 |
| 2 | `get_skill_info(skill_name)` | 获取技能的详细使用说明 |
| 3 | `run_skill_script(skill_name, script_name)` | 执行技能提供的脚本 |

**特点**：
- `install_skill` - 从 URL/Git 安装新技能
- `load_skill` - 加载新创建的技能（用于 skill-creator 创建后）
- `reload_skill` - 重新加载已修改的技能
- 缺少工具时，使用 `skill-creator` 技能创建新技能

### 3. MCP 外部服务（全量暴露）

MCP (Model Context Protocol) 连接外部服务，**工具定义已全量展示**：

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "MCP Servers" 清单 | 包含完整的工具定义和参数 |
| 2 | `call_mcp_tool(server, tool_name, arguments)` | 直接调用 |

**特点**：连接数据库、API 等外部服务

### 工具选择原则

1. **系统工具**：文件操作、命令执行、浏览器、记忆等基础能力
2. **Skills**：复杂任务、特定领域能力、可复用的工作流
3. **MCP**：外部服务集成（数据库、第三方 API）
4. **找不到工具？使用 `skill-creator` 技能创建一个！**

**记住：这三类都是工具，都可以调用，不要说"我没有这个能力"！**
"""

        prompt = f"""{base_prompt}

{system_info}
{skill_catalog}
{mcp_catalog}
{memory_context}

{tools_text}

{tools_guide}

## 核心原则 (最高优先级!!!)

### 第一铁律：任务型请求必须使用工具

**⚠️ 先判断请求类型，再决定是否调用工具！**

| 请求类型 | 示例 | 处理方式 |
|---------|------|----------|
| **任务型** | "打开百度"、"提醒我开会"、"查天气" | ✅ **必须调用工具** |
| **对话型** | "你好"、"什么是机器学习"、"谢谢" | ✅ 可直接回复 |

**任务型请求的正确做法**：
| 场景 | ❌ 错误 | ✅ 正确 |
|------|--------|--------|
| 用户要提醒 | "好的我会提醒你" | 调用 schedule_task |
| 用户要查信息 | 凭记忆回答 | 调用 web_search |
| 用户要操作文件 | 描述怎么操作 | 调用 write_file/read_file |

**对话型请求**：直接回复即可，不需要调用工具。

### 第二铁律：没有工具就创造工具

**绝不说"我没有这个能力"！立即行动：**

**方式一：临时脚本（一次性任务）**
```
# 根据运行环境信息中的当前工作目录和临时目录建议
write_file("data/temp/task.py", "脚本内容")
run_shell("python data/temp/task.py")
```

**方式二：搜索安装（有现成的）**
```
search_github → install_skill → 使用
```

**方式三：创建技能（需要复用）**
```
使用 skill-creator 技能 → 创建 SKILL.md → 保存到 skills/<name>/ → 调用 load_skill 加载 → 使用
```

**方式四：修改已有技能**
```
修改 skills/<name>/SKILL.md → 调用 reload_skill 重新加载 → 修改立即生效
```

**从"不会"到"会"只需要几分钟！**

### 第三铁律：问题自己解决

**遇到问题，默认自己解决，不要甩给用户：**

- 报错了？自己读日志、分析、修复
- 缺信息？自己用工具查找
- 不确定？先尝试，失败了再换方法
- **只有完全无法解决才询问用户**

### 第四铁律：永不放弃

- 第一次失败？换个方法再试
- 第二次失败？再换一个
- 工具不够用？创建新工具
- 信息不完整？主动去查找

**禁止说"我做不到"、"这超出了我的能力"、"请你自己..."！**
**正确做法：分析问题 → 搜索方案 → 获取工具 → 执行任务 → 验证结果**

---

## 重要提示

### 深度思考模式 (Thinking Mode)

**默认启用 thinking 模式**，这样可以保证回答质量。

如果遇到非常简单的任务（如：简单问候、快速提醒），可以调用 `enable_thinking(enabled=false)` 临时关闭以加快响应。
大多数情况下保持默认启用即可，不需要主动管理。

### Plan 模式（复杂任务必须使用！）

**当任务需要超过 2 步完成时，先调用 create_plan 创建计划：**

**触发条件**：
- 用户请求中有"然后"、"接着"、"之后"等词
- 涉及多个工具协作（如：打开网页 + 搜索 + 截图 + 发送）
- 需要依次完成多个操作

**执行流程**：
1. `create_plan` → 创建计划，通知用户
2. 执行步骤 → `update_plan_step` 更新状态
3. 重复 2 直到所有步骤完成
4. `complete_plan` → 生成总结

**示例**：
用户："打开百度搜索天气并截图发我"
→ create_plan → browser_navigate("https://www.baidu.com/s?wd=天气") + browser_screenshot + update_plan_step → deliver_artifacts + complete_plan

### 工具调用
- 工具直接使用工具名调用，不需要任何前缀
- **提醒/定时任务必须使用 schedule_task 工具**，不要只是回复"好的"
- 当用户说"X分钟后提醒我"时，立即调用 schedule_task 创建任务

### 主动沟通

- 对话型请求：直接回答即可，不需要固定的“收到/开始处理”确认语。
- 任务型请求：在关键节点给出简短进度与结果（避免刷屏）。
- 如涉及附件交付：使用 `deliver_artifacts` 并以回执为证据（不要空口宣称“已发送/已交付”）。

### 定时任务/提醒 (极其重要!!!)

**当用户请求设置提醒、定时任务时，你必须立即调用 schedule_task 工具！**
**禁止只回复"好的，我会提醒你"这样的文字！那样任务不会被创建！**
**只有调用了 schedule_task 工具，任务才会真正被调度执行！**

**⚠️ 任务类型判断 (task_type) - 这是最重要的决策！**

**默认使用 reminder！除非明确需要AI执行操作才用 task！**

✅ **reminder** (90%的情况都是这个!):
- 只需要到时间发一条消息提醒用户
- 例子: "提醒我喝水"、"叫我起床"、"站立提醒"、"开会提醒"、"午睡提醒"
- 特点: 用户说"提醒我xxx"、"叫我xxx"、"通知我xxx"

❌ **task** (仅10%的特殊情况):
- 需要AI在触发时执行查询、操作、截图等
- 例子: "查天气告诉我"、"截图发给我"、"执行脚本"、"帮我发消息给别人"
- 特点: 用户说"帮我做xxx"、"执行xxx"、"查询xxx"

**创建任务后，必须明确告知用户**:
- reminder: "好的，到时间我会提醒你：[提醒内容]" (只发一条消息)
- task: "好的，到时间我会自动执行：[任务内容]" (AI会运行并汇报结果)

调用 schedule_task 时的参数:

1. **简单提醒** (task_type="reminder"):
   - name: "喝水提醒"
   - description: "提醒用户喝水"
   - task_type: "reminder"
   - trigger_type: "once"
   - trigger_config: {{"run_at": "2026-02-01 10:00"}}
   - reminder_message: "⏰ 该喝水啦！记得保持水分摄入哦~"

2. **复杂任务** (task_type="task"):
   - name: "每日天气查询"
   - description: "查询今日天气并告知用户"
   - task_type: "task"
   - trigger_type: "cron"
   - trigger_config: {{"cron": "0 8 * * *"}}
   - prompt: "查询今天的天气，并以友好的方式告诉用户"

**触发类型**:
- once: 一次性，trigger_config 包含 run_at
- interval: 间隔执行，trigger_config 包含 interval_minutes
- cron: 定时执行，trigger_config 包含 cron 表达式

**再次强调：收到提醒请求时，第一反应就是调用 schedule_task 工具！**

### 系统已内置功能 (不需要自己实现!)

以下功能**系统已经内置**，当用户提到时，不要尝试"开发"或"实现"，而是直接使用：

1. **语音转文字** - 系统**已自动处理**语音识别！
   - 用户发送的语音消息会被系统**自动**转写为文字（通过在线 STT 服务）
   - ⚠️ **消息来源判断规则**（必须严格遵守）：
     - 带 `[来源:语音转写]` 前缀 → 该消息是语音消息的转写结果
     - 带 `[语音转文字: ...]` 或 `[语音内容: ...]` 标签 → 该消息包含语音转写内容
     - **没有以上任何标记的纯文本 → 这是用户键盘输入的文本消息，不是语音！**
   - **绝对禁止**：把没有语音标记的普通文本当作"语音命令"来处理或要求确认
   - 如果看到 `[语音: X秒]` 但没有文字内容，说明自动识别失败
   - **只有**在自动识别失败时（如看到"语音识别失败"提示），才需要手动处理语音文件
   - ⚠️ **重要**：不要每次收到语音消息都调用语音识别工具！系统已经自动处理了！

2. **图片理解** - 用户发送的图片会自动传递给你进行多模态理解
   - 你可以直接"看到"用户发送的图片并描述或分析

3. **Telegram 配对** - 已内置配对验证机制

**当用户说"帮我实现语音转文字"时**：
- ❌ 不要开始写代码、安装依赖、配置 ffmpeg
- ❌ 不要调用语音识别技能或工具去处理
- ✅ 告诉用户"语音转文字已内置（需配置在线 STT 端点），请发送语音测试"

**语音消息处理流程**：
1. 用户发送语音 → 2. 系统自动下载并通过在线 STT 转文字 → 3. 你收到的是带 `[来源:语音转写]` 前缀的转写文字
4. 只有当你看到"[语音识别失败]"或"自动识别失败"时，才需要用 get_voice_file 工具获取文件路径并手动处理
5. **再次强调**：没有语音标记的消息就是普通文本输入，直接按文本处理即可

### 记忆使用原则
**上下文优先**：当前对话内容永远优先于记忆中的信息。
**不要让记忆主导对话**——每次对话都是新鲜的开始，记忆中的事情等用户主动提起或真正相关时再说。
记忆系统的详细使用说明见系统提示词中的"你的记忆系统"章节。

### 诚实原则 (极其重要!!!)
**绝对禁止编造不存在的功能或进度！**

❌ **严禁以下行为**：
- 声称"正在运行"、"已完成"但实际没有创建任何文件/脚本
- 在回复中贴一段代码假装在执行，但实际没有调用任何工具
- 声称"每X秒监控"但没有创建对应的定时任务
- 承诺"5分钟内完成"但根本没有开始执行

✅ **正确做法**：
- 如果需要创建脚本，必须调用 write_file 工具实际写入
- 如果需要定时任务，必须调用 schedule_task 工具实际创建
- 如果做不到，诚实告知"这个功能我目前无法实现，原因是..."
- 如果需要时间开发，先实际开发完成，再告诉用户结果

**用户信任比看起来厉害更重要！宁可说"我做不到"也不要骗人！**
{profile_prompt}"""
        
        if self._custom_prompt_suffix:
            prompt = prompt + f"\n\n{self._custom_prompt_suffix}"

        prompt += self._build_multi_agent_prompt_section()
        
        return prompt

    def _build_system_prompt_compiled_sync(self, task_description: str = "", session_type: str = "cli") -> str:
        """同步版本：启动时构建初始系统提示词（此时事件循环可能未就绪）"""
        if getattr(self, "_org_context", None):
            ctx = getattr(self, "_context", None)
            if ctx and hasattr(ctx, "system") and ctx.system:
                return ctx.system

        ctx_window = self._get_raw_context_window()
        prompt = self.prompt_assembler._build_compiled_sync(
            task_description, session_type=session_type, context_window=ctx_window,
            is_sub_agent=self._is_sub_agent_call,
        )
        if self._custom_prompt_suffix:
            prompt += f"\n\n{self._custom_prompt_suffix}"
        prompt += self._build_multi_agent_prompt_section()
        return prompt

    async def _build_system_prompt_compiled(self, task_description: str = "", session_type: str = "cli") -> str:
        """
        使用编译管线构建系统提示词 (v2)

        Token 消耗降低约 55%，从 ~6300 降到 ~2800。
        异步版本：预先异步执行向量搜索，避免阻塞事件循环。

        Args:
            task_description: 任务描述 (用于检索相关记忆)
            session_type: 会话类型 "cli" 或 "im"

        Returns:
            编译后的系统提示词
        """
        if getattr(self, "_org_context", None):
            ctx = getattr(self, "_context", None)
            if ctx and hasattr(ctx, "system") and ctx.system:
                return ctx.system

        ctx_window = self._get_raw_context_window()
        prompt = await self.prompt_assembler.build_system_prompt_compiled(
            task_description, session_type=session_type, context_window=ctx_window,
            is_sub_agent=self._is_sub_agent_call,
        )
        if self._custom_prompt_suffix:
            prompt += f"\n\n{self._custom_prompt_suffix}"
        prompt += self._build_multi_agent_prompt_section()
        return prompt

    def _build_multi_agent_prompt_section(self) -> str:
        """Generate a system prompt section describing the multi-agent system.

        Only called when settings.multi_agent_enabled is True.
        Tells the LLM: identity, roster, delegation rules with strict priority:
        delegate > spawn > create.

        Sub-agents are NOT given delegation capabilities to prevent
        recursive delegation chains (sub-agent spawning sub-sub-agents).
        """
        if getattr(self, "_org_context", None):
            return ""

        from ..agents.presets import SYSTEM_PRESETS
        from ..config import settings

        if not settings.multi_agent_enabled:
            return ""

        if self._is_sub_agent_call:
            return (
                "\n\n---\n"
                "## 🔒 子 Agent 工作模式\n"
                "你当前是被主 Agent 委派的**子 Agent**，专注完成被分配的任务即可。\n"
                "**禁止**使用 delegate_to_agent、delegate_parallel、create_agent、"
                "spawn_agent 等委派工具。不要创建或委派其他 Agent。\n"
                "直接用你自己的专业工具（如 web_search、browser、read_file 等）完成任务。\n"
            )

        profile = self._agent_profile
        if profile:
            identity_section = (
                f"你是「{profile.name}」({profile.icon})，{profile.description}。"
            )
            my_id = profile.id
        else:
            identity_section = "你是默认通用助手。"
            my_id = "default"

        # Roster — only persistent agents (system + custom)
        agents_lines = []
        for p in SYSTEM_PRESETS:
            if p.id == my_id:
                continue
            skills_desc = f"技能: {', '.join(p.skills)}" if p.skills else "技能: 全部"
            agents_lines.append(
                f"  - {p.icon} **{p.name}** (`{p.id}`) — {p.description} ({skills_desc})"
            )

        try:
            store_dir = settings.data_dir / "agents"
            if store_dir.exists():
                from ..agents.profile import get_profile_store
                store = get_profile_store()
                preset_ids = {sp.id for sp in SYSTEM_PRESETS}
                for p in store.list_all(include_ephemeral=False):
                    if p.id == my_id or p.id in preset_ids:
                        continue
                    agents_lines.append(
                        f"  - {p.icon} **{p.name}** (`{p.id}`) — {p.description}"
                    )
        except Exception:
            pass

        roster = "\n".join(agents_lines) if agents_lines else "  （暂无其他可用 Agent）"

        # Available skills list
        skills_lines = []
        try:
            catalog = getattr(self, "skill_catalog", None)
            if catalog:
                reg = getattr(catalog, "registry", None)
                if reg:
                    for entry in reg.list_all():
                        skills_lines.append(f"`{entry.name}`")
        except Exception:
            pass
        skills_list = ", ".join(skills_lines) if skills_lines else "（系统会自动分配默认技能）"

        return f"""

## 多Agent协作系统（重要 — 你必须严格遵循）

{identity_section}

你拥有一支专业 Agent 团队。你的工具优先级如下（**必须严格按此顺序选择**）：

### 🔴 绝对禁止

- **严禁**为每个新任务都创建全新 Agent — 系统已有丰富的专业 Agent 可直接使用
- **严禁**在能用 `delegate_to_agent` 直接委派时使用 `spawn_agent` 或 `create_agent`
- **严禁**在能用 `spawn_agent` 继承时使用 `create_agent` 从零创建

### 可用的 Agent 团队

{roster}

### ⚡ 工具选择优先级（必须严格遵循，从上到下判断）

**Level 1 — 直接委派 `delegate_to_agent`（首选，单个任务用这个）**

已有 Agent 能处理该任务 → 直接委派，不需要任何修改。

```
delegate_to_agent(agent_id="browser-agent", message="详细任务描述", reason="原因")
```

**Level 2 — 继承定制 `spawn_agent`（需要定制或多个并行副本时使用）**

- 已有 Agent 基本匹配但需要微调 → 继承并追加技能/提示词
- **需要同类 Agent 的多个独立副本并行工作** → 用 spawn_agent 为每个任务创建独立实例
- 每次 spawn 生成唯一 ID，天然支持并行，**任务完成后自动销毁**

```
spawn_agent(inherit_from="browser-agent", message="任务描述", extra_skills=["额外技能"], custom_prompt_overlay="补充提示", reason="原因")
```

**Level 3 — 并行委派 `delegate_parallel`（多个独立任务同时执行）**

多个独立任务可同时执行时 → 并行委派。
⚠️ 同类任务（如多个调研）→ 所有任务用**同一个 agent_id**，系统自动创建独立副本：

```
delegate_parallel(tasks=[
  {{"agent_id": "browser-agent", "message": "调研项目A..."}},
  {{"agent_id": "browser-agent", "message": "调研项目B..."}}
])
```

**Level 4 — 全新创建 `create_agent`（最后手段，极少使用）**

**仅当以上 3 种方式都不适用**（系统中完全没有相关 Agent 可用或继承）时才使用。

```
create_agent(name="名称", description="描述", skills=["技能"], custom_prompt="提示词")
```

### 🔴 任务分配原则（严格遵守）

1. **专业对口**：只把任务分配给**专业对口**的 Agent。调研任务→网探/浏览器Agent，代码任务→码哥，文档任务→文助。**严禁**把调研任务分给代码助手，或把编码任务分给文档助手。
2. **同类任务并行**：当需要多个 Agent **同时做同类事情**（如"用多个 Agent 同时调研"），应使用 `spawn_agent` 创建**同一个最合适 Agent 的多个副本**，而不是把任务分配给不相关的 Agent 凑数。例如：3 个调研任务 → spawn 3 个网探副本，而不是分给网探+码哥+数析。
3. **异类任务并行**：当多个任务**性质不同**时（如同时需要调研+写代码+分析数据），才分配给不同专业的 Agent。

- 默认创建临时 Agent（ephemeral），任务结束自动清理
- 仅当用户明确要求"记住这个Agent"时才设 `persistent=true`
- 如果系统检测到已有类似 Agent，会建议使用 spawn_agent 代替
- 可用技能列表: {skills_list}
- 每会话最多 5 个动态 Agent

### 委派判断规则

在执行任何工具之前，先判断当前任务是否应该委派：

1. **涉及文档处理**（PPT/Word/Excel/PDF） → `delegate_to_agent(agent_id="office-doc", ...)`
2. **涉及编写代码或调试** → `delegate_to_agent(agent_id="code-assistant", ...)`
3. **涉及网络搜索、浏览网页、项目调研、信息采集** → `delegate_to_agent(agent_id="browser-agent", ...)`
4. **涉及数据分析或可视化** → `delegate_to_agent(agent_id="data-analyst", ...)`
5. **已有 Agent 接近但需微调** → `spawn_agent(inherit_from="最接近的agent", ...)`
6. **多个独立同类任务并行**（如同时调研3个项目） → `delegate_parallel` 且所有任务用**同一个 agent_id**
7. **多个独立异类任务并行**（如调研+编码+分析） → `delegate_parallel` 用不同 agent_id
8. **完全没有相关 Agent** → `create_agent(...)`（极少使用）

只有当任务是**简单通用问答**、**不涉及上述任何专业领域**、或**用户明确要你亲自做**时，才自己处理。

### 关键规则

1. `message` 必须包含充分上下文（用户原始需求、相关数据、前序结论），让目标 Agent 能独立完成
2. 结果返回后，你**整合**并**用你自己的语气**回复用户
3. 委派深度上限 5 层
4. 如果委派失败或超时，告知用户并尝试自己处理
5. **有依赖的任务串行委派**（B 需要 A 的结果 → 先 A 再 B）
6. **独立任务必须用 `delegate_parallel` 并行**，不要逐个串行浪费时间
7. 对话历史中可能包含以下标记，它们记录了你之前**实际执行**过的操作：
   - **[子Agent工作总结]**：子Agent的任务、完成状态、交付的文件路径和结果摘要
   - **[执行摘要]**：你自己调用的工具及其结果
   你必须仔细阅读这些内容，把它们当作已发生的事实。不要否认已完成的操作，不要说"我没有做过"，不要重复执行已经成功完成的工作。当用户提到相关产出（文件、报告、分析结果）时，直接引用历史记录中的信息。

### 协作行为准则

- 你是协调者，主动告知用户你在调度团队（如"我让数据分析师来处理..."）
- 永远优先复用已有 Agent，避免创建不必要的新 Agent
- spawn_agent 创建的临时 Agent 任务完成即消失，放心使用
- 不要对同一任务反复试不同 Agent
- 如果所有 Agent 都处理不了，诚实告知用户"""

    def _generate_tools_text(self) -> str:
        """
        从 BASE_TOOLS 动态生成工具列表文本

        按类别分组显示，包含重要参数说明
        """
        # 工具分类
        categories = {
            "File System": ["run_shell", "write_file", "read_file", "list_directory"],
            "Skills Management": [
                "list_skills",
                "get_skill_info",
                "run_skill_script",
                "get_skill_reference",
                "install_skill",
                "load_skill",
                "reload_skill",
            ],
            "Memory Management": ["add_memory", "search_memory", "get_memory_stats"],
            "Browser Automation": [
                "browser_open",
                "browser_navigate",
                "browser_click",
                "browser_type",
                "browser_get_content",
                "browser_screenshot",
                "browser_close",
            ],
            "Scheduled Tasks": [
                "schedule_task",
                "list_scheduled_tasks",
                "cancel_scheduled_task",
                "trigger_scheduled_task",
            ],
        }

        # 构建工具名到完整定义的映射
        tool_map = {t["name"]: t for t in self._tools}

        lines = ["## Available Tools"]

        for category, tool_names in categories.items():
            # 过滤出存在的工具
            existing_tools = [(name, tool_map[name]) for name in tool_names if name in tool_map]

            if existing_tools:
                lines.append(f"\n### {category}")
                for name, tool_def in existing_tools:
                    desc = tool_def.get("description", "")
                    # 不再截断描述，完整显示
                    lines.append(f"- **{name}**: {desc}")

                    # 显示重要参数（可选）
                    schema = tool_def.get("input_schema", {})
                    schema.get("properties", {})
                    schema.get("required", [])

                    # 注意：工具的完整参数定义通过 tools=self._tools 传递给 LLM API
                    # 这里只在 system prompt 中简要列出，避免过长

        # 添加未分类的工具
        categorized = set()
        for names in categories.values():
            categorized.update(names)

        uncategorized = [(t["name"], t) for t in self._tools if t["name"] not in categorized]
        if uncategorized:
            lines.append("\n### Other Tools")
            for name, tool_def in uncategorized:
                desc = tool_def.get("description", "")
                lines.append(f"- **{name}**: {desc}")

        return "\n".join(lines)

    def _get_max_context_tokens(self) -> int:
        """动态获取当前模型的可用上下文 token 数。"""
        return _shared_get_max_context_tokens(self.brain)

    def _get_raw_context_window(self) -> int:
        """获取当前端点配置的原始 context_window 值（用于传递给预算系统）。"""
        return _shared_get_raw_context_window(self.brain)

    # NOTE: _estimate_tokens / _group_messages 已迁移至 context_utils / context_manager
    # 以下保留 v1.25.x 的兼容方法，委托给共享实现
    def _estimate_tokens(self, text: str) -> int:
        """
        估算文本的 token 数量

        使用中英文感知算法：中文约 1.5 字符/token，英文约 4 字符/token。
        与 prompt.budget.estimate_tokens() 保持一致，避免各处估算值差异过大。
        """
        if not text:
            return 0
        # 统计中文字符数量
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        total_chars = len(text)
        english_chars = total_chars - chinese_chars
        # 中文约 1.5 字符/token，英文约 4 字符/token
        chinese_tokens = chinese_chars / 1.5
        english_tokens = english_chars / 4
        return max(int(chinese_tokens + english_tokens), 1)

    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        """估算消息列表的 token 数量（委托给 context_manager 的统一算法）"""
        return self.context_manager.estimate_messages_tokens(messages)

    @staticmethod
    def _group_messages(messages: list[dict]) -> list[list[dict]]:
        """
        将消息列表分组为"工具交互组"，保证 tool_calls/tool 配对不被拆散

        分组规则：
        - assistant 消息如果包含 tool_calls（即 content 中有 type=tool_use），
          则该 assistant 和紧随其后所有 role=user 且仅含 tool_result 的消息归为同一组
        - 其他消息各自独立成组
        - 系统注入的纯文本 user 消息（如 LoopGuard 提示）独立成组

        Returns:
            分组后的列表，每个元素是一组消息（list[dict]）
        """
        if not messages:
            return []

        groups: list[list[dict]] = []
        i = 0

        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")
            content = msg.get("content", "")

            # 检测 assistant 消息是否包含 tool_use
            has_tool_calls = False
            if role == "assistant" and isinstance(content, list):
                has_tool_calls = any(
                    isinstance(item, dict) and item.get("type") == "tool_use"
                    for item in content
                )

            if has_tool_calls:
                # 开始一个工具交互组：assistant(tool_calls) + 后续的 tool_result 消息
                group = [msg]
                i += 1
                while i < len(messages):
                    next_msg = messages[i]
                    next_role = next_msg.get("role", "")
                    next_content = next_msg.get("content", "")

                    # user 消息仅含 tool_result → 属于本工具组
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

                    # tool 角色消息（OpenAI 格式）→ 也属于本工具组
                    if next_role == "tool":
                        group.append(next_msg)
                        i += 1
                        continue

                    # 其他消息类型 → 工具组结束
                    break

                groups.append(group)
            else:
                # 普通消息独立成组
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
        """将本轮用户发送的媒体/文件记录到记忆系统"""
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
        tool_calls: list[dict], tool_results: list[dict],
    ) -> list[dict]:
        """从 assistant 工具调用中提取生成的文件"""
        attachments: list[dict] = []
        _FILE_TOOLS = {"write_file", "save_file", "create_file", "download_file"}
        _MEDIA_EXTENSIONS = {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
            ".mp4", ".webm", ".mov", ".avi",
            ".mp3", ".wav", ".ogg", ".flac",
            ".pdf", ".docx", ".xlsx", ".pptx", ".csv",
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
                    attachments.append({
                        "filename": Path(path).name,
                        "local_path": path,
                        "mime_type": mime,
                        "direction": "outbound",
                    })

        for tr in tool_results:
            result_str = str(tr.get("result", tr.get("content", "")))
            for token in result_str.split():
                p = Path(token)
                if p.suffix.lower() in _MEDIA_EXTENSIONS and len(token) < 500:
                    mime = _mt.guess_type(token)[0] or "application/octet-stream"
                    attachments.append({
                        "filename": p.name,
                        "local_path": token,
                        "mime_type": mime,
                        "direction": "outbound",
                    })

        seen = set()
        unique = []
        for a in attachments:
            key = a.get("local_path") or a.get("filename", "")
            if key and key not in seen:
                seen.add(key)
                unique.append(a)
        return unique

    async def _compress_context(
        self, messages: list[dict], max_tokens: int = None, system_prompt: str = None,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """委托给统一的 context_manager.compress_if_needed()。"""
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
        """压缩超大 tool_result / tool_use.input，使用 LLM 摘要。

        逐条扫描，tokens > threshold 的 tool_result 调 LLM 压缩为精简摘要，
        保留结构（role/type 等不变）。
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
        """将任意协程包装为可被 cancel_event 立即中断的操作。

        如果 cancel_event 先于 coro 完成，抛出 UserCancelledError。
        如果 cancel_event 为 None 或任务无活跃 task，直接 await coro。
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
            reason=self._cancel_reason or "用户请求停止",
            source="cancellable_await",
        )

    async def _llm_compress_text(
        self, text: str, target_tokens: int, context_type: str = "general"
    ) -> str:
        """
        使用 LLM 压缩一段文本到目标 token 数

        Args:
            text: 要压缩的文本
            target_tokens: 目标 token 数
            context_type: 上下文类型（tool_result/tool_input/conversation）

        Returns:
            压缩后的文本
        """
        # 如果文本本身超出 LLM 上下文能处理的范围，先做硬截断
        max_input = CHUNK_MAX_TOKENS * CHARS_PER_TOKEN
        if len(text) > max_input:
            # 保留头尾，中间截断
            head_size = int(max_input * 0.6)
            tail_size = int(max_input * 0.3)
            text = text[:head_size] + "\n...(中间内容过长已省略)...\n" + text[-tail_size:]

        target_chars = target_tokens * CHARS_PER_TOKEN

        if context_type == "tool_result":
            system_prompt = (
                "你是一个信息压缩助手。请将以下工具执行结果压缩为简洁摘要，"
                "保留关键数据、状态码、错误信息和重要输出，去掉冗余细节。"
            )
        elif context_type == "tool_input":
            system_prompt = (
                "你是一个信息压缩助手。请将以下工具调用参数压缩为简洁摘要，"
                "保留关键参数名和值，去掉冗余内容。"
            )
        else:
            system_prompt = (
                "你是一个对话压缩助手。请将以下对话内容压缩为简洁摘要，"
                "保留用户意图、关键决策、执行结果和当前状态。"
            )

        _tt = set_tracking_context(TokenTrackingContext(
            operation_type="context_compress",
            operation_detail=context_type,
        ))
        try:
            response = await self._cancellable_await(
                self.brain.messages_create_async(
                    model=self.brain.model,
                    max_tokens=target_tokens,
                    system=system_prompt,
                    messages=[
                        {
                            "role": "user",
                            "content": f"请将以下内容压缩到 {target_chars} 字以内:\n\n{text}",
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
                    # thinking 块 fallback：当模型把摘要放在 thinking 中时
                    if not summary:
                        summary = block.thinking if isinstance(block.thinking, str) else str(block.thinking)

            # 如果仍然为空，记录警告并回退到硬截断
            if not summary.strip():
                logger.warning(
                    f"[Compress] LLM returned empty summary (tokens_out={response.usage.output_tokens}), "
                    f"falling back to hard truncation"
                )
                if len(text) > target_chars:
                    head = int(target_chars * 0.7)
                    tail = int(target_chars * 0.2)
                    return text[:head] + "\n...(压缩失败，已截断)...\n" + text[-tail:]
                return text

            return summary.strip()

        except UserCancelledError:
            raise
        except Exception as e:
            logger.warning(f"LLM compression failed: {e}")
            if len(text) > target_chars:
                head = int(target_chars * 0.7)
                tail = int(target_chars * 0.2)
                return text[:head] + "\n...(压缩失败，已截断)...\n" + text[-tail:]
            return text
        finally:
            reset_tracking_context(_tt)

    def _extract_message_text(self, msg: dict) -> str:
        """
        从消息中提取文本内容（包括 tool_use/tool_result 结构化信息）

        Args:
            msg: 消息字典

        Returns:
            提取的文本内容
        """
        role = "用户" if msg["role"] == "user" else "助手"
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
                        input_summary, _ = _st(input_summary, 3000, save_full=False, label="compress_input")
                        texts.append(f"[调用工具: {name}, 参数: {input_summary}]")
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
                        result_text, _ = _st(result_text, 10000, save_full=False, label="compress_result")
                        is_error = item.get("is_error", False)
                        status = "错误" if is_error else "成功"
                        texts.append(f"[工具结果({status}): {result_text}]")
            if texts:
                return f"{role}: {' '.join(texts)}\n"

        return ""

    async def _summarize_messages_chunked(
        self, messages: list[dict], target_tokens: int
    ) -> str:
        """
        分块 LLM 摘要消息列表

        将消息按 CHUNK_MAX_TOKENS 分块，每块独立调 LLM 压缩，
        最后将所有块的摘要拼接。如果摘要拼接后还很长，再做一次汇总压缩。

        Args:
            messages: 要摘要的消息列表
            target_tokens: 最终目标 token 数

        Returns:
            摘要文本
        """
        if not messages:
            return ""

        # 将消息转换为文本并分块
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

        # 每块独立压缩
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            chunk_tokens = self._estimate_tokens(chunk)
            # 每块的目标 = 总目标 / 块数（均分）
            chunk_target = max(int(target_tokens / len(chunks)), 100)

            _tt2 = set_tracking_context(TokenTrackingContext(
                operation_type="context_compress",
                operation_detail=f"chunk_{i}",
            ))
            try:
                response = await self._cancellable_await(
                    self.brain.messages_create_async(
                        model=self.brain.model,
                        max_tokens=chunk_target,
                        system=(
                            "你是一个对话压缩助手。请将以下对话片段压缩为简洁摘要。\n"
                            "要求：\n"
                            "1. 保留用户的原始意图和关键指令\n"
                            "2. 保留工具调用的名称、关键参数和执行结果（成功/失败/关键输出）\n"
                            "3. 保留重要的状态变化和决策\n"
                            "4. 去掉重复信息、冗余输出和中间过程细节\n"
                            "5. 使用简练的描述，不需要保留原文格式"
                        ),
                        messages=[
                            {
                                "role": "user",
                                "content": (
                                    f"请将以下对话片段（第 {i + 1}/{len(chunks)} 块，"
                                    f"约 {chunk_tokens} tokens）压缩到 {chunk_target * CHARS_PER_TOKEN} 字以内:\n\n"
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
                        # thinking 块 fallback：当模型把摘要放在 thinking 中时
                        if not summary:
                            summary = block.thinking if isinstance(block.thinking, str) else str(block.thinking)

                if not summary.strip():
                    # 摘要为空，回退到硬截断
                    logger.warning(f"[Compress] Chunk {i + 1} returned empty summary, using hard truncation")
                    max_chars = chunk_target * CHARS_PER_TOKEN
                    if len(chunk) > max_chars:
                        chunk_summaries.append(
                            chunk[:max_chars // 2] + "\n...(摘要失败，已截断)...\n"
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
                    chunk_summaries.append(
                        chunk[:max_chars // 2] + "\n...(摘要失败，已截断)...\n"
                    )
                else:
                    chunk_summaries.append(chunk)
            finally:
                reset_tracking_context(_tt2)

        # 拼接所有块摘要
        combined = "\n---\n".join(chunk_summaries)
        combined_tokens = self._estimate_tokens(combined)

        # 如果拼接后还超过目标的 2 倍，再做一次汇总压缩
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
        递归压缩：减少保留的最近组数量，继续压缩（保证 tool 配对完整性）

        Args:
            messages: 当前消息列表
            max_tokens: 目标 token 上限

        Returns:
            压缩后的消息列表
        """
        current_tokens = self._estimate_messages_tokens(messages)

        if current_tokens <= max_tokens:
            return messages

        # 按组边界切割，保留最近 2 组（比 _compress_context 的 MIN_RECENT_TURNS 更少）
        groups = self._group_messages(messages)
        recent_group_count = min(2, len(groups))

        if len(groups) <= recent_group_count:
            # 只有最近的几个组了，做最后一次 tool_result 压缩
            logger.warning("Cannot compress further, attempting final tool_result compression")
            return await self._compress_large_tool_results(messages, threshold=1000)

        early_groups = groups[:-recent_group_count]
        recent_groups = groups[-recent_group_count:]

        early_messages = [msg for group in early_groups for msg in group]
        recent_messages = [msg for group in recent_groups for msg in group]

        # 用 LLM 压缩早期消息
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
        硬保底：当 LLM 压缩后仍超过 hard_limit，直接硬截断保证能提交到 API

        策略：
        1. 从最早的消息开始丢弃，保留最近的消息
        2. 将丢弃的消息入队到提取队列避免永久丢失
        3. 对剩余消息中仍然过大的单条内容做字符级截断
        4. 添加截断提示让模型知道上下文不完整
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

        # 策略二：如果只剩 2 条还是超限，对单条消息内容做字符级截断
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
                            + "\n\n...[内容过长已硬截断]...\n\n"
                            + content[-keep_tail:]
                        ),
                    }
                elif isinstance(content, list):
                    # 对 list 类型内容，截断其中过大的文本块
                    new_content = []
                    for item in content:
                        if isinstance(item, dict):
                            for key in ("text", "content"):
                                val = item.get(key, "")
                                if isinstance(val, str) and len(val) > max_chars_per_msg:
                                    keep_h = int(max_chars_per_msg * 0.7)
                                    keep_t = int(max_chars_per_msg * 0.2)
                                    item = dict(item)
                                    item[key] = (
                                        val[:keep_h]
                                        + "\n...[硬截断]...\n"
                                        + val[-keep_t:]
                                    )
                        new_content.append(item)
                    truncated[i] = {**msg, "content": new_content}

        truncated.insert(0, {
            "role": "user",
            "content": (
                "[context_note: 早期对话已自动整理] "
                "请正常回复，保持详细程度和输出质量不变。"
            ),
        })

        final_tokens = self._estimate_messages_tokens(truncated)
        logger.warning(
            f"[HardTruncate] Final: {final_tokens} tokens "
            f"(hard_limit={hard_limit}, messages={len(truncated)})"
        )
        return truncated

    async def chat(self, message: str, session_id: str | None = None) -> str:
        """
        对话接口 - 委托给 chat_with_session() 复用完整处理链路

        内部创建/复用一个持久的 CLI Session，使 CLI 获得与 IM 通道一致的能力：
        Prompt Compiler、高级循环检测、Task Monitor、记忆检索、上下文压缩等。

        Args:
            message: 用户消息
            session_id: 可选的会话标识（用于日志）

        Returns:
            Agent 响应
        """
        if not self._initialized:
            await self.initialize()

        # 懒初始化 CLI Session（在 Agent 生命周期内持久存在）
        if not hasattr(self, '_cli_session') or self._cli_session is None:
            from ..sessions.session import Session
            self._cli_session = Session.create(
                channel="cli", chat_id="cli", user_id="user"
            )
            self._cli_session.set_metadata("_memory_manager", self.memory_manager)

        # 模拟 Gateway 的消息管理流程：先记录用户消息到 Session
        self._cli_session.add_message("user", message)
        session_messages = self._cli_session.context.get_messages()

        # 委托给统一的 chat_with_session
        response = await self.chat_with_session(
            message=message,
            session_messages=session_messages,
            session_id=session_id or self._cli_session.id,
            session=self._cli_session,
            gateway=None,  # CLI 无 Gateway
        )

        # 记录 Assistant 响应到 Session（工具执行摘要作为独立字段）
        _cli_meta: dict = {}
        try:
            _cli_tool_summary = self.build_tool_trace_summary()
            if _cli_tool_summary:
                _cli_meta["tool_summary"] = _cli_tool_summary
        except Exception:
            pass
        self._cli_session.add_message("assistant", response, **_cli_meta)

        # 同步更新旧属性（保持向后兼容：conversation_history 属性、/status 命令等依赖）
        self._conversation_history.append(
            {"role": "user", "content": message, "timestamp": datetime.now().isoformat()}
        )
        self._conversation_history.append(
            {"role": "assistant", "content": response, "timestamp": datetime.now().isoformat()}
        )
        # 防止内存泄漏：限制 _conversation_history 大小（保留最近 200 条）
        _max_cli_history = 200
        if len(self._conversation_history) > _max_cli_history:
            self._conversation_history = self._conversation_history[-_max_cli_history:]

        return response

    # ==================== 会话流水线: 共享准备 / 收尾 / 入口 ====================

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
    ) -> tuple[list[dict], str, "TaskMonitor", str, Any]:
        """
        会话流水线 - 共享准备阶段。

        chat_with_session() 和 chat_with_session_stream() 共用此方法，
        确保 IM/Desktop 两条路径走完全一致的准备逻辑。

        步骤:
        1. Memory session align
        2. IM context setup
        3. Agent state / log session setup
        4. Proactive engine update
        5. User turn memory record
        6. Trait mining
        7. Prompt Compiler (两段式第一阶段)
        8. Plan 模式自动检测
        9. Task definition setup
        10. Message history build (含上下文边界标记、多模态/附件)
        11. Context compression
        12. TaskMonitor creation

        Args:
            message: 用户消息
            session_messages: Session 的对话历史
            session_id: 会话 ID（用于日志）
            session: Session 对象
            gateway: MessageGateway 对象
            conversation_id: 稳定对话线程 ID
            attachments: Desktop Chat 附件列表 (可选)

        Returns:
            (messages, session_type, task_monitor, conversation_id, im_tokens)
        """
        # 1. 对齐 MemoryManager 会话
        # memory safe_id 统一用 session.session_key 派生，与 im_channel fallback
        # 和 sessions/manager backfill 的查询逻辑保持一致。
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
                # 1.5 新会话时清空 Scratchpad 工作记忆，避免跨会话泄漏
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

        # 2. IM context setup（协程隔离）
        from .im_context import set_im_context

        im_tokens = set_im_context(
            session=session if gateway else None,
            gateway=gateway,
        )

        # 2.5 注入 memory_manager 到 session metadata（供 session 截断时入队提取）
        if session is not None:
            session.set_metadata("_memory_manager", self.memory_manager)

        # 3. Agent state / log session
        self._current_session = session
        self.agent_state.current_session = session

        from ..logging import get_session_log_buffer
        get_session_log_buffer().set_current_session(conversation_id)

        logger.info(f"[Session:{session_id}] User: {message}")

        # 4. Proactive engine: 记录用户互动时间
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
                from .persona import persist_trait_to_memory
                for trait in mined_traits:
                    persist_trait_to_memory(
                        self.memory_manager, trait.dimension, trait.preference,
                        trait.confidence, trait.source,
                    )
                if mined_traits:
                    logger.debug(f"[TraitMiner] Mined {len(mined_traits)} traits from user message")
            except Exception as e:
                logger.debug(f"[TraitMiner] Mining failed (non-critical): {e}")

        # 7. Prompt Compiler (两段式第一阶段)
        compiled_message = message
        compiler_output = ""
        compiler_summary = ""

        if self._should_compile_prompt(message):
            try:
                compiled_message, compiler_output = await asyncio.wait_for(
                    self._compile_prompt(message), timeout=15,
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[Session:{session_id}] Prompt compilation failed/timed out: {e}")
            if compiler_output:
                logger.info(f"[Session:{session_id}] Prompt compiled")
                compiler_summary = self._summarize_compiler_output(compiler_output)

                # 8. Plan 模式自动检测
                from ..tools.handlers.plan import require_plan_for_session, should_require_plan

                is_compound = (
                    "task_type: compound" in compiler_output
                    or "task_type:compound" in compiler_output
                )
                has_multi_actions = should_require_plan(message)

                if is_compound or has_multi_actions:
                    require_plan_for_session(conversation_id, True)
                    logger.info(
                        f"[Session:{session_id}] Multi-step task detected "
                        f"(compound={is_compound}, multi_actions={has_multi_actions}), Plan required"
                    )

        # 9. Task definition setup
        self._current_task_definition = compiler_summary
        self._current_task_query = compiler_summary or message

        # 9.5 话题切换检测 — 仅 IM 通道（telegram/wechat/feishu 等）
        # Desktop/CLI 不做话题检测，完整保留对话历史让 LLM 自行处理上下文。
        # 防御性浅拷贝：_detect_topic_change 可能通过 insert() 注入边界标记，
        # 如果直接操作 session.context.messages 的活引用，边界消息会永久积累导致
        # 连续 user 角色消息 → API 报错 / 模型混乱 / 工具重复执行
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
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[Session:{session_id}] Topic change detection failed/timed out: {e}")
            if topic_changed:
                _boundary_msg = {
                    "role": "user",
                    "content": "[上下文边界]",
                    "timestamp": datetime.now().isoformat(),
                }
                # 将边界标记插入到 session_messages 的倒数第二位（当前消息之前）
                if session_messages and session_messages[-1].get("role") == "user":
                    session_messages.insert(-1, _boundary_msg)
                else:
                    session_messages.append(_boundary_msg)
                # 同步更新 Session 模型的话题边界索引
                if hasattr(session.context, "mark_topic_boundary"):
                    session.context.mark_topic_boundary()
                logger.info(
                    f"[Session:{session_id}] Topic change detected, "
                    f"inserted context boundary"
                )
                # Extract memories from the previous topic before starting new one
                try:
                    saved = await self.memory_manager.extract_on_topic_change()
                    if saved:
                        logger.info(f"[Session:{session_id}] Topic-change extraction: {saved} memories")
                except Exception as _tc_err:
                    logger.debug(f"[Session:{session_id}] Topic-change extraction failed: {_tc_err}")

        # 9.7 同步更新 Scratchpad 当前任务
        _new_task = compiler_summary or message[:200]
        if _new_task:
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
        # session_messages 已包含当前轮用户消息（gateway 调用前 add_message），
        # 当前轮由下方 compiled_message 单独追加，需排除最后一条避免重复。
        history_messages = session_messages
        if history_messages and history_messages[-1].get("role") == "user":
            history_messages = history_messages[:-1]

        _STRIP_MARKERS = ["\n\n[子Agent工作总结]", "\n\n[执行摘要]"]

        messages: list[dict] = []
        for msg in history_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "assistant":
                for _marker in _STRIP_MARKERS:
                    if _marker in content:
                        content = content[:content.index(_marker)]
                if content.startswith("[执行摘要]") or content.startswith("[子Agent工作总结]"):
                    content = ""
            # 为历史消息注入 [HH:MM] 时间戳，帮助 LLM 理解对话时序
            ts = msg.get("timestamp", "")
            if ts and content:
                try:
                    dt = datetime.fromisoformat(ts)
                    content = f"[{dt.strftime('%H:%M')}] {content}"
                except (ValueError, TypeError):
                    pass
            if role in ("user", "assistant") and content:
                if messages and messages[-1]["role"] == role:
                    messages[-1]["content"] += "\n" + content
                else:
                    messages.append({"role": role, "content": content})

        # 上下文连续标记（合并到当前用户消息前缀，避免插入假 assistant 回复破坏对话连贯性）
        _has_history = bool(messages)
        logger.debug(
            f"[Session:{session_id}] _prepare_session_context: "
            f"{len(messages)} history msgs, has_history={_has_history}"
        )

        # 当前用户消息（支持多模态）
        pending_images = session.get_metadata("pending_images") if session else None
        pending_videos = session.get_metadata("pending_videos") if session else None
        pending_audio = session.get_metadata("pending_audio") if session else None
        pending_files = session.get_metadata("pending_files") if session else None

        # 处理文档文件 — PDF/Word/Excel/PPT 等
        # 如果 LLM 原生支持 PDF 则构建 DocumentBlock，否则降级提取文本
        document_blocks = []
        if pending_files:
            llm_client_for_pdf = getattr(self.brain, "_llm_client", None)
            has_pdf_cap = llm_client_for_pdf and llm_client_for_pdf.has_any_endpoint_with_capability("pdf")
            for fdata in pending_files:
                if has_pdf_cap and fdata.get("type") == "document":
                    document_blocks.append(fdata)
                    logger.info(f"[Session:{session_id}] PDF → native DocumentBlock")
                else:
                    fname = fdata.get("filename", "unknown")
                    local_path = fdata.get("local_path", "")
                    extracted = ""
                    if local_path and Path(local_path).exists():
                        try:
                            from openakita.channels.media.handler import MediaHandler
                            _handler = MediaHandler()
                            _ext = Path(local_path).suffix.lower()
                            if _ext == ".pdf":
                                extracted = await _handler._extract_pdf(Path(local_path))
                            elif _ext == ".docx":
                                extracted = await _handler._extract_docx(Path(local_path))
                            elif _ext == ".xlsx":
                                extracted = await _handler._extract_xlsx(Path(local_path))
                            elif _ext == ".pptx":
                                extracted = await _handler._extract_pptx(Path(local_path))
                            elif _ext in (
                                ".txt", ".md", ".json", ".csv", ".py",
                                ".js", ".html", ".css", ".xml", ".yaml", ".yml",
                                ".log", ".ini", ".cfg", ".toml",
                            ):
                                extracted = Path(local_path).read_bytes().decode(
                                    "utf-8", errors="ignore"
                                )
                            else:
                                logger.info(
                                    f"[Session:{session_id}] "
                                    f"Unsupported file type {_ext}, providing path hint"
                                )
                        except Exception as _ext_err:
                            logger.warning(
                                f"[Session:{session_id}] "
                                f"Text extraction failed for {fname}: {_ext_err}"
                            )
                    if extracted and extracted.strip():
                        _DOC_TEXT_LIMIT = 80_000
                        if len(extracted) > _DOC_TEXT_LIMIT:
                            extracted = extracted[:_DOC_TEXT_LIMIT] + "\n...(文档过长，已截断)"
                        _type_label = Path(fname).suffix.upper().lstrip(".") or "文档"
                        compiled_message += (
                            f"\n\n--- {_type_label}文件: {fname} ---\n"
                            f"{extracted}\n"
                            f"--- 文件结束 ---"
                        )
                        logger.info(
                            f"[Session:{session_id}] {_type_label} → text extracted "
                            f"({len(extracted)} chars)"
                        )
                    else:
                        compiled_message += f"\n[文档附件: {fname}，本地路径: {local_path}]"
                        logger.warning(
                            f"[Session:{session_id}] Text extraction empty for {fname}"
                        )

        # 二级音频决策：LLM原生audio > 在线STT
        audio_blocks = []
        if pending_audio:
            llm_client = getattr(self.brain, "_llm_client", None)
            has_audio_cap = llm_client and llm_client.has_any_endpoint_with_capability("audio")

            if has_audio_cap:
                # Tier 1: LLM 原生音频输入
                for aud in pending_audio:
                    local_path = aud.get("local_path", "")
                    if local_path and Path(local_path).exists():
                        try:
                            from ..channels.media.audio_utils import ensure_llm_compatible
                            compat_path = ensure_llm_compatible(local_path)
                            audio_blocks.append({
                                "type": "audio",
                                "source": {
                                    "type": "base64",
                                    "media_type": aud.get("mime_type", "audio/wav"),
                                    "data": base64.b64encode(Path(compat_path).read_bytes()).decode("utf-8"),
                                    "format": Path(compat_path).suffix.lstrip(".") or "wav",
                                },
                            })
                            logger.info(f"[Session:{session_id}] Audio → native AudioBlock")
                        except Exception as e:
                            logger.error(f"[Session:{session_id}] Failed to build AudioBlock: {e}")
            else:
                # Tier 2: 在线 STT（如果可用）
                stt_client = None
                im_gateway = gateway or (session.get_metadata("_gateway") if session else None)
                if im_gateway and hasattr(im_gateway, "stt_client"):
                    stt_client = im_gateway.stt_client

                if stt_client and stt_client.is_available:
                    for aud in pending_audio:
                        local_path = aud.get("local_path", "")
                        existing_transcription = aud.get("transcription")
                        if existing_transcription:
                            continue  # 已有转写结果，不重复调用
                        if local_path and Path(local_path).exists():
                            try:
                                stt_result = await stt_client.transcribe(local_path)
                                if stt_result:
                                    # 用在线 STT 结果替换输入
                                    if not compiled_message.strip() or "[语音:" in compiled_message:
                                        compiled_message = stt_result
                                    else:
                                        compiled_message = f"{compiled_message}\n\n[语音内容(在线识别): {stt_result}]"
                                    logger.info(f"[Session:{session_id}] Audio → online STT: {stt_result[:50]}...")
                            except Exception as e:
                                logger.warning(f"[Session:{session_id}] Online STT failed: {e}")
                # Gateway 已处理的转写结果已包含在 input_text 中

        # 如果有历史消息，给当前用户消息加上连续提示前缀
        if _has_history and compiled_message:
            compiled_message = (
                "[以上是之前的对话历史。"
                "请直接回应以下最新消息，不要重复执行历史中已完成的操作：]\n"
                + compiled_message
            )

        # === 角色交替保护 ===
        # 如果历史末尾是 user 消息（通常由上下文边界标记产生），
        # 将其文本合并到当前消息前缀，避免连续同角色消息导致 API 错误或模型混乱
        if messages and messages[-1]["role"] == "user":
            _trailing_user = messages.pop()
            _trailing_text = _trailing_user.get("content", "")
            if isinstance(_trailing_text, str) and _trailing_text:
                compiled_message = _trailing_text + "\n" + compiled_message
            elif _trailing_text:
                # 非字符串内容（如多模态 list），无法文本合并，恢复原位
                messages.append(_trailing_user)

        # Desktop Chat 附件处理（与 IM 的 pending_images 对齐）
        if attachments and not pending_images:
            content_blocks: list[dict] = []
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
                    content_blocks.append({"type": "image_url", "image_url": {"url": att_url}})
                elif is_video and att_url:
                    content_blocks.append({"type": "video_url", "video_url": {"url": att_url}})
                elif att_type == "document" and att_url:
                    content_blocks.append({
                        "type": "text",
                        "text": f"[文档: {att_name} ({att_mime})] URL: {att_url}",
                    })
                elif att_url:
                    content_blocks.append({
                        "type": "text",
                        "text": f"[附件: {att_name} ({att_mime})] URL: {att_url}",
                    })
            if content_blocks:
                messages.append({"role": "user", "content": content_blocks})
            elif compiled_message:
                messages.append({"role": "user", "content": compiled_message})
        elif pending_images or pending_videos or audio_blocks or document_blocks:
            # IM 路径: 多模态（图片 + 视频 + 音频 + 文档）
            # 对齐 audio/PDF 的模式：先检查能力，无能力时降级为文本
            content_parts: list[dict] = []
            _text_for_llm = compiled_message.strip()

            llm_client = getattr(self.brain, "_llm_client", None)
            has_vision = (
                llm_client and llm_client.has_any_endpoint_with_capability("vision")
            )
            has_video = (
                llm_client and llm_client.has_any_endpoint_with_capability("video")
            )

            embed_images = pending_images if has_vision else None
            embed_videos = pending_videos if has_video else None

            # 图片占位符替换（仅在实际嵌入时才改为「请直接查看」）
            _is_img_placeholder = (
                _text_for_llm
                and re.fullmatch(r"(\[图片: [^\]]+\]\s*)+", _text_for_llm)
            )
            if pending_images and _is_img_placeholder:
                if embed_images:
                    _text_for_llm = (
                        f"用户发送了 {len(pending_images)} 张图片"
                        "（已附在消息中，请直接查看）。"
                        "请描述或回应你所看到的图片内容。"
                    )
                else:
                    _text_for_llm = ""

            # 视频占位符替换
            _is_vid_placeholder = (
                _text_for_llm
                and re.fullmatch(r"(\[视频: [^\]]+\]\s*)+", _text_for_llm)
            )
            if pending_videos and _is_vid_placeholder:
                if embed_videos:
                    _text_for_llm = (
                        f"用户发送了 {len(pending_videos)} 个视频"
                        "（已附在消息中，请直接查看）。"
                        "请描述或回应你所看到的视频内容。"
                    )
                else:
                    _text_for_llm = ""

            # 图片降级提示
            if pending_images and not has_vision:
                img_paths = [
                    img.get("local_path", "")
                    for img in pending_images if img.get("local_path")
                ]
                notice = (
                    f"[用户发送了 {len(pending_images)} 张图片，"
                    f"当前模型不支持图片输入"
                )
                if img_paths:
                    notice += (
                        f"。文件路径: {'; '.join(img_paths)}"
                        f"。如需查看图片内容，请使用 view_image 工具"
                    )
                notice += "]"
                _text_for_llm = (
                    f"{_text_for_llm}\n\n{notice}" if _text_for_llm else notice
                )
                logger.info(
                    f"[Session:{session_id}] No vision endpoint, "
                    f"degrading {len(pending_images)} images to text notice"
                )

            # 视频降级提示
            if pending_videos and not has_video:
                vid_paths = [
                    v.get("local_path", "")
                    for v in pending_videos if v.get("local_path")
                ]
                notice = (
                    f"[用户发送了 {len(pending_videos)} 个视频，"
                    f"当前模型不支持视频输入"
                )
                if vid_paths:
                    notice += f"。文件路径: {'; '.join(vid_paths)}"
                notice += "]"
                _text_for_llm = (
                    f"{_text_for_llm}\n\n{notice}" if _text_for_llm else notice
                )
                logger.info(
                    f"[Session:{session_id}] No video endpoint, "
                    f"degrading {len(pending_videos)} videos to text notice"
                )

            # 组装 content_parts
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

            # 如果所有媒体均已降级为文本，发纯文本消息而非多模态 list
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
                    f"[Session:{session_id}] Multimodal message "
                    f"with {', '.join(media_info)}"
                )
        else:
            # 普通文本消息
            messages.append({"role": "user", "content": compiled_message})

        # 10.5. Record incoming attachments (images/videos/files) to memory
        self._record_inbound_attachments(
            session_id, pending_images, pending_videos,
            pending_audio, pending_files, attachments,
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
            retrospect_threshold=60,
            fallback_model=self.brain.get_fallback_model(session_id),
        )
        task_monitor.start(self.brain.model)
        self._current_task_monitor = task_monitor

        # session_type 检测
        # desktop 聊天面板与 CLI 同属本地交互，应启用 ForceToolCall 验收
        # 仅真正的 IM 通道（telegram/wechat/feishu 等）使用 im 模式
        _channel = getattr(session, "channel", None) if session else None
        session_type = "im" if _channel and _channel not in ("cli", "desktop") else "cli"

        return messages, session_type, task_monitor, conversation_id, im_tokens

    async def _finalize_session(
        self,
        response_text: str,
        session: Any,
        session_id: str,
        task_monitor: "TaskMonitor",
    ) -> None:
        """
        会话流水线 - 共享收尾阶段。

        chat_with_session() 和 chat_with_session_stream() 共用此方法。

        步骤:
        1. 将 react_trace 摘要写入 session metadata（供 IM 使用）
        2. 完成 TaskMonitor + 后台复盘
        3. 记录 assistant 响应到 memory
        4. 清理临时状态
        """
        # 0. 快照当前 trace（防止并发会话覆盖 _last_react_trace）
        _trace_snapshot = list(getattr(self.reasoning_engine, "_last_react_trace", None) or [])
        self._last_finalized_trace = _trace_snapshot

        # 0b. 提取轻量 token 用量摘要（供 SSE/API 在 cleanup 后仍可读取）
        self._last_usage_summary = self._extract_usage_summary(_trace_snapshot)

        # 1. 思维链摘要 → session metadata
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

        # 3. Memory: 记录 assistant 响应（含工具调用数据）
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
        if response_text:
            outbound_attachments = self._extract_outbound_attachments(
                _all_tool_calls, _all_tool_results
            )
            self.memory_manager.record_turn(
                "assistant", response_text,
                tool_calls=_all_tool_calls,
                tool_results=_all_tool_results,
                attachments=outbound_attachments or None,
            )
        else:
            logger.warning(
                f"[Session:{session_id}] Skipping record_turn for empty assistant response"
            )
        try:
            logger.info(f"[Session:{session_id}] Agent: {response_text}")
        except (UnicodeEncodeError, OSError):
            logger.info(f"[Session:{session_id}] Agent: (response logged, {len(response_text)} chars)")

        # 4. 自动关闭未完成的 Plan
        # 如果 LLM 未显式调用 complete_plan，此处兜底：
        # - 标记剩余步骤状态（in_progress→completed, pending→skipped）
        # - 保存并注销 Plan
        # 注意：ask_user 退出时不关闭 Plan（用户回复后需继续执行）
        # 注意：子 Agent 调用时不关闭 Plan（Plan 属于父 Agent）
        exit_reason = getattr(self.reasoning_engine, "_last_exit_reason", "normal")
        is_sub_agent = getattr(self, "_is_sub_agent_call", False)
        if exit_reason != "ask_user" and not is_sub_agent:
            conversation_id = getattr(self, "_current_conversation_id", "") or session_id
            try:
                from ..tools.handlers.plan import auto_close_plan
                if auto_close_plan(conversation_id):
                    logger.info(f"[Session:{session_id}] Plan auto-closed at finalize")
            except Exception as e:
                logger.debug(f"[Plan] auto_close_plan failed: {e}")

            # 及时结束 memory session，触发记忆提取
            try:
                task_desc = (getattr(self, "_current_task_query", "") or "").strip()[:200]
                self.memory_manager.end_session(task_desc, success=True)
                logger.debug(f"[Session:{session_id}] memory_manager.end_session() called")
            except Exception as e:
                logger.debug(f"[Session:{session_id}] memory end_session failed: {e}")

        # 5. Cleanup（总是执行，放在 finally 中由调用方保证）
        # 注意：此方法不做 cleanup，cleanup 统一在 _cleanup_session_state() 中

    def _cleanup_session_state(self, im_tokens: Any) -> None:
        """
        会话流水线 - 状态清理（总是在 finally 中调用）。

        im_tokens 可能为 None（_prepare_session_context 在 step 2 之前/之后异常时）,
        此时 contextvar 残留由下次 set_im_context 覆盖，这里跳过 reset 即可。
        """
        self._current_task_definition = ""
        self._current_task_query = ""
        if im_tokens is not None:
            with contextlib.suppress(Exception):
                from .im_context import reset_im_context
                reset_im_context(im_tokens)
        self._current_session = None
        self.agent_state.current_session = None
        self._current_task_monitor = None
        # 重置任务状态，避免已取消/已完成的任务泄漏到下一次会话
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

        # Clean up task-local session references to prevent dict growth
        if _sid:
            self._pending_cancels.pop(_sid, None)
        if self._current_conversation_id:
            self._pending_cancels.pop(self._current_conversation_id, None)
        self._current_session_id = None
        self._current_conversation_id = None

        # 释放推理引擎中残留的大对象（working_messages / checkpoints），
        # working_messages 可能持有数十 MB 的工具结果（截图 base64、网页内容等）
        # 注意：不清理 _last_finalized_trace，它由 orchestrator/SSE 读取，
        # 会在下次 _finalize_session 时自然被覆盖
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
        endpoint_override: str | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
    ) -> str:
        """
        使用外部 Session 历史进行对话（用于 IM / CLI 通道）。

        走完整的 Agent 流水线：Prompt Compiler → 上下文构建 → ReasoningEngine.run()。
        与 chat_with_session_stream() 共享 _prepare_session_context / _finalize_session。

        Args:
            message: 用户消息
            session_messages: Session 的对话历史
            session_id: 会话 ID
            session: Session 对象
            gateway: MessageGateway 对象
            endpoint_override: 端点覆盖（为 None 时使用 _preferred_endpoint）
            thinking_mode: 思考模式覆盖 ('auto'/'on'/'off'/None)
            thinking_depth: 思考深度 ('low'/'medium'/'high'/None)

        Returns:
            Agent 响应
        """
        if not self._initialized:
            await self.initialize()

        endpoint_override = endpoint_override or self._preferred_endpoint

        # === 停止指令检测 ===
        message_lower = message.strip().lower()
        if message_lower in self.STOP_COMMANDS or message.strip() in self.STOP_COMMANDS:
            self.cancel_current_task(f"用户发送停止指令: {message}", session_id=session_id)
            logger.info(f"[StopTask] User requested to stop (session={session_id}): {message}")
            return "✅ 好的，已停止当前任务。有什么其他需要帮助的吗？"

        # 解析 conversation_id（提前，以便清理时使用正确的 key）
        self._current_session_id = session_id
        conversation_id = self._resolve_conversation_id(session, session_id)
        self._current_conversation_id = conversation_id

        # 清理上一轮残留的任务状态（按 session 隔离）
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

        # 清除上一轮残留的 pending_cancels（disconnect watcher 可能在清理后写入）
        self._pending_cancels.pop(session_id, None) if session_id else None

        # 用户主动发新消息 → 无条件清除所有端点冷却期，不让上一轮的错误阻塞本轮
        llm_client = getattr(self.brain, "_llm_client", None)
        if llm_client:
            llm_client.reset_all_cooldowns(force_all=True)

        im_tokens = None
        try:
            # 准备阶段前检查：仅捕获 prepare 开始前一刻的取消信号
            if self._is_session_cancelled(session_id):
                self._consume_pending_cancel(session_id)
                logger.info(f"[Session:{session_id}] Cancelled before prepare, returning immediately")
                return "✅ 好的，已停止当前任务。"

            # === 共享准备 ===
            messages, session_type, task_monitor, conversation_id, im_tokens = (
                await self._prepare_session_context(
                    message=message,
                    session_messages=session_messages,
                    session_id=session_id,
                    session=session,
                    gateway=gateway,
                    conversation_id=conversation_id,
                )
            )

            # 准备阶段后检查（含 pending cancel）
            _conv_cancel_id = conversation_id or session_id
            if self._is_session_cancelled(session_id) or self._is_session_cancelled(_conv_cancel_id):
                self._consume_pending_cancel(session_id)
                self._consume_pending_cancel(_conv_cancel_id)
                logger.info(f"[Session:{session_id}] Cancelled during prepare, returning immediately")
                return "✅ 好的，已停止当前任务。"

            # === 从 session metadata 读取 thinking 偏好（IM 通道使用） ===
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

            # === 构建 IM 思维链进度回调 ===
            # 受 im_chain_push 开关控制：默认关闭以减少刷屏，不影响内部 trace 保存
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

            # === 核心推理 (同步返回) ===
            response_text = await self._chat_with_tools_and_context(
                messages, task_monitor=task_monitor, session_type=session_type,
                thinking_mode=_thinking_mode, thinking_depth=_thinking_depth,
                progress_callback=_progress_cb,
                session=session,
                endpoint_override=endpoint_override,
            )

            # === flush 残留的 IM 进度消息，确保思维链先于回答到达 ===
            if gateway and session:
                try:
                    await gateway.flush_progress(session)
                except Exception:
                    pass

            # === 共享收尾 ===
            await self._finalize_session(
                response_text=response_text,
                session=session,
                session_id=session_id,
                task_monitor=task_monitor,
            )

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
        endpoint_override: str | None = None,
        attachments: list | None = None,
        thinking_mode: str | None = None,
        thinking_depth: str | None = None,
    ):
        """
        流式版 chat_with_session，yield SSE 事件字典。

        走与 chat_with_session() 完全一致的 Agent 流水线（共享准备/收尾），
        中间推理部分使用 reasoning_engine.reason_stream() 实现流式输出。

        用于 Desktop Chat API (/api/chat) 的 SSE 通道。

        Args:
            message: 用户消息
            session_messages: Session 的对话历史
            session_id: 会话 ID
            session: Session 对象
            gateway: MessageGateway 对象
            plan_mode: 是否启用 Plan 模式
            endpoint_override: 端点覆盖
            attachments: Desktop Chat 附件列表
            thinking_mode: 思考模式覆盖 ('auto'/'on'/'off'/None)
            thinking_depth: 思考深度 ('low'/'medium'/'high'/None)

        Yields:
            SSE 事件字典 {"type": "...", ...}
        """
        if not self._initialized:
            await self.initialize()

        endpoint_override = endpoint_override or self._preferred_endpoint

        # === 停止指令检测 ===
        message_lower = message.strip().lower()
        if message_lower in self.STOP_COMMANDS or message.strip() in self.STOP_COMMANDS:
            self.cancel_current_task(f"用户发送停止指令: {message}", session_id=session_id)
            logger.info(f"[StopTask] User requested to stop (session={session_id}): {message}")
            yield {"type": "plan_cancelled"}
            yield {"type": "text_delta", "content": "✅ 好的，已停止当前任务。有什么其他需要帮助的吗？"}
            yield {"type": "done"}
            return

        # 解析 conversation_id（提前，以便清理时使用正确的 key）
        self._current_session_id = session_id
        conversation_id = self._resolve_conversation_id(session, session_id)
        self._current_conversation_id = conversation_id

        # 清理上一轮残留的任务状态（按 session 隔离）
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

        # 清除上一轮残留的 pending_cancels（disconnect watcher 可能在清理后写入）
        self._pending_cancels.pop(session_id, None) if session_id else None

        # 用户主动发新消息 → 无条件清除所有端点冷却期
        llm_client = getattr(self.brain, "_llm_client", None)
        if llm_client:
            llm_client.reset_all_cooldowns(force_all=True)

        im_tokens = None
        _reply_text = ""
        try:
            # 立即发送心跳，让前端知道请求已被接收（准备阶段可能包含多个 LLM 调用）
            yield {"type": "heartbeat"}

            # 准备阶段前检查：如果 session 有挂起的取消信号，立即退出
            if self._is_session_cancelled(session_id):
                self._consume_pending_cancel(session_id)
                logger.info(f"[Session:{session_id}] Cancelled before prepare, returning immediately")
                yield {"type": "text_delta", "content": "✅ 好的，已停止当前任务。"}
                yield {"type": "done"}
                return

            # === 共享准备 ===
            messages, session_type, task_monitor, conversation_id, im_tokens = (
                await self._prepare_session_context(
                    message=message,
                    session_messages=session_messages,
                    session_id=session_id,
                    session=session,
                    gateway=gateway,
                    conversation_id=conversation_id,
                    attachments=attachments,
                )
            )

            yield {"type": "heartbeat"}

            # 准备阶段后检查：如果准备期间收到了取消信号（含 pending cancel）
            _conv_cancel_id = conversation_id or session_id
            if self._is_session_cancelled(session_id) or self._is_session_cancelled(_conv_cancel_id):
                self._consume_pending_cancel(session_id)
                self._consume_pending_cancel(_conv_cancel_id)
                logger.info(f"[Session:{session_id}] Cancelled during prepare, returning immediately")
                yield {"type": "text_delta", "content": "✅ 好的，已停止当前任务。"}
                yield {"type": "done"}
                return

            # === 构建 System Prompt（与 _chat_with_tools_and_context 一致） ===
            task_description = (getattr(self, "_current_task_query", "") or "").strip()
            if not task_description:
                task_description = self._get_last_user_request(messages).strip()

            system_prompt = await self._build_system_prompt_compiled(
                task_description=task_description,
                session_type=session_type,
            )

            # 注入 TaskDefinition
            task_def = (getattr(self, "_current_task_definition", "") or "").strip()
            if task_def:
                system_prompt += f"\n\n## Developer: TaskDefinition\n{task_def}\n"

            base_system_prompt = system_prompt

            # === 从 session metadata 读取 thinking 偏好（IM 通道使用） ===
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

            # === 核心推理 (流式) ===
            _agent_profile_id = "default"
            if session and hasattr(session, "context"):
                _agent_profile_id = getattr(session.context, "agent_profile_id", "default") or "default"
            async for event in self.reasoning_engine.reason_stream(
                messages=messages,
                tools=self._effective_tools,
                system_prompt=system_prompt,
                base_system_prompt=base_system_prompt,
                task_description=task_description,
                task_monitor=task_monitor,
                session_type=session_type,
                plan_mode=plan_mode,
                endpoint_override=endpoint_override,
                conversation_id=conversation_id,
                thinking_mode=_thinking_mode,
                thinking_depth=_thinking_depth,
                agent_profile_id=_agent_profile_id,
                session=session,
            ):
                # 收集回复文本（用于 session 保存 & memory）
                if event.get("type") == "text_delta":
                    _reply_text += event.get("content", "")
                elif event.get("type") == "ask_user" and not _reply_text:
                    _reply_text = event.get("question", "")
                yield event

            # === 共享收尾（始终执行，即使回复文本为空也要记录 memory/trace） ===
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

    def _resolve_conversation_id(self, session: Any, session_id: str) -> str:
        """将调用方传入的 session_id 作为规范 conversation_id 直接返回。

        Desktop 路径: session_id = raw chat_id (由前端 conversation_id 传入)
        IM 路径:      session_id = session.id (由 orchestrator._call_agent 传入)
        CLI 路径:     session_id = "cli_<uuid>"

        不再取 session.session_key，避免 task key 与 pool key 不一致。
        """
        return session_id

    def _extract_usage_summary(self, trace: list[dict]) -> dict:
        """从 react_trace 提取轻量 token 用量摘要。

        在 _finalize_session 中调用，提前缓存结果。
        cleanup 释放大对象后，chat.py 仍可读取此摘要而不依赖完整 trace。
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
        # 估算上下文 token 数
        try:
            re = self.reasoning_engine
            ctx_mgr = getattr(self, "context_manager", None) or getattr(re, "_context_manager", None)
            if ctx_mgr and hasattr(ctx_mgr, "get_max_context_tokens"):
                msgs = getattr(re, "_last_working_messages", None) or []
                summary["context_tokens"] = ctx_mgr.estimate_messages_tokens(msgs) if msgs else 0
                summary["context_limit"] = ctx_mgr.get_max_context_tokens()
        except Exception:
            pass
        return summary

    _DELEGATION_TOOLS = frozenset({
        "delegate_to_agent", "delegate_parallel", "spawn_agent",
    })

    def build_tool_trace_summary(self) -> str:
        """
        从最新的 react_trace 生成工具执行摘要文本。

        返回格式:

          [子Agent工作总结]      (仅多Agent委派时存在)
          1. [网探] 任务: ... | 状态: ✅完成 | 交付文件: ...
          2. [文助] 任务: ... | 状态: ✅完成 | 交付文件: ...

          [执行摘要]
          - tool_name({key: val}) → result_hint...

        调用方将返回值存入消息的 ``tool_summary`` 元数据字段（不要拼入 content）。
        空字符串表示无工具调用。
        """
        from .tool_executor import save_overflow, smart_truncate

        trace = getattr(self, "_last_finalized_trace", None) or \
            getattr(self.reasoning_engine, "_last_react_trace", None) or []
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
                        val_truncated, _ = smart_truncate(val_str, param_budget, save_full=False, label="param")
                        kv[k] = val_truncated
                    param_hint = str(kv) if kv else ""

                result_hint = ""
                for tr in it.get("tool_results", []):
                    if tr.get("tool_use_id") == tc.get("id", ""):
                        raw = str(tr.get("result_content", tr.get("result_preview", "")))
                        max_len = 800 if name in self._DELEGATION_TOOLS else per_tool_budget
                        if len(raw) > max_len:
                            result_hint = raw[:max_len].replace("\n", " ") + "..."
                            truncated_full_results.append(
                                f"=== {name} (id={tc.get('id', '')}) ===\n{raw}"
                            )
                        else:
                            result_hint = raw.replace("\n", " ")
                        break

                line = f"- {name}"
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
            lines.append(
                f"[部分工具结果已截断, 完整内容: {overflow_path}, 可用 read_file 查看]"
            )

        parts: list[str] = []

        if has_delegation:
            ws_section = self._build_work_summary_section()
            if ws_section:
                parts.append(ws_section)

        parts.append("\n\n[执行摘要]\n" + "\n".join(lines))

        return "".join(parts)

    def _build_work_summary_section(self) -> str:
        """Build [子Agent工作总结] section from sub_agent_records.

        Placed BEFORE [执行摘要] so that high-level task summaries appear
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
        lines = ["\n\n[子Agent工作总结]"]
        for i, ws in enumerate(summaries, 1):
            lines.append(f"{i}. {ws}")
        return "\n".join(lines)

    def _build_chain_summary(self, react_trace: list[dict]) -> list[dict] | None:
        """
        从 ReAct trace 构建思维链摘要（用于 IM 消息 metadata）。

        每个迭代生成一个摘要项，包含 thinking 预览和工具调用列表。
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
        两段式 Prompt 第一阶段：Prompt Compiler

        将用户的原始请求转化为结构化的任务定义。
        使用独立上下文，不进入核心对话历史。

        Args:
            user_message: 用户原始消息

        Returns:
            (compiled_prompt, raw_compiler_output)
            - compiled_prompt: 编译后的提示词（默认保持用户原始消息，避免污染主对话 messages）
            - raw_compiler_output: Prompt Compiler 的原始输出（用于日志）
        """
        try:
            # 调用 Brain 的 Compiler 专用方法（独立快速模型，禁用思考，失败回退主模型）
            response = await self.brain.compiler_think(
                prompt=user_message,
                system=PROMPT_COMPILER_SYSTEM,
            )

            # 移除 thinking 标签（回退到主模型时可能带有）
            compiler_output = (
                strip_thinking_tags(response.content).strip() if response.content else ""
            )
            logger.info(f"Prompt compiled: {compiler_output}")

            # 关键策略：不把 compiler_output 直接塞回 user message（避免污染主模型 messages）
            # 后续会将短摘要注入 system/developer 段，并复用为 memory 检索 query
            return user_message, compiler_output

        except Exception as e:
            logger.warning(f"Prompt compilation failed: {e}, using original message")
            # 编译失败时直接使用原始消息
            return user_message, ""

    def _summarize_compiler_output(self, compiler_output: str, max_chars: int = 600) -> str:
        """
        将 Prompt Compiler 的 YAML 输出压缩为短摘要（用于 system/developer 注入与 memory query）。

        目标：稳定、短、可复用，不污染主 messages。
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
        执行任务复盘分析

        当任务耗时过长时，让 LLM 分析原因，找出可以改进的地方。

        Args:
            task_monitor: 任务监控器

        Returns:
            复盘分析结果
        """
        try:
            context = task_monitor.get_retrospect_context()
            prompt = RETROSPECT_PROMPT.format(context=context)

            # 使用 Brain 进行复盘分析（独立上下文）
            response = await self.brain.think(
                prompt=prompt,
                system="你是一个任务执行分析专家。请简洁地分析任务执行情况，找出耗时原因和改进建议。",
            )

            result = strip_thinking_tags(response.content).strip() if response.content else ""

            # 保存复盘结果到监控器
            task_monitor.metrics.retrospect_result = result

            # 如果发现明显的重复错误模式，记录到记忆中
            if "重复" in result or "无效" in result or "弯路" in result:
                try:
                    from ..memory.types import Memory, MemoryPriority, MemoryType

                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"任务执行复盘发现问题：{result}",
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
        后台执行任务复盘分析

        这个方法在后台异步执行，不阻塞主响应。
        复盘结果会保存到文件，供每日自检系统读取汇总。

        Args:
            task_monitor: 任务监控器
            session_id: 会话 ID
        """
        try:
            # 执行复盘分析
            retrospect_result = await self._do_task_retrospect(task_monitor)

            if not retrospect_result:
                return

            # 保存到复盘存储
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
        判断是否需要进行 Prompt 编译

        仅基于长度判断：极短消息信息量不足以产生有意义的 TaskDefinition，
        编译是纯浪费。消息类型分类（闲聊/问答/任务）由大模型自己决定，
        不在此处做关键词/正则匹配。
        """
        # 极短消息不需要编译（信息量不足以产生有意义的结构化 TaskDefinition）
        if len(message.strip()) < 20:
            return False

        # 纯图片/语音消息不需要编译（Compiler 看不到多模态内容，编译只会产生误导性任务定义）
        stripped = message.strip()
        if re.fullmatch(r"(\[图片: [^\]]+\]\s*)+", stripped):
            return False
        if re.fullmatch(r"(\[语音转文字: [^\]]+\]\s*)+", stripped):
            return False

        # 其他情况都进行编译
        return True

    async def _detect_topic_change(
        self, session_messages: list[dict], new_message: str, session: Any = None
    ) -> bool:
        """检测当前消息是否是新话题（与近期对话无关）。

        结合多层上下文（当前任务、对话摘要、近期消息）让 LLM 做综合判断。
        仅在 IM 通道调用。

        Returns:
            True 表示检测到话题切换
        """
        if not new_message or len(new_message.strip()) < 5:
            return False
        if not session_messages:
            return False

        _new = new_message.strip()

        # ---- 构建多层上下文 ----

        context_parts: list[str] = []

        # Layer 1: 当前任务/话题（如果有）
        if session:
            task_desc = session.context.get_variable("task_description") if hasattr(session, "context") else None
            if task_desc:
                context_parts.append(f"当前任务: {task_desc}")
            summary = getattr(session.context, "summary", None) if hasattr(session, "context") else None
            if summary:
                from .tool_executor import smart_truncate as _st
                summary_trunc, _ = _st(summary, 600, save_full=False, label="topic_summary")
                context_parts.append(f"对话摘要: {summary_trunc}")

        from .tool_executor import smart_truncate as _st
        recent = session_messages[-6:]
        dialog_lines: list[str] = []
        for msg in recent:
            role = "用户" if msg.get("role") == "user" else "助手"
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                preview, _ = _st(content, 500, save_full=False, label="topic_content")
                preview = preview.replace("\n", " ")
                dialog_lines.append(f"{role}: {preview}")
        if dialog_lines:
            context_parts.append("近期对话:\n" + "\n".join(dialog_lines))

        if not context_parts:
            return False

        full_context = "\n\n".join(context_parts)

        new_trunc, _ = _st(_new, 800, save_full=False, label="topic_new")
        try:
            response = await self.brain.compiler_think(
                prompt=(
                    f"{full_context}\n\n"
                    f"新消息: {new_trunc}\n\n"
                    "判断：新消息是延续当前话题(CONTINUE)，还是开启全新话题(NEW)？\n"
                    "只输出一个单词：CONTINUE 或 NEW"
                ),
                system=(
                    "你是话题切换检测器。结合当前任务和近期对话上下文，"
                    "判断新消息是否属于同一话题。\n"
                    "CONTINUE: 新消息是对当前话题的跟进、补充、确认、追问，"
                    "或与当前任务相关的后续操作。\n"
                    "NEW: 新消息引入了与当前对话完全无关的新话题或新任务。\n"
                    "只输出一个单词。"
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
        """获取最后一条用户请求（当前任务的原始请求）"""
        from .tool_executor import smart_truncate

        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and not content.startswith("[系统]"):
                    result, _ = smart_truncate(content, 3000, save_full=False, label="user_request")
                    return result
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            if not text.startswith("[系统]"):
                                result, _ = smart_truncate(text, 3000, save_full=False, label="user_request")
                                return result
        return ""

    @staticmethod
    def _build_tool_fallback_summary(
        executed_tool_names: list[str],
        delivery_receipts: list[dict],
    ) -> str | None:
        """当 LLM 多次未返回可见文本时，从工具执行记录构建 fallback 摘要。"""
        parts: list[str] = []

        if delivery_receipts:
            for r in delivery_receipts:
                desc = r.get("description") or r.get("summary") or r.get("title") or ""
                if desc:
                    parts.append(f"• {desc}")
            if parts:
                return "已完成以下操作：\n" + "\n".join(parts)

        if executed_tool_names:
            unique = list(dict.fromkeys(executed_tool_names))
            tool_summary = "、".join(unique[:10])
            if len(unique) > 10:
                tool_summary += f" 等共 {len(unique)} 项"
            return f"任务已执行完毕（使用了工具：{tool_summary}），但模型未生成文本总结。如需详情请重新提问。"

        return None

    async def _verify_task_completion(
        self,
        user_request: str,
        assistant_response: str,
        executed_tools: list[str],
        delivery_receipts: list[dict] | None = None,
    ) -> bool:
        """
        任务完成度复核

        让 LLM 判断当前响应是否真正完成了用户的意图，
        而不是仅仅返回了中间状态的文本。

        Args:
            user_request: 用户原始请求
            assistant_response: 助手当前响应
            executed_tools: 已执行的工具列表

        Returns:
            True 如果任务已完成，False 如果需要继续执行
        """
        delivery_receipts = delivery_receipts or []

        # === Quick completion check (evidence-based) ===
        # 交付型任务：必须以 deliver_artifacts 的成功回执作为“已交付”证据，而不是仅凭工具名。
        if "deliver_artifacts" in (executed_tools or []):
            delivered = [r for r in delivery_receipts if r.get("status") == "delivered"]
            if delivered:
                logger.info(
                    f"[TaskVerify] deliver_artifacts delivered={len(delivered)}, marking as completed"
                )
                return True

        # Plan 明确完成：允许快速完成（避免卡在 verify）
        if "complete_plan" in (executed_tools or []):
            logger.info("[TaskVerify] complete_plan executed, marking as completed")
            return True

        # 如果响应宣称“已发送/已交付”，但没有任何交付证据，默认判定未完成（避免空口刷屏）
        if any(
            k in (assistant_response or "") for k in ("已发送", "已交付", "已发给你", "已发给您")
        ) and not delivery_receipts and "deliver_artifacts" not in (executed_tools or []):
            logger.info(
                "[TaskVerify] delivery claim without receipts/tools, marking as INCOMPLETE"
            )
            return False

        # === Plan 步骤检查：如果有活跃 Plan 且有未完成步骤，强制继续执行 ===
        from ..tools.handlers.plan import get_plan_handler_for_session, has_active_plan

        conversation_id = getattr(self, "_current_conversation_id", None) or getattr(
            self, "_current_session_id", None
        )
        if conversation_id and has_active_plan(conversation_id):
            handler = get_plan_handler_for_session(conversation_id)
            plan = handler.get_plan_for(conversation_id) if handler else None
            if plan:
                steps = plan.get("steps", [])
                pending = [s for s in steps if s.get("status") in ("pending", "in_progress")]

                if pending:
                    pending_ids = [s.get("id", "?") for s in pending[:3]]
                    logger.info(
                        f"[TaskVerify] Plan has {len(pending)} pending steps: {pending_ids}, forcing continue"
                    )
                    return False

                if plan.get("status") != "completed":
                    logger.info(
                        "[TaskVerify] All plan steps done but plan not formally completed, proceeding to LLM verification"
                    )
                    # 继续执行 LLM 验证，不强制返回 False

        # 依赖 LLM 进行判断
        from .tool_executor import smart_truncate
        user_display, _ = smart_truncate(user_request, 3000, save_full=False, label="verify_user")
        response_display, _ = smart_truncate(assistant_response, 8000, save_full=False, label="verify_response")

        verify_prompt = f"""请判断以下交互是否已经**完成**用户的意图。

## 用户消息
{user_display}

## 助手响应
{response_display}

## 已执行的工具
{", ".join(executed_tools) if executed_tools else "无"}

## 附件交付回执（如有）
{delivery_receipts if delivery_receipts else "无"}

## 判断标准

### 非任务类消息（直接判 COMPLETED）
- 如果用户消息是**闲聊/问候**（如"在吗""你好""在不在""嗨""干嘛呢"），助手已礼貌回复 → **COMPLETED**
- 如果用户消息是**简单确认/反馈**（如"好的""收到""嗯""哦"），助手已简短回应 → **COMPLETED**
- 如果用户消息是**简单问答**（如"几点了""天气怎么样"），助手已给出回答 → **COMPLETED**

### 任务类消息
- 如果已执行 write_file 工具，说明文件已保存，保存任务完成
- 如果已执行 browser_navigate/browser_click 等浏览器工具，说明浏览器操作已执行
- 工具执行成功即表示该操作完成，不要求响应文本中包含文件内容
- 如果响应只是说"现在开始..."、"让我..."且没有工具执行，说明任务还在进行中
- 如果响应包含明确的操作确认（如"已完成"、"已发送"、"已保存"），任务完成

## 回答要求
请用以下格式回答：
STATUS: COMPLETED 或 INCOMPLETE
EVIDENCE: 完成的证据（如有）
MISSING: 缺失的内容（如有）
NEXT: 建议的下一步（如有）"""

        try:
            response = await self.brain.think(
                prompt=verify_prompt,
                system="你是一个任务完成度判断助手。请分析任务是否完成，并说明证据和缺失项。",
            )

            result = response.content.strip().upper() if response.content else ""
            # 建议 33: 改进的完成度判断
            is_completed = "STATUS: COMPLETED" in result or (
                "COMPLETED" in result and "INCOMPLETE" not in result
            )

            logger.info(
                f"[TaskVerify] user_request={user_request[:50]}... response={assistant_response[:50]}... result={result} -> {is_completed}"
            )
            return is_completed

        except Exception as e:
            logger.warning(f"[TaskVerify] Failed to verify: {e}, assuming INCOMPLETE")
            return False  # 验证失败时不要默认完成，交由上层计数器做兜底退出

    async def _cancellable_llm_call(self, cancel_event: asyncio.Event, **kwargs) -> Any:
        """将 LLM 调用包装为可取消的 asyncio.Task，配合 cancel_event 竞速。

        当 cancel_event 先于 LLM 返回被 set() 时，抛出 UserCancelledError。
        """
        logger.info(f"[CancellableLLM] 发起可取消 LLM 调用, cancel_event.is_set={cancel_event.is_set()}")
        _tt = set_tracking_context(TokenTrackingContext(
            operation_type="chat",
            session_id=kwargs.get("conversation_id", ""),
            channel="cli",
        ))
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
                logger.info("[CancellableLLM] LLM 调用先完成，正常返回")
                return llm_task.result()
            else:
                reason = self._cancel_reason or "用户请求停止"
                logger.info(f"[CancellableLLM] cancel_event 先触发，抛出 UserCancelledError: {reason!r}")
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
        """取消后立即返回默认文本，后台异步发起 LLM 收尾。

        Args:
            working_messages: 当前的工作消息列表
            system_prompt: 当前的系统提示词
            current_model: 当前使用的模型

        Returns:
            固定的取消文本（不等待 LLM）
        """
        cancel_reason = self._cancel_reason or "用户请求停止"
        default_farewell = "✅ 好的，已停止当前任务。"

        logger.info(
            f"[StopTask][CancelFarewell] 立即返回默认文本，后台发起 LLM 收尾: "
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
        """后台执行 LLM 收尾调用，将结果持久化到上下文（不阻塞用户）。"""
        farewell_text = "✅ 好的，已停止当前任务。"
        try:
            cancel_msg = (
                f"[系统通知] 用户发送了停止指令「{cancel_reason}」，"
                "请立即停止当前操作，简要告知用户已停止以及当前进度（1~2 句话即可）。"
                "不要调用任何工具。"
            )
            working_messages.append({"role": "user", "content": cancel_msg})

            _tt = set_tracking_context(TokenTrackingContext(
                operation_type="farewell", channel="api",
            ))
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
                logger.info(f"[StopTask][BgFarewell] LLM farewell 完成: {farewell_text[:100]}")
            except TimeoutError:
                logger.warning("[StopTask][BgFarewell] LLM farewell 超时 (5s)")
            except Exception as e:
                logger.warning(f"[StopTask][BgFarewell] LLM farewell 失败: {e}")
            finally:
                reset_tracking_context(_tt)
        except Exception as e:
            logger.warning(f"[StopTask][BgFarewell] 后台收尾异常: {e}")

        self._persist_cancel_to_context(cancel_reason, farewell_text)

    def _persist_cancel_to_context(self, cancel_reason: str, farewell_text: str) -> None:
        """将中断事件持久化到 _context.messages 对话历史。

        确保后续对话中 LLM 能看到之前的中断历史。
        """
        try:
            ctx = getattr(self, "_context", None)
            if ctx and hasattr(ctx, "messages"):
                ctx.messages.append({
                    "role": "user",
                    "content": f"[用户中断了上一个任务: {cancel_reason}]",
                })
                ctx.messages.append({
                    "role": "assistant",
                    "content": farewell_text,
                })
                logger.debug(f"[StopTask] Cancel event persisted to context (reason={cancel_reason})")
        except Exception as e:
            logger.warning(f"[StopTask] Failed to persist cancel to context: {e}")

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
    ) -> str:
        """
        使用指定的消息上下文进行对话（委托给 ReasoningEngine）

        Phase 2 重构: 保留 system prompt / task_description 的构建逻辑，
        将核心推理循环委托给 self.reasoning_engine.run()。

        Args:
            messages: 对话消息列表
            use_session_prompt: 是否使用 Session 专用的 System Prompt
            task_monitor: 任务监控器
            session_type: 会话类型 ("cli" 或 "im")
            thinking_mode: 思考模式覆盖 ('auto'/'on'/'off'/None)
            thinking_depth: 思考深度 ('low'/'medium'/'high'/None)
            progress_callback: 进度回调 async fn(str) -> None，IM 实时思维链
            endpoint_override: 端点覆盖

        Returns:
            最终响应文本
        """
        # === 构建 System Prompt ===
        task_description = self._get_last_user_request(messages).strip()
        if not task_description:
            task_description = (getattr(self, "_current_task_query", "") or "").strip()

        if use_session_prompt:
            system_prompt = await self._build_system_prompt_compiled(
                task_description=task_description,
                session_type=session_type,
            )
        else:
            system_prompt = self._context.system

        # 注入 TaskDefinition
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

        # === 委托给 ReasoningEngine ===
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
        )

    # ==================== 取消状态代理属性 ====================

    @property
    def _task_cancelled(self) -> bool:
        """统一的取消状态查询（委托到 TaskState，兼容旧代码引用）"""
        return (
            hasattr(self, "agent_state")
            and self.agent_state is not None
            and self.agent_state.is_task_cancelled
        )

    @property
    def _cancel_reason(self) -> str:
        """统一的取消原因查询（委托到 TaskState，兼容旧代码引用）"""
        if hasattr(self, "agent_state") and self.agent_state:
            return self.agent_state.task_cancel_reason
        return ""

    def set_interrupt_enabled(self, enabled: bool) -> None:
        """
        设置是否启用中断检查

        Args:
            enabled: 是否启用
        """
        self._interrupt_enabled = enabled
        logger.info(f"Interrupt check {'enabled' if enabled else 'disabled'}")

    def cancel_current_task(self, reason: str = "用户请求停止", session_id: str | None = None) -> None:
        """
        取消正在执行的任务。

        如果指定 session_id，仅取消该会话的任务和计划；否则取消所有。
        当 session 没有活跃 task 时（如处于准备阶段），将 cancel 存入 _pending_cancels，
        等待后续检查点消费。

        Args:
            reason: 取消原因
            session_id: 可选会话 ID，实现跨通道隔离
        """
        has_state = hasattr(self, "agent_state") and self.agent_state

        if session_id and has_state:
            task = self.agent_state.get_task_for_session(session_id)
            _effective_sid = session_id
            # task key = session_id (raw chat_id)，一般精确匹配即可命中。
            # 若仍未找到，兜底用 _current_conversation_id / _current_session_id。
            if not task:
                for _alt_key in (self._current_conversation_id, self._current_session_id):
                    if _alt_key and _alt_key != session_id:
                        task = self.agent_state.get_task_for_session(_alt_key)
                        if task:
                            _effective_sid = _alt_key
                            break
            task_status = task.status.value if task else "N/A"
            logger.info(
                f"[StopTask] cancel_current_task 被调用: reason={reason!r}, "
                f"session_id={session_id}, effective_sid={_effective_sid!r}, "
                f"task_status={task_status}"
            )
            if task:
                self.agent_state.cancel_task(reason, session_id=_effective_sid)
            else:
                logger.warning(
                    f"[StopTask] No task found for session {session_id}, "
                    f"storing as pending cancel"
                )
                self._pending_cancels[session_id] = reason
        elif has_state:
            has_task = self.agent_state.current_task is not None
            task_status = self.agent_state.current_task.status.value if has_task else "N/A"
            logger.info(
                f"[StopTask] cancel_current_task 被调用: reason={reason!r}, "
                f"has_state={has_state}, has_task={has_task}, task_status={task_status}"
            )
            self.agent_state.cancel_task(reason)

        try:
            from ..tools.handlers.plan import cancel_plan
            if session_id:
                if cancel_plan(session_id):
                    logger.info(f"[StopTask] Cancelled active plan for session {session_id}")
            else:
                from ..tools.handlers.plan import _session_active_plans
                for sid in list(_session_active_plans.keys()):
                    if cancel_plan(sid):
                        logger.info(f"[StopTask] Cancelled active plan for session {sid}")
        except Exception as e:
            logger.warning(f"[StopTask] Failed to cancel plan: {e}")

        logger.info(f"[StopTask] Task cancellation completed: {reason}")

    def _is_session_cancelled(self, session_id: str | None = None) -> bool:
        """检查指定 session 是否有 prepare 阶段写入的挂起取消信号。

        仅检查 _pending_cancels（task 不存在时由 cancel_current_task 写入）。
        不检查 task.cancelled，因为那可能是上一轮残留的旧 task，会导致误判。
        实时取消由 cancel_event 机制在 reason_stream/run 内部处理。
        """
        return bool(session_id and session_id in self._pending_cancels)

    def _consume_pending_cancel(self, session_id: str | None = None) -> str | None:
        """消费并返回挂起的取消原因，如果没有则返回 None。"""
        if session_id:
            return self._pending_cancels.pop(session_id, None)
        return None

    def is_stop_command(self, message: str) -> bool:
        """
        检查消息是否为停止指令

        Args:
            message: 用户消息

        Returns:
            是否为停止指令
        """
        msg_lower = message.strip().lower()
        return msg_lower in self.STOP_COMMANDS or message.strip() in self.STOP_COMMANDS

    def is_skip_command(self, message: str) -> bool:
        """
        检查消息是否为跳过当前步骤指令

        Args:
            message: 用户消息

        Returns:
            是否为跳过指令
        """
        msg_lower = message.strip().lower()
        return msg_lower in self.SKIP_COMMANDS or message.strip() in self.SKIP_COMMANDS

    def classify_interrupt(self, message: str) -> str:
        """
        分类中断消息类型

        Args:
            message: 用户消息

        Returns:
            "stop" / "skip" / "insert"
        """
        if self.is_stop_command(message):
            return "stop"
        elif self.is_skip_command(message):
            return "skip"
        else:
            return "insert"

    def skip_current_step(self, reason: str = "用户请求跳过当前步骤", session_id: str | None = None) -> bool:
        """
        跳过当前正在执行的工具/步骤（不终止整个任务）

        Args:
            reason: 跳过原因
            session_id: 可选会话 ID，实现跨通道隔离

        Returns:
            是否成功设置 skip（False 表示无活跃任务）
        """
        has_state = hasattr(self, "agent_state") and self.agent_state
        if not has_state:
            logger.warning(f"[SkipStep] No agent_state to skip: {reason}")
            return False

        _effective_sid = session_id or getattr(self, "_current_session_id", None)
        task = (
            self.agent_state.get_task_for_session(_effective_sid) if _effective_sid else None
        )
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
        logger.warning(
            f"[SkipStep] No active task to skip: {reason} "
            f"(session_id={session_id})"
        )
        return False

    async def insert_user_message(self, text: str, session_id: str | None = None) -> bool:
        """
        向当前任务注入用户消息（任务执行期间的非指令消息）

        Args:
            text: 用户消息文本
            session_id: 可选会话 ID，实现跨通道隔离

        Returns:
            是否成功入队（False 表示无活跃任务，消息被丢弃）
        """
        has_state = hasattr(self, "agent_state") and self.agent_state
        if not has_state:
            logger.warning(f"[UserInsert] No agent_state, message dropped: {text[:50]}...")
            return False

        _effective_sid = session_id or getattr(self, "_current_session_id", None)
        task = (
            self.agent_state.get_task_for_session(_effective_sid) if _effective_sid else None
        )
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
            logger.info(f"[UserInsert] User message queued: {text[:50]}... (effective_sid={_effective_sid!r})")
            return True
        logger.warning(f"[UserInsert] No active task, message dropped: {text[:50]}...")
        return False

    async def execute_task_from_message(self, message: str) -> TaskResult:
        """从消息创建并执行任务"""
        task = Task(
            id=str(uuid.uuid4())[:8],
            description=message,
            session_id=getattr(self, "_current_session_id", None),  # 关联当前会话
            priority=1,
        )
        return await self.execute_task(task)

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        执行工具调用

        优先使用 handler_registry 执行，不支持的工具使用旧的 if-elif 兜底
        执行后自动附加 WARNING/ERROR 日志到返回结果

        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数

        Returns:
            工具执行结果（包含执行期间的警告/错误日志）
        """
        logger.info(f"Executing tool: {tool_name} with {tool_input}")

        # ============================================
        # Plan 模式强制检查
        # ============================================
        # 如果当前 session 被标记为需要 Plan（compound 任务），
        # 但还没有创建 Plan，则拒绝执行其他工具。
        # 计划管理工具始终放行，避免死锁。
        _PLAN_TOOLS = {"create_plan", "update_plan_step", "complete_plan", "get_plan_status"}
        if tool_name not in _PLAN_TOOLS:
            from ..tools.handlers.plan import has_active_plan, is_plan_required

            session_id = getattr(self, "_current_session_id", None)
            if session_id and is_plan_required(session_id) and not has_active_plan(session_id):
                return (
                    "⚠️ **这是一个多步骤任务，必须先创建计划！**\n\n"
                    "请先调用 `create_plan` 工具创建任务计划，然后再执行具体操作。\n\n"
                    "示例：\n"
                    "```\n"
                    "create_plan(\n"
                    "  task_summary='写脚本获取时间并显示',\n"
                    "  steps=[\n"
                    "    {id: 'step1', description: '创建Python脚本', tool: 'write_file'},\n"
                    "    {id: 'step2', description: '执行脚本', tool: 'run_shell'},\n"
                    "    {id: 'step3', description: '读取结果', tool: 'read_file'}\n"
                    "  ]\n"
                    ")\n"
                    "```"
                )

        # 导入日志缓存
        from ..logging import get_session_log_buffer

        log_buffer = get_session_log_buffer()

        # 记录执行前的日志数量
        logs_before = log_buffer.get_logs(count=500)
        logs_before_count = len(logs_before)

        try:
            # 优先使用 handler_registry 执行
            if self.handler_registry.has_tool(tool_name):
                result = await self.handler_registry.execute_by_tool(tool_name, tool_input)
            else:
                # 未注册的工具
                return f"❌ 未知工具: {tool_name}。请检查工具名称是否正确。"

            # 获取执行期间产生的新日志（WARNING/ERROR/CRITICAL）
            all_logs = log_buffer.get_logs(count=500)
            new_logs = [
                log
                for log in all_logs[logs_before_count:]
                if log["level"] in ("WARNING", "ERROR", "CRITICAL")
            ]

            # 如果有警告/错误日志，附加到结果
            if new_logs:
                result += "\n\n[执行日志]:\n"
                for log in new_logs[-10:]:  # 最多显示 10 条
                    result += f"[{log['level']}] {log['module']}: {log['message']}\n"

            # ★ 通用截断守卫（与 ToolExecutor._guard_truncate 逻辑一致）
            result = ToolExecutor._guard_truncate(tool_name, result)

            return result

        except Exception as e:
            logger.error(f"Tool execution error: {e}", exc_info=True)
            return f"工具执行错误: {str(e)}"

    async def execute_task(self, task: Task) -> TaskResult:
        """
        执行任务（带工具调用）

        安全模型切换策略：
        1. 超时或错误时先重试 3 次
        2. 重试次数用尽后才切换到备用模型
        3. 切换时废弃已有的工具调用历史，从任务原始描述开始重新处理

        Args:
            task: 任务对象

        Returns:
            TaskResult
        """
        import time

        start_time = time.time()

        if not self._initialized:
            await self.initialize()

        logger.info(f"Executing task: {task.description}")

        # === 创建任务监控器 ===
        task_monitor = TaskMonitor(
            task_id=task.id,
            description=task.description,
            session_id=task.session_id,
            timeout_seconds=settings.progress_timeout_seconds,
            hard_timeout_seconds=settings.hard_timeout_seconds,
            retrospect_threshold=60,  # 复盘阈值：60秒
            fallback_model=self.brain.get_fallback_model(task.session_id),  # 动态获取备用模型
            retry_before_switch=3,  # 切换前重试 3 次
        )
        task_monitor.start(self.brain.model)

        # 使用已构建的系统提示词 (包含技能清单)
        # 技能清单已在初始化时注入到 _context.system 中
        system_prompt = (
            self._context.system
            + """

## Task Execution Strategy

请使用工具来实际执行任务:

1. **Check skill catalog above** - 技能清单已在上方，根据描述判断是否有匹配的技能
2. **If skill matches**: Use `get_skill_info(skill_name)` to load full instructions
3. **Run script**: Use `run_skill_script(skill_name, script_name, args)`
4. **If no skill matches**: Use `skill-creator` skill to create one, then `load_skill` to load it

永不放弃，直到任务完成！"""
        )

        # === Plan 持久化：保存不含 Plan 的基础提示词，循环内动态追加 ===
        _base_system_prompt_task = system_prompt
        _task_conversation_id = task.session_id or f"task:{task.id}"

        def _build_effective_system_prompt_task() -> str:
            """在基础提示词上动态追加活跃 Plan 段落（Task 路径）"""
            from ..tools.handlers.plan import get_active_plan_prompt

            prompt = _base_system_prompt_task
            plan_section = get_active_plan_prompt(_task_conversation_id)
            if plan_section:
                prompt += f"\n\n{plan_section}\n"
            return prompt

        # === 关键：保存原始任务描述，用于模型切换时重置上下文 ===
        original_task_message = {"role": "user", "content": task.description}
        messages = [original_task_message.copy()]

        max_tool_iterations = settings.max_iterations  # Ralph Wiggum 模式：永不放弃
        iteration = 0
        final_response = ""
        current_model = self.brain.model
        conversation_id = task.session_id or f"task:{task.id}"

        def _resolve_endpoint_name(model_or_endpoint: str) -> str | None:
            """将 'endpoint_name' 或 'model' 解析为 endpoint_name（任务循环专用，最小兼容）。"""
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

        # 循环检测委托给 RuntimeSupervisor（与 ReasoningEngine 共用同一套策略）
        _task_supervisor = self.reasoning_engine.supervisor
        _task_supervisor.reset()
        _task_consecutive_tool_rounds = 0

        MAX_TASK_MODEL_SWITCHES = 2
        _task_switch_count = 0
        _total_llm_retries = 0
        MAX_TOTAL_LLM_RETRIES = 3

        # 追问计数器：当 LLM 没有调用工具时，最多追问几次
        no_tool_call_count = 0
        max_no_tool_retries = max(0, int(getattr(settings, "force_tool_call_max_retries", 1)))

        # 获取 cancel_event（用于 LLM 调用竞速取消）
        _cancel_event = (
            self.agent_state.current_task.cancel_event
            if self.agent_state and self.agent_state.current_task
            else asyncio.Event()
        )

        try:
            while iteration < max_tool_iterations:
                # C8: 每轮迭代开始时检查任务是否被取消
                if self._task_cancelled:
                    logger.info(
                        f"[StopTask] Task cancelled in execute_task: {self._cancel_reason}"
                    )
                    return "✅ 任务已停止。"

                iteration += 1
                logger.info(f"Task iteration {iteration}")

                # 任务监控：开始迭代
                task_monitor.begin_iteration(iteration, current_model)

                # === 安全模型切换检查 ===
                # 检查是否超时且重试次数已用尽
                if task_monitor.should_switch_model:
                    # 熔断检查：防止无限模型切换循环
                    _task_switch_count += 1
                    if _task_switch_count > MAX_TASK_MODEL_SWITCHES:
                        logger.error(
                            f"[Task:{task.id}] Exceeded max model switches "
                            f"({MAX_TASK_MODEL_SWITCHES}), aborting task"
                        )
                        return (
                            "❌ 任务执行失败，已尝试多个模型仍无法恢复。\n"
                            "💡 你可以直接重新发送来重试。"
                        )

                    new_model = task_monitor.fallback_model
                    if not new_model:
                        logger.warning("[ModelSwitch] No fallback model available for sub-agent timeout")
                        return "任务失败：所有模型端点均不可用，请检查网络连接。"
                    task_monitor.switch_model(
                        new_model,
                        f"任务执行超过 {task_monitor.timeout_seconds} 秒，重试 {task_monitor.retry_count} 次后切换",
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
                                f"❌ 任务失败：模型切换失败（{msg}），无法继续执行。\n"
                                "💡 建议：请检查网络连接，或在设置中心确认至少有一个模型配置正确。"
                            )
                    else:
                        logger.warning(f"[ModelSwitch] Cannot resolve endpoint for '{new_model}'")

                    current_model = new_model

                    # === 关键：重置上下文，废弃工具调用历史 ===
                    logger.warning(
                        f"[ModelSwitch] Task {task.id}: Switching to {new_model}, resetting context. "
                        f"Discarding {len(messages) - 1} tool-related messages"
                    )
                    messages = [original_task_message.copy()]

                    # 添加模型切换说明 + tool-state revalidation barrier
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[系统提示] 发生模型切换：之前的 tool_use/tool_result 历史已清除。现在所有工具状态一律视为未知。\n"
                                "在执行任何状态型工具前，必须先做状态复核：浏览器先 browser_open；MCP 先 list_mcp_servers；桌面先 desktop_window/desktop_inspect。\n"
                                "请从头开始处理上面的任务请求。"
                            ),
                        }
                    )

                    # 重置循环检测
                    _task_supervisor.reset()
                    _task_consecutive_tool_rounds = 0

                try:
                    # 检查并压缩上下文（任务执行可能产生大量工具输出）
                    if iteration > 1:
                        messages = await self._compress_context(
                            messages, system_prompt=_build_effective_system_prompt_task()
                        )

                    # 调用 Brain（可被 cancel_event 中断）
                    response = await self._cancellable_llm_call(
                        _cancel_event,
                        max_tokens=self.brain.max_tokens,
                        system=_build_effective_system_prompt_task(),
                        tools=self._effective_tools,
                        messages=messages,
                        conversation_id=conversation_id,
                    )

                    # 成功调用，重置重试计数
                    task_monitor.reset_retry_count()

                except UserCancelledError:
                    logger.info(f"[StopTask] LLM call interrupted by user cancel in execute_task {task.id}")
                    return await self._handle_cancel_farewell(
                        messages, _build_effective_system_prompt_task(), current_model
                    )

                except Exception as e:
                    logger.error(f"[LLM] Brain call failed in task {task.id}: {e}")

                    # ── 全局重试计数 ──
                    _total_llm_retries += 1
                    if _total_llm_retries > MAX_TOTAL_LLM_RETRIES:
                        logger.error(
                            f"[Task:{task.id}] Global retry limit reached "
                            f"({_total_llm_retries}/{MAX_TOTAL_LLM_RETRIES}), aborting"
                        )
                        return (
                            f"❌ 任务执行失败，已重试 {MAX_TOTAL_LLM_RETRIES} 次仍无法恢复。\n"
                            f"错误: {str(e)[:200]}\n"
                            "💡 你可以直接重新发送来重试。"
                        )

                    # ── 结构性错误快速熔断 ──
                    from ..llm.types import AllEndpointsFailedError as _Aefe
                    from .reasoning_engine import ReasoningEngine
                    if isinstance(e, _Aefe) and e.is_structural:
                        _already = getattr(self, '_task_structural_stripped', False)
                        if not _already:
                            stripped, did_strip = ReasoningEngine._strip_heavy_content(messages)
                            if did_strip:
                                logger.warning(f"[Task:{task.id}] Structural error: stripping heavy content, retrying once")
                                self._task_structural_stripped = True
                                messages.clear()
                                messages.extend(stripped)
                                llm_client = getattr(self.brain, "_llm_client", None)
                                if llm_client:
                                    llm_client.reset_all_cooldowns(include_structural=True)
                                continue
                        logger.error(f"[Task:{task.id}] Structural error, aborting: {str(e)[:200]}")
                        return (
                            f"❌ API 请求格式错误，无法恢复。\n"
                            f"错误: {str(e)[:200]}\n"
                            "💡 你可以直接重新发送来重试。"
                        )

                    # 记录错误并判断是否应该重试
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
                                f"❌ 任务执行失败，已尝试多个模型仍无法恢复。\n"
                                f"错误: {str(e)[:200]}\n"
                                "💡 你可以直接重新发送来重试。"
                            )

                        new_model = task_monitor.fallback_model
                        if not new_model:
                            logger.warning("[ModelSwitch] No fallback model available for sub-agent error")
                            return "任务失败：所有模型端点均不可用，请检查网络连接。"
                        task_monitor.switch_model(
                            new_model,
                            f"LLM 调用失败，重试 {task_monitor.retry_count} 次后切换: {e}",
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
                                # switch_model 失败（目标在冷静期），不重置 retry_count
                                # 直接 break，避免无限重试
                                return (
                                    f"❌ 任务失败：模型切换失败（{msg}），无法继续执行。\n"
                                    "💡 建议：请检查网络连接，或在设置中心确认至少有一个模型配置正确。"
                                )
                        else:
                            logger.warning(
                                f"[ModelSwitch] Cannot resolve endpoint for '{new_model}'"
                            )
                        current_model = new_model

                        # 重置上下文 + barrier
                        logger.warning(
                            f"[ModelSwitch] Task {task.id}: Switching to {new_model} due to errors, resetting context"
                        )
                        messages = [original_task_message.copy()]
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "[系统提示] 发生模型切换：之前的 tool_use/tool_result 历史已清除。现在所有工具状态一律视为未知。\n"
                                    "在执行任何状态型工具前，必须先做状态复核：浏览器先 browser_open；MCP 先 list_mcp_servers；桌面先 desktop_window/desktop_inspect。\n"
                                    "请从头开始处理上面的任务请求。"
                                ),
                            }
                        )
                        _task_supervisor.reset()
                        _task_consecutive_tool_rounds = 0
                        continue

                # 检测 max_tokens 截断
                _task_stop = getattr(response, "stop_reason", "")
                if str(_task_stop) == "max_tokens":
                    logger.warning(
                        f"[Task:{task.id}] ⚠️ LLM output truncated (stop_reason=max_tokens, limit={self.brain.max_tokens})"
                    )

                # 处理响应
                tool_calls = []
                text_content = ""

                for block in response.content:
                    if block.type == "text":
                        text_content += block.text
                    elif block.type == "tool_use":
                        d = {
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                        if getattr(block, "provider_extra", None):
                            d["provider_extra"] = block.provider_extra
                        tool_calls.append(d)

                # 任务监控：结束迭代
                task_monitor.end_iteration(text_content if text_content else "")

                # 如果有文本响应，保存（过滤 thinking 标签和工具调用模拟文本）
                if text_content:
                    cleaned_text = clean_llm_response(text_content)
                    # 只有在没有工具调用时才保存文本作为最终响应
                    # 如果有工具调用，这个文本可能是 LLM 的思考过程
                    if not tool_calls and cleaned_text:
                        # ForceToolCall 重试时，LLM 常返回短确认（"好的，已完成"），
                        # 不能用它覆盖前一轮已生成的完整内容（如新闻/热搜等）。
                        # 仅在新文本更长或 final_response 为空时才更新。
                        if not final_response or len(cleaned_text) >= len(final_response):
                            final_response = cleaned_text

                # 如果没有工具调用，检查是否需要强制要求调用工具
                if not tool_calls:
                    no_tool_call_count += 1

                    # 如果还有追问次数，强制要求调用工具
                    if no_tool_call_count <= max_no_tool_retries:
                        logger.warning(
                            f"[ForceToolCall] Task LLM returned text without tool calls (attempt {no_tool_call_count}/{max_no_tool_retries})"
                        )

                        # 将 LLM 的响应加入历史
                        if text_content:
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": text_content}],
                                }
                            )

                        # 追加强制要求调用工具的消息
                        messages.append(
                            {
                                "role": "user",
                                "content": "[系统] 若确实需要工具，请调用相应工具；若不需要工具（纯对话/问答），请直接回答，不要复述系统规则。",
                            }
                        )
                        continue  # 继续循环，让 LLM 调用工具

                    # 追问次数用尽，任务完成
                    break

                # 循环检测：委托给 RuntimeSupervisor
                _task_consecutive_tool_rounds += 1
                _task_supervisor.record_consecutive_tool_rounds(
                    _task_consecutive_tool_rounds
                )
                _round_sigs = []
                for _tc in tool_calls:
                    _n = _tc.get("name", "")
                    try:
                        _p = json.dumps(
                            _tc.get("input", {}), sort_keys=True, ensure_ascii=False
                        )
                    except Exception:
                        _p = str(_tc.get("input", {}))
                    _round_sigs.append(
                        f"{_n}({hashlib.md5(_p.encode()).hexdigest()[:8]})"
                    )
                _sig = "+".join(sorted(_round_sigs))
                _task_supervisor.record_tool_signature(_sig)

                _task_intervention = _task_supervisor.evaluate(iteration)
                if _task_intervention and _task_intervention.should_terminate:
                    logger.warning(
                        f"[Supervisor] Task loop terminated: "
                        f"{_task_intervention.message}"
                    )
                    final_response = (
                        "⚠️ 检测到工具调用陷入死循环，任务已自动终止。"
                        "请重新描述您的需求。"
                    )
                    break
                if _task_intervention and _task_intervention.should_inject_prompt:
                    messages.append({
                        "role": "user",
                        "content": _task_intervention.prompt_injection,
                    })

                # 执行工具调用
                # MiniMax M2.1 Interleaved Thinking 支持：
                # 必须完整保留 thinking 块以保持思维链连续性
                assistant_content = []
                for block in response.content:
                    if block.type == "thinking":
                        # 保留 thinking 块（MiniMax M2.1 要求）
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
                        d = {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                        if getattr(block, "provider_extra", None):
                            d["provider_extra"] = block.provider_extra
                        assistant_content.append(d)

                messages.append({"role": "assistant", "content": assistant_content})

                # 执行每个工具并收集结果
                # execute_task() 场景没有“工具间中断检查”的强需求，可按配置启用并行
                tool_results, executed_names, _ = await self._execute_tool_calls_batch(
                    tool_calls,
                    task_monitor=task_monitor,
                    allow_interrupt_checks=False,
                    capture_delivery_receipts=False,
                )

                messages.append({"role": "user", "content": tool_results})

                # === 统一处理 skip 反思 + 用户插入消息 ===
                if self.agent_state and self.agent_state.current_task:
                    await self.agent_state.current_task.process_post_tool_signals(messages)

                # 注意：不在工具执行后检查 stop_reason，让循环继续获取 LLM 的最终总结
            # 循环结束后，如果 final_response 内容不够充实，让 LLM 生成详细总结
            # 阈值 30：中文单字=1 char，大多数"状态确认"型回复 <20 chars，
            # 大多数有实际内容的回复 >30 chars（如天气、查询结果等）
            if not final_response or len(final_response.strip()) < 30:
                logger.info(
                    f"Task completed but final_response too short "
                    f"({len(final_response.strip()) if final_response else 0} chars), "
                    f"requesting detailed summary..."
                )
                try:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "任务已执行完毕，但你的回复内容太简短了。"
                                "请把具体的执行结果完整地告诉用户——"
                                "用户将直接看到你返回的文字，确保包含关键数据和结论。"
                            ),
                        }
                    )
                    _tt_sum = set_tracking_context(TokenTrackingContext(
                        operation_type="task_summary",
                        session_id=conversation_id or "",
                        channel="scheduler",
                    ))
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
                    final_response = "✅ 任务已停止。"
                except Exception as e:
                    logger.warning(f"Failed to get summary: {e}")
                    final_response = "任务已执行完成。"
        finally:
            # 清理 per-conversation override，避免影响后续任务/会话
            with contextlib.suppress(Exception):
                self.brain.restore_default_model(conversation_id=conversation_id)

        # === 完成任务监控 ===
        metrics = task_monitor.complete(
            success=True,
            response=final_response,
        )

        # === 后台复盘分析（如果任务耗时过长，不阻塞响应） ===
        if metrics.retrospect_needed:
            # 创建后台任务执行复盘，不等待结果
            asyncio.create_task(
                self._do_task_retrospect_background(task_monitor, task.session_id or task.id)
            )
            logger.info(f"[Task:{task.id}] Retrospect scheduled (background)")

        task.mark_completed(final_response)

        duration = time.time() - start_time

        # === 桌面通知（仅本地通道：cli/desktop；IM 通道已有自己的通知机制）===
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
        """格式化任务结果"""
        if result.success:
            return f"""✅ 任务完成

{result.data}

---
迭代次数: {result.iterations}
耗时: {result.duration_seconds:.2f}秒"""
        else:
            return f"""❌ 任务未能完成

错误: {result.error}

---
尝试次数: {result.iterations}
耗时: {result.duration_seconds:.2f}秒

我会继续尝试其他方法..."""

    async def self_check(self) -> dict[str, Any]:
        """
        自检

        Returns:
            自检结果
        """
        logger.info("Running self-check...")

        results = {
            "timestamp": datetime.now().isoformat(),
            "status": "healthy",
            "checks": {},
        }

        # 检查 Brain
        try:
            response = await self.brain.think("你好，这是一个测试。请回复'OK'。")
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

        # 检查 Identity
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

        # 检查配置
        results["checks"]["config"] = {
            "status": "ok" if settings.anthropic_api_key else "error",
            "message": "API key configured" if settings.anthropic_api_key else "API key missing",
        }

        # 检查技能系统 (SKILL.md 规范)
        skill_count = self.skill_registry.count
        results["checks"]["skills"] = {
            "status": "ok",
            "message": f"已安装 {skill_count} 个技能 (Agent Skills 规范)",
            "count": skill_count,
            "skills": [s.name for s in self.skill_registry.list_all()],
        }

        # 检查技能目录
        skills_path = settings.skills_path
        results["checks"]["skills_dir"] = {
            "status": "ok" if skills_path.exists() else "warning",
            "message": str(skills_path),
        }

        # 检查 MCP 客户端
        mcp_servers = self.mcp_client.list_servers()
        mcp_connected = self.mcp_client.list_connected()
        results["checks"]["mcp"] = {
            "status": "ok",
            "message": f"配置 {len(mcp_servers)} 个服务器, 已连接 {len(mcp_connected)} 个",
            "servers": mcp_servers,
            "connected": mcp_connected,
        }

        logger.info(f"Self-check complete: {results['status']}")

        return results

    def _on_iteration(self, iteration: int, task: Task) -> None:
        """Ralph 循环迭代回调"""
        logger.debug(f"Ralph iteration {iteration} for task {task.id}")

    def _on_error(self, error: str, task: Task) -> None:
        """Ralph 循环错误回调"""
        logger.warning(f"Ralph error for task {task.id}: {error}")

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized

    @property
    def conversation_history(self) -> list[dict]:
        """对话历史"""
        return self._conversation_history.copy()

    # ==================== 记忆系统方法 ====================

    def set_scheduler_gateway(self, gateway: Any) -> None:
        """
        设置定时任务调度器的消息网关

        用于定时任务执行后发送通知到 IM 通道

        Args:
            gateway: MessageGateway 实例
        """
        if hasattr(self, "_task_executor") and self._task_executor:
            self._task_executor.gateway = gateway
            # 同时传递 persona/memory/proactive 引用，供活人感心跳等系统任务使用
            self._task_executor.persona_manager = getattr(self, "persona_manager", None)
            self._task_executor.memory_manager = getattr(self, "memory_manager", None)
            self._task_executor.proactive_engine = getattr(self, "proactive_engine", None)
            logger.info("Scheduler gateway configured")

    async def shutdown(
        self, task_description: str = "", success: bool = True, errors: list = None
    ) -> None:
        """
        关闭 Agent 并保存记忆

        Args:
            task_description: 会话的主要任务描述
            success: 任务是否成功
            errors: 遇到的错误列表
        """
        logger.info("Shutting down agent...")

        # 结束记忆会话
        self.memory_manager.end_session(
            task_description=task_description,
            success=success,
            errors=errors or [],
        )

        # 等待记忆系统挂起的异步任务（episode 生成等）
        try:
            await self.memory_manager.await_pending_tasks(timeout=15.0)
        except Exception as e:
            logger.warning(f"Failed to await memory pending tasks: {e}")

        self._running = False
        logger.info("Agent shutdown complete")

    async def consolidate_memories(self) -> dict:
        """
        整理记忆 (批量处理未处理的会话)

        适合在空闲时段 (如凌晨) 由 cron job 调用

        Returns:
            整理结果统计
        """
        logger.info("Starting memory consolidation...")
        return await self.memory_manager.consolidate_daily()

    def get_memory_stats(self) -> dict:
        """获取记忆统计"""
        return self.memory_manager.get_stats()
