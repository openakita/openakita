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

    # === HTTP API 网络绑定与访问控制（PR-L1: 默认仅本机, lan_mode 显式开启） ===
    api_host: str = Field(
        default="127.0.0.1",
        description=(
            "HTTP API 绑定地址。默认 127.0.0.1（仅本机访问）；"
            "若需被同网段其他机器访问，请改为 0.0.0.0 并同时开启 api_lan_mode。"
            "环境变量 API_HOST 仍可覆盖此值，但出于安全审计目的建议改在配置里显式设置。"
        ),
    )
    api_port: int = Field(default=18900, description="HTTP API 监听端口")
    api_lan_mode: bool = Field(
        default=False,
        description=(
            "是否暴露到局域网。默认 False=只监听 127.0.0.1。"
            "开启后会自动把 host 改成 0.0.0.0，同时强制要求设置 web_access 密码或 api_token，"
            "否则启动会失败（避免无密码裸奔）。"
        ),
    )
    api_token: str = Field(
        default="",
        description=(
            "API 共享访问令牌（可选）。设置后非本机请求必须在 Authorization: Bearer <token> "
            "或 X-OpenAkita-Token 头里携带它，作为 web_access 密码之外的二次校验。"
            "首次开启 lan_mode 时若未填写会自动生成一个 32 字符 token 并写入 .env。"
        ),
    )

    grep_timeout_sec: int = Field(
        default=30,
        ge=5,
        le=600,
        description="单次 grep（文件内容搜索）最大耗时（秒），超时返回提示以避免 worker 被大目录卡住。",
    )

    # PR-R1: 系统 prompt / catalog header 的语言。
    # 取值 "zh"（中文，默认）/ "en"（英文）。tool_catalog header、AGENTS.md 段
    # 引导文本等会按此切换；工具自身的 description 仍按工具定义里的语言。
    prompt_lang: str = Field(
        default="zh",
        description="System prompt 主语言：'zh' 中文 / 'en' 英文。",
    )

    # PR-T1: 灰度开关（feature flags）配置入口。
    # core/feature_flags.py 会读取这里的 dict，作为持久化的 flag 覆盖源；
    # 优先级：runtime override > 环境变量 OPENAKITA_FF_DISABLE/ENABLE >
    #         settings.feature_flags > 代码内默认值。
    # 在 .env / openakita.toml 里这样写就能关掉本批新行为之一：
    #   FEATURE_FLAGS={"text_replace_on_restart_v1": false}
    # 解析失败永不阻断启动；不在此处写白名单，未知 flag 直接被 is_enabled 当作 False。
    feature_flags: dict = Field(
        default_factory=dict,
        description=(
            "灰度开关 dict，用于覆盖 core/feature_flags.py 中的默认值。"
            "可在配置文件 / 环境变量 FEATURE_FLAGS（JSON）中按 flag_name=true/false 设置，"
            "便于一键回退某个治本修复到老路径。"
        ),
    )

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
        default=300,
        ge=5,
        description="Ralph/ReAct 循环最大迭代次数（最终防死循环硬上限；默认 300，复杂任务可调到 500+）",
    )

    # Plan 模式建议阈值（ComplexitySignal.score 达到此值时建议用户使用 Plan 模式）
    plan_suggest_threshold: int = Field(
        default=5,
        ge=2,
        le=10,
        description="复杂度评分达到该阈值时建议 Plan 模式（2~10，越高越不容易触发建议）",
    )

    # 自检配置
    selfcheck_autofix: bool = Field(
        default=True,
        description="自检时是否执行自动修复（设为 false 则只分析不修复）",
    )

    # === 任务超时策略 ===
    # 默认对齐 Claude Code 哲学：CLI/IM 真人对话场景不做"agent 自检自杀"，
    # 卡死由用户主动按"停止"/Esc 中断。仅在程序化场景（CI/SDK 批跑）需要兜底时打开。
    # - progress_timeout_seconds: 若连续超过该时间没有任何进展（LLM返回/工具完成/迭代推进），视为卡死。0=禁用。
    # - hard_timeout_seconds: 任务硬上限（仅在确定要限制总时长时启用）。0=禁用。
    progress_timeout_seconds: int = Field(
        default=0,
        description="无进展超时阈值（秒），0=禁用（默认）。建议值 1200（20 分钟）",
    )
    hard_timeout_seconds: int = Field(
        default=0,
        description="硬超时上限（秒），0=禁用（默认）。仅作为最终兜底，避免无限任务",
    )

    # === ForceToolCall（工具护栏）===
    # 默认信任模型自主判断是否需要工具；仅由意图分析或用户配置显式开启追问。
    force_tool_call_max_retries: int = Field(
        default=0,
        description="当模型未调用工具时，最多追问要求调用工具的次数（0=禁用，信任模型自主判断）",
    )
    force_tool_call_im_floor: int = Field(
        default=0,
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
    tool_hard_timeout_seconds: int = Field(
        default=0,
        description="普通工具调用硬超时（秒），0=不限时（默认，由用户/工具自身中断控制）",
    )
    long_running_tool_timeout_seconds: int = Field(
        default=0,
        description="长耗时工具（shell/browser/org 等）硬超时（秒），0=不限时（默认）",
    )
    tool_result_max_chars: int = Field(
        default=32000,
        ge=1000,
        description="单个工具结果进入模型前的兜底截断字符数；完整内容会保存到 overflow 文件",
    )
    tool_overflow_max_files: int = Field(
        default=200,
        ge=10,
        description="工具超长输出 overflow 目录保留的最大文件数",
    )
    run_shell_default_block_timeout_ms: int = Field(
        default=30000,
        ge=0,
        description="run_shell 未显式设置 block_timeout_ms/timeout 时的阻塞等待毫秒数；0=立即后台化",
    )
    run_shell_max_block_timeout_ms: int = Field(
        default=1800000,
        ge=0,
        description="run_shell 兼容 timeout 参数换算后的最大阻塞等待毫秒数；0=不额外钳制",
    )
    powershell_default_timeout_seconds: int = Field(
        default=120,
        ge=0,
        description="run_powershell 默认等待时间（秒）；0=不设置子进程超时",
    )
    powershell_max_timeout_seconds: int = Field(
        default=1800,
        ge=0,
        description="run_powershell 显式 timeout 的最大值（秒）；0=不额外钳制",
    )
    cli_command_timeout_seconds: int = Field(
        default=300,
        ge=0,
        description="CLI-Anything 普通命令默认等待时间（秒）；0=不设置子进程超时",
    )
    opencli_command_timeout_seconds: int = Field(
        default=300,
        ge=0,
        description="OpenCLI list/doctor 默认等待时间（秒）；0=不设置子进程超时",
    )
    opencli_task_timeout_seconds: int = Field(
        default=900,
        ge=0,
        description="OpenCLI run 默认等待时间（秒）；0=不设置子进程超时",
    )
    read_file_default_limit: int = Field(
        default=2000,
        ge=1,
        description="read_file 未指定 limit 时默认读取的行数",
    )
    web_search_attempt_timeout_seconds: int = Field(
        default=25,
        ge=0,
        description=(
            "web_search/news_search 单次外部搜索源等待上限（秒），0=不限。"
            "超时只跳过本次搜索等待并把结果交给模型继续决策，不判定整个任务失败"
        ),
    )

    allow_parallel_tools_with_interrupt_checks: bool = Field(
        default=False,
        description="是否允许在启用“工具间中断检查”时也并行执行工具（会降低中断插入粒度，默认关闭）",
    )

    # === 工具常驻加载 ===
    always_load_tools: list = Field(
        default_factory=list,
        description="用户指定的常驻工具名列表，不会被 defer（如 browser_navigate, edit_notebook）",
    )
    always_load_categories: list = Field(
        default_factory=list,
        description="用户指定的常驻工具分类（如 Browser, MCP），该分类下所有工具不 defer",
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
    llm_debug_enabled: bool = Field(default=True, description="是否保存 LLM 请求/响应调试快照")
    llm_debug_retention_days: int = Field(default=3, description="LLM 调试快照保留天数")
    llm_debug_max_size_mb: int = Field(default=512, description="LLM 调试快照目录最大体积（MB）")

    # === 全局代理配置 ===
    # 用于 LLM API 请求的代理（如果透明代理不生效）
    http_proxy: str = Field(default="", description="HTTP 代理地址 (如 http://127.0.0.1:7890)")
    https_proxy: str = Field(default="", description="HTTPS 代理地址 (如 http://127.0.0.1:7890)")
    all_proxy: str = Field(default="", description="全局代理地址（优先级高于 http/https proxy）")
    no_proxy: str = Field(
        default="",
        description="不走代理的地址（逗号分隔，支持 IP / CIDR / 域名后缀，如 192.168.0.0/16,.internal）",
    )

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
        default=0,
        ge=0,
        description="MCP 工具/资源/提示词调用超时时间（秒），0=不限制；连接超时单独由 mcp_connect_timeout 控制",
    )
    mcp_connect_timeout: int = Field(
        default=30, description="MCP 服务器连接超时时间（秒），默认 30 秒"
    )
    mcp_auto_connect: bool = Field(default=False, description="启动时是否自动连接所有 MCP 服务器")

    # === 调度器配置 ===
    scheduler_timezone: str = Field(default="Asia/Shanghai", description="调度器时区")
    scheduler_task_timeout: int = Field(
        default=1200, description="定时任务执行超时时间（秒），默认 1200 秒（20分钟）"
    )
    scheduler_background_token_budget: int = Field(
        default=120000,
        description="单次后台系统任务的 token 预算，达到后在安全检查点暂停（0=不限制）",
    )
    scheduler_selfcheck_fix_token_budget: int = Field(
        default=60000,
        description="单次自检自动修复的 token 预算，达到后跳过后续自动修复（0=不限制）",
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
    wework_ws_thinking_indicator: bool = Field(
        default=True, description="收到消息后立即发送'思考中'流式首帧提示"
    )
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
    dingtalk_client_secret: str = Field(
        default="", description="钉钉 Client Secret（原 App Secret）"
    )

    # OneBot 协议（通用）
    onebot_enabled: bool = Field(default=False, description="是否启用 OneBot")
    onebot_mode: str = Field(
        default="reverse",
        description="OneBot 连接模式: reverse（反向WS，推荐）或 forward（正向WS）",
    )
    onebot_ws_url: str = Field(
        default="ws://127.0.0.1:8080", description="OneBot 正向 WS 地址（仅 forward 模式）"
    )
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
    session_max_history: int = Field(default=2000, description="会话消息硬上限（日常由 metadata trim 控制体积）")
    session_storage_path: str = Field(default="data/sessions", description="会话存储路径")

    # === 多 Agent 模式 (Beta) ===
    multi_agent_enabled: bool = Field(
        default=True,
        description="多Agent模式 (Beta)，开启后支持多Agent协作、专用Agent、IM多Bot等",
    )
    coordinator_mode_enabled: bool = Field(
        default=True,
        description=(
            "协调者模式 (CC-3)：启用后，role=coordinator 的 Agent 仅能委派/规划，"
            "不能直接执行文件/命令操作。组织模式下的协调者节点（有下级的节点）"
            "始终启用协调者提示词，与本开关解耦。"
        ),
    )

    # IM 多 Bot 配置（多Agent模式下支持同一通道类型多个Bot实例）
    im_bots: list[dict] = Field(default_factory=list)

    # === 人格系统配置 ===
    persona_name: str = Field(
        default="default",
        description="当前激活的人格预设名称 (default/business/tech_expert/butler/girlfriend/boyfriend/family/jarvis)",
    )

    # === 记忆回顾（Memory Nudge）配置 ===
    memory_nudge_enabled: bool = Field(
        default=True,
        description="是否启用周期性记忆回顾（每 N 轮对话后用 LLM 审视对话并提取值得记忆的内容）",
    )
    memory_nudge_interval: int = Field(
        default=10,
        description="每隔多少轮对话触发一次记忆回顾（0 表示禁用）",
    )

    # === Smart Approval 配置 ===
    smart_approval_enabled: bool = Field(
        default=False,
        description="是否启用 LLM 辅助风险评估（对 CONFIRM 级操作用 LLM 做预判）",
    )

    # === Docker 执行后端配置 ===
    docker_backend_enabled: bool = Field(
        default=False,
        description="是否启用 Docker 容器执行后端（需要本机安装 Docker）",
    )
    docker_image: str = Field(
        default="python:3.12-slim",
        description="Docker 执行后端使用的镜像",
    )
    docker_network: str = Field(
        default="none",
        description="Docker 网络模式: none(断网) | bridge(默认桥接) | host",
    )

    # === 活人感引擎配置 ===
    proactive_enabled: bool = Field(default=True, description="是否启用活人感模式")
    proactive_max_daily_messages: int = Field(default=3, description="每日最多主动消息数")
    proactive_min_interval_minutes: int = Field(
        default=120, description="两条主动消息最短间隔（分钟）"
    )
    proactive_quiet_hours_start: int = Field(default=23, description="安静时段开始（小时，0-23）")
    proactive_quiet_hours_end: int = Field(default=7, description="安静时段结束（小时，0-23）")
    proactive_idle_threshold_hours: int = Field(
        default=3, description="用户空闲多久后触发闲聊问候（小时），AI 会根据反馈动态调整"
    )

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
    sticker_mirrors: list[str] = Field(
        default_factory=list,
        description=(
            "自定义表情包镜像 URL 列表，优先于内置镜像尝试。"
            "支持两种格式：1) CDN 镜像基址（追加相对路径），"
            "2) GitHub 代理前缀（追加完整原始 URL）。"
            "示例: ['https://ghp.ci/https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/']"
        ),
    )

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
    context_real_usage_decay: float = Field(
        default=0.9,
        ge=0.1,
        le=1.0,
        description="用上一轮真实 input_tokens 反向校准上下文压力时的衰减系数",
    )
    context_token_anomaly_threshold: int = Field(
        default=80000,
        description="单轮 LLM usage 触发强制压缩/降载的阈值（不是直接终止阈值）。值越大越宽松，长任务建议 ≥80000",
    )
    context_token_anomaly_max_recoveries: int = Field(
        default=3,
        ge=0,
        description="单任务内 token 异常触发后允许强制压缩恢复的次数，超过后才允许硬终止；长任务建议 3~5",
    )
    context_hard_terminate_ratio: float = Field(
        default=0.98,
        ge=0.5,
        le=0.99,
        description=(
            "硬终止比例：单轮 input+output tokens 占模型上下文窗口的此比例时，"
            "LoopBudgetGuard 才允许真正终止任务（0.5~0.99，越大越宽松）。"
            "如果当前压力安全且未到此比例，即使触发了 token 异常阈值也只压缩不终止"
        ),
    )
    context_cached_summary_chars: int = Field(
        default=2400,
        description="缓存/聚合工具结果摘要的默认字符预算",
    )
    context_tool_results_total_chars: int = Field(
        default=80000,
        description="单轮工具结果进入上下文前的总字符预算（后续会按上下文压力动态调整）",
    )
    api_tools_schema_budget_tokens: int = Field(
        default=12000,
        description="发送给 LLM API 的 tools schema 估算 token 预算，超出后动态 defer 非核心工具",
    )
    same_tool_call_limit: int = Field(
        default=0,
        ge=0,
        description="同一工具同参数在单任务内允许执行的最大次数，0=不限（默认）。建议值 8~12",
    )
    readonly_stagnation_limit: int = Field(
        default=0,
        ge=0,
        description="只读探索连续无新信息的软提醒轮数，0=禁用（默认）。建议值 3",
    )
    readonly_stagnation_hard_limit: int = Field(
        default=0,
        ge=0,
        description="只读探索连续无新信息的硬终止轮数，0=禁用（默认）。建议值 10~15",
    )

    # === Harness 配置 ===
    # 默认全部关闭/不限，对齐 Claude Code 风格（CLI 真人场景不强加业务护栏）。
    # 仅在程序化场景（CI/SDK 批跑、定时任务、组织看门狗等）需要兜底时打开。
    supervisor_enabled: bool = Field(
        default=False,
        description="是否启用运行时监督器 (RuntimeSupervisor)，默认关闭。开启后会在工具抖动/编辑抖动/推理死循环等模式被检测到时主动干预",
    )
    task_budget_tokens: int = Field(default=0, description="单次任务最大 token 消耗，0=不限（默认）")
    task_budget_cost: float = Field(default=0.0, description="单次任务最大成本 USD，0=不限（默认）")
    task_budget_duration: int = Field(
        default=0,
        description="单次任务最大时长（秒），0=不限（默认）。建议值 600~3600",
    )
    task_budget_iterations: int = Field(
        default=0,
        description="单次任务最大迭代次数，0=不限（默认）。max_iterations 仍是 ReAct 循环硬上限",
    )
    task_budget_tool_calls: int = Field(
        default=0,
        description="单次任务最大工具调用次数，0=不限（默认）。建议值 100~300",
    )

    # === 追踪配置 ===
    tracing_enabled: bool = Field(
        default=True, description="是否启用 Agent 追踪（轻量模式默认开启）"
    )
    tracing_export_dir: str = Field(default="data/traces", description="追踪导出目录")
    tracing_console_export: bool = Field(default=False, description="是否同时导出到控制台")

    # === 评估配置 ===
    evaluation_enabled: bool = Field(default=False, description="是否启用每日自动评估")
    evaluation_output_dir: str = Field(default="data/evaluation", description="评估报告输出目录")

    # === 组织编排 · 任务链终止防护 ===
    # 这组开关用于防止：
    # 1) 同一 chain 被重复交付/验收导致附件与交付物重复；
    # 2) 任务验收完成后节点仍被后续消息唤醒、自主启动新的 ReAct 循环；
    # 3) 任务完成后自动向上级发送"已完成"通知从而引发新的父级推理。
    # 默认全部开启；如需回退旧行为只需将对应项设为 false。
    org_reject_resubmit_after_accept: bool = Field(
        default=True,
        description="禁止在 chain 已 accepted/delivered 之后再次 submit_deliverable",
    )
    org_suppress_closed_chain_reactivation: bool = Field(
        default=True,
        description="chain 已关闭(accepted/rejected/cancelled)时抑制其消息触发 ReAct 重新激活",
    )
    org_post_task_notify_parent: bool = Field(
        default=False,
        description="任务完成时是否自动向父节点发送[通知]：False 表示不主动唤醒父级",
    )

    # === 组织编排 · 多层级指挥治理（org-orchestration-fix） ===
    # 这组开关用于治理"CEO -> CMO -> 多个执行者 -> CMO 汇总 -> CEO 回包"
    # 这类多层级指挥场景，解决以下根因：
    #   1) 子链 chain_id 默认 _now_iso()，导致父子链断裂、tracker 子树失明
    #   2) 协调者用 org_send_message(question) 派任务，绕过 chain 注册
    #   3) Supervisor 把合法 poll 当死循环 TERMINATE
    #   4) 缺少阻塞等待原语，协调者只能轮询
    #   5) 完成判定一次性 set，CEO 拿不到最终汇总
    # 默认全部开启；任一项设为 false 可一键回退到旧行为，旧代码路径保留。
    org_chain_parent_enforced: bool = Field(
        default=True,
        description=(
            "强制 chain 父子关系：delegate 时为子任务新建 chain 并挂到 caller "
            "current chain 之下；submit 强制复用 caller current chain；"
            "tracker 完成判定走整棵子树。关闭后回退到旧的'复用 caller chain'语义。"
        ),
    )
    org_question_task_guard: bool = Field(
        default=True,
        description=(
            "拦截协调者用 org_send_message(question) 派发任务的反模式："
            "若 sender 有下属且消息文本含'撰写/优化/产出/完成/给出/生成'等任务措辞，"
            "拒绝发送并提示改用 org_delegate_task。"
        ),
    )
    org_supervisor_poll_whitelist: bool = Field(
        default=True,
        description=(
            "Supervisor 对 org_list_delegated_tasks / org_wait_for_deliverable "
            "等合法轮询/等待工具，抬高重复阈值且最高仅 NUDGE，绝不 TERMINATE。"
        ),
    )
    org_wait_primitive_enabled: bool = Field(
        default=True,
        description=(
            "启用 org_wait_for_deliverable 工具：协调者派完任务后可阻塞等待"
            "下级交付，避免 org_list_delegated_tasks 轮询触发 Supervisor 死循环。"
        ),
    )
    org_root_post_summary: bool = Field(
        default=True,
        description=(
            "用户命令完成判定的两阶段状态机：所有子链关闭 + root IDLE 时，"
            "先 push 一条 task_complete 到 root inbox 唤醒 root 产出最终汇总，"
            "等 root 二次 IDLE 后再 set completed。关闭后退回到一阶段判定。"
        ),
    )

    # === 组织编排 · 用户命令生命周期看门狗 ===
    # 用户通过 send_command 下发一条顶层指令后，完成判定由事件驱动
    # （所有委派链 chain 关闭 + root IDLE + root inbox 空）。下列时间参数
    # 仅用于看门狗：防止组织真正卡死（LLM 挂起、死锁）时命令无限挂起。
    # 任一进度信号（token / 工具完成 / 节点状态切换 / chain 事件）到达
    # 都会让 warn/autostop 计时器归零，因此长时但持续产出的任务不会被误停。
    # 默认全部关闭：CLI/IM 真人协作场景下，多 Agent 死锁由用户在指挥台手动按【强制终止】处理。
    # 仅在程序化/无人值守场景需要兜底时，在【组织设置 → 任务看门狗】中打开。
    org_command_stuck_warn_secs: int = Field(
        default=0,
        description="无进度多久（秒）向前端发出 stuck_warning 提示，0=禁用（默认）。建议值 600",
    )
    org_command_stuck_autostop_secs: int = Field(
        default=0,
        description="无进度多久（秒）兜底 soft_stop 组织，0=禁用（默认）。建议值 3600",
    )
    org_command_timeout_secs: int = Field(
        default=0,
        description="单条命令最长运行时间（秒）硬上限，0=不限时（默认）。建议值 10800",
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
        # 关键：忽略空字符串环境变量（例如 .env 里写了 PROGRESS_TIMEOUT_SECONDS=）
        # 否则 pydantic 会尝试把 "" 解析成 int/bool，导致启动失败。
        "env_ignore_empty": True,
    }

    def reload(self) -> list[str]:
        """从 .env 文件重新加载配置，返回发生变更的字段名列表。

        创建一个新的 Settings 实例（会重新读取 .env），
        然后把所有字段值拷贝回当前单例。

        运行时持久化字段（``_PERSISTABLE_KEYS``）由 RuntimeState 管理，
        不从 .env 覆盖，避免 im_bots 等被重置。
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
    "tool_hard_timeout_seconds",
    "long_running_tool_timeout_seconds",
    "tool_result_max_chars",
    "tool_overflow_max_files",
    "run_shell_default_block_timeout_ms",
    "run_shell_max_block_timeout_ms",
    "powershell_default_timeout_seconds",
    "powershell_max_timeout_seconds",
    "cli_command_timeout_seconds",
    "opencli_command_timeout_seconds",
    "opencli_task_timeout_seconds",
    "read_file_default_limit",
    "web_search_attempt_timeout_seconds",
    "always_load_tools",
    "always_load_categories",
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
        from .utils.redaction import redact_value

        data: dict = {}
        for key in _PERSISTABLE_KEYS:
            data[key] = getattr(settings, key)
        try:
            safe_json_write(self.state_file, data)
            logger.info(f"[RuntimeState] Saved: {redact_value(data)}")
        except Exception as e:
            logger.error(f"[RuntimeState] Failed to save: {e}")

    def load(self) -> None:
        """从 JSON 文件恢复设置到 settings 单例，仅覆盖可持久化字段（支持 .bak 回退）。"""
        from .utils.atomic_io import read_json_safe
        from .utils.redaction import redact_value

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
                        applied.append(
                            f"{key}: {redact_value(old_val)} -> {redact_value(new_val)}"
                        )
            if applied:
                logger.info(f"[RuntimeState] Restored: {'; '.join(applied)}")
            else:
                logger.info("[RuntimeState] State loaded, no changes needed.")
        except Exception as e:
            logger.error(f"[RuntimeState] Failed to load: {e}")


def _create_settings_safe() -> Settings:
    """Create the global Settings instance with recovery for poisoned .env files.

    If a field in .env has an unparseable value (e.g. Python repr instead of JSON
    for complex types), remove that field from .env and retry. This handles the
    case where _PERSISTABLE_KEYS fields were incorrectly written to .env by older
    code — those fields are managed by RuntimeState, not .env.
    """
    import re

    max_retries = 3
    last_err: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return Settings()
        except Exception as e:
            last_err = e
            if attempt == max_retries:
                break

            err_msg = str(e)
            logger.error(f"[Config] Settings init failed (attempt {attempt + 1}): {err_msg}")

            env_path = Path.cwd() / ".env"
            if not env_path.exists():
                break

            field_match = re.search(r'field "(\w+)"', err_msg)
            if not field_match:
                break

            bad_field = field_match.group(1).upper()
            logger.warning(f"[Config] Removing poisoned key '{bad_field}' from .env and retrying")

            try:
                lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
                cleaned = [
                    ln for ln in lines
                    if not ln.strip().startswith(f"{bad_field}=")
                ]
                env_path.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
            except Exception as io_err:
                logger.error(f"[Config] Failed to repair .env: {io_err}")
                break

    raise last_err  # type: ignore[misc]


# 全局配置实例
settings = _create_settings_safe()

# 全局运行时状态管理器
runtime_state = RuntimeState()

# ---------------------------------------------------------------------------
# 重启信号标志
# ---------------------------------------------------------------------------
# 由 /api/config/restart 端点设置，main.py serve() 循环检测此标志决定是否重启。
_restart_requested: bool = False
