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

    def run(self) -> bool:
        """运行完整的安装向导"""
        try:
            self._show_welcome()
            self._check_environment()
            self._create_directories()
            self._configure_llm()
            self._configure_compiler()
            self._configure_im_channels()
            self._configure_memory()
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
        console.print("  - Moonshot: https://api.moonshot.cn/v1\n")

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

            existing_data["endpoints"] = [
                {
                    "name": "primary",
                    "provider": provider,
                    "api_type": api_type,
                    "base_url": base_url,
                    "api_key_env": api_key_env,
                    "model": model,
                    "priority": 1,
                    "max_tokens": int(self.config.get("MAX_TOKENS", "8192")),
                    "timeout": 180,
                    "capabilities": ["text", "tools"],
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
        console.print("  [5] Skip\n")

        choice = Prompt.ask("Select channel", choices=["1", "2", "3", "4", "5"], default="5")

        if choice == "1":
            self._configure_telegram()
        elif choice == "2":
            self._configure_feishu()
        elif choice == "3":
            self._configure_wework()
        elif choice == "4":
            self._configure_dingtalk()

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

        corp_id = Prompt.ask("Enter Corp ID")
        agent_id = Prompt.ask("Enter Agent ID")
        secret = Prompt.ask("Enter Secret", password=True)

        self.config["WEWORK_ENABLED"] = "true"
        self.config["WEWORK_CORP_ID"] = corp_id
        self.config["WEWORK_AGENT_ID"] = agent_id
        self.config["WEWORK_SECRET"] = secret

    def _configure_dingtalk(self):
        """配置钉钉"""
        console.print("\n[bold]DingTalk Configuration[/bold]\n")

        app_key = Prompt.ask("Enter App Key")
        app_secret = Prompt.ask("Enter App Secret", password=True)

        self.config["DINGTALK_ENABLED"] = "true"
        self.config["DINGTALK_CLIENT_ID"] = app_key
        self.config["DINGTALK_CLIENT_SECRET"] = app_secret

    def _configure_memory(self):
        """配置记忆系统"""
        console.print("[bold cyan]Step 5: Configure Memory System[/bold cyan]\n")

        console.print("OpenAkita uses vector embeddings for semantic memory search.\n")

        # Embedding 模型选择
        console.print("Embedding model options:\n")
        console.print("  [1] Chinese optimized (shibing624/text2vec-base-chinese) - ~100MB")
        console.print("  [2] English optimized (all-MiniLM-L6-v2) - ~90MB")
        console.print("  [3] Multilingual (paraphrase-multilingual-MiniLM-L12-v2) - ~120MB\n")

        choice = Prompt.ask("Select embedding model", choices=["1", "2", "3"], default="1")

        models = {
            "1": "shibing624/text2vec-base-chinese",
            "2": "sentence-transformers/all-MiniLM-L6-v2",
            "3": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        }
        self.config["EMBEDDING_MODEL"] = models[choice]

        # GPU 加速
        use_gpu = Confirm.ask("Use GPU for embeddings (requires CUDA)?", default=False)
        self.config["EMBEDDING_DEVICE"] = "cuda" if use_gpu else "cpu"

        console.print("\n[green]Memory configuration complete![/green]\n")

    def _configure_advanced(self):
        """高级配置"""
        console.print("[bold cyan]Step 6: Advanced Configuration (Optional)[/bold cyan]\n")

        configure_advanced = Confirm.ask("Configure advanced options?", default=False)

        if not configure_advanced:
            # 使用默认值
            self.config.setdefault("MAX_TOKENS", "8192")
            self.config.setdefault("MAX_ITERATIONS", "100")
            self.config.setdefault("LOG_LEVEL", "INFO")
            console.print("[dim]Using default advanced settings.[/dim]\n")
            return

        # Max tokens
        max_tokens = Prompt.ask("Max output tokens", default="8192")
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

        # Proactive (living presence)
        use_proactive = Confirm.ask("Enable living-presence mode? (proactive greetings & follow-ups)", default=False)
        if use_proactive:
            self.config["PROACTIVE_ENABLED"] = "true"

        # Multi-agent
        use_multi = Confirm.ask("Enable multi-agent orchestration?", default=False)
        if use_multi:
            self.config["ORCHESTRATION_ENABLED"] = "true"

        console.print("\n[green]Advanced configuration complete![/green]\n")

    def _write_env_file(self):
        """写入 .env 文件"""
        console.print("[bold cyan]Step 7: Saving Configuration[/bold cyan]\n")

        # 检查是否已存在
        if self.env_path.exists():
            overwrite = Confirm.ask(
                f".env file already exists at {self.env_path}. Overwrite?", default=False
            )
            if not overwrite:
                # 备份旧文件
                backup_path = self.env_path.with_suffix(".env.backup")
                self.env_path.rename(backup_path)
                console.print(f"  [yellow]Backed up to {backup_path}[/yellow]")

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
            "# === LLM API ===",
            f"ANTHROPIC_API_KEY={self.config.get('ANTHROPIC_API_KEY', '')}",
            f"ANTHROPIC_BASE_URL={self.config.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com')}",
            "",
            "# === Model Configuration ===",
            f"DEFAULT_MODEL={self.config.get('DEFAULT_MODEL', 'claude-sonnet-4-20250514')}",
            f"MAX_TOKENS={self.config.get('MAX_TOKENS', '8192')}",
            f"THINKING_MODE={self.config.get('THINKING_MODE', 'auto')}",
            "",
            "# === Agent Configuration ===",
            "AGENT_NAME=OpenAkita",
            f"MAX_ITERATIONS={self.config.get('MAX_ITERATIONS', '100')}",
            "AUTO_CONFIRM=false",
            "",
            "# === Paths ===",
            "DATABASE_PATH=data/agent.db",
            f"LOG_LEVEL={self.config.get('LOG_LEVEL', 'INFO')}",
            "",
        ]

        # IM 通道配置
        if self.config.get("TELEGRAM_ENABLED"):
            lines.extend(
                [
                    "# === Telegram ===",
                    f"TELEGRAM_ENABLED={self.config.get('TELEGRAM_ENABLED', 'false')}",
                    f"TELEGRAM_BOT_TOKEN={self.config.get('TELEGRAM_BOT_TOKEN', '')}",
                    f"TELEGRAM_REQUIRE_PAIRING={self.config.get('TELEGRAM_REQUIRE_PAIRING', 'true')}",
                    "",
                ]
            )

        if self.config.get("FEISHU_ENABLED"):
            lines.extend(
                [
                    "# === Feishu ===",
                    f"FEISHU_ENABLED={self.config.get('FEISHU_ENABLED', 'false')}",
                    f"FEISHU_APP_ID={self.config.get('FEISHU_APP_ID', '')}",
                    f"FEISHU_APP_SECRET={self.config.get('FEISHU_APP_SECRET', '')}",
                    "",
                ]
            )

        if self.config.get("WEWORK_ENABLED"):
            lines.extend(
                [
                    "# === WeCom ===",
                    f"WEWORK_ENABLED={self.config.get('WEWORK_ENABLED', 'false')}",
                    f"WEWORK_CORP_ID={self.config.get('WEWORK_CORP_ID', '')}",
                    f"WEWORK_AGENT_ID={self.config.get('WEWORK_AGENT_ID', '')}",
                    f"WEWORK_SECRET={self.config.get('WEWORK_SECRET', '')}",
                    "",
                ]
            )

        if self.config.get("DINGTALK_ENABLED"):
            lines.extend(
                [
                    "# === DingTalk ===",
                    f"DINGTALK_ENABLED={self.config.get('DINGTALK_ENABLED', 'false')}",
                    f"DINGTALK_CLIENT_ID={self.config.get('DINGTALK_CLIENT_ID', '')}",
                    f"DINGTALK_CLIENT_SECRET={self.config.get('DINGTALK_CLIENT_SECRET', '')}",
                    "",
                ]
            )

        # 记忆系统配置
        lines.extend(
            [
                "# === Memory System ===",
                f"EMBEDDING_MODEL={self.config.get('EMBEDDING_MODEL', 'shibing624/text2vec-base-chinese')}",
                f"EMBEDDING_DEVICE={self.config.get('EMBEDDING_DEVICE', 'cpu')}",
                "MEMORY_HISTORY_DAYS=30",
                "",
            ]
        )

        # 多 Agent 配置
        if self.config.get("ORCHESTRATION_ENABLED"):
            lines.extend(
                [
                    "# === Multi-Agent Orchestration ===",
                    "ORCHESTRATION_ENABLED=true",
                    "ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555",
                    "ORCHESTRATION_MIN_WORKERS=1",
                    "ORCHESTRATION_MAX_WORKERS=5",
                    "",
                ]
            )

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
                # 动态导入避免循环依赖
                import httpx

                api_key = self.config.get("ANTHROPIC_API_KEY", "")
                base_url = self.config.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

                # 简单的 API 测试
                headers = {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }

                # 尝试发送一个简单请求
                with httpx.Client(timeout=30) as client:
                    response = client.post(
                        f"{base_url.rstrip('/')}/v1/messages",
                        headers=headers,
                        json={
                            "model": self.config.get("DEFAULT_MODEL", "claude-sonnet-4-20250514"),
                            "max_tokens": 10,
                            "messages": [{"role": "user", "content": "Hi"}],
                        },
                    )

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
