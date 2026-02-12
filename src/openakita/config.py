"""
OpenAkita 配置模块
"""

import json
import logging
from pathlib import Path

from pydantic import Field
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
    max_tokens: int = Field(default=8192, description="最大输出 token 数")

    # Agent 配置
    agent_name: str = Field(default="OpenAkita", description="Agent 名称")
    max_iterations: int = Field(default=100, description="Ralph 循环最大迭代次数")
    auto_confirm: bool = Field(default=False, description="是否自动确认危险操作")

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
        default=600,
        description="无进展超时阈值（秒）。超过该时间无进展则触发超时处理（默认 600）",
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
        description="当模型未调用工具时，最多追问要求调用工具的次数（0=禁用）",
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

    # GitHub
    github_token: str = Field(default="", description="GitHub Token")

    # === 备用 LLM 端点配置 ===
    # Kimi (月之暗面 Moonshot AI) - 备用端点 1
    kimi_api_key: str = Field(default="", description="Kimi API Key")
    kimi_base_url: str = Field(default="https://api.moonshot.cn/v1", description="Kimi API URL")
    kimi_model: str = Field(default="kimi-k2-0711-preview", description="Kimi 模型")

    # DashScope (阿里云通义) - 备用端点 2
    dashscope_api_key: str = Field(default="", description="DashScope API Key")
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1", description="DashScope API URL"
    )
    dashscope_model: str = Field(default="qwen3-max", description="DashScope 模型")

    # DashScope 图像生成 (Qwen-Image) - 同一 Key，不同接口
    dashscope_image_api_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        description="DashScope Qwen-Image 同步接口 URL（默认北京地域）",
    )

    # MiniMax - 备用端点 3
    minimax_api_key: str = Field(default="", description="MiniMax API Key")
    minimax_base_url: str = Field(
        default="https://api.minimaxi.com/v1", description="MiniMax API URL（OpenAI 兼容）"
    )
    minimax_model: str = Field(default="MiniMax-M2.1", description="MiniMax 模型")

    # === 调度器配置 ===
    scheduler_enabled: bool = Field(default=True, description="是否启用定时任务调度器")
    scheduler_timezone: str = Field(default="Asia/Shanghai", description="调度器时区")
    scheduler_max_concurrent: int = Field(default=5, description="最大并发任务数")
    scheduler_task_timeout: int = Field(
        default=600, description="定时任务执行超时时间（秒），默认 600 秒（10分钟）"
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

    # 企业微信（智能机器人）
    wework_enabled: bool = Field(default=False, description="是否启用企业微信")
    wework_corp_id: str = Field(default="", description="企业微信 Corp ID")
    wework_token: str = Field(default="", description="企业微信回调 Token")
    wework_encoding_aes_key: str = Field(default="", description="企业微信回调加密 AES Key")
    wework_callback_port: int = Field(default=9880, description="企业微信回调服务端口")
    wework_callback_host: str = Field(default="0.0.0.0", description="企业微信回调服务绑定地址")

    # 钉钉
    dingtalk_enabled: bool = Field(default=False, description="是否启用钉钉")
    dingtalk_client_id: str = Field(default="", description="钉钉 Client ID（原 App Key）")
    dingtalk_client_secret: str = Field(default="", description="钉钉 Client Secret（原 App Secret）")

    # QQ (OneBot)
    qq_enabled: bool = Field(default=False, description="是否启用 QQ")
    qq_onebot_url: str = Field(default="ws://127.0.0.1:8080", description="OneBot WebSocket URL")

    # === 会话配置 ===
    session_timeout_minutes: int = Field(default=30, description="会话超时时间（分钟）")
    session_max_history: int = Field(default=50, description="会话最大历史消息数")
    session_storage_path: str = Field(default="data/sessions", description="会话存储路径")

    # === 多 Agent 协同配置 ===
    orchestration_enabled: bool = Field(default=False, description="是否启用多 Agent 协同")
    orchestration_mode: str = Field(
        default="single",
        description="编排模式: single(单Agent) | handoff(进程内Handoff) | master-worker(ZMQ跨进程)",
    )
    orchestration_bus_address: str = Field(
        default="tcp://127.0.0.1:5555", description="ZMQ 总线地址"
    )
    orchestration_pub_address: str = Field(
        default="tcp://127.0.0.1:5556", description="ZMQ 广播地址"
    )
    orchestration_min_workers: int = Field(default=1, description="最小 Worker 数量")
    orchestration_max_workers: int = Field(default=5, description="最大 Worker 数量")
    orchestration_heartbeat_interval: int = Field(default=5, description="Worker 心跳间隔（秒）")
    orchestration_health_check_interval: int = Field(default=10, description="健康检查间隔（秒）")

    # === 人格系统配置 ===
    persona_name: str = Field(
        default="default", description="当前激活的人格预设名称 (default/business/tech_expert/butler/girlfriend/boyfriend/family/jarvis)"
    )

    # === 活人感引擎配置 ===
    proactive_enabled: bool = Field(default=False, description="是否启用活人感模式")
    proactive_max_daily_messages: int = Field(default=3, description="每日最多主动消息数")
    proactive_min_interval_minutes: int = Field(default=120, description="两条主动消息最短间隔（分钟）")
    proactive_quiet_hours_start: int = Field(default=23, description="安静时段开始（小时，0-23）")
    proactive_quiet_hours_end: int = Field(default=7, description="安静时段结束（小时，0-23）")
    proactive_idle_threshold_hours: int = Field(default=24, description="用户空闲多久后触发闲聊问候（小时）")

    # === 表情包配置 ===
    sticker_enabled: bool = Field(default=True, description="是否启用表情包功能")
    sticker_data_dir: str = Field(default="data/sticker", description="表情包数据目录")

    # === 追踪配置 ===
    tracing_enabled: bool = Field(default=False, description="是否启用 Agent 追踪")
    tracing_export_dir: str = Field(default="data/traces", description="追踪导出目录")
    tracing_console_export: bool = Field(default=False, description="是否同时导出到控制台")

    # === 评估配置 ===
    evaluation_enabled: bool = Field(default=False, description="是否启用每日自动评估")
    evaluation_output_dir: str = Field(default="data/evaluation", description="评估报告输出目录")

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
        """
        fresh = Settings()
        changed: list[str] = []
        for field_name in self.model_fields:
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
    def skills_path(self) -> Path:
        """技能目录路径"""
        return self.project_root / "skills"

    @property
    def specs_path(self) -> Path:
        """规格文档目录路径"""
        return self.project_root / "specs"

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
        """把当前 settings 中的可持久化字段写入 JSON 文件。"""
        data: dict = {}
        for key in _PERSISTABLE_KEYS:
            data[key] = getattr(settings, key)
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"[RuntimeState] Saved: {data}")
        except Exception as e:
            logger.error(f"[RuntimeState] Failed to save: {e}")

    def load(self) -> None:
        """从 JSON 文件恢复设置到 settings 单例，仅覆盖可持久化字段。"""
        if not self.state_file.exists():
            logger.info("[RuntimeState] No saved state found, using defaults.")
            return
        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = json.load(f)
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
