"""
OpenAkita configuration module
"""

import logging
import os
from pathlib import Path

os.environ.setdefault("OPENAKITA", "1")

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application configuration"""

    # Anthropic API
    anthropic_api_key: str = Field(default="", description="Anthropic API Key")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        description="Anthropic API Base URL (supports forwarding services like Yunwu AI)",
    )
    default_model: str = Field(
        default="claude-opus-4-5-20251101-thinking", description="Default model to use"
    )
    max_tokens: int = Field(
        default=0,
        description="Maximum output tokens (0=unlimited, use model default; fallback value is only used when the Anthropic API requires this parameter)",
    )

    # Agent configuration
    agent_name: str = Field(default="OpenAkita", description="Agent name")
    max_iterations: int = Field(
        default=30,
        ge=5,
        description="Max iterations for the Ralph loop (minimum 5, recommended 20~50)",
    )

    # Plan mode suggestion threshold (when ComplexitySignal.score reaches this value, suggest Plan mode to the user)
    plan_suggest_threshold: int = Field(
        default=5,
        ge=2,
        le=10,
        description="Suggest Plan mode when complexity score reaches this threshold (2~10, higher = less likely to trigger suggestion)",
    )

    # Self-check configuration
    selfcheck_autofix: bool = Field(
        default=True,
        description="Whether to auto-fix during self-check (set to false for analysis-only without repairs)",
    )

    # === Task timeout strategy ===
    # Goal: avoid hangs rather than limiting long tasks. Prefer "no-progress timeout".
    # - progress_timeout_seconds: if no progress (LLM return / tool completion / iteration advance) for this duration, consider it hung.
    # - hard_timeout_seconds: optional hard cap (default disabled=0). Only as a final safety net against infinite tasks.
    progress_timeout_seconds: int = Field(
        default=1200,
        description="No-progress timeout threshold (seconds). Trigger timeout handling when no progress for this long (default 1200)",
    )
    hard_timeout_seconds: int = Field(
        default=0,
        description="Hard timeout cap (seconds, 0=disabled). Only as a final safety net against infinite tasks",
    )

    # === ForceToolCall (tool guardrail) ===
    # When the model returns only text without calling tools on a "likely needs tool" task, Agent can nudge once to push a tool call.
    # Set to 0 to disable entirely (recommended for IM chitchat / customer service style conversations).
    force_tool_call_max_retries: int = Field(
        default=2,
        description="Max number of nudges asking the model to call a tool when it doesn't (0=disabled, trust the model's judgment)",
    )
    force_tool_call_im_floor: int = Field(
        default=2,
        description="Minimum ForceToolCall retry count for IM channels (0=same as global, no floor enforced)",
    )
    confirmation_text_max_retries: int = Field(
        default=2,
        description="Max follow-up retries when no visible text after tool execution (0=disabled)",
    )

    # === Parallel tool execution ===
    # When the model returns multiple tool_use/tool_calls in one turn, Agent may execute tools in parallel to boost throughput.
    # Default 1: preserve existing serial semantics (safest, especially for "chain-of-thought continuity" tool chains).
    tool_max_parallel: int = Field(
        default=1,
        description="Max concurrent tool calls per turn (default 1=serial; >1 enables parallel)",
    )

    allow_parallel_tools_with_interrupt_checks: bool = Field(
        default=False,
        description="Whether to allow parallel tool execution even when inter-tool interrupt checks are enabled (reduces interrupt insertion granularity, default disabled)",
    )

    # === Persistent tool loading ===
    always_load_tools: list = Field(
        default_factory=list,
        description="User-specified always-loaded tool names, never deferred (e.g. browser_navigate, edit_notebook)",
    )
    always_load_categories: list = Field(
        default_factory=list,
        description="User-specified always-loaded tool categories (e.g. Browser, MCP); all tools in this category will not be deferred",
    )

    # Thinking mode configuration
    thinking_mode: str = Field(
        default="auto",
        description="Thinking mode: auto (auto-detect), always (always on), never (never on)",
    )
    im_chain_push: bool = Field(
        default=False,
        description="Whether IM channels push chain-of-thought progress (thinking process, tool calls, etc.) to the user; disabling does not affect internal saving. Default disabled to reduce noise",
    )
    thinking_keywords: list = Field(
        default_factory=lambda: [
            "分析",
            "推理",
            "思考",
            "评估",
            "比较",
            "规划",
            "设计",
            "架构",
            "优化",
            "debug",
            "调试",
            "复杂",
            "困难",
            "analyze",
            "reason",
            "think",
            "evaluate",
            "compare",
            "plan",
            "design",
        ],
        description="Keywords that trigger thinking mode",
    )

    # Path configuration
    project_root: Path = Field(
        default_factory=lambda: Path.cwd(), description="Project root directory (defaults to current working directory)"
    )
    database_path: str = Field(default="data/agent.db", description="Database path")

    # === Logging configuration ===
    log_level: str = Field(default="INFO", description="Log level")
    log_dir: str = Field(default="logs", description="Log directory")
    log_file_prefix: str = Field(default="openakita", description="Log file prefix")
    log_max_size_mb: int = Field(default=10, description="Max size per log file (MB)")
    log_backup_count: int = Field(default=30, description="Number of log files to retain")
    log_retention_days: int = Field(default=30, description="Log retention days")
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="Log format"
    )
    log_to_console: bool = Field(default=True, description="Whether to output to console")
    log_to_file: bool = Field(default=True, description="Whether to output to file")

    # === Whisper speech recognition ===
    whisper_enabled: bool = Field(
        default=False,
        description="Whether to enable local Whisper speech recognition (large model, high memory usage; falls back to online STT when disabled)",
    )
    whisper_model: str = Field(
        default="base", description="Whisper model (tiny/base/small/medium/large)"
    )
    whisper_language: str = Field(
        default="zh",
        description=(
            "Whisper speech recognition language: "
            "zh (Chinese) | en (English, automatically uses smaller/faster .en model) | "
            "auto (auto-detect) | other language codes"
        ),
    )

    # === Global proxy configuration ===
    # Proxy for LLM API requests (when transparent proxy does not work)
    http_proxy: str = Field(default="", description="HTTP proxy URL (e.g. http://127.0.0.1:7890)")
    https_proxy: str = Field(default="", description="HTTPS proxy URL (e.g. http://127.0.0.1:7890)")
    all_proxy: str = Field(default="", description="Global proxy URL (takes priority over http/https proxy)")

    # === Force IPv4 mode ===
    # Some VPNs (e.g. LetsTAP) do not support IPv6; enable this option to force IPv4
    force_ipv4: bool = Field(
        default=False, description="Force IPv4 (works around IPv6 compatibility issues with certain VPNs)"
    )

    # === Model download source configuration ===
    # Local embedding models are downloaded from HuggingFace, which may be slow in some regions
    # Supports: auto (auto-select) | huggingface (official) | hf-mirror (China mirror) | modelscope
    model_download_source: str = Field(
        default="auto",
        description="Model download source: auto (pick fastest) | huggingface | hf-mirror | modelscope",
    )

    # === Embedding model configuration ===
    embedding_model: str = Field(
        default="shibing624/text2vec-base-chinese",
        description="Embedding model name (e.g. shibing624/text2vec-base-chinese)",
    )
    embedding_device: str = Field(
        default="cpu",
        description="Embedding model runtime device (cpu or cuda)",
    )

    # === Search backend configuration (v2) ===
    search_backend: str = Field(
        default="fts5",
        description="Memory search backend: fts5 (default, zero-deps) | chromadb (optional, local vector) | api_embedding (optional, online API)",
    )
    embedding_api_provider: str = Field(
        default="",
        description="Online embedding API provider: dashscope | openai (only required when search_backend=api_embedding)",
    )
    embedding_api_key: str = Field(
        default="",
        description="Online embedding API key (only required when search_backend=api_embedding)",
    )
    embedding_api_model: str = Field(
        default="text-embedding-v3",
        description="Online embedding model name (e.g. text-embedding-v3, text-embedding-3-small)",
    )

    # === Memory system configuration ===
    memory_history_days: int = Field(default=30, description="Memory retention days")
    memory_max_history_files: int = Field(default=1000, description="Max number of history files")
    memory_max_history_size_mb: int = Field(default=500, description="Max total size of history files (MB)")

    # === Web Search configuration ===
    search_provider: str = Field(
        default="auto",
        description=(
            "Web search provider: "
            "auto (auto-select, recommended) | ddgs (DuckDuckGo, no key required) | "
            "brave (Brave Search) | tavily (Tavily) | exa (Exa)"
        ),
    )
    search_fallback_enabled: bool = Field(
        default=True,
        description="Whether to auto-fall back to the next available provider on search failure (final fallback is DuckDuckGo)",
    )
    brave_api_key: str = Field(
        default="",
        description="Brave Search API Key (https://api.search.brave.com/)",
    )
    tavily_api_key: str = Field(
        default="",
        description="Tavily Search API Key (https://tavily.com/)",
    )
    exa_api_key: str = Field(
        default="",
        description="Exa Search API Key (https://exa.ai/)",
    )

    # GitHub
    github_token: str = Field(default="", description="GitHub Token")

    # DashScope API Key (used by image generation tool)
    dashscope_api_key: str = Field(default="", description="DashScope API Key")

    # DashScope image generation (Qwen-Image) - same key, different endpoint
    dashscope_image_api_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        description="DashScope Qwen-Image synchronous endpoint URL (defaults to Beijing region)",
    )

    # === MCP configuration ===
    mcp_enabled: bool = Field(default=True, description="Whether to enable MCP (Model Context Protocol)")
    mcp_timeout: int = Field(default=60, description="MCP tool call timeout (seconds), default 60")
    mcp_connect_timeout: int = Field(
        default=30, description="MCP server connection timeout (seconds), default 30"
    )
    mcp_auto_connect: bool = Field(default=False, description="Whether to auto-connect to all MCP servers on startup")

    # === Scheduler configuration ===
    scheduler_timezone: str = Field(default="Asia/Shanghai", description="Scheduler timezone")
    scheduler_task_timeout: int = Field(
        default=1200, description="Scheduled task execution timeout (seconds), default 1200 (20 minutes)"
    )

    # === Memory consolidation configuration ===
    memory_consolidation_onboarding_days: int = Field(
        default=7,
        description="New-user onboarding period in days, during which memory consolidation runs more frequently (default 7 days)",
    )
    memory_consolidation_onboarding_interval_hours: int = Field(
        default=3,
        description="Memory consolidation interval during onboarding (hours, default 3)",
    )

    # === Memory mode ===
    # mode1: Fragment memory — entity-attribute-based semantic memory fragments,
    #         good for simple preferences / facts, fast retrieval but lacks cross-session association.
    # mode2: Relational graph — multi-dimensional (time/causality/entity/action/context) interwoven graph memory,
    #         supports causal reasoning, timeline traceback, cross-session entity tracking, suited for complex long-term interactions.
    # auto:  Auto-select — intelligently routes to mode1 or mode2 based on query features (causality, timeline,
    #         cross-session, entity tracking), combining the strengths of both.
    memory_mode: str = Field(
        default="auto",
        description="Memory mode: mode1 (fragment) / mode2 (relational graph) / auto (auto-select, recommended)",
    )
    mdrm_max_hops: int = Field(
        default=3,
        description="Max hops for graph traversal",
    )
    mdrm_consolidation_enabled: bool = Field(
        default=True,
        description="Whether to enable relational memory consolidation",
    )
    mdrm_backfill_on_first_enable: bool = Field(
        default=True,
        description="Backfill mode1 historical data on first enabling mode2/auto",
    )

    # === Group chat response strategy ===
    group_response_mode: str = Field(
        default="mention_only",
        description="Group chat response mode: always (respond to all) / mention_only (respond only when @mentioned, default) / smart (AI decides)",
    )

    # === Channel configuration ===
    # Telegram
    telegram_enabled: bool = Field(default=False, description="Whether to enable Telegram")
    telegram_bot_token: str = Field(default="", description="Telegram Bot Token")
    telegram_webhook_url: str = Field(default="", description="Telegram Webhook URL")
    telegram_pairing_code: str = Field(default="", description="Telegram pairing code (auto-generated if empty)")
    telegram_require_pairing: bool = Field(default=True, description="Whether pairing verification is required")
    telegram_proxy: str = Field(
        default="",
        description="Telegram proxy URL (e.g. http://127.0.0.1:7890 or socks5://127.0.0.1:1080)",
    )

    # Feishu
    feishu_enabled: bool = Field(default=False, description="Whether to enable Feishu")
    feishu_app_id: str = Field(default="", description="Feishu App ID")
    feishu_app_secret: str = Field(default="", description="Feishu App Secret")

    # WeCom (smart bot — HTTP callback mode)
    wework_enabled: bool = Field(default=False, description="Whether to enable WeCom (HTTP callback mode)")
    wework_corp_id: str = Field(default="", description="WeCom Corp ID")
    wework_token: str = Field(default="", description="WeCom callback Token")
    wework_encoding_aes_key: str = Field(default="", description="WeCom callback encryption AES Key")
    wework_callback_port: int = Field(default=9880, description="WeCom callback service port")
    wework_callback_host: str = Field(default="0.0.0.0", description="WeCom callback service bind address")

    # WeCom (smart bot — WebSocket persistent connection mode)
    wework_ws_enabled: bool = Field(default=False, description="Whether to enable WeCom WebSocket persistent connection")
    wework_ws_bot_id: str = Field(default="", description="WeCom bot ID (obtained from console)")
    wework_ws_secret: str = Field(default="", description="WeCom bot Secret (obtained from console)")
    wework_ws_thinking_indicator: bool = Field(
        default=True, description="Send 'thinking' streaming first frame immediately upon receiving a message"
    )
    wework_ws_msg_item_images: bool = Field(
        default=False,
        description="Use msg_item to send images in streaming replies (current WeCom versions may not render; default disabled)",
    )
    wework_ws_webhook_url: str = Field(
        default="",
        description="WeCom group bot webhook URL (used in WS mode to send images/voice/files)",
    )

    # DingTalk
    dingtalk_enabled: bool = Field(default=False, description="Whether to enable DingTalk")
    dingtalk_client_id: str = Field(default="", description="DingTalk Client ID (formerly App Key)")
    dingtalk_client_secret: str = Field(
        default="", description="DingTalk Client Secret (formerly App Secret)"
    )

    # OneBot protocol (generic)
    onebot_enabled: bool = Field(default=False, description="Whether to enable OneBot")
    onebot_mode: str = Field(
        default="reverse",
        description="OneBot connection mode: reverse (reverse WS, recommended) or forward (forward WS)",
    )
    onebot_ws_url: str = Field(
        default="ws://127.0.0.1:8080", description="OneBot forward WS URL (forward mode only)"
    )
    onebot_reverse_host: str = Field(default="0.0.0.0", description="OneBot reverse WS listen host")
    onebot_reverse_port: int = Field(default=6700, description="OneBot reverse WS listen port")
    onebot_access_token: str = Field(default="", description="OneBot access token (optional)")

    # QQ official bot
    qqbot_enabled: bool = Field(default=False, description="Whether to enable QQ official bot")
    qqbot_app_id: str = Field(default="", description="QQ bot AppID")
    qqbot_app_secret: str = Field(default="", description="QQ bot AppSecret")
    qqbot_sandbox: bool = Field(default=False, description="Whether to use the sandbox environment")
    qqbot_mode: str = Field(
        default="websocket",
        description="QQ bot connection mode: websocket (default, no public network needed) or webhook (requires public IP/domain)",
    )
    qqbot_webhook_port: int = Field(default=9890, description="QQ Webhook callback service port")
    qqbot_webhook_path: str = Field(default="/qqbot/callback", description="QQ Webhook callback path")

    # WeChat personal account (iLink Bot API)
    wechat_enabled: bool = Field(default=False, description="Whether to enable WeChat personal account")
    wechat_token: str = Field(default="", description="WeChat iLink Bot Token (obtained via QR code login)")

    # === Session configuration ===
    session_timeout_minutes: int = Field(default=30, description="Session timeout (minutes)")
    session_max_history: int = Field(default=50, description="Max session history messages")
    session_storage_path: str = Field(default="data/sessions", description="Session storage path")

    # === Multi-agent mode (Beta) ===
    multi_agent_enabled: bool = Field(
        default=True,
        description="Multi-agent mode (Beta); when enabled, supports multi-agent collaboration, specialized agents, IM multi-bot, etc.",
    )
    coordinator_mode_enabled: bool = Field(
        default=False,
        description="Coordinator mode (CC-3): when enabled, role=coordinator agents can only delegate/plan and cannot directly execute file/command operations",
    )

    # IM multi-bot configuration (supports multiple bot instances per channel type in multi-agent mode)
    im_bots: list[dict] = Field(default_factory=list)

    # === Persona system configuration ===
    persona_name: str = Field(
        default="default",
        description="Currently active persona preset name (default/business/tech_expert/butler/girlfriend/boyfriend/family/jarvis)",
    )

    # === Memory Nudge configuration ===
    memory_nudge_enabled: bool = Field(
        default=True,
        description="Whether to enable periodic memory review (after every N turns, use LLM to review the conversation and extract memory-worthy content)",
    )
    memory_nudge_interval: int = Field(
        default=10,
        description="Trigger memory review every N turns (0 = disabled)",
    )

    # === Smart Approval configuration ===
    smart_approval_enabled: bool = Field(
        default=False,
        description="Whether to enable LLM-assisted risk assessment (use LLM to pre-judge CONFIRM-level operations)",
    )

    # === Docker execution backend configuration ===
    docker_backend_enabled: bool = Field(
        default=False,
        description="Whether to enable Docker container execution backend (requires Docker installed locally)",
    )
    docker_image: str = Field(
        default="python:3.12-slim",
        description="Docker image used by the execution backend",
    )
    docker_network: str = Field(
        default="none",
        description="Docker network mode: none (offline) | bridge (default bridge) | host",
    )

    # === Proactive engine configuration ===
    proactive_enabled: bool = Field(default=True, description="Whether to enable proactive (liveness) mode")
    proactive_max_daily_messages: int = Field(default=3, description="Max proactive messages per day")
    proactive_min_interval_minutes: int = Field(
        default=120, description="Minimum interval between two proactive messages (minutes)"
    )
    proactive_quiet_hours_start: int = Field(default=23, description="Quiet hours start (hour, 0-23)")
    proactive_quiet_hours_end: int = Field(default=7, description="Quiet hours end (hour, 0-23)")
    proactive_idle_threshold_hours: int = Field(
        default=3, description="Idle time (hours) before triggering a small-talk greeting; AI adjusts dynamically based on feedback"
    )

    # === UI preferences ===
    ui_theme: str = Field(
        default="system",
        description="Desktop client theme: system (follow OS) | light | dark",
    )
    ui_language: str = Field(
        default="zh",
        description="Desktop client language: zh (Chinese) | en (English)",
    )

    # === Desktop notification configuration ===
    desktop_notify_enabled: bool = Field(
        default=True,
        description="Whether to show a system desktop notification on task completion (Windows Toast / macOS / Linux notify-send)",
    )
    desktop_notify_sound: bool = Field(
        default=True,
        description="Whether the desktop notification plays a system notification sound",
    )

    # === Sticker configuration ===
    sticker_enabled: bool = Field(default=True, description="Whether to enable the sticker feature")
    sticker_data_dir: str = Field(default="data/sticker", description="Sticker data directory")
    sticker_mirrors: list[str] = Field(
        default_factory=list,
        description=(
            "Custom sticker mirror URL list, tried before built-in mirrors. "
            "Two formats supported: 1) CDN mirror base (relative path appended), "
            "2) GitHub proxy prefix (full original URL appended). "
            "Example: ['https://ghp.ci/https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/']"
        ),
    )

    # === Bug Report / Feedback configuration ===
    # The following three values are public identifiers (similar to reCAPTCHA site keys), not secrets.
    # The official release needs to ship defaults for out-of-the-box usage;
    # fork users can override via .env with their own values, or leave blank to disable the corresponding feature.
    bug_report_endpoint: str = Field(
        default="https://feedback-openakita.fzstack.com",
        description="Feedback upload endpoint URL (Alibaba Cloud FC). Blank = disable feedback feature.",
    )
    captcha_scene_id: str = Field(
        default="jkyrkj0w",
        description="Alibaba Cloud Captcha 2.0 scene ID (public identifier, delivered to frontend). Blank = skip captcha.",
    )
    captcha_prefix: str = Field(
        default="yiqg72",
        description="Alibaba Cloud Captcha 2.0 prefix identity tag (public identifier, delivered to frontend).",
    )

    # === OpenAkita Platform (Agent Hub / Skill Store) ===
    hub_enabled: bool = Field(
        default=False,
        description="Enable OpenAkita Platform connection (Agent Hub / Skill Store). When disabled, remote marketplace tools are not registered.",
    )
    hub_api_url: str = Field(
        default="https://openakita.ai/api",
        description="OpenAkita Platform API base URL for Agent Hub and Skill Store",
    )
    hub_api_key: str = Field(
        default="",
        description="OpenAkita Platform API Key (ak_live_...)",
    )
    hub_device_id: str = Field(
        default="",
        description="Local device identifier (auto-generated UUID)",
    )

    # === Context management configuration ===
    context_max_window: int = Field(
        default=0,
        description="Global max context input length (tokens). Effective value is min(this value, endpoint context_window). 0 = no limit, use endpoint max directly",
    )
    context_compression_ratio: float = Field(
        default=0.25,
        description="Context compression target ratio; older conversations are compressed to this percentage (0.05~0.5)",
    )
    context_compression_threshold: float = Field(
        default=0.85,
        description="Soft limit ratio that triggers compression — compression begins when context tokens exceed this fraction of the hard cap (0.5~0.95; higher = triggers later)",
    )
    context_boundary_compression_ratio: float = Field(
        default=0.25,
        description="Cross-topic boundary compression ratio; old topics are compressed to this percentage (0.05~0.5)",
    )
    context_min_recent_turns: int = Field(
        default=12,
        description="Minimum number of recent conversation turns to retain during compression (4~20)",
    )
    context_enable_tool_compression: bool = Field(
        default=True,
        description="Whether to enable standalone compression of oversized tool results",
    )
    context_large_tool_threshold: int = Field(
        default=5000,
        description="Token threshold that triggers standalone compression of a single tool result",
    )

    # === Harness configuration ===
    supervisor_enabled: bool = Field(
        default=True, description="Whether to enable the runtime supervisor (RuntimeSupervisor)"
    )
    task_budget_tokens: int = Field(default=0, description="Max token consumption per task (0=unlimited)")
    task_budget_cost: float = Field(default=0.0, description="Max cost per task in USD (0=unlimited)")
    task_budget_duration: int = Field(
        default=600, description="Max task duration in seconds (0=unlimited, default 600=10 minutes)"
    )
    task_budget_iterations: int = Field(
        default=50, description="Max iterations per task (0=unlimited, default 50)"
    )
    task_budget_tool_calls: int = Field(
        default=30, description="Max tool calls per task (0=unlimited, default 30)"
    )

    # === Tracing configuration ===
    tracing_enabled: bool = Field(
        default=True, description="Whether to enable agent tracing (enabled by default in lightweight mode)"
    )
    tracing_export_dir: str = Field(default="data/traces", description="Tracing export directory")
    tracing_console_export: bool = Field(default=False, description="Whether to also export to console")

    # === Evaluation configuration ===
    evaluation_enabled: bool = Field(default=False, description="Whether to enable daily automated evaluation")
    evaluation_output_dir: str = Field(default="data/evaluation", description="Evaluation report output directory")

    # === Organization orchestration · Task chain termination safeguards ===
    # This group of switches prevents:
    # 1) The same chain being delivered/accepted repeatedly, causing duplicate attachments and deliverables;
    # 2) After a task is accepted, nodes still being woken up by subsequent messages and autonomously starting new ReAct loops;
    # 3) Auto-sending a "completed" notification to the parent after task completion, triggering new parent-level reasoning.
    # All enabled by default; to revert to the old behavior, set the corresponding item to false.
    org_reject_resubmit_after_accept: bool = Field(
        default=True,
        description="Forbid calling submit_deliverable again after a chain is accepted/delivered",
    )
    org_suppress_closed_chain_reactivation: bool = Field(
        default=True,
        description="Suppress ReAct reactivation triggered by messages on a closed chain (accepted/rejected/cancelled)",
    )
    org_post_task_notify_parent: bool = Field(
        default=False,
        description="Whether to auto-send a [Notification] to the parent node on task completion: False means do not proactively wake the parent",
    )

    # === Organization orchestration · User command lifecycle watchdog ===
    # After the user dispatches a top-level command via send_command, completion is event-driven
    # (all delegation chains closed + root IDLE + root inbox empty). The time parameters below
    # are used only by the watchdog: to prevent indefinite command hangs when the organization
    # truly deadlocks (LLM hang, deadlock). Any progress signal (token / tool completion / node
    # state transition / chain event) resets the warn/autostop timers, so long-running but
    # steadily-producing tasks won't be stopped by mistake.
    org_command_stuck_warn_secs: int = Field(
        default=300,
        description="Emit a stuck_warning to the frontend after N seconds of no progress (does not terminate the command, default 300=5 minutes)",
    )
    org_command_stuck_autostop_secs: int = Field(
        default=1800,
        description="Fall back to soft_stop of the organization after N seconds of no progress (default 1800=30 minutes)",
    )
    org_command_timeout_secs: int = Field(
        default=10800,
        description="Hard cap on the max runtime of a single command (seconds); 0 or negative means no limit (default 10800=3 hours)",
    )

    @model_validator(mode="after")
    def _enforce_min_max_iterations(self) -> "Settings":
        MIN_ITERATIONS = 15
        if self.max_iterations < MIN_ITERATIONS:
            logger.warning(
                "[Config] max_iterations=%d is too low (minimum %d). "
                "Resetting to %d. Please update your .env file.",
                self.max_iterations,
                MIN_ITERATIONS,
                MIN_ITERATIONS,
            )
            self.max_iterations = MIN_ITERATIONS
        return self

    @model_validator(mode="before")
    @classmethod
    def _strip_inline_comments(cls, values: dict) -> dict:  # type: ignore[override]
        """Strip inline comments from env values before type coercion.

        .env files may contain lines like ``MAX_TOKENS=4096  # recommended value``.
        If an external caller (e.g. Tauri bridge) passes the raw value including
        the comment as an OS env-var, Pydantic would fail to parse ``"4096 # ..."``
        as ``int``.  This validator runs *before* field-level coercion and removes
        everything after an unquoted `` #`` / ``\\t#`` pattern.
        """
        if not isinstance(values, dict):
            return values
        cleaned: dict = {}
        for k, v in values.items():
            if isinstance(v, str) and not (len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'")):
                for sep in (" #", "\t#"):
                    idx = v.find(sep)
                    if idx != -1:
                        v = v[:idx].rstrip()
                        break
            cleaned[k] = v
        return cleaned

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        # Key: ignore empty-string environment variables (e.g. PROGRESS_TIMEOUT_SECONDS= in .env)
        # Otherwise pydantic tries to parse "" as int/bool and startup fails.
        "env_ignore_empty": True,
    }

    def reload(self) -> list[str]:
        """Reload configuration from the .env file, returning the list of changed field names.

        Creates a new Settings instance (which re-reads .env),
        then copies all field values back into the current singleton.

        Runtime-persisted fields (``_PERSISTABLE_KEYS``) are managed by RuntimeState
        and are not overridden from .env, to avoid resetting im_bots and similar.
        """
        _skip = set(_PERSISTABLE_KEYS)
        fresh = Settings()
        changed: list[str] = []
        for field_name in self.model_fields:
            if field_name in _skip:
                continue
            old_val = getattr(self, field_name)
            new_val = getattr(fresh, field_name)
            if old_val != new_val:
                setattr(self, field_name, new_val)
                changed.append(field_name)
        if changed:
            logger.info(f"[Settings] Reloaded from .env, changed: {changed}")
        else:
            logger.info("[Settings] Reloaded from .env, no changes detected")
        return changed

    @property
    def identity_path(self) -> Path:
        """Identity configuration directory path"""
        return self.project_root / "identity"

    @property
    def soul_path(self) -> Path:
        """SOUL.md path"""
        return self.identity_path / "SOUL.md"

    @property
    def agent_path(self) -> Path:
        """AGENT.md path"""
        return self.identity_path / "AGENT.md"

    @property
    def user_path(self) -> Path:
        """USER.md path"""
        return self.identity_path / "USER.md"

    @property
    def memory_path(self) -> Path:
        """MEMORY.md path"""
        return self.identity_path / "MEMORY.md"

    @property
    def personas_path(self) -> Path:
        """Persona preset directory path"""
        return self.identity_path / "personas"

    @property
    def sticker_data_path(self) -> Path:
        """Sticker data directory path"""
        return self.project_root / self.sticker_data_dir

    @property
    def openakita_home(self) -> Path:
        """User data root directory; prefers the OPENAKITA_ROOT env var, defaults to ~/.openakita"""
        import os

        env_root = os.environ.get("OPENAKITA_ROOT", "").strip()
        if env_root:
            return Path(env_root)
        return Path.home() / ".openakita"

    @property
    def user_workspace_path(self) -> Path:
        """Current user workspace path.

        If project_root is located under openakita_home/workspaces/ (production mode),
        use project_root directly as the workspace path; otherwise (development mode) fall back to default.
        """
        ws_dir = self.openakita_home / "workspaces"
        try:
            self.project_root.resolve().relative_to(ws_dir.resolve())
            return self.project_root.resolve()
        except ValueError:
            return ws_dir / "default"

    @property
    def skills_path(self) -> Path:
        """User skill install directory (~/.openakita/workspaces/default/skills)

        All skills installed or created via install_skill / skill-creator live here.
        This directory lives under the user home and is writable even in packaged builds.
        In development mode the project-level skills/ is still scanned (via SKILL_DIRECTORIES),
        but the install target is unified to this path.
        """
        return self.user_workspace_path / "skills"

    @property
    def specs_path(self) -> Path:
        """Spec document directory path"""
        return self.project_root / "specs"

    @property
    def data_dir(self) -> Path:
        """Data storage directory (project_root/data)"""
        return self.project_root / "data"

    @property
    def db_full_path(self) -> Path:
        """Full database path"""
        return self.project_root / self.database_path

    @property
    def log_dir_path(self) -> Path:
        """Full log directory path"""
        return self.project_root / self.log_dir

    @property
    def log_file_path(self) -> Path:
        """Main log file path"""
        return self.log_dir_path / f"{self.log_file_prefix}.log"

    @property
    def error_log_path(self) -> Path:
        """Error log file path (records ERROR/CRITICAL only)"""
        return self.log_dir_path / "error.log"

    @property
    def selfcheck_dir(self) -> Path:
        """Self-check report directory"""
        return self.project_root / "data" / "selfcheck"

    @property
    def mcp_config_path(self) -> Path:
        """User MCP configuration directory (writable, safe in packaged mode)

        Path: {project_root}/data/mcp/servers/
        MCP server configurations added by the AI via tools are saved in this directory.
        At startup, both the built-in mcps/ and this directory are scanned.
        """
        return self.project_root / "data" / "mcp" / "servers"

    @property
    def mcp_builtin_path(self) -> Path:
        """Built-in MCP configuration directory (shipped with the project, may be read-only when packaged)

        Prefer project_root/mcps (development mode);
        if missing, fall back to the wheel-packaged location site-packages/openakita/builtin_mcps/.
        """
        dev_path = self.project_root / "mcps"
        if dev_path.exists():
            return dev_path
        pkg_path = Path(__file__).resolve().parent / "builtin_mcps"
        if pkg_path.exists():
            return pkg_path
        return dev_path


# ---------------------------------------------------------------------------
# Runtime state persistence
# ---------------------------------------------------------------------------
# Stores settings dynamically modified by the user through conversation
# (persona, proactive toggle, etc.) so they remain in effect after Agent restart.
# Storage location: data/runtime_state.json
# ---------------------------------------------------------------------------

# Settings field names that need to be persisted
_PERSISTABLE_KEYS: list[str] = [
    "persona_name",
    "memory_nudge_enabled",
    "memory_nudge_interval",
    "proactive_enabled",
    "proactive_max_daily_messages",
    "proactive_min_interval_minutes",
    "proactive_quiet_hours_start",
    "proactive_quiet_hours_end",
    "ui_theme",
    "ui_language",
    "im_bots",
    "force_tool_call_max_retries",
    "force_tool_call_im_floor",
    "confirmation_text_max_retries",
    "always_load_tools",
    "always_load_categories",
]


class RuntimeState:
    """
    Lightweight runtime state persistence.

    After modifying persistable fields on the settings singleton, call save() to write to disk;
    call load() at Agent startup to restore from disk.
    """

    def __init__(self, state_file: Path | None = None):
        # Lazy resolution (cannot access project_root before settings is created)
        self._state_file = state_file

    @property
    def state_file(self) -> Path:
        if self._state_file is None:
            self._state_file = settings.project_root / "data" / "runtime_state.json"
        return self._state_file

    def save(self) -> None:
        """Write the persistable fields from settings to a JSON file (atomic write + backup)."""
        from .utils.atomic_io import safe_json_write

        data: dict = {}
        for key in _PERSISTABLE_KEYS:
            data[key] = getattr(settings, key)
        try:
            safe_json_write(self.state_file, data)
            logger.info(f"[RuntimeState] Saved: {data}")
        except Exception as e:
            logger.error(f"[RuntimeState] Failed to save: {e}")

    def load(self) -> None:
        """Restore settings from a JSON file into the settings singleton, overriding only persistable fields (with .bak fallback support)."""
        from .utils.atomic_io import read_json_safe

        data = read_json_safe(self.state_file)
        if data is None:
            logger.info("[RuntimeState] No saved state found, using defaults.")
            return
        try:
            applied = []
            for key in _PERSISTABLE_KEYS:
                if key in data:
                    old_val = getattr(settings, key)
                    new_val = data[key]
                    if old_val != new_val:
                        setattr(settings, key, new_val)
                        applied.append(f"{key}: {old_val} -> {new_val}")
            if applied:
                logger.info(f"[RuntimeState] Restored: {'; '.join(applied)}")
            else:
                logger.info("[RuntimeState] State loaded, no changes needed.")
        except Exception as e:
            logger.error(f"[RuntimeState] Failed to load: {e}")


# Global configuration instance
settings = Settings()

# Global runtime state manager
runtime_state = RuntimeState()

# ---------------------------------------------------------------------------
# Restart signal flag
# ---------------------------------------------------------------------------
# Set by the /api/config/restart endpoint; main.py serve() loop checks this flag to decide whether to restart.
_restart_requested: bool = False
