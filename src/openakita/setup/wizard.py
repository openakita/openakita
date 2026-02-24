"""
OpenAkita 交互式安装向导

一键启动，引导用户完成所有配置
"""

import json
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


class SetupWizard:
    """交互式安装向导"""

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self.env_path = self.project_dir / ".env"
        self.config = {}
        self._locale = "zh"           # 默认中文
        self._defaults = {            # locale 推导的默认值，_choose_locale() 会覆盖
            "MODEL_DOWNLOAD_SOURCE": "hf-mirror",
            "EMBEDDING_MODEL": "shibing624/text2vec-base-chinese",
            "WHISPER_LANGUAGE": "zh",
            "SCHEDULER_TIMEZONE": "Asia/Shanghai",
        }

    def run(self) -> bool:
        """运行完整的安装向导"""
        try:
            self._show_welcome()
            self._check_environment()
            self._choose_locale()          # 先选语言/地区，影响后续所有默认值
            self._create_directories()
            self._configure_llm()
            self._configure_compiler()
            self._configure_im_channels()
            self._configure_memory()
            self._configure_voice()        # 语音识别单独一步
            self._configure_advanced()
            self._write_env_file()
            self._test_connection()
            self._show_completion()
            return True
        except KeyboardInterrupt:
            console.print("\n\n[yellow]安装已取消[/yellow]")
            return False
        except Exception as e:
            console.print(f"\n[red]安装出错: {e}[/red]")
            return False

    def _show_welcome(self):
        """显示欢迎界面"""
        console.clear()

        welcome_text = """
# 🐕 Welcome to OpenAkita

**Your Loyal and Reliable AI Companion**

This wizard will help you set up OpenAkita in a few simple steps:

1. Configure LLM API (Claude, OpenAI-compatible, etc.)
2. Set up IM channels (optional: Telegram, Feishu, etc.)
3. Configure memory system
4. Test connection

Press Ctrl+C at any time to cancel.
        """

        console.print(
            Panel(Markdown(welcome_text), title="OpenAkita Setup Wizard", border_style="cyan")
        )
        console.print()

        Prompt.ask("[cyan]Press Enter to continue[/cyan]", default="")

    def _check_environment(self):
        """检查运行环境"""
        console.print("\n[bold cyan]Step 1: Checking Environment[/bold cyan]\n")

        checks = []

        # Python 版本
        py_version = sys.version_info
        py_ok = py_version >= (3, 11)
        checks.append(
            (
                "Python Version",
                f"{py_version.major}.{py_version.minor}.{py_version.micro}",
                py_ok,
                "≥ 3.11 required",
            )
        )

        # 检查是否在虚拟环境
        in_venv = sys.prefix != sys.base_prefix
        checks.append(
            (
                "Virtual Environment",
                "Active" if in_venv else "Not detected",
                True,  # 不强制要求
                "Recommended",
            )
        )

        # 检查目录可写
        writable = os.access(self.project_dir, os.W_OK)
        checks.append(("Directory Writable", str(self.project_dir), writable, "Required"))

        # 显示检查结果
        table = Table(show_header=True)
        table.add_column("Check", style="cyan")
        table.add_column("Status", style="white")
        table.add_column("Result", style="white")

        all_ok = True
        for name, status, ok, note in checks:
            result = "[green]✓[/green]" if ok else "[red]✗[/red]"
            if not ok and "required" in note.lower():
                all_ok = False
            table.add_row(name, status, result)

        console.print(table)

        if not all_ok:
            console.print("\n[red]Environment check failed. Please fix the issues above.[/red]")
            sys.exit(1)

        console.print("\n[green]Environment check passed![/green]\n")

    # ------------------------------------------------------------------
    # 语言 / 地区选择 — 影响后续所有默认值
    # ------------------------------------------------------------------

    def _detect_locale(self) -> str:
        """尝试从系统 locale 探测语言（仅作为默认推荐）"""
        import locale

        try:
            lang, _ = locale.getdefaultlocale()
            if lang and lang.lower().startswith("zh"):
                return "zh"
        except Exception:
            pass
        return "en"

    def _choose_locale(self):
        """选择语言/地区，自动推导后续配置的合理默认值"""
        console.print("[bold cyan]Language & Region[/bold cyan]\n")
        console.print("This affects default settings for model downloads, voice recognition, etc.\n")

        detected = self._detect_locale()
        default_choice = "1" if detected == "zh" else "2"

        console.print("  [1] 中文 / 中国大陆 (Chinese)")
        console.print("  [2] English / International\n")

        choice = Prompt.ask(
            "Select language / region",
            choices=["1", "2"],
            default=default_choice,
        )

        if choice == "1":
            self._locale = "zh"
            # 国内默认值
            self._defaults = {
                "MODEL_DOWNLOAD_SOURCE": "hf-mirror",
                "EMBEDDING_MODEL": "shibing624/text2vec-base-chinese",
                "WHISPER_LANGUAGE": "zh",
                "SCHEDULER_TIMEZONE": "Asia/Shanghai",
            }
            console.print("\n[green]已选择：中文 / 中国大陆[/green]")
            console.print("[dim]模型将默认从国内镜像下载，语音识别默认中文[/dim]\n")
        else:
            self._locale = "en"
            # 国际默认值
            self._defaults = {
                "MODEL_DOWNLOAD_SOURCE": "huggingface",
                "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
                "WHISPER_LANGUAGE": "en",
                "SCHEDULER_TIMEZONE": "UTC",
            }
            console.print("\n[green]Selected: English / International[/green]")
            console.print("[dim]Models will download from HuggingFace, voice recognition defaults to English[/dim]\n")

    def _create_directories(self):
        """创建必要的目录结构"""
        console.print("[bold cyan]Step 2: Creating Directory Structure[/bold cyan]\n")

        directories = [
            ("data", "Database and cache"),
            ("identity", "Agent identity files"),
            ("skills", "Downloaded skills"),
            ("logs", "Log files"),
        ]

        for dir_name, description in directories:
            dir_path = self.project_dir / dir_name
            dir_path.mkdir(exist_ok=True)

            # 创建 .gitkeep
            gitkeep = dir_path / ".gitkeep"
            if not gitkeep.exists():
                gitkeep.touch()

            console.print(f"  [green]✓[/green] {dir_name}/ - {description}")

        console.print("\n[green]Directories created![/green]\n")

    def _configure_llm(self):
        """配置 LLM API"""
        console.print("[bold cyan]Step 3: Configure LLM API[/bold cyan]\n")

        # 选择 API 类型
        console.print("Which LLM API would you like to use?\n")
        console.print("  [1] Anthropic Claude (recommended)")
        console.print("  [2] OpenAI-compatible API")
        console.print("  [3] Other provider\n")

        choice = Prompt.ask("Select option", choices=["1", "2", "3"], default="1")

        if choice == "1":
            self._configure_anthropic()
        elif choice == "2":
            self._configure_openai_compatible()
        else:
            self._configure_custom_provider()

        # 选择默认模型
        console.print("\n[bold]Select default model:[/bold]\n")

        models = [
            ("claude-sonnet-4-20250514", "Claude Sonnet 4 - Balanced (default)"),
            ("claude-opus-4-5-20250514", "Claude Opus 4.5 - Most capable"),
            ("claude-opus-4-5-20251101-thinking", "Claude Opus 4.5 + Extended Thinking"),
            ("gpt-4o", "GPT-4o (OpenAI)"),
            ("qwen3-max", "Qwen3 Max (Alibaba)"),
            ("custom", "Enter custom model name"),
        ]

        for i, (_model, desc) in enumerate(models, 1):
            console.print(f"  [{i}] {desc}")

        model_choice = Prompt.ask(
            "\nSelect model", choices=[str(i) for i in range(1, len(models) + 1)], default="1"
        )

        idx = int(model_choice) - 1
        if models[idx][0] == "custom":
            self.config["DEFAULT_MODEL"] = Prompt.ask("Enter model name")
        else:
            self.config["DEFAULT_MODEL"] = models[idx][0]

        # Extended Thinking 模式
        if "thinking" in self.config.get("DEFAULT_MODEL", "").lower():
            self.config["THINKING_MODE"] = "always"
        else:
            use_thinking = Confirm.ask(
                "\nEnable extended thinking mode for complex tasks?", default=True
            )
            self.config["THINKING_MODE"] = "auto" if use_thinking else "never"

        console.print("\n[green]LLM configuration complete![/green]\n")

    def _configure_anthropic(self):
        """配置 Anthropic API"""
        console.print("\n[bold]Anthropic Claude Configuration[/bold]\n")

        # API Key
        api_key = Prompt.ask("Enter your Anthropic API Key", password=True)
        self.config["ANTHROPIC_API_KEY"] = api_key

        # Base URL (可选)
        use_proxy = Confirm.ask("Use a custom API endpoint (proxy/mirror)?", default=False)

        if use_proxy:
            base_url = Prompt.ask("Enter API Base URL", default="https://api.anthropic.com")
            self.config["ANTHROPIC_BASE_URL"] = base_url
        else:
            self.config["ANTHROPIC_BASE_URL"] = "https://api.anthropic.com"

    def _configure_openai_compatible(self):
        """配置 OpenAI 兼容 API"""
        console.print("\n[bold]OpenAI-compatible API Configuration[/bold]\n")

        # 常见提供商
        console.print("Common providers:")
        console.print("  - OpenAI: https://api.openai.com/v1")
        console.print("  - DashScope: https://dashscope.aliyuncs.com/compatible-mode/v1")
        console.print("  - DeepSeek: https://api.deepseek.com/v1")
        console.print("  - Moonshot: https://api.moonshot.cn/v1")
        console.print("  - 智谱 AI (国内): https://open.bigmodel.cn/api/paas/v4")
        console.print("  - Zhipu AI (国际): https://api.z.ai/api/paas/v4\n")

        base_url = Prompt.ask("Enter API Base URL", default="https://api.openai.com/v1")
        self.config["ANTHROPIC_BASE_URL"] = base_url

        api_key = Prompt.ask("Enter your API Key", password=True)
        self.config["ANTHROPIC_API_KEY"] = api_key

    def _configure_custom_provider(self):
        """配置自定义提供商"""
        console.print("\n[bold]Custom Provider Configuration[/bold]\n")

        base_url = Prompt.ask("Enter API Base URL")
        self.config["ANTHROPIC_BASE_URL"] = base_url

        api_key = Prompt.ask("Enter your API Key", password=True)
        self.config["ANTHROPIC_API_KEY"] = api_key

    def _configure_compiler(self):
        """配置 Prompt Compiler 专用模型（可选）"""
        console.print("[bold cyan]Step 3b: Configure Prompt Compiler Model (Optional)[/bold cyan]\n")

        console.print(
            "Prompt Compiler 使用快速小模型对用户指令做预处理，可大幅降低响应延迟。\n"
            "建议使用 qwen-turbo、gpt-4o-mini 等低延迟模型，不需要启用思考模式。\n"
            "如果跳过此步，系统运行时会自动回退到主模型（速度较慢）。\n"
        )

        configure = Confirm.ask("Configure Prompt Compiler?", default=True)

        if not configure:
            console.print("[dim]Skipping Compiler configuration (will use main model as fallback).[/dim]\n")
            return

        # 选择 Provider
        console.print("\nSelect provider for Compiler:\n")
        console.print("  [1] DashScope (qwen-turbo-latest, recommended)")
        console.print("  [2] OpenAI-compatible")
        console.print("  [3] Same provider as main model")
        console.print("  [4] Skip\n")

        choice = Prompt.ask("Select option", choices=["1", "2", "3", "4"], default="1")

        if choice == "4":
            console.print("[dim]Skipping Compiler configuration.[/dim]\n")
            return

        compiler_config: dict = {}

        if choice == "1":
            compiler_config["provider"] = "dashscope"
            compiler_config["api_type"] = "openai"
            compiler_config["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            compiler_config["api_key_env"] = "DASHSCOPE_API_KEY"
            compiler_config["model"] = Prompt.ask(
                "Model name", default="qwen-turbo-latest"
            )
            # 检查是否需要单独配置 API Key
            existing_key = self.config.get("DASHSCOPE_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
            if not existing_key:
                api_key = Prompt.ask("Enter DashScope API Key", password=True)
                self.config["DASHSCOPE_API_KEY"] = api_key
        elif choice == "2":
            console.print("\nCommon fast models:")
            console.print("  - qwen-turbo-latest (DashScope)")
            console.print("  - gpt-4o-mini (OpenAI)")
            console.print("  - deepseek-chat (DeepSeek)\n")

            compiler_config["provider"] = "openai-compatible"
            compiler_config["api_type"] = "openai"
            compiler_config["base_url"] = Prompt.ask(
                "API Base URL", default="https://api.openai.com/v1"
            )
            compiler_config["api_key_env"] = Prompt.ask(
                "API Key env var name", default="COMPILER_API_KEY"
            )
            api_key = Prompt.ask("Enter API Key", password=True)
            self.config[compiler_config["api_key_env"]] = api_key
            compiler_config["model"] = Prompt.ask("Model name", default="gpt-4o-mini")
        elif choice == "3":
            # 复用主模型的 provider 配置
            compiler_config["provider"] = "same-as-main"
            compiler_config["api_type"] = "openai"
            compiler_config["base_url"] = self.config.get(
                "ANTHROPIC_BASE_URL", "https://api.anthropic.com"
            )
            compiler_config["api_key_env"] = "ANTHROPIC_API_KEY"
            compiler_config["model"] = Prompt.ask(
                "Model name (use a faster/cheaper variant)",
                default="gpt-4o-mini",
            )

        self.config["_compiler_primary"] = compiler_config

        # 是否添加备用端点
        add_backup = Confirm.ask("\nAdd a backup Compiler endpoint?", default=False)

        if add_backup:
            console.print("\nBackup Compiler endpoint:\n")
            backup_config: dict = {}
            backup_config["api_type"] = "openai"
            backup_config["base_url"] = Prompt.ask(
                "API Base URL", default=compiler_config.get("base_url", "")
            )
            backup_config["api_key_env"] = Prompt.ask(
                "API Key env var name", default=compiler_config.get("api_key_env", "")
            )
            # 如果 env var 不同于主 compiler，需要设置 key
            if backup_config["api_key_env"] != compiler_config.get("api_key_env"):
                api_key = Prompt.ask("Enter API Key", password=True)
                self.config[backup_config["api_key_env"]] = api_key
            backup_config["provider"] = Prompt.ask(
                "Provider name", default=compiler_config.get("provider", "openai-compatible")
            )
            backup_config["model"] = Prompt.ask("Model name", default="qwen-plus-latest")
            self.config["_compiler_backup"] = backup_config

        console.print("\n[green]Prompt Compiler configuration complete![/green]\n")

    def _write_llm_endpoints(self):
        """将主模型和 Compiler 端点配置写入 data/llm_endpoints.json"""
        endpoints_path = self.project_dir / "data" / "llm_endpoints.json"

        # 如果文件已存在，读取现有内容以保留用户手动编辑的部分
        existing_data: dict = {}
        if endpoints_path.exists():
            try:
                existing_data = json.loads(endpoints_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # 构建主端点（如果现有配置中没有的话）
        if not existing_data.get("endpoints"):
            api_key_env = "ANTHROPIC_API_KEY"
            base_url = self.config.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
            model = self.config.get("DEFAULT_MODEL", "claude-sonnet-4-20250514")

            # 判断 api_type
            api_type = "anthropic" if "anthropic.com" in base_url else "openai"
            provider = "anthropic" if api_type == "anthropic" else "openai-compatible"

            # 从模型名自动推断能力（而非硬编码）
            from openakita.llm.capabilities import (
                get_provider_slug_from_base_url,
                infer_capabilities,
            )
            provider_slug = get_provider_slug_from_base_url(base_url) or provider
            caps = infer_capabilities(model, provider_slug=provider_slug)
            capabilities = [k for k, v in caps.items() if v and k != "thinking_only"]
            if not capabilities:
                capabilities = ["text", "tools"]

            existing_data["endpoints"] = [
                {
                    "name": "primary",
                    "provider": provider,
                    "api_type": api_type,
                    "base_url": base_url,
                    "api_key_env": api_key_env,
                    "model": model,
                    "priority": 1,
                    "max_tokens": int(self.config.get("MAX_TOKENS", "0")),
                    "timeout": 180,
                    "capabilities": capabilities,
                }
            ]

        # 构建 Compiler 端点
        compiler_endpoints = []

        primary_cfg = self.config.get("_compiler_primary")
        if primary_cfg:
            compiler_endpoints.append({
                "name": "compiler-primary",
                "provider": primary_cfg.get("provider", "openai-compatible"),
                "api_type": primary_cfg.get("api_type", "openai"),
                "base_url": primary_cfg.get("base_url", ""),
                "api_key_env": primary_cfg.get("api_key_env", ""),
                "model": primary_cfg.get("model", ""),
                "priority": 1,
                "max_tokens": 2048,
                "timeout": 30,
                "capabilities": ["text"],
                "note": "Prompt Compiler 主端点（快速模型，不启用思考）",
            })

        backup_cfg = self.config.get("_compiler_backup")
        if backup_cfg:
            compiler_endpoints.append({
                "name": "compiler-backup",
                "provider": backup_cfg.get("provider", "openai-compatible"),
                "api_type": backup_cfg.get("api_type", "openai"),
                "base_url": backup_cfg.get("base_url", ""),
                "api_key_env": backup_cfg.get("api_key_env", ""),
                "model": backup_cfg.get("model", ""),
                "priority": 2,
                "max_tokens": 2048,
                "timeout": 30,
                "capabilities": ["text"],
                "note": "Prompt Compiler 备用端点",
            })

        if compiler_endpoints:
            existing_data["compiler_endpoints"] = compiler_endpoints

        # 确保 settings 存在
        if not existing_data.get("settings"):
            existing_data["settings"] = {
                "retry_count": 2,
                "retry_delay_seconds": 2,
                "health_check_interval": 60,
                "fallback_on_error": True,
            }

        # 写入文件
        endpoints_path.parent.mkdir(parents=True, exist_ok=True)
        endpoints_path.write_text(
            json.dumps(existing_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"  [green]✓[/green] LLM endpoints saved to {endpoints_path}")

    def _configure_im_channels(self):
        """配置 IM 通道"""
        console.print("[bold cyan]Step 4: Configure IM Channels (Optional)[/bold cyan]\n")

        setup_im = Confirm.ask(
            "Would you like to set up an IM channel (Telegram, etc.)?", default=False
        )

        if not setup_im:
            console.print("[dim]Skipping IM channel configuration.[/dim]\n")
            return

        # 选择通道
        console.print("\nAvailable channels:\n")
        console.print("  [1] Telegram (recommended)")
        console.print("  [2] Feishu (Lark)")
        console.print("  [3] WeCom (企业微信)")
        console.print("  [4] DingTalk (钉钉)")
        console.print("  [5] OneBot (NapCat / Lagrange 等)")
        console.print("  [6] QQ 官方机器人")
        console.print("  [7] Skip\n")

        choice = Prompt.ask("Select channel", choices=["1", "2", "3", "4", "5", "6", "7"], default="7")

        if choice == "1":
            self._configure_telegram()
        elif choice == "2":
            self._configure_feishu()
        elif choice == "3":
            self._configure_wework()
        elif choice == "4":
            self._configure_dingtalk()
        elif choice == "5":
            self._configure_onebot()
        elif choice == "6":
            self._configure_qqbot()

        console.print("\n[green]IM channel configuration complete![/green]\n")

    def _configure_telegram(self):
        """配置 Telegram"""
        console.print("\n[bold]Telegram Bot Configuration[/bold]\n")
        console.print("To create a bot, message @BotFather on Telegram and use /newbot\n")

        token = Prompt.ask("Enter your Bot Token", password=True)
        self.config["TELEGRAM_ENABLED"] = "true"
        self.config["TELEGRAM_BOT_TOKEN"] = token

        use_pairing = Confirm.ask("Require pairing code for new users?", default=True)
        self.config["TELEGRAM_REQUIRE_PAIRING"] = "true" if use_pairing else "false"

        # Webhook（可选）
        webhook_url = Prompt.ask(
            "Webhook URL (leave empty for long-polling)", default=""
        )
        if webhook_url:
            self.config["TELEGRAM_WEBHOOK_URL"] = webhook_url

        # 代理配置（大陆用户常用）
        use_proxy = Confirm.ask("Use a proxy for Telegram? (recommended in mainland China)", default=False)
        if use_proxy:
            proxy = Prompt.ask(
                "Enter proxy URL",
                default="http://127.0.0.1:7890",
            )
            self.config["TELEGRAM_PROXY"] = proxy

    def _configure_feishu(self):
        """配置飞书"""
        console.print("\n[bold]Feishu (Lark) Configuration[/bold]\n")

        app_id = Prompt.ask("Enter App ID")
        app_secret = Prompt.ask("Enter App Secret", password=True)

        self.config["FEISHU_ENABLED"] = "true"
        self.config["FEISHU_APP_ID"] = app_id
        self.config["FEISHU_APP_SECRET"] = app_secret

    def _configure_wework(self):
        """配置企业微信"""
        console.print("\n[bold]WeCom Configuration[/bold]\n")
        console.print("Note: WeCom callback requires a public URL (use ngrok/frp/cpolar)\n")

        corp_id = Prompt.ask("Enter Corp ID")

        self.config["WEWORK_ENABLED"] = "true"
        self.config["WEWORK_CORP_ID"] = corp_id

        # 回调加解密配置（智能机器人必填）
        console.print("\n[bold]Callback Configuration (required for Smart Bot):[/bold]\n")
        console.print("Get these from WeCom admin -> Smart Bot -> Receive Messages settings\n")

        token = Prompt.ask("Enter callback Token")
        if token:
            self.config["WEWORK_TOKEN"] = token

        aes_key = Prompt.ask("Enter EncodingAESKey")
        if aes_key:
            self.config["WEWORK_ENCODING_AES_KEY"] = aes_key

        port = Prompt.ask("Callback port", default="9880")
        if port != "9880":
            self.config["WEWORK_CALLBACK_PORT"] = port

        host = Prompt.ask("Callback bind host", default="0.0.0.0")
        if host != "0.0.0.0":
            self.config["WEWORK_CALLBACK_HOST"] = host

    def _configure_dingtalk(self):
        """配置钉钉"""
        console.print("\n[bold]DingTalk Configuration[/bold]\n")

        app_key = Prompt.ask("Enter App Key")
        app_secret = Prompt.ask("Enter App Secret", password=True)

        self.config["DINGTALK_ENABLED"] = "true"
        self.config["DINGTALK_CLIENT_ID"] = app_key
        self.config["DINGTALK_CLIENT_SECRET"] = app_secret

    def _configure_onebot(self):
        """配置 OneBot 协议通道"""
        console.print("\n[bold]OneBot Configuration[/bold]\n")
        console.print("OneBot 通道需要先部署 NapCat / Lagrange 等 OneBot 实现端\n")
        console.print("参考: https://github.com/botuniverse/onebot-11\n")

        onebot_url = Prompt.ask(
            "Enter OneBot WebSocket URL",
            default="ws://127.0.0.1:8080",
        )

        access_token = Prompt.ask(
            "Enter Access Token (leave empty if not set)",
            default="",
            password=True,
        )

        self.config["ONEBOT_ENABLED"] = "true"
        self.config["ONEBOT_WS_URL"] = onebot_url
        if access_token:
            self.config["ONEBOT_ACCESS_TOKEN"] = access_token

    def _configure_qqbot(self):
        """配置 QQ 官方机器人"""
        console.print("\n[bold]QQ 官方机器人 Configuration[/bold]\n")
        console.print("请前往 QQ 开放平台 (https://q.qq.com) 创建机器人并获取凭据\n")

        app_id = Prompt.ask("Enter AppID")
        app_secret = Prompt.ask("Enter AppSecret", password=True)

        self.config["QQBOT_ENABLED"] = "true"
        self.config["QQBOT_APP_ID"] = app_id
        self.config["QQBOT_APP_SECRET"] = app_secret

        use_sandbox = Confirm.ask("Enable sandbox mode (测试环境)?", default=True)
        self.config["QQBOT_SANDBOX"] = "true" if use_sandbox else "false"

        # 接入模式
        console.print("\nAccess mode:\n")
        console.print("  [1] WebSocket (default, no public IP needed)")
        console.print("  [2] Webhook (requires public IP/domain)\n")
        mode_choice = Prompt.ask("Select mode", choices=["1", "2"], default="1")
        if mode_choice == "2":
            self.config["QQBOT_MODE"] = "webhook"
            port = Prompt.ask("Webhook port", default="9890")
            self.config["QQBOT_WEBHOOK_PORT"] = port
            path = Prompt.ask("Webhook path", default="/qqbot/callback")
            self.config["QQBOT_WEBHOOK_PATH"] = path
        else:
            self.config["QQBOT_MODE"] = "websocket"

    def _configure_memory(self):
        """配置记忆系统"""
        console.print("[bold cyan]Step 5: Configure Memory System[/bold cyan]\n")

        console.print("OpenAkita uses vector embeddings for semantic memory search.\n")

        # 根据 locale 推导默认选项
        defaults = getattr(self, "_defaults", {})
        default_embed = defaults.get("EMBEDDING_MODEL", "shibing624/text2vec-base-chinese")
        default_src = defaults.get("MODEL_DOWNLOAD_SOURCE", "auto")

        # Embedding 模型选择
        models_list = [
            ("1", "shibing624/text2vec-base-chinese", "Chinese optimized (~100MB)"),
            ("2", "sentence-transformers/all-MiniLM-L6-v2", "English optimized (~90MB)"),
            ("3", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "Multilingual (~120MB)"),
        ]
        # 找到默认选项的序号
        default_model_choice = "1"
        for num, model_id, _ in models_list:
            if model_id == default_embed:
                default_model_choice = num
                break

        console.print("Embedding model options:\n")
        for num, _model_id, desc in models_list:
            marker = " ← recommended" if num == default_model_choice else ""
            console.print(f"  [{num}] {desc}{marker}")
        console.print()

        choice = Prompt.ask(
            "Select embedding model",
            choices=["1", "2", "3"],
            default=default_model_choice,
        )
        self.config["EMBEDDING_MODEL"] = {n: m for n, m, _ in models_list}[choice]

        # GPU 加速
        use_gpu = Confirm.ask("Use GPU for embeddings (requires CUDA)?", default=False)
        self.config["EMBEDDING_DEVICE"] = "cuda" if use_gpu else "cpu"

        # 模型下载源
        src_options = [
            ("1", "auto", "Auto (自动选择最快的源)"),
            ("2", "hf-mirror", "hf-mirror (HuggingFace 国内镜像)"),
            ("3", "modelscope", "ModelScope (魔搭社区)"),
            ("4", "huggingface", "HuggingFace (官方源)"),
        ]
        # 根据 locale 推导默认选项
        _src_to_num = {s: n for n, s, _ in src_options}
        default_src_choice = _src_to_num.get(default_src, "1")

        console.print("\nModel download source:\n")
        for num, _, desc in src_options:
            marker = " ← recommended" if num == default_src_choice else ""
            console.print(f"  [{num}] {desc}{marker}")
        console.print()

        src_choice = Prompt.ask(
            "Select download source",
            choices=["1", "2", "3", "4"],
            default=default_src_choice,
        )
        self.config["MODEL_DOWNLOAD_SOURCE"] = {n: s for n, s, _ in src_options}[src_choice]

        console.print("\n[green]Memory configuration complete![/green]\n")

    def _configure_voice(self):
        """配置语音识别 (Whisper)"""
        console.print("[bold cyan]Step 5b: Voice Recognition (Optional)[/bold cyan]\n")

        use_voice = Confirm.ask("Enable local voice recognition (Whisper)?", default=True)
        if not use_voice:
            self.config.setdefault("WHISPER_MODEL", "base")
            self.config.setdefault("WHISPER_LANGUAGE", getattr(self, "_defaults", {}).get("WHISPER_LANGUAGE", "zh"))
            console.print("[dim]Voice will be configured with defaults, model downloads on first use.[/dim]\n")
            return

        defaults = getattr(self, "_defaults", {})
        default_lang = defaults.get("WHISPER_LANGUAGE", "zh")

        # 语言选择
        console.print("Voice recognition language:\n")
        lang_options = [
            ("1", "zh", "中文 (Chinese)"),
            ("2", "en", "English (uses smaller, faster .en model)"),
            ("3", "auto", "Auto-detect language"),
        ]
        default_lang_choice = {"zh": "1", "en": "2", "auto": "3"}.get(default_lang, "1")

        for num, _, desc in lang_options:
            marker = " ← recommended" if num == default_lang_choice else ""
            console.print(f"  [{num}] {desc}{marker}")
        console.print()

        lang_choice = Prompt.ask(
            "Select voice language",
            choices=["1", "2", "3"],
            default=default_lang_choice,
        )
        whisper_lang = {n: code for n, code, _ in lang_options}[lang_choice]
        self.config["WHISPER_LANGUAGE"] = whisper_lang

        # 模型大小选择
        console.print("\nWhisper model size:\n")
        model_options = [
            ("1", "tiny", "Tiny (~39MB)  - fastest, lower accuracy"),
            ("2", "base", "Base (~74MB)  - recommended, balanced"),
            ("3", "small", "Small (~244MB) - good accuracy"),
            ("4", "medium", "Medium (~769MB) - high accuracy"),
            ("5", "large", "Large (~1.5GB) - highest accuracy, resource-heavy"),
        ]
        # 英语时 .en 模型更小，提示用户
        if whisper_lang == "en":
            console.print("[dim]  Note: English .en models are auto-selected and are more efficient[/dim]\n")

        model_choice = Prompt.ask(
            "Select model size",
            choices=["1", "2", "3", "4", "5"],
            default="2",
        )
        self.config["WHISPER_MODEL"] = {n: m for n, m, _ in model_options}[model_choice]

        console.print("\n[green]Voice configuration complete![/green]\n")

    def _configure_advanced(self):
        """高级配置"""
        console.print("[bold cyan]Step 6: Advanced Configuration (Optional)[/bold cyan]\n")

        configure_advanced = Confirm.ask("Configure advanced options?", default=False)

        if not configure_advanced:
            # 使用默认值
            self.config.setdefault("MAX_TOKENS", "0")
            self.config.setdefault("MAX_ITERATIONS", "300")
            self.config.setdefault("LOG_LEVEL", "INFO")
            console.print("[dim]Using default advanced settings.[/dim]\n")
            return

        # Max tokens
        max_tokens = Prompt.ask("Max output tokens (0=不限制)", default="0")
        self.config["MAX_TOKENS"] = max_tokens

        # Max iterations
        max_iter = Prompt.ask("Max iterations per task", default="100")
        self.config["MAX_ITERATIONS"] = max_iter

        # Log level
        log_level = Prompt.ask(
            "Log level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO"
        )
        self.config["LOG_LEVEL"] = log_level

        # Persona
        persona = Prompt.ask(
            "Persona preset (role personality)",
            choices=["default", "business", "tech_expert", "butler", "girlfriend", "boyfriend", "family", "jarvis"],
            default="default",
        )
        if persona != "default":
            self.config["PERSONA_NAME"] = persona

        # Sticker (表情包)
        use_sticker = Confirm.ask("Enable sticker (emoji packs) in IM?", default=True)
        self.config["STICKER_ENABLED"] = "true" if use_sticker else "false"

        # Proactive (living presence)
        use_proactive = Confirm.ask("Enable living-presence mode? (proactive greetings & follow-ups)", default=False)
        if use_proactive:
            self.config["PROACTIVE_ENABLED"] = "true"
            max_daily = Prompt.ask("  Max daily proactive messages", default="3")
            self.config["PROACTIVE_MAX_DAILY_MESSAGES"] = max_daily
            min_interval = Prompt.ask("  Min interval between messages (minutes)", default="120")
            self.config["PROACTIVE_MIN_INTERVAL_MINUTES"] = min_interval
            quiet_start = Prompt.ask("  Quiet hours start (0-23)", default="23")
            self.config["PROACTIVE_QUIET_HOURS_START"] = quiet_start
            quiet_end = Prompt.ask("  Quiet hours end (0-23)", default="7")
            self.config["PROACTIVE_QUIET_HOURS_END"] = quiet_end

        # Scheduler (调度器)
        console.print("\n[bold]Scheduler Configuration:[/bold]")
        use_scheduler = Confirm.ask("Enable task scheduler? (recommended)", default=True)
        self.config["SCHEDULER_ENABLED"] = "true" if use_scheduler else "false"
        if use_scheduler:
            defaults = getattr(self, "_defaults", {})
            tz = Prompt.ask("  Timezone", default=defaults.get("SCHEDULER_TIMEZONE", "Asia/Shanghai"))
            self.config["SCHEDULER_TIMEZONE"] = tz

        # Session (会话)
        console.print("\n[bold]Session Configuration:[/bold]")
        session_timeout = Prompt.ask("Session timeout (minutes)", default="30")
        self.config["SESSION_TIMEOUT_MINUTES"] = session_timeout
        session_history = Prompt.ask("Max session history messages", default="50")
        self.config["SESSION_MAX_HISTORY"] = session_history

        # Network proxy
        console.print("\n[bold]Network Proxy (optional):[/bold]")
        use_proxy = Confirm.ask("Configure network proxy?", default=False)
        if use_proxy:
            http_proxy = Prompt.ask("HTTP_PROXY", default="http://127.0.0.1:7890")
            self.config["HTTP_PROXY"] = http_proxy
            self.config["HTTPS_PROXY"] = http_proxy

        # GitHub token
        console.print("\n[bold]GitHub Token (optional):[/bold]")
        console.print("Used for downloading skills and GitHub API access\n")
        github_token = Prompt.ask("Enter GitHub Token (leave empty to skip)", default="", password=True)
        if github_token:
            self.config["GITHUB_TOKEN"] = github_token

        # Multi-agent
        use_multi = Confirm.ask("\nEnable multi-agent orchestration?", default=False)
        if use_multi:
            self.config["ORCHESTRATION_ENABLED"] = "true"
            mode = Prompt.ask(
                "  Orchestration mode",
                choices=["single", "handoff", "master-worker"],
                default="single",
            )
            self.config["ORCHESTRATION_MODE"] = mode

        console.print("\n[green]Advanced configuration complete![/green]\n")

    def _write_env_file(self):
        """写入 .env 文件"""
        console.print("[bold cyan]Step 7: Saving Configuration[/bold cyan]\n")

        # 检查是否已存在
        if self.env_path.exists():
            overwrite = Confirm.ask(
                f".env file already exists at {self.env_path}. Overwrite?", default=True
            )
            if not overwrite:
                console.print("  [dim]Keeping existing .env file.[/dim]")
                console.print("  [dim]New configuration saved to .env.new for reference.[/dim]")
                # 将新配置写入 .env.new 供参考
                env_content = self._generate_env_content()
                new_path = self.env_path.parent / ".env.new"
                new_path.write_text(env_content, encoding="utf-8")
                console.print(f"  [green]✓[/green] Reference config saved to {new_path}")
                # 继续写入 llm_endpoints
                self._write_llm_endpoints()
                return

        # 构建 .env 内容
        env_content = self._generate_env_content()

        # 写入文件
        self.env_path.write_text(env_content, encoding="utf-8")
        console.print(f"  [green]✓[/green] Configuration saved to {self.env_path}")

        # 写入 llm_endpoints.json（主模型端点 + Compiler 端点）
        self._write_llm_endpoints()

        # 创建 identity 示例文件
        self._create_identity_examples()

        console.print("\n[green]Configuration saved![/green]\n")

    def _generate_env_content(self) -> str:
        """生成 .env 文件内容"""
        lines = [
            "# OpenAkita Configuration",
            "# Generated by setup wizard",
            "",
            "# ========== LLM API ==========",
            f"ANTHROPIC_API_KEY={self.config.get('ANTHROPIC_API_KEY', '')}",
            f"ANTHROPIC_BASE_URL={self.config.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com')}",
            "",
            "# ========== Model Configuration ==========",
            f"DEFAULT_MODEL={self.config.get('DEFAULT_MODEL', 'claude-sonnet-4-20250514')}",
            f"MAX_TOKENS={self.config.get('MAX_TOKENS', '0')}",
            f"THINKING_MODE={self.config.get('THINKING_MODE', 'auto')}",
        ]

        lines.extend([
            "",
            "# ========== Agent Configuration ==========",
            "AGENT_NAME=OpenAkita",
            f"MAX_ITERATIONS={self.config.get('MAX_ITERATIONS', '300')}  # ReAct 循环最大迭代次数",
            "AUTO_CONFIRM=false  # 工具调用是否自动确认（无需人工审批）",
            "SELFCHECK_AUTOFIX=true  # Agent 自检发现问题后是否自动修复",
            "FORCE_TOOL_CALL_MAX_RETRIES=1  # LLM 未返回工具调用时的强制重试次数",
            "TOOL_MAX_PARALLEL=1  # 并行工具调用最大数量",
            "# ALLOW_PARALLEL_TOOLS_WITH_INTERRUPT_CHECKS=false",
            "",
            "# ========== Timeout ==========",
            "PROGRESS_TIMEOUT_SECONDS=600  # 任务无进展超时（秒），0=不限",
            "HARD_TIMEOUT_SECONDS=0  # 任务硬超时（秒），0=不限",
            "",
            "# ========== Paths & Logging ==========",
            "DATABASE_PATH=data/agent.db",
            f"LOG_LEVEL={self.config.get('LOG_LEVEL', 'INFO')}",
            "LOG_DIR=logs  # 日志文件目录",
            "LOG_FILE_PREFIX=openakita  # 日志文件名前缀",
            "LOG_MAX_SIZE_MB=10  # 单个日志文件最大大小（MB）",
            "LOG_BACKUP_COUNT=30  # 日志文件保留份数",
            "LOG_RETENTION_DAYS=30  # 日志文件保留天数",
            "LOG_TO_CONSOLE=true  # 是否输出到控制台",
            "LOG_TO_FILE=true  # 是否写入文件",
            "# LOG_FORMAT=%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "",
            "# ========== Tools ==========",
            "MCP_ENABLED=true  # 启用 MCP 工具服务器",
            "MCP_BROWSER_ENABLED=true  # 启用浏览器自动化",
            "DESKTOP_ENABLED=true  # 启用桌面自动化（截屏/键鼠）",
            "",
        ])

        # 网络代理
        if self.config.get("HTTP_PROXY") or self.config.get("HTTPS_PROXY"):
            lines.extend([
                "# ========== Network Proxy ==========",
                f"HTTP_PROXY={self.config.get('HTTP_PROXY', '')}",
                f"HTTPS_PROXY={self.config.get('HTTPS_PROXY', '')}",
                "# ALL_PROXY=",
                "# FORCE_IPV4=false",
                "",
            ])
        else:
            lines.extend([
                "# ========== Network Proxy (optional) ==========",
                "# HTTP_PROXY=http://127.0.0.1:7890",
                "# HTTPS_PROXY=http://127.0.0.1:7890",
                "# ALL_PROXY=socks5://127.0.0.1:1080",
                "# FORCE_IPV4=false",
                "",
            ])

        # GitHub Token
        if self.config.get("GITHUB_TOKEN"):
            lines.extend([
                "# ========== GitHub Token ==========",
                f"GITHUB_TOKEN={self.config['GITHUB_TOKEN']}",
                "",
            ])
        else:
            lines.extend([
                "# ========== GitHub Token (optional) ==========",
                "# GITHUB_TOKEN=",
                "",
            ])

        # Whisper
        whisper_lang = self.config.get("WHISPER_LANGUAGE", "zh")
        lines.extend([
            "# ========== Voice (optional) ==========",
            f"WHISPER_MODEL={self.config.get('WHISPER_MODEL', 'base')}",
            f"WHISPER_LANGUAGE={whisper_lang}",
            "",
        ])

        # IM 通道配置
        lines.append("# ========== IM Channels ==========")

        if self.config.get("TELEGRAM_ENABLED"):
            lines.extend([
                f"TELEGRAM_ENABLED={self.config.get('TELEGRAM_ENABLED', 'false')}",
                f"TELEGRAM_BOT_TOKEN={self.config.get('TELEGRAM_BOT_TOKEN', '')}",
                f"TELEGRAM_REQUIRE_PAIRING={self.config.get('TELEGRAM_REQUIRE_PAIRING', 'true')}",
            ])
            if self.config.get("TELEGRAM_WEBHOOK_URL"):
                lines.append(f"TELEGRAM_WEBHOOK_URL={self.config['TELEGRAM_WEBHOOK_URL']}")
            else:
                lines.append("# TELEGRAM_WEBHOOK_URL=")
            lines.append("# TELEGRAM_PAIRING_CODE=")
            if self.config.get("TELEGRAM_PROXY"):
                lines.append(f"TELEGRAM_PROXY={self.config['TELEGRAM_PROXY']}")
            else:
                lines.append("# TELEGRAM_PROXY=")
        else:
            lines.extend([
                "TELEGRAM_ENABLED=false",
                "# TELEGRAM_BOT_TOKEN=",
                "# TELEGRAM_WEBHOOK_URL=",
                "# TELEGRAM_PAIRING_CODE=",
                "# TELEGRAM_PROXY=",
            ])
        lines.append("")

        if self.config.get("FEISHU_ENABLED"):
            lines.extend([
                f"FEISHU_ENABLED={self.config.get('FEISHU_ENABLED', 'false')}",
                f"FEISHU_APP_ID={self.config.get('FEISHU_APP_ID', '')}",
                f"FEISHU_APP_SECRET={self.config.get('FEISHU_APP_SECRET', '')}",
            ])
        else:
            lines.extend([
                "FEISHU_ENABLED=false",
                "# FEISHU_APP_ID=",
                "# FEISHU_APP_SECRET=",
            ])
        lines.append("")

        if self.config.get("WEWORK_ENABLED"):
            lines.extend([
                f"WEWORK_ENABLED={self.config.get('WEWORK_ENABLED', 'false')}",
                f"WEWORK_CORP_ID={self.config.get('WEWORK_CORP_ID', '')}",
                f"WEWORK_TOKEN={self.config.get('WEWORK_TOKEN', '')}",
                f"WEWORK_ENCODING_AES_KEY={self.config.get('WEWORK_ENCODING_AES_KEY', '')}",
                f"WEWORK_CALLBACK_PORT={self.config.get('WEWORK_CALLBACK_PORT', '9880')}",
                f"WEWORK_CALLBACK_HOST={self.config.get('WEWORK_CALLBACK_HOST', '0.0.0.0')}",
            ])
        else:
            lines.extend([
                "WEWORK_ENABLED=false",
                "# WEWORK_CORP_ID=",
                "# WEWORK_TOKEN=",
                "# WEWORK_ENCODING_AES_KEY=",
                "# WEWORK_CALLBACK_PORT=9880",
                "# WEWORK_CALLBACK_HOST=0.0.0.0",
            ])
        lines.append("")

        if self.config.get("DINGTALK_ENABLED"):
            lines.extend([
                f"DINGTALK_ENABLED={self.config.get('DINGTALK_ENABLED', 'false')}",
                f"DINGTALK_CLIENT_ID={self.config.get('DINGTALK_CLIENT_ID', '')}",
                f"DINGTALK_CLIENT_SECRET={self.config.get('DINGTALK_CLIENT_SECRET', '')}",
            ])
        else:
            lines.extend([
                "DINGTALK_ENABLED=false",
                "# DINGTALK_CLIENT_ID=",
                "# DINGTALK_CLIENT_SECRET=",
            ])
        lines.append("")

        if self.config.get("ONEBOT_ENABLED"):
            lines.extend([
                f"ONEBOT_ENABLED={self.config.get('ONEBOT_ENABLED', 'false')}",
                f"ONEBOT_WS_URL={self.config.get('ONEBOT_WS_URL', 'ws://127.0.0.1:8080')}",
                f"ONEBOT_ACCESS_TOKEN={self.config.get('ONEBOT_ACCESS_TOKEN', '')}",
            ])
        else:
            lines.extend([
                "ONEBOT_ENABLED=false",
                "# ONEBOT_WS_URL=ws://127.0.0.1:8080",
                "# ONEBOT_ACCESS_TOKEN=",
            ])
        lines.append("")

        if self.config.get("QQBOT_ENABLED"):
            lines.extend([
                f"QQBOT_ENABLED={self.config.get('QQBOT_ENABLED', 'false')}",
                f"QQBOT_APP_ID={self.config.get('QQBOT_APP_ID', '')}",
                f"QQBOT_APP_SECRET={self.config.get('QQBOT_APP_SECRET', '')}",
                f"QQBOT_SANDBOX={self.config.get('QQBOT_SANDBOX', 'true')}",
                f"QQBOT_MODE={self.config.get('QQBOT_MODE', 'websocket')}",
            ])
            if self.config.get("QQBOT_MODE") == "webhook":
                lines.append(f"QQBOT_WEBHOOK_PORT={self.config.get('QQBOT_WEBHOOK_PORT', '9890')}")
                lines.append(f"QQBOT_WEBHOOK_PATH={self.config.get('QQBOT_WEBHOOK_PATH', '/qqbot/callback')}")
            else:
                lines.append("# QQBOT_WEBHOOK_PORT=9890")
                lines.append("# QQBOT_WEBHOOK_PATH=/qqbot/callback")
        else:
            lines.extend([
                "QQBOT_ENABLED=false",
                "# QQBOT_APP_ID=",
                "# QQBOT_APP_SECRET=",
                "# QQBOT_SANDBOX=true",
                "# QQBOT_MODE=websocket",
                "# QQBOT_WEBHOOK_PORT=9890",
                "# QQBOT_WEBHOOK_PATH=/qqbot/callback",
            ])
        lines.append("")

        # 人格系统
        lines.extend([
            "# ========== Persona ==========",
            f"PERSONA_NAME={self.config.get('PERSONA_NAME', 'default')}",
            "",
        ])

        # 表情包
        lines.extend([
            "# ========== Sticker ==========",
            f"STICKER_ENABLED={self.config.get('STICKER_ENABLED', 'true')}",
            "# STICKER_DATA_DIR=data/sticker",
            "",
        ])

        # 活人感模式 —— 启用后 Agent 会主动发消息（问候、跟进、闲聊等），模拟真人互动节奏
        lines.append("# ========== Proactive (Living Presence) ==========")
        if self.config.get("PROACTIVE_ENABLED") == "true":
            lines.extend([
                "PROACTIVE_ENABLED=true  # 启用活人感模式",
                f"PROACTIVE_MAX_DAILY_MESSAGES={self.config.get('PROACTIVE_MAX_DAILY_MESSAGES', '3')}  # 每日最多主动消息数",
                f"PROACTIVE_MIN_INTERVAL_MINUTES={self.config.get('PROACTIVE_MIN_INTERVAL_MINUTES', '120')}  # 两条主动消息最短间隔（分钟）",
                f"PROACTIVE_QUIET_HOURS_START={self.config.get('PROACTIVE_QUIET_HOURS_START', '23')}  # 免打扰时段开始（24h）",
                f"PROACTIVE_QUIET_HOURS_END={self.config.get('PROACTIVE_QUIET_HOURS_END', '7')}  # 免打扰时段结束（24h）",
                f"PROACTIVE_IDLE_THRESHOLD_HOURS={self.config.get('PROACTIVE_IDLE_THRESHOLD_HOURS', '3')}  # 用户空闲多久后触发主动问候（AI 动态调整）",
            ])
        else:
            lines.extend([
                "PROACTIVE_ENABLED=false  # 启用活人感模式（主动问候/跟进/闲聊）",
                "# PROACTIVE_MAX_DAILY_MESSAGES=3  # 每日最多主动消息数",
                "# PROACTIVE_MIN_INTERVAL_MINUTES=120  # 两条主动消息最短间隔（分钟）",
                "# PROACTIVE_QUIET_HOURS_START=23  # 免打扰时段开始（24h）",
                "# PROACTIVE_QUIET_HOURS_END=7  # 免打扰时段结束（24h）",
                "# PROACTIVE_IDLE_THRESHOLD_HOURS=3  # 用户空闲多久后触发主动问候（AI 动态调整）",
            ])
        lines.append("")

        # 记忆系统配置
        lines.extend([
            "# ========== Memory System ==========",
            f"EMBEDDING_MODEL={self.config.get('EMBEDDING_MODEL', 'shibing624/text2vec-base-chinese')}",
            f"EMBEDDING_DEVICE={self.config.get('EMBEDDING_DEVICE', 'cpu')}  # 嵌入模型运行设备: cpu / cuda / mps",
            f"MODEL_DOWNLOAD_SOURCE={self.config.get('MODEL_DOWNLOAD_SOURCE', 'auto')}  # 模型下载源: auto / huggingface / modelscope",
            "MEMORY_HISTORY_DAYS=30  # 记忆保留天数",
            "MEMORY_MAX_HISTORY_FILES=1000  # 最大历史文件数",
            "MEMORY_MAX_HISTORY_SIZE_MB=500  # 历史文件最大总大小（MB）",
            "",
        ])

        # 调度器
        lines.extend([
            "# ========== Scheduler ==========",
            f"SCHEDULER_ENABLED={self.config.get('SCHEDULER_ENABLED', 'true')}",
            f"SCHEDULER_TIMEZONE={self.config.get('SCHEDULER_TIMEZONE', 'Asia/Shanghai')}",
            "SCHEDULER_MAX_CONCURRENT=5  # 最大并发调度任务数",
            "SCHEDULER_TASK_TIMEOUT=600  # 单个调度任务超时（秒）",
            "",
        ])

        # 会话
        lines.extend([
            "# ========== Session ==========",
            f"SESSION_TIMEOUT_MINUTES={self.config.get('SESSION_TIMEOUT_MINUTES', '30')}  # 会话超时（分钟）",
            f"SESSION_MAX_HISTORY={self.config.get('SESSION_MAX_HISTORY', '50')}  # 每个会话保留的最大消息条数",
            "SESSION_STORAGE_PATH=data/sessions  # 会话持久化存储路径",
            "",
        ])

        # 多 Agent 配置
        lines.append("# ========== Multi-Agent Orchestration ==========")
        if self.config.get("ORCHESTRATION_ENABLED") == "true":
            lines.extend([
                "ORCHESTRATION_ENABLED=true  # 启用多 Agent 协作",
                f"ORCHESTRATION_MODE={self.config.get('ORCHESTRATION_MODE', 'single')}  # 编排模式: single / parallel / pipeline",
                "ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555  # ZeroMQ 请求总线地址",
                "ORCHESTRATION_PUB_ADDRESS=tcp://127.0.0.1:5556  # ZeroMQ 发布地址",
                "ORCHESTRATION_MIN_WORKERS=1  # 最小 Worker 数",
                "ORCHESTRATION_MAX_WORKERS=5  # 最大 Worker 数",
            ])
        else:
            lines.extend([
                "ORCHESTRATION_ENABLED=false",
                "# ORCHESTRATION_MODE=single",
                "# ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555",
            ])
        lines.append("")

        return "\n".join(lines)

    def _create_identity_examples(self):
        """创建 identity 目录下的示例文件"""
        identity_dir = self.project_dir / "identity"
        identity_dir.mkdir(exist_ok=True)

        # SOUL.md - Agent 的核心身份
        soul_example = identity_dir / "SOUL.md"
        if not soul_example.exists():
            soul_example.write_text(
                """# Agent Soul

你是 OpenAkita，一个忠诚可靠的 AI 助手。

## 核心特质
- 永不放弃，持续尝试直到成功
- 诚实可靠，不会隐瞒问题
- 主动学习，不断自我改进

## 行为准则
- 优先考虑用户的真实需求
- 遇到困难时寻找替代方案
- 保持简洁清晰的沟通方式
""",
                encoding="utf-8",
            )
            console.print("  [green]✓[/green] Created identity/SOUL.md")

    def _test_connection(self):
        """测试 API 连接"""
        console.print("[bold cyan]Step 8: Testing Connection[/bold cyan]\n")

        test_api = Confirm.ask("Test API connection now?", default=True)

        if not test_api:
            console.print("[dim]Skipping connection test.[/dim]\n")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Testing API connection...", total=None)

            try:
                import httpx

                api_key = self.config.get("ANTHROPIC_API_KEY", "")
                base_url = self.config.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
                model = self.config.get("DEFAULT_MODEL", "claude-sonnet-4-20250514")
                is_anthropic = "anthropic.com" in base_url

                if is_anthropic:
                    headers = {
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    }
                    url = f"{base_url.rstrip('/')}/v1/messages"
                    body: dict = {
                        "model": model,
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "Hi"}],
                    }
                else:
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "content-type": "application/json",
                    }
                    url = f"{base_url.rstrip('/')}/chat/completions"
                    body = {
                        "model": model,
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "Hi"}],
                    }

                with httpx.Client(timeout=30) as client:
                    response = client.post(url, headers=headers, json=body)

                    if response.status_code == 200:
                        progress.update(
                            task, description="[green]✓ API connection successful![/green]"
                        )
                    elif response.status_code == 401:
                        progress.update(task, description="[red]✗ Invalid API key[/red]")
                    else:
                        progress.update(
                            task,
                            description=f"[yellow]⚠ API returned status {response.status_code}[/yellow]",
                        )

            except Exception as e:
                progress.update(task, description=f"[yellow]⚠ Could not test: {e}[/yellow]")

        console.print()

    def _show_completion(self):
        """显示完成信息"""
        completion_text = """
# 🎉 Setup Complete!

OpenAkita has been configured successfully.

## Quick Start

**Start the CLI:**
```bash
openakita
```

**Or run as service (Telegram/IM):**
```bash
openakita serve
```

## Configuration Files

- `.env` - Environment variables
- `identity/SOUL.md` - Agent personality
- `data/` - Database and cache

## Next Steps

1. Customize `identity/SOUL.md` to personalize your agent
2. Run `openakita` to start chatting
3. Check `openakita --help` for all commands

## Documentation

- GitHub: https://github.com/openakita/openakita
- Docs: https://github.com/openakita/openakita/tree/main/docs

Enjoy your loyal AI companion! 🐕
        """

        console.print(
            Panel(Markdown(completion_text), title="Setup Complete", border_style="green")
        )


def run_wizard(project_dir: str | None = None):
    """运行安装向导的入口函数"""
    path = Path(project_dir) if project_dir else Path.cwd()
    wizard = SetupWizard(path)
    return wizard.run()
