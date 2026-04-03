"""
OpenAkita 配置模块
"""

import logging
import os
from pathlib import Path

os.environ.setdefault("OPENAKITA", "1")

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """应用配置"""

    # Anthropic API
    anthropic_api_key: str = Field(default="", description="Anthropic API Key")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        description="Anthropic API Base URL (支持云雾AI等转发服务)",
    )
    default_model: str = Field(
        default="claude-opus-4-5-20251101-thinking", description="默认使用的模型"
    )
    max_tokens: int = Field(
        default=0,
        description="最大输出 token 数 (0=不限制，使用模型默认上限；仅 Anthropic API 强制要求此参数时才会自动使用兜底值)",
    )

    # Agent 配置
    agent_name: str = Field(default="OpenAkita", description="Agent 名称")
    max_iterations: int = Field(
        default=30,
        ge=5,
        description="Ralph 循环最大迭代次数（最小值 5，推荐 20~50）",
    )

    # 自检配置
    selfcheck_autofix: bool = Field(
        default=True,
        description="自检时是否执行自动修复（设为 false 则只分析不修复）",
    )

    # === 任务超时策略 ===
    # 目标：避免“卡死”而不是限制长任务。推荐使用“无进展超时”。
    # - progress_timeout_seconds: 若连续超过该时间没有任何进展（LLM返回/工具完成/迭代推进），视为卡死。
    # - hard_timeout_seconds: 可选硬上限（默认关闭=0）。仅作为最终兜底，避免无限任务。
    progress_timeout_seconds: int = Field(
        default=1200,
        description="无进展超时阈值（秒）。超过该时间无进展则触发超时处理（默认 1200）",
    )
    hard_timeout_seconds: int = Field(
        default=0,
        description="硬超时上限（秒，0=禁用）。仅作为最终兜底，避免无限任务",
    )

    # === ForceToolCall（工具护栏）===
    # 当模型在“可能需要工具”的任务中只给文本不调用工具时，Agent 可追问 1 次以推动工具调用。
    # 设为 0 可完全关闭该行为（推荐 IM 闲聊/客服式对话场景）。
    force_tool_call_max_retries: int = Field(
        default=1,
        description="当模型未调用工具时，最多追问要求调用工具的次数（0=禁用，信任模型自主判断）",
    )
    force_tool_call_im_floor: int = Field(
        default=1,
        description="IM 通道的 ForceToolCall 最低重试次数（0=与全局一致，不强制下限）",
    )
    confirmation_text_max_retries: int = Field(
        default=1,
        description="工具执行后无可见文本时的最大追问次数（0=禁用）",
    )

    # === 工具并行执行 ===
    # 单轮模型返回多个 tool_use/tool_calls 时，Agent 可选择并行执行工具以提升吞吐。
    # 默认 1：保持现有串行语义（最安全，尤其是带“思维链连续性”的工具链）。
    tool_max_parallel: int = Field(
        default=1,
        description="单轮并行工具调用最大并发数（默认 1=串行；>1 启用并行）",
    )

    allow_parallel_tools_with_interrupt_checks: bool = Field(
        default=False,
        description="是否允许在启用“工具间中断检查”时也并行执行工具（会降低中断插入粒度，默认关闭）",
    )

    # Thinking 模式配置
    thinking_mode: str = Field(
        default="auto",
        description="Thinking 模式: auto(自动判断), always(始终启用), never(从不启用)",
    )
    im_chain_push: bool = Field(
        default=False,
        description="IM 通道是否推送思维链进度（💭思考过程、工具调用等）给用户，关闭不影响内部保存。默认关闭以减少刷屏",
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
        description="触发 thinking 模式的关键词",
    )

    # 路径配置
    project_root: Path = Field(
        default_factory=lambda: Path.cwd(), description="项目根目录 (默认为当前工作目录)"
    )
    database_path: str = Field(default="data/agent.db", description="数据库路径")

    # === 日志配置 ===
    log_level: str = Field(default="INFO", description="日志级别")
    log_dir: str = Field(default="logs", description="日志目录")
    log_file_prefix: str = Field(default="openakita", description="日志文件前缀")
    log_max_size_mb: int = Field(default=10, description="单个日志文件最大大小（MB）")
    log_backup_count: int = Field(default=30, description="保留的日志文件数量")
    log_retention_days: int = Field(default=30, description="日志保留天数")
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="日志格式"
    )
    log_to_console: bool = Field(default=True, description="是否输出到控制台")
    log_to_file: bool = Field(default=True, description="是否输出到文件")

    # === Whisper 语音识别 ===
    whisper_enabled: bool = Field(
        default=False,
        description="是否启用本地 Whisper 语音识别（模型较大，占用内存高；关闭后使用在线 STT）",
    )
    whisper_model: str = Field(
        default="base", description="Whisper 模型 (tiny/base/small/medium/large)"
    )
    whisper_language: str = Field(
        default="zh",
        description=(
            "Whisper 语音识别语言: "
            "zh(中文) | en(英文，自动使用更小更快的 .en 模型) | "
            "auto(自动检测语言) | 其他语言代码"
        ),
    )

    # === 全局代理配置 ===
    # 用于 LLM API 请求的代理（如果透明代理不生效）
    http_proxy: str = Field(default="", description="HTTP 代理地址 (如 http://127.0.0.1:7890)")
    https_proxy: str = Field(default="", description="HTTPS 代理地址 (如 http://127.0.0.1:7890)")
    all_proxy: str = Field(default="", description="全局代理地址（优先级高于 http/https proxy）")

    # === IPv4 强制模式 ===
    # 某些 VPN（如 LetsTAP）不支持 IPv6，启用此选项强制使用 IPv4
    force_ipv4: bool = Field(
        default=False, description="强制使用 IPv4（解决某些 VPN 的 IPv6 兼容性问题）"
    )

    # === 模型下载源配置 ===
    # 本地 embedding 模型从 HuggingFace 下载，国内可能很慢
    # 支持: auto(自动选择) | huggingface(官方) | hf-mirror(国内镜像) | modelscope(魔搭社区)
    model_download_source: str = Field(
        default="auto",
        description="模型下载源: auto(自动选择最快源) | huggingface | hf-mirror | modelscope",
    )

    # === Embedding 模型配置 ===
    embedding_model: str = Field(
        default="shibing624/text2vec-base-chinese",
        description="Embedding 模型名称 (如 shibing624/text2vec-base-chinese)",
    )
    embedding_device: str = Field(
        default="cpu",
        description="Embedding 模型运行设备 (cpu 或 cuda)",
    )

    # === 搜索后端配置 (v2) ===
    search_backend: str = Field(
        default="fts5",
        description="记忆搜索后端: fts5(默认,零依赖) | chromadb(可选,本地向量) | api_embedding(可选,在线API)",
    )
    embedding_api_provider: str = Field(
        default="",
        description="在线 Embedding API 提供商: dashscope | openai (仅 search_backend=api_embedding 时需要)",
    )
    embedding_api_key: str = Field(
        default="",
        description="在线 Embedding API Key (仅 search_backend=api_embedding 时需要)",
    )
    embedding_api_model: str = Field(
        default="text-embedding-v3",
        description="在线 Embedding 模型名称 (如 text-embedding-v3, text-embedding-3-small)",
    )

    # === 记忆系统配置 ===
    memory_history_days: int = Field(default=30, description="记忆保留天数")
    memory_max_history_files: int = Field(default=1000, description="最大历史文件数")
    memory_max_history_size_mb: int = Field(default=500, description="历史文件最大总大小(MB)")

    # GitHub
    github_token: str = Field(default="", description="GitHub Token")

    # DashScope API Key (used by image generation tool)
    dashscope_api_key: str = Field(default="", description="DashScope API Key")

    # DashScope 图像生成 (Qwen-Image) - 同一 Key，不同接口
    dashscope_image_api_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        description="DashScope Qwen-Image 同步接口 URL（默认北京地域）",
    )

    # === MCP 配置 ===
    mcp_enabled: bool = Field(default=True, description="是否启用 MCP (Model Context Protocol)")
    mcp_timeout: int = Field(
        default=60, description="MCP 工具调用超时时间（秒），默认 60 秒"
    )
    mcp_connect_timeout: int = Field(
        default=30, description="MCP 服务器连接超时时间（秒），默认 30 秒"
    )
    mcp_auto_connect: bool = Field(
        default=False, description="启动时是否自动连接所有 MCP 服务器"
    )

    # === 调度器配置 ===
    scheduler_timezone: str = Field(default="Asia/Shanghai", description="调度器时区")
    scheduler_task_timeout: int = Field(
        default=1200, description="定时任务执行超时时间（秒），默认 1200 秒（20分钟）"
    )

    # === 记忆整理配置 ===
    memory_consolidation_onboarding_days: int = Field(
        default=7,
        description="新用户适应期天数，期间记忆整理频率提高（默认 7 天）",
    )
    memory_consolidation_onboarding_interval_hours: int = Field(
        default=3,
        description="适应期内记忆整理间隔（小时，默认 3 小时）",
    )

    # === 记忆模式 ===
    # mode1: 碎片化记忆 — 基于实体-属性的语义记忆片段，适合简单偏好/事实存储，
    #         检索快但缺乏跨会话关联能力。
    # mode2: 关系型图谱 — 多维度(时间/因果/实体/动作/上下文)交织的图结构记忆，
    #         支持因果推理、时间线回溯、跨会话实体追踪，适合复杂长期交互。
    # auto:  自动选择 — 根据查询特征(是否涉及因果、时间线、跨会话、实体追踪)
    #         智能路由到 mode1 或 mode2，兼顾两者优势。
    memory_mode: str = Field(
        default="auto",
        description="记忆模式: mode1(碎片化) / mode2(关系型图谱) / auto(自动选择，推荐)",
    )
    mdrm_max_hops: int = Field(
        default=3,
        description="图遍历最大跳数",
    )
    mdrm_consolidation_enabled: bool = Field(
        default=True,
        description="是否启用关系型记忆整合",
    )
    mdrm_backfill_on_first_enable: bool = Field(
        default=True,
        description="首次启用 mode2/auto 时回填模式 1 历史数据",
    )

    # === 群聊响应策略 ===
    group_response_mode: str = Field(
        default="mention_only",
        description="群聊响应模式: always(全响应) / mention_only(仅@时响应，默认) / smart(AI判断)",
    )

    # === 通道配置 ===
    # Telegram
    telegram_enabled: bool = Field(default=False, description="是否启用 Telegram")
    telegram_bot_token: str = Field(default="", description="Telegram Bot Token")
    telegram_webhook_url: str = Field(default="", description="Telegram Webhook URL")
    telegram_pairing_code: str = Field(default="", description="Telegram 配对码（留空则自动生成）")
    telegram_require_pairing: bool = Field(default=True, description="是否需要配对验证")
    telegram_proxy: str = Field(
        default="",
        description="Telegram 代理地址 (如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080)",
    )

    # 飞书
    feishu_enabled: bool = Field(default=False, description="是否启用飞书")
    feishu_app_id: str = Field(default="", description="飞书 App ID")
    feishu_app_secret: str = Field(default="", description="飞书 App Secret")

    # 企业微信（智能机器人 — HTTP 回调模式）
    wework_enabled: bool = Field(default=False, description="是否启用企业微信（HTTP 回调模式）")
    wework_corp_id: str = Field(default="", description="企业微信 Corp ID")
    wework_token: str = Field(default="", description="企业微信回调 Token")
    wework_encoding_aes_key: str = Field(default="", description="企业微信回调加密 AES Key")
    wework_callback_port: int = Field(default=9880, description="企业微信回调服务端口")
    wework_callback_host: str = Field(default="0.0.0.0", description="企业微信回调服务绑定地址")

    # 企业微信（智能机器人 — WebSocket 长连接模式）
    wework_ws_enabled: bool = Field(default=False, description="是否启用企业微信 WebSocket 长连接")
    wework_ws_bot_id: str = Field(default="", description="企业微信机器人 ID（后台获取）")
    wework_ws_secret: str = Field(default="", description="企业微信机器人 Secret（后台获取）")
    wework_ws_thinking_indicator: bool = Field(default=True, description="收到消息后立即发送'思考中'流式首帧提示")
    wework_ws_msg_item_images: bool = Field(
        default=False,
        description="流式回复中使用 msg_item 发送图片（当前企业微信版本可能不渲染，默认关闭）",
    )
    wework_ws_webhook_url: str = Field(
        default="",
        description="企业微信群机器人 Webhook URL（用于 WS 模式下发送图片/语音/文件）",
    )

    # 钉钉
    dingtalk_enabled: bool = Field(default=False, description="是否启用钉钉")
    dingtalk_client_id: str = Field(default="", description="钉钉 Client ID（原 App Key）")
    dingtalk_client_secret: str = Field(default="", description="钉钉 Client Secret（原 App Secret）")

    # OneBot 协议（通用）
    onebot_enabled: bool = Field(default=False, description="是否启用 OneBot")
    onebot_mode: str = Field(
        default="reverse",
        description="OneBot 连接模式: reverse（反向WS，推荐）或 forward（正向WS）",
    )
    onebot_ws_url: str = Field(default="ws://127.0.0.1:8080", description="OneBot 正向 WS 地址（仅 forward 模式）")
    onebot_reverse_host: str = Field(default="0.0.0.0", description="OneBot 反向 WS 监听地址")
    onebot_reverse_port: int = Field(default=6700, description="OneBot 反向 WS 监听端口")
    onebot_access_token: str = Field(default="", description="OneBot 访问令牌（可选）")

    # QQ 官方机器人
    qqbot_enabled: bool = Field(default=False, description="是否启用 QQ 官方机器人")
    qqbot_app_id: str = Field(default="", description="QQ 机器人 AppID")
    qqbot_app_secret: str = Field(default="", description="QQ 机器人 AppSecret")
    qqbot_sandbox: bool = Field(default=False, description="是否使用沙箱环境")
    qqbot_mode: str = Field(
        default="websocket",
        description="QQ 机器人接入模式: websocket (默认，无需公网) 或 webhook (需要公网IP/域名)",
    )
    qqbot_webhook_port: int = Field(default=9890, description="QQ Webhook 回调服务端口")
    qqbot_webhook_path: str = Field(default="/qqbot/callback", description="QQ Webhook 回调路径")

    # 微信个人号 (iLink Bot API)
    wechat_enabled: bool = Field(default=False, description="是否启用微信个人号")
    wechat_token: str = Field(default="", description="微信 iLink Bot Token（扫码登录获取）")

    # === 会话配置 ===
    session_timeout_minutes: int = Field(default=30, description="会话超时时间（分钟）")
    session_max_history: int = Field(default=50, description="会话最大历史消息数")
    session_storage_path: str = Field(default="data/sessions", description="会话存储路径")

    # === 多 Agent 模式 (Beta) ===
    multi_agent_enabled: bool = Field(
        default=False,
        description="多Agent模式 (Beta)，开启后支持多Agent协作、专用Agent、IM多Bot等",
    )
    coordinator_mode_enabled: bool = Field(
        default=False,
        description="协调者模式 (CC-3)：启用后，role=coordinator 的 Agent 仅能委派/规划，不能直接执行文件/命令操作",
    )

    # IM 多 Bot 配置（多Agent模式下支持同一通道类型多个Bot实例）
    im_bots: list[dict] = Field(default_factory=list)

    # === 人格系统配置 ===
    persona_name: str = Field(
        default="default", description="当前激活的人格预设名称 (default/business/tech_expert/butler/girlfriend/boyfriend/family/jarvis)"
    )

    # === 活人感引擎配置 ===
    proactive_enabled: bool = Field(default=True, description="是否启用活人感模式")
    proactive_max_daily_messages: int = Field(default=3, description="每日最多主动消息数")
    proactive_min_interval_minutes: int = Field(default=120, description="两条主动消息最短间隔（分钟）")
    proactive_quiet_hours_start: int = Field(default=23, description="安静时段开始（小时，0-23）")
    proactive_quiet_hours_end: int = Field(default=7, description="安静时段结束（小时，0-23）")
    proactive_idle_threshold_hours: int = Field(default=3, description="用户空闲多久后触发闲聊问候（小时），AI 会根据反馈动态调整")

    # === UI 偏好配置 ===
    ui_theme: str = Field(
        default="system",
        description="桌面客户端主题: system(跟随系统) | light(浅色) | dark(深色)",
    )
    ui_language: str = Field(
        default="zh",
        description="桌面客户端语言: zh(中文) | en(英文)",
    )

    # === 桌面通知配置 ===
    desktop_notify_enabled: bool = Field(
        default=True,
        description="任务完成时是否弹出系统桌面通知（Windows Toast / macOS / Linux notify-send）",
    )
    desktop_notify_sound: bool = Field(
        default=True,
        description="桌面通知是否播放系统提示音",
    )

    # === 表情包配置 ===
    sticker_enabled: bool = Field(default=True, description="是否启用表情包功能")
    sticker_data_dir: str = Field(default="data/sticker", description="表情包数据目录")

    # === Bug Report / Feedback 配置 ===
    # 以下三个值是公开标识（类似 reCAPTCHA site key），不是密钥。
    # 官方发行版需要预填默认值以实现开箱即用；
    # fork 用户可通过 .env 覆盖为自己的值，留空则禁用对应功能。
    bug_report_endpoint: str = Field(
        default="https://feedback-openakita.fzstack.com",
        description="反馈上传端点 URL（阿里云 FC）。留空 = 禁用反馈功能。",
    )
    captcha_scene_id: str = Field(
        default="jkyrkj0w",
        description="阿里云人机验证 2.0 场景ID（公开标识，下发到前端）。留空 = 跳过验证码。",
    )
    captcha_prefix: str = Field(
        default="yiqg72",
        description="阿里云人机验证 2.0 prefix 身份标（公开标识，下发到前端）。",
    )

    # === OpenAkita Platform (Agent Hub / Skill Store) ===
    hub_enabled: bool = Field(
        default=False,
        description="启用 OpenAkita Platform 连接（Agent Hub / Skill Store）。关闭时不注册远程市场工具。",
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

    # === 上下文管理配置 ===
    context_max_window: int = Field(
        default=0,
        description="全局上下文最大输入长度 (tokens)。实际生效时取 min(此值, 端点 context_window)。0=不限制，直接使用端点上限",
    )
    context_compression_ratio: float = Field(
        default=0.25,
        description="上下文压缩目标比例，早期对话压缩到原文的该百分比 (0.05~0.5)",
    )
    context_compression_threshold: float = Field(
        default=0.85,
        description="触发压缩的软限比例——上下文 token 数超过硬上限的该比例时开始压缩 (0.5~0.95，越大越晚触发)",
    )
    context_boundary_compression_ratio: float = Field(
        default=0.25,
        description="跨话题边界压缩比例，旧话题压缩到该百分比 (0.05~0.5)",
    )
    context_min_recent_turns: int = Field(
        default=12,
        description="压缩时至少保留的最近对话组数 (4~20)",
    )
    context_enable_tool_compression: bool = Field(
        default=True,
        description="是否启用超长工具结果独立压缩",
    )
    context_large_tool_threshold: int = Field(
        default=5000,
        description="触发单条工具结果独立压缩的 token 阈值",
    )

    # === Harness 配置 ===
    supervisor_enabled: bool = Field(default=True, description="是否启用运行时监督器 (RuntimeSupervisor)")
    task_budget_tokens: int = Field(default=0, description="单次任务最大 token 消耗 (0=不限制)")
    task_budget_cost: float = Field(default=0.0, description="单次任务最大成本 USD (0=不限制)")
    task_budget_duration: int = Field(default=600, description="单次任务最大时长秒 (0=不限制，默认 600=10分钟)")
    task_budget_iterations: int = Field(default=50, description="单次任务最大迭代次数 (0=不限制，默认 50)")
    task_budget_tool_calls: int = Field(default=30, description="单次任务最大工具调用次数 (0=不限制，默认 30)")

    # === 追踪配置 ===
    tracing_enabled: bool = Field(default=True, description="是否启用 Agent 追踪（轻量模式默认开启）")
    tracing_export_dir: str = Field(default="data/traces", description="追踪导出目录")
    tracing_console_export: bool = Field(default=False, description="是否同时导出到控制台")

    # === 评估配置 ===
    evaluation_enabled: bool = Field(default=False, description="是否启用每日自动评估")
    evaluation_output_dir: str = Field(default="data/evaluation", description="评估报告输出目录")

    @model_validator(mode="after")
    def _enforce_min_max_iterations(self) -> "Settings":
        MIN_ITERATIONS = 15
        if self.max_iterations < MIN_ITERATIONS:
            logger.warning(
                "[Config] max_iterations=%d is too low (minimum %d). "
                "Resetting to %d. Please update your .env file.",
                self.max_iterations, MIN_ITERATIONS, MIN_ITERATIONS,
            )
            self.max_iterations = MIN_ITERATIONS
        return self

    @model_validator(mode="before")
    @classmethod
    def _strip_inline_comments(cls, values: dict) -> dict:  # type: ignore[override]
        """Strip inline comments from env values before type coercion.

        .env files may contain lines like ``MAX_TOKENS=4096  # 常规推荐值``.
        If an external caller (e.g. Tauri bridge) passes the raw value including
        the comment as an OS env-var, Pydantic would fail to parse ``"4096 # ..."``
        as ``int``.  This validator runs *before* field-level coercion and removes
        everything after an unquoted `` #`` / ``\\t#`` pattern.
        """
        if not isinstance(values, dict):
            return values
        cleaned: dict = {}
        for k, v in values.items():
            if isinstance(v, str) and not (
                len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'")
            ):
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
        # 关键：忽略空字符串环境变量（例如 .env 里写了 PROGRESS_TIMEOUT_SECONDS=）
        # 否则 pydantic 会尝试把 "" 解析成 int/bool，导致启动失败。
        "env_ignore_empty": True,
    }

    def reload(self) -> list[str]:
        """从 .env 文件重新加载配置，返回发生变更的字段名列表。

        创建一个新的 Settings 实例（会重新读取 .env），
        然后把所有字段值拷贝回当前单例。

        运行时持久化字段（``_PERSISTABLE_KEYS``）由 RuntimeState 管理，
        不从 .env 覆盖，避免 im_bots / multi_agent_enabled 等被重置。
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
        """身份配置目录路径"""
        return self.project_root / "identity"

    @property
    def soul_path(self) -> Path:
        """SOUL.md 路径"""
        return self.identity_path / "SOUL.md"

    @property
    def agent_path(self) -> Path:
        """AGENT.md 路径"""
        return self.identity_path / "AGENT.md"

    @property
    def user_path(self) -> Path:
        """USER.md 路径"""
        return self.identity_path / "USER.md"

    @property
    def memory_path(self) -> Path:
        """MEMORY.md 路径"""
        return self.identity_path / "MEMORY.md"

    @property
    def personas_path(self) -> Path:
        """人格预设目录路径"""
        return self.identity_path / "personas"

    @property
    def sticker_data_path(self) -> Path:
        """表情包数据目录路径"""
        return self.project_root / self.sticker_data_dir

    @property
    def openakita_home(self) -> Path:
        """用户数据根目录，优先使用 OPENAKITA_ROOT 环境变量，默认 ~/.openakita"""
        import os
        env_root = os.environ.get("OPENAKITA_ROOT", "").strip()
        if env_root:
            return Path(env_root)
        return Path.home() / ".openakita"

    @property
    def user_workspace_path(self) -> Path:
        """当前用户工作区路径。

        如果 project_root 位于 openakita_home/workspaces/ 下（生产模式），
        直接使用 project_root 作为工作区路径；否则（开发模式）回退到 default。
        """
        ws_dir = self.openakita_home / "workspaces"
        try:
            self.project_root.resolve().relative_to(ws_dir.resolve())
            return self.project_root.resolve()
        except ValueError:
            return ws_dir / "default"

    @property
    def skills_path(self) -> Path:
        """用户技能安装目录 (~/.openakita/workspaces/default/skills)

        所有通过 install_skill / skill-creator 安装或创建的技能都存放在此目录。
        该目录位于用户 home 下，打包版本也有写权限。
        开发模式下项目级 skills/ 仍会被扫描（通过 SKILL_DIRECTORIES），但安装目标统一为此路径。
        """
        return self.user_workspace_path / "skills"

    @property
    def specs_path(self) -> Path:
        """规格文档目录路径"""
        return self.project_root / "specs"

    @property
    def data_dir(self) -> Path:
        """数据存储目录 (project_root/data)"""
        return self.project_root / "data"

    @property
    def db_full_path(self) -> Path:
        """数据库完整路径"""
        return self.project_root / self.database_path

    @property
    def log_dir_path(self) -> Path:
        """日志目录完整路径"""
        return self.project_root / self.log_dir

    @property
    def log_file_path(self) -> Path:
        """主日志文件路径"""
        return self.log_dir_path / f"{self.log_file_prefix}.log"

    @property
    def error_log_path(self) -> Path:
        """错误日志文件路径（只记录 ERROR/CRITICAL）"""
        return self.log_dir_path / "error.log"

    @property
    def selfcheck_dir(self) -> Path:
        """自检报告目录"""
        return self.project_root / "data" / "selfcheck"

    @property
    def mcp_config_path(self) -> Path:
        """用户 MCP 配置目录（可写，打包模式安全）

        路径: {project_root}/data/mcp/servers/
        AI 通过工具添加的 MCP 服务器配置保存在此目录。
        启动时同时扫描内置 mcps/ 和此目录。
        """
        return self.project_root / "data" / "mcp" / "servers"

    @property
    def mcp_builtin_path(self) -> Path:
        """内置 MCP 配置目录（随项目分发，打包后可能只读）

        优先使用 project_root/mcps（开发模式），
        若不存在则回退到 wheel 打包位置 site-packages/openakita/builtin_mcps/。
        """
        dev_path = self.project_root / "mcps"
        if dev_path.exists():
            return dev_path
        pkg_path = Path(__file__).resolve().parent / "builtin_mcps"
        if pkg_path.exists():
            return pkg_path
        return dev_path


# ---------------------------------------------------------------------------
# 运行时状态持久化
# ---------------------------------------------------------------------------
# 用于保存用户通过对话动态修改的设置（角色、活人感开关等），
# 使其在 Agent 重启后依然生效。
# 存储位置: data/runtime_state.json
# ---------------------------------------------------------------------------

# 需要持久化的 settings 字段名
_PERSISTABLE_KEYS: list[str] = [
    "persona_name",
    "proactive_enabled",
    "proactive_max_daily_messages",
    "proactive_min_interval_minutes",
    "proactive_quiet_hours_start",
    "proactive_quiet_hours_end",
    "ui_theme",
    "ui_language",
    "multi_agent_enabled",
    "im_bots",
    "force_tool_call_max_retries",
    "force_tool_call_im_floor",
    "confirmation_text_max_retries",
]


class RuntimeState:
    """
    轻量级运行时状态持久化。

    在 settings 单例上修改可持久化字段后，调用 save() 写入磁盘；
    在 Agent 启动时调用 load() 从磁盘恢复。
    """

    def __init__(self, state_file: Path | None = None):
        # 延迟解析（settings 还没创建时不能访问 project_root）
        self._state_file = state_file

    @property
    def state_file(self) -> Path:
        if self._state_file is None:
            self._state_file = settings.project_root / "data" / "runtime_state.json"
        return self._state_file

    def save(self) -> None:
        """把当前 settings 中的可持久化字段写入 JSON 文件（原子写入 + 备份）。"""
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
        """从 JSON 文件恢复设置到 settings 单例，仅覆盖可持久化字段（支持 .bak 回退）。"""
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


# 全局配置实例
settings = Settings()

# 全局运行时状态管理器
runtime_state = RuntimeState()

# ---------------------------------------------------------------------------
# 重启信号标志
# ---------------------------------------------------------------------------
# 由 /api/config/restart 端点设置，main.py serve() 循环检测此标志决定是否重启。
_restart_requested: bool = False
