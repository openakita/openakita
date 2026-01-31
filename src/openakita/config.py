"""
OpenAkita 配置模块
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""
    
    # Anthropic API
    anthropic_api_key: str = Field(default="", description="Anthropic API Key")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        description="Anthropic API Base URL (支持云雾AI等转发服务)"
    )
    default_model: str = Field(
        default="claude-opus-4-5-20251101-thinking",
        description="默认使用的模型"
    )
    max_tokens: int = Field(default=8192, description="最大输出 token 数")
    
    # Agent 配置
    agent_name: str = Field(default="OpenAkita", description="Agent 名称")
    max_iterations: int = Field(default=100, description="Ralph 循环最大迭代次数")
    auto_confirm: bool = Field(default=False, description="是否自动确认危险操作")
    
    # Thinking 模式配置
    thinking_mode: str = Field(
        default="auto",
        description="Thinking 模式: auto(自动判断), always(始终启用), never(从不启用)"
    )
    thinking_keywords: list = Field(
        default_factory=lambda: [
            "分析", "推理", "思考", "评估", "比较", "规划", "设计",
            "架构", "优化", "debug", "调试", "复杂", "困难",
            "analyze", "reason", "think", "evaluate", "compare", "plan", "design"
        ],
        description="触发 thinking 模式的关键词"
    )
    fast_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="快速模型（不使用 thinking）"
    )
    
    # 路径配置
    project_root: Path = Field(
        default_factory=lambda: Path.cwd(),
        description="项目根目录 (默认为当前工作目录)"
    )
    database_path: str = Field(default="data/agent.db", description="数据库路径")
    
    # 日志
    log_level: str = Field(default="INFO", description="日志级别")
    
    # GitHub
    github_token: str = Field(default="", description="GitHub Token")
    
    
    # === 调度器配置 ===
    scheduler_enabled: bool = Field(default=True, description="是否启用定时任务调度器")
    scheduler_timezone: str = Field(default="Asia/Shanghai", description="调度器时区")
    scheduler_max_concurrent: int = Field(default=5, description="最大并发任务数")
    
    # === 通道配置 ===
    # Telegram
    telegram_enabled: bool = Field(default=False, description="是否启用 Telegram")
    telegram_bot_token: str = Field(default="", description="Telegram Bot Token")
    telegram_webhook_url: str = Field(default="", description="Telegram Webhook URL")
    telegram_pairing_code: str = Field(default="", description="Telegram 配对码（留空则自动生成）")
    telegram_require_pairing: bool = Field(default=True, description="是否需要配对验证")
    
    # 飞书
    feishu_enabled: bool = Field(default=False, description="是否启用飞书")
    feishu_app_id: str = Field(default="", description="飞书 App ID")
    feishu_app_secret: str = Field(default="", description="飞书 App Secret")
    
    # 企业微信
    wework_enabled: bool = Field(default=False, description="是否启用企业微信")
    wework_corp_id: str = Field(default="", description="企业微信 Corp ID")
    wework_agent_id: str = Field(default="", description="企业微信 Agent ID")
    wework_secret: str = Field(default="", description="企业微信 Secret")
    
    # 钉钉
    dingtalk_enabled: bool = Field(default=False, description="是否启用钉钉")
    dingtalk_app_key: str = Field(default="", description="钉钉 App Key")
    dingtalk_app_secret: str = Field(default="", description="钉钉 App Secret")
    
    # QQ (OneBot)
    qq_enabled: bool = Field(default=False, description="是否启用 QQ")
    qq_onebot_url: str = Field(default="ws://127.0.0.1:8080", description="OneBot WebSocket URL")
    
    # === 会话配置 ===
    session_timeout_minutes: int = Field(default=30, description="会话超时时间（分钟）")
    session_max_history: int = Field(default=50, description="会话最大历史消息数")
    session_storage_path: str = Field(default="data/sessions", description="会话存储路径")
    
    # === 多 Agent 协同配置 ===
    orchestration_enabled: bool = Field(default=False, description="是否启用多 Agent 协同")
    orchestration_bus_address: str = Field(default="tcp://127.0.0.1:5555", description="ZMQ 总线地址")
    orchestration_pub_address: str = Field(default="tcp://127.0.0.1:5556", description="ZMQ 广播地址")
    orchestration_min_workers: int = Field(default=1, description="最小 Worker 数量")
    orchestration_max_workers: int = Field(default=5, description="最大 Worker 数量")
    orchestration_heartbeat_interval: int = Field(default=5, description="Worker 心跳间隔（秒）")
    orchestration_health_check_interval: int = Field(default=10, description="健康检查间隔（秒）")
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }
    
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


# 全局配置实例
settings = Settings()
