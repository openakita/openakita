"""
OpenAkita 交互式安装向导

一键启动，引导用户完成所有配置
"""

import asyncio
import json
import math
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

_TOTAL_STEPS = 11


def _ask_secret(prompt_text: str, *, allow_empty: bool = False) -> str:
    """Prompt for a secret value, then echo a masked confirmation so the user
    knows something was captured.  Returns the raw input string."""
    kwargs: dict = {"password": True}
    if allow_empty:
        kwargs["default"] = ""
    value = Prompt.ask(prompt_text, **kwargs)
    if value:
        if len(value) > 8:
            masked = value[:3] + "*" * (len(value) - 3)
        else:
            masked = "*" * len(value)
        console.print(f"  [dim]Received: {masked}[/dim]")
    return value

_CHINA_SLUGS = {
    "dashscope", "kimi-cn", "minimax-cn", "siliconflow",
    "volcengine", "zhipu-cn", "qianfan", "hunyuan", "yunwu",
    "longcat", "iflow",
}


def _load_providers() -> list[dict]:
    """Load provider definitions from the shared providers.json."""
    providers_path = Path(__file__).resolve().parents[1] / "llm" / "registries" / "providers.json"
    return json.loads(providers_path.read_text(encoding="utf-8"))


class SetupWizard:
    """交互式安装向导"""

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self.env_path = self.project_dir / ".env"
        self.config: dict = {}
        self._locale = "zh"
        self._defaults: dict = {
            "MODEL_DOWNLOAD_SOURCE": "hf-mirror",
            "EMBEDDING_MODEL": "shibing624/text2vec-base-chinese",
            "WHISPER_LANGUAGE": "zh",
            "SCHEDULER_TIMEZONE": "Asia/Shanghai",
        }
        self._llm_endpoints: list[dict] = []
        self._providers: list[dict] = _load_providers()
        self._selected_channel: str = ""
        self._channel_deps_ok: bool = True
        self._channel_deps_missing: list[str] = []

    def _step_screen(self, step: int, title: str):
        """Clear the terminal and show a step header panel."""
        console.clear()
        console.print(
            Panel(
                f"[bold]{title}[/bold]",
                subtitle=f"Step {step}/{_TOTAL_STEPS}",
                border_style="cyan",
            )
        )
        console.print()

    def run(self, *, quick: bool = False) -> bool:
        """运行安装向导。

        Args:
            quick: 快速模式 — 仅 Provider + API Key + Model 三项。
        """
        try:
            self._show_welcome()
            self._confirm_risk_agreement()
            self._check_environment()
            if quick:
                return self._run_quick()
            self._choose_locale()
            self._create_directories()
            self._configure_llm()
            self._configure_compiler()
            self._configure_im_channels()
            self._configure_memory()
            self._configure_voice()
            self._configure_advanced()
            self._write_env_file()
            self._check_channel_deps()
            self._verify_channel_credentials()
            self._test_connection()
            self._show_completion()
            return True
        except KeyboardInterrupt:
            console.print("\n\n[yellow]安装已取消[/yellow]")
            return False
        except Exception as e:
            console.print(f"\n[red]安装出错: {e}[/red]")
            return False

    def _run_quick(self) -> bool:
        """快速模式：仅 Provider + API Key + Model，然后写 .env 并测试。"""
        console.print(Panel("[bold cyan]Quick Setup Mode[/bold cyan]\n仅需三步：选择 Provider → 填写 API Key → 选择模型", border_style="cyan"))
        console.print()

        self._create_directories()
        self._configure_llm()
        self._write_env_file()

        # 测试连接（失败后提供恢复菜单）
        while True:
            success = self._test_connection_safe()
            if success:
                break
            console.print()
            choice = Prompt.ask(
                "[bold]测试失败，如何继续？[/bold]\n"
                "  [cyan]1[/cyan] 重试连接测试\n"
                "  [cyan]2[/cyan] 修改 LLM 配置\n"
                "  [cyan]3[/cyan] 跳过测试，完成配置",
                choices=["1", "2", "3"],
                default="1",
            )
            if choice == "1":
                continue
            elif choice == "2":
                self._configure_llm()
                self._write_env_file()
            else:
                break

        self._show_completion()
        return True

    def _test_connection_safe(self) -> bool:
        """执行连接测试，捕获异常并返回成功/失败。"""
        try:
            self._test_connection()
            return True
        except Exception as e:
            console.print(f"[red]连接测试失败: {e}[/red]")
            return False

    def _show_welcome(self):
        """显示欢迎界面"""
        console.clear()

        welcome_text = """
# Welcome to OpenAkita

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

    def _confirm_risk_agreement(self):
        """显示使用风险须知，要求用户输入确认文字"""
        console.clear()
        agreement_text = """
## 使用风险须知 / Risk Acknowledgment

OpenAkita 是一款基于大语言模型（LLM）驱动的 AI Agent 软件。
在使用前，你需要了解并接受以下事项：

**1. 行为不可完全预测**
AI Agent 的行为受底层大语言模型驱动，其输出具有概率性和不确定性。
即使在相同输入下，Agent 也可能产生不同的行为结果，包括但不限于：
执行非预期的文件操作、发送非预期的消息、调用非预期的工具等。

**2. 使用过程必须监督**
你有责任在使用过程中保持对 AI Agent 行为的监督。对于需要审批的
工具调用（如文件删除、系统命令执行、消息发送等），请在确认操作
内容合理后再批准执行。强烈建议不要在无人监督的情况下开启自动
确认模式（AUTO_CONFIRM）。

**3. 可能造成的风险**
AI Agent 在执行任务时可能导致：
- 数据丢失或损坏（如误删文件、覆盖重要数据）
- 发送不当消息（如通过 IM 通道发送错误内容）
- 执行危险系统命令
- 产生非预期的 API 调用和费用消耗
- 其他无法预见的副作用

**4. 免责声明**
OpenAkita 按「现状」(AS IS) 提供，不附带任何形式的明示或暗示
担保。项目维护者和贡献者不对因使用本软件而产生的任何直接、间接、
偶然、特殊或后果性损害承担责任。你应当自行承担使用本软件的全部
风险。

**5. 数据安全**
你的对话内容、配置信息和工具调用记录可能被发送至第三方 LLM 服务
商。请勿在对话中提供敏感的个人信息、密码、密钥等机密数据，除非
你充分了解并接受相关风险。
"""
        console.print(Panel(Markdown(agreement_text), title="Risk Acknowledgment", border_style="yellow"))
        console.print()

        if not Confirm.ask(
            "[bold]是否已阅读并同意以上风险须知？[/bold]",
            default=False,
        ):
            console.print("\n[red]未同意风险须知，安装向导已退出。[/red]")
            console.print("[dim]如需继续，请重新运行 openakita init[/dim]")
            sys.exit(1)
        console.print("\n[green]✓ 已确认，继续安装向导。[/green]\n")

    def _check_environment(self):
        """检查运行环境"""
        self._step_screen(1, "Checking Environment")

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
        self._step_screen(2, "Language & Region")
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
        self._step_screen(3, "Creating Directory Structure")

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

    # ------------------------------------------------------------------
    # LLM 端点配置（Provider 选择 → Coding Plan → URL → Key → 模型）
    # ------------------------------------------------------------------

    def _configure_llm(self):
        """配置 LLM 端点（支持循环添加多个端点）"""
        endpoint_index = 0
        while True:
            endpoint_index += 1
            self._step_screen(4, "Configure LLM Endpoints")

            if self._llm_endpoints:
                console.print("[dim]Already configured endpoints:[/dim]")
                for i, ep in enumerate(self._llm_endpoints, 1):
                    tag = " (Coding Plan)" if ep.get("coding_plan") else ""
                    console.print(f"  {i}. {ep['name']} ({ep['provider']} / {ep['model']}){tag}")
                console.print()

            provider = self._pick_provider()
            if provider is None:
                break

            ep = self._configure_single_endpoint(provider, endpoint_index)
            if ep:
                self._llm_endpoints.append(ep)

            console.print()
            add_more = Confirm.ask("Add another LLM endpoint?", default=False)
            if not add_more:
                break

        # Backfill .env compat vars from the first endpoint for legacy code paths
        if self._llm_endpoints:
            first = self._llm_endpoints[0]
            self.config.setdefault("ANTHROPIC_API_KEY", self.config.get(first.get("api_key_env", ""), ""))
            self.config.setdefault("ANTHROPIC_BASE_URL", first.get("base_url", ""))
            self.config.setdefault("DEFAULT_MODEL", first.get("model", ""))

        # Extended thinking
        model_name = self.config.get("DEFAULT_MODEL", "")
        if "thinking" in model_name.lower():
            self.config["THINKING_MODE"] = "always"
        else:
            use_thinking = Confirm.ask(
                "\nEnable extended thinking mode for complex tasks?", default=True
            )
            self.config["THINKING_MODE"] = "auto" if use_thinking else "never"

        # Summary
        endpoints_path = self.project_dir / "data" / "llm_endpoints.json"
        console.print(f"\n[green]LLM configuration complete![/green]")
        console.print(
            f"[dim]Advanced endpoint settings can be edited in {endpoints_path}[/dim]\n"
        )

    def _pick_provider(self) -> dict | None:
        """Show grouped provider list and let the user pick one."""
        local, intl, china = [], [], []
        for p in self._providers:
            if p.get("is_local"):
                local.append(p)
            elif p.get("slug") in _CHINA_SLUGS:
                china.append(p)
            else:
                intl.append(p)

        idx = 1
        index_map: dict[int, dict] = {}

        console.print("[bold]Select LLM Provider:[/bold]\n")

        if local:
            console.print("  [dim]-- Local --[/dim]")
            for p in local:
                console.print(f"  [cyan][{idx}][/cyan] {p['name']}")
                index_map[idx] = p
                idx += 1

        if intl:
            console.print("  [dim]-- International --[/dim]")
            for p in intl:
                tag = " [yellow](Coding Plan)[/yellow]" if p.get("coding_plan_base_url") else ""
                console.print(f"  [cyan][{idx}][/cyan] {p['name']}{tag}")
                index_map[idx] = p
                idx += 1

        if china:
            console.print("  [dim]-- China --[/dim]")
            for p in china:
                tag = " [yellow](Coding Plan)[/yellow]" if p.get("coding_plan_base_url") else ""
                console.print(f"  [cyan][{idx}][/cyan] {p['name']}{tag}")
                index_map[idx] = p
                idx += 1

        console.print()
        valid = [str(i) for i in range(1, idx)]
        choice = Prompt.ask("Select provider", choices=valid, default="1")
        return index_map.get(int(choice))

    def _configure_single_endpoint(self, provider: dict, index: int) -> dict | None:
        """Walk the user through Coding Plan toggle → Base URL → API Key → Model."""
        slug = provider["slug"]
        api_type = provider.get("api_type", "openai")
        default_url = provider.get("default_base_url", "")
        requires_key = provider.get("requires_api_key", True)
        api_key_env = provider.get("api_key_env_suggestion", "API_KEY")

        # --- Coding Plan toggle (before Base URL, matching Desktop behavior) ---
        coding_plan = False
        cp_url = provider.get("coding_plan_base_url")
        if cp_url:
            console.print()
            coding_plan = Confirm.ask(
                f"[cyan]{provider['name']}[/cyan] supports Coding Plan. Enable it?",
                default=False,
            )
            if coding_plan:
                api_type = provider.get("coding_plan_api_type", api_type)
                default_url = cp_url

        # --- Base URL ---
        console.print(f"\n[bold]API Base URL for {provider['name']}[/bold]")
        base_url = Prompt.ask("Base URL", default=default_url)

        # --- API Key ---
        api_key = ""
        if requires_key:
            api_key = _ask_secret(f"API Key (saved to env var {api_key_env})")
            self.config[api_key_env] = api_key

        # --- Fetch model list ---
        model = self._select_model(api_type, base_url, slug, api_key)

        name = "primary" if index == 1 else f"endpoint-{index}"

        from openakita.llm.capabilities import (
            get_provider_slug_from_base_url,
            infer_capabilities,
        )
        resolved_slug = get_provider_slug_from_base_url(base_url) or slug
        caps = infer_capabilities(model, provider_slug=resolved_slug)
        capabilities = [k for k, v in caps.items() if v and k != "thinking_only"]
        if not capabilities:
            capabilities = ["text", "tools"]

        ep: dict = {
            "name": name,
            "provider": slug,
            "api_type": api_type,
            "base_url": base_url,
            "api_key_env": api_key_env,
            "model": model,
            "priority": index,
            "max_tokens": 0,
            "timeout": 180,
            "capabilities": capabilities,
        }
        if coding_plan:
            ep["coding_plan"] = True
        return ep

    # ------------------------------------------------------------------
    # Model selection (fetch from API or manual input)
    # ------------------------------------------------------------------

    def _fetch_models(self, api_type: str, base_url: str, slug: str, api_key: str) -> list[dict]:
        """Fetch model list from provider API. Returns [] on failure."""
        from openakita.setup_center.bridge import (
            _list_models_anthropic,
            _list_models_openai,
        )

        fn = _list_models_anthropic if api_type == "anthropic" else _list_models_openai
        try:
            loop = asyncio.new_event_loop()
            try:
                models = loop.run_until_complete(fn(api_key, base_url, slug))
            finally:
                loop.close()
            return models
        except Exception:
            return []

    def _select_model(self, api_type: str, base_url: str, slug: str, api_key: str) -> str:
        """Fetch models and let the user pick, with manual-input fallback."""
        console.print()

        models: list[dict] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching model list...", total=None)
            models = self._fetch_models(api_type, base_url, slug, api_key)
            if models:
                progress.update(task, description=f"[green]Fetched {len(models)} models[/green]")
            else:
                progress.update(task, description="[yellow]Could not fetch model list[/yellow]")

        if not models:
            console.print("[dim]Enter the model name manually.[/dim]")
            return Prompt.ask("Model name")

        return self._paginated_model_picker(models)

    def _paginated_model_picker(self, models: list[dict], page_size: int = 15) -> str:
        """Display a paginated model list. Accepts a number, p/n for paging,
        or any other text as a direct model name."""
        total_pages = math.ceil(len(models) / page_size)
        page = 0

        while True:
            start = page * page_size
            end = min(start + page_size, len(models))
            page_models = models[start:end]

            console.print(f"\n[bold]Models (page {page + 1}/{total_pages}, {len(models)} total):[/bold]\n")
            for i, m in enumerate(page_models, 1):
                console.print(f"  [cyan][{i}][/cyan] {m['id']}")

            console.print()

            nav_parts = []
            if page > 0:
                nav_parts.append("\\[p] prev page")
            if page < total_pages - 1:
                nav_parts.append("\\[n] next page")
            if nav_parts:
                console.print(f"  {' | '.join(nav_parts)}")

            console.print("  [dim]Or type a model name directly[/dim]")
            console.print()
            raw = Prompt.ask("Select model").strip()

            raw_lower = raw.lower()
            if raw_lower == "p":
                if page > 0:
                    page -= 1
                else:
                    console.print("[dim]Already on first page.[/dim]")
                continue
            if raw_lower == "n":
                if page < total_pages - 1:
                    page += 1
                else:
                    console.print("[dim]Already on last page.[/dim]")
                continue

            if raw.isdigit():
                num = int(raw)
                if 1 <= num <= len(page_models):
                    return page_models[num - 1]["id"]
                console.print("[red]Invalid number, try again.[/red]")
                continue

            if raw:
                return raw

            console.print("[red]Please enter a number or model name.[/red]")

    def _configure_compiler(self):
        """配置 Prompt Compiler 专用模型（可选）"""
        self._step_screen(5, "Configure Prompt Compiler Model (Optional)")

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
                api_key = _ask_secret("Enter DashScope API Key")
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
            api_key = _ask_secret("Enter API Key")
            self.config[compiler_config["api_key_env"]] = api_key
            compiler_config["model"] = Prompt.ask("Model name", default="gpt-4o-mini")
        elif choice == "3":
            first_ep = self._llm_endpoints[0] if self._llm_endpoints else {}
            compiler_config["provider"] = first_ep.get("provider", "openai-compatible")
            compiler_config["api_type"] = first_ep.get("api_type", "openai")
            compiler_config["base_url"] = first_ep.get(
                "base_url", self.config.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
            )
            compiler_config["api_key_env"] = first_ep.get("api_key_env", "ANTHROPIC_API_KEY")
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
                api_key = _ask_secret("Enter API Key")
                self.config[backup_config["api_key_env"]] = api_key
            backup_config["provider"] = Prompt.ask(
                "Provider name", default=compiler_config.get("provider", "openai-compatible")
            )
            backup_config["model"] = Prompt.ask("Model name", default="qwen-plus-latest")
            self.config["_compiler_backup"] = backup_config

        console.print("\n[green]Prompt Compiler configuration complete![/green]\n")

    def _write_llm_endpoints(self):
        """将 LLM 端点和 Compiler 端点写入 data/llm_endpoints.json"""
        endpoints_path = self.project_dir / "data" / "llm_endpoints.json"

        existing_data: dict = {}
        if endpoints_path.exists():
            try:
                existing_data = json.loads(endpoints_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # Use wizard-collected endpoints if available; otherwise fall back to legacy config
        if self._llm_endpoints:
            max_tokens = int(self.config.get("MAX_TOKENS", "0"))
            for ep in self._llm_endpoints:
                if ep.get("max_tokens", 0) == 0:
                    ep["max_tokens"] = max_tokens
            existing_data["endpoints"] = self._llm_endpoints
        elif not existing_data.get("endpoints"):
            api_key_env = "ANTHROPIC_API_KEY"
            base_url = self.config.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
            model = self.config.get("DEFAULT_MODEL", "claude-sonnet-4-20250514")
            api_type = "anthropic" if "anthropic.com" in base_url else "openai"
            provider = "anthropic" if api_type == "anthropic" else "openai-compatible"

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

        # Compiler endpoints
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

        if not existing_data.get("settings"):
            existing_data["settings"] = {
                "retry_count": 2,
                "retry_delay_seconds": 2,
                "health_check_interval": 60,
                "fallback_on_error": True,
            }

        from openakita.llm.endpoint_manager import EndpointManager

        mgr = EndpointManager(self.project_dir)
        # Use EndpointManager's atomic write for safety
        mgr._json_path.parent.mkdir(parents=True, exist_ok=True)
        mgr._write_json(existing_data)
        console.print(f"  [green]✓[/green] LLM endpoints saved to {mgr.json_path}")

    def _configure_im_channels(self):
        """配置 IM 通道"""
        self._step_screen(6, "Configure IM Channels (Optional)")

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

        channel_map = {
            "1": ("telegram", self._configure_telegram),
            "2": ("feishu", self._configure_feishu),
            "3": ("wework", self._configure_wework),
            "4": ("dingtalk", self._configure_dingtalk),
            "5": ("onebot", self._configure_onebot),
            "6": ("qqbot", self._configure_qqbot),
        }
        if choice in channel_map:
            channel_name, configure_fn = channel_map[choice]
            self._selected_channel = channel_name
            configure_fn()

        console.print("\n[green]IM channel configuration complete![/green]\n")

    def _configure_telegram(self):
        """配置 Telegram"""
        console.print("\n[bold]Telegram Bot Configuration[/bold]\n")
        console.print("To create a bot, message @BotFather on Telegram and use /newbot\n")

        token = _ask_secret("Enter your Bot Token")
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
        """配置飞书（支持扫码创建 / 手动输入 / 使用现有凭证）"""
        console.print("\n[bold]Feishu (Lark) Configuration[/bold]\n")

        existing_id = self.config.get("FEISHU_APP_ID", "")
        existing_secret = self.config.get("FEISHU_APP_SECRET", "")

        choices = ["1", "2"]
        console.print("  [cyan]1[/cyan]  扫码创建飞书机器人（推荐）")
        console.print("  [cyan]2[/cyan]  手动输入 App ID / App Secret")
        if existing_id and existing_secret:
            choices.append("3")
            masked = existing_id[:4] + "****"
            console.print(f"  [cyan]3[/cyan]  使用现有凭证 ({masked})")

        mode = Prompt.ask("选择方式", choices=choices, default="1")

        if mode == "1":
            self._feishu_qr_onboard()
        elif mode == "2":
            app_id = Prompt.ask("Enter App ID")
            app_secret = _ask_secret("Enter App Secret")
            self.config["FEISHU_APP_ID"] = app_id
            self.config["FEISHU_APP_SECRET"] = app_secret
        else:
            console.print(f"  [dim]保留现有凭证: {existing_id[:4]}****[/dim]")

        self.config["FEISHU_ENABLED"] = "true"

        # 流式输出配置
        console.print()
        streaming = Confirm.ask("启用流式卡片输出？（实时显示 AI 回复）", default=True)
        self.config["FEISHU_STREAMING_ENABLED"] = "true" if streaming else "false"
        if streaming:
            group_streaming = Confirm.ask("群聊中也启用流式输出？", default=True)
            self.config["FEISHU_GROUP_STREAMING"] = "true" if group_streaming else "false"

        # 群聊回复模式
        console.print()
        console.print("群聊回复模式：")
        console.print("  [cyan]1[/cyan]  mention_only — 仅 @机器人 时回复（默认）")
        console.print("  [cyan]2[/cyan]  smart — 智能判断是否需要回复")
        console.print("  [cyan]3[/cyan]  always — 所有消息都回复")
        grp_mode = Prompt.ask("选择", choices=["1", "2", "3"], default="1")
        mode_map = {"1": "mention_only", "2": "smart", "3": "always"}
        self.config["FEISHU_GROUP_RESPONSE_MODE"] = mode_map[grp_mode]

    def _feishu_qr_onboard(self):
        """执行飞书 Device Flow 扫码建应用"""
        import asyncio as _asyncio

        from openakita.setup.feishu_onboard import (
            FeishuOnboard,
            FeishuOnboardError,
            render_qr_terminal,
        )

        domain = Prompt.ask("飞书版本", choices=["feishu", "lark"], default="feishu")
        ob = FeishuOnboard(domain=domain)

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            console=console, transient=True,
        ) as progress:
            task = progress.add_task("正在初始化 Device Flow...", total=None)
            try:
                init_data = _asyncio.run(ob.init())
                device_code = init_data["device_code"]
                _asyncio.run(ob.begin(device_code))
            except Exception as e:
                console.print(f"[red]初始化失败: {e}[/red]")
                console.print("[dim]请改用手动输入方式[/dim]")
                app_id = Prompt.ask("Enter App ID")
                app_secret = _ask_secret("Enter App Secret")
                self.config["FEISHU_APP_ID"] = app_id
                self.config["FEISHU_APP_SECRET"] = app_secret
                return
            progress.remove_task(task)

        verification_uri = init_data.get("verification_uri", "")
        console.print(Panel(
            f"请使用飞书 APP 扫描下方二维码完成授权\n\n"
            f"或在浏览器中打开: [link]{verification_uri}[/link]",
            title="飞书扫码授权",
            border_style="green",
        ))
        render_qr_terminal(verification_uri)

        console.print("\n[dim]等待扫码授权（最多 3 分钟）...[/dim]")
        try:
            result = _asyncio.run(ob.poll_until_done(device_code, interval=3.0, max_attempts=60))
            app_id = result.get("app_id", "")
            app_secret = result.get("app_secret", "")
            if app_id and app_secret:
                self.config["FEISHU_APP_ID"] = app_id
                self.config["FEISHU_APP_SECRET"] = app_secret
                console.print(f"[green]✓ 授权成功！App ID: {app_id[:4]}****[/green]")
            else:
                console.print("[yellow]授权返回数据不完整，请手动输入[/yellow]")
                self.config["FEISHU_APP_ID"] = Prompt.ask("Enter App ID")
                self.config["FEISHU_APP_SECRET"] = _ask_secret("Enter App Secret")
        except FeishuOnboardError as e:
            console.print(f"[red]扫码超时或被拒绝: {e}[/red]")
            console.print("[dim]请改用手动输入方式[/dim]")
            self.config["FEISHU_APP_ID"] = Prompt.ask("Enter App ID")
            self.config["FEISHU_APP_SECRET"] = _ask_secret("Enter App Secret")

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
        app_secret = _ask_secret("Enter App Secret")

        self.config["DINGTALK_ENABLED"] = "true"
        self.config["DINGTALK_CLIENT_ID"] = app_key
        self.config["DINGTALK_CLIENT_SECRET"] = app_secret

    def _configure_onebot(self):
        """配置 OneBot 协议通道"""
        console.print("\n[bold]OneBot Configuration[/bold]\n")
        console.print("OneBot 通道需要先部署 NapCat / Lagrange 等 OneBot 实现端\n")
        console.print("参考: https://github.com/botuniverse/onebot-11\n")

        console.print("Connection mode:\n")
        console.print("  [1] Reverse WebSocket (recommended, NapCat connects to OpenAkita)")
        console.print("  [2] Forward WebSocket (OpenAkita connects to NapCat)\n")
        mode_choice = Prompt.ask("Select mode", choices=["1", "2"], default="1")

        self.config["ONEBOT_ENABLED"] = "true"

        if mode_choice == "1":
            self.config["ONEBOT_MODE"] = "reverse"
            reverse_port = Prompt.ask("Enter reverse WS listen port", default="6700")
            self.config["ONEBOT_REVERSE_PORT"] = reverse_port
            console.print(
                f"\n[dim]NapCat 端请配置 Websocket 客户端，"
                f"地址填 ws://<本机IP>:{reverse_port}[/dim]\n"
            )
        else:
            self.config["ONEBOT_MODE"] = "forward"
            onebot_url = Prompt.ask(
                "Enter OneBot WebSocket URL",
                default="ws://127.0.0.1:8080",
            )
            self.config["ONEBOT_WS_URL"] = onebot_url

        access_token = _ask_secret("Enter Access Token (leave empty if not set)", allow_empty=True)
        if access_token:
            self.config["ONEBOT_ACCESS_TOKEN"] = access_token

    def _configure_qqbot(self):
        """配置 QQ 官方机器人"""
        console.print("\n[bold]QQ 官方机器人 Configuration[/bold]\n")
        console.print("请前往 QQ 开放平台 (https://q.qq.com) 创建机器人并获取凭据\n")

        app_id = Prompt.ask("Enter AppID")
        app_secret = _ask_secret("Enter AppSecret")

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
        self._step_screen(7, "Configure Memory System")

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
        self._step_screen(8, "Voice Recognition (Optional)")

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
        self._step_screen(9, "Advanced Configuration (Optional)")

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
        github_token = _ask_secret("Enter GitHub Token (leave empty to skip)", allow_empty=True)
        if github_token:
            self.config["GITHUB_TOKEN"] = github_token

        console.print("\n[green]Advanced configuration complete![/green]\n")

    def _write_env_file(self):
        """写入 .env 文件"""
        self._step_screen(10, "Saving Configuration")

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
        ]

        # Write all provider-specific API keys (LLM endpoints + compiler endpoints)
        written_keys = {"ANTHROPIC_API_KEY"}
        all_endpoints = list(self._llm_endpoints)
        for cfg_key in ("_compiler_primary", "_compiler_backup"):
            cfg = self.config.get(cfg_key)
            if cfg:
                all_endpoints.append(cfg)
        for ep in all_endpoints:
            env_var = ep.get("api_key_env", "")
            if env_var and env_var not in written_keys:
                lines.append(f"{env_var}={self.config.get(env_var, '')}")
                written_keys.add(env_var)

        lines.extend([
            "",
            "# ========== Model Configuration ==========",
            f"DEFAULT_MODEL={self.config.get('DEFAULT_MODEL', 'claude-sonnet-4-20250514')}",
            f"MAX_TOKENS={self.config.get('MAX_TOKENS', '0')}",
            f"THINKING_MODE={self.config.get('THINKING_MODE', 'auto')}",
        ])

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
                f"FEISHU_STREAMING_ENABLED={self.config.get('FEISHU_STREAMING_ENABLED', 'true')}",
                f"FEISHU_GROUP_STREAMING={self.config.get('FEISHU_GROUP_STREAMING', 'true')}",
                f"FEISHU_GROUP_RESPONSE_MODE={self.config.get('FEISHU_GROUP_RESPONSE_MODE', 'mention_only')}",
            ])
        else:
            lines.extend([
                "FEISHU_ENABLED=false",
                "# FEISHU_APP_ID=",
                "# FEISHU_APP_SECRET=",
                "# FEISHU_STREAMING_ENABLED=true",
                "# FEISHU_GROUP_STREAMING=true",
                "# FEISHU_GROUP_RESPONSE_MODE=mention_only",
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
            onebot_mode = self.config.get("ONEBOT_MODE", "reverse")
            lines.extend([
                f"ONEBOT_ENABLED={self.config.get('ONEBOT_ENABLED', 'false')}",
                f"ONEBOT_MODE={onebot_mode}",
            ])
            if onebot_mode == "forward":
                lines.append(f"ONEBOT_WS_URL={self.config.get('ONEBOT_WS_URL', 'ws://127.0.0.1:8080')}")
                lines.append("# ONEBOT_REVERSE_PORT=6700")
                lines.append("# ONEBOT_REVERSE_HOST=0.0.0.0")
            else:
                lines.append(f"ONEBOT_REVERSE_PORT={self.config.get('ONEBOT_REVERSE_PORT', '6700')}")
                lines.append(f"ONEBOT_REVERSE_HOST={self.config.get('ONEBOT_REVERSE_HOST', '0.0.0.0')}")
                lines.append("# ONEBOT_WS_URL=ws://127.0.0.1:8080")
            lines.append(f"ONEBOT_ACCESS_TOKEN={self.config.get('ONEBOT_ACCESS_TOKEN', '')}")
        else:
            lines.extend([
                "ONEBOT_ENABLED=false",
                "# ONEBOT_MODE=reverse",
                "# ONEBOT_WS_URL=ws://127.0.0.1:8080",
                "# ONEBOT_REVERSE_PORT=6700",
                "# ONEBOT_REVERSE_HOST=0.0.0.0",
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

    def _check_channel_deps(self):
        """检查并安装已选 IM 通道的可选依赖。"""
        if not self._selected_channel:
            return

        # Telegram 是核心依赖，无需额外安装
        if self._selected_channel == "telegram":
            return

        import importlib
        import subprocess

        from openakita.channels.deps import CHANNEL_DEPS, CHANNEL_EXTRAS
        from openakita.runtime_env import IS_FROZEN

        deps = CHANNEL_DEPS.get(self._selected_channel, [])
        if not deps:
            return

        missing_pip: list[str] = []
        missing_display: list[str] = []
        for import_name, pip_name in deps:
            try:
                importlib.import_module(import_name)
            except ImportError:
                missing_pip.append(pip_name)
                missing_display.append(f"{pip_name} ({import_name})")

        if not missing_pip:
            console.print(
                f"  [green]✓[/green] {self._selected_channel} 通道依赖已就绪"
            )
            return

        console.print(
            f"\n  [yellow]⚠[/yellow] {self._selected_channel} 通道缺少依赖: "
            f"[bold]{', '.join(missing_display)}[/bold]"
        )

        # 尝试自动安装
        if IS_FROZEN:
            # 打包环境：复用 main.py 的 _ensure_channel_deps 逻辑（启动时会再试一次）
            console.print(
                "  [dim]打包环境下依赖将在服务启动时自动安装；\n"
                "  若启动后仍不可用，请前往「设置中心 → Python 环境」点击「一键修复」[/dim]"
            )
            self._channel_deps_ok = False
            self._channel_deps_missing = missing_pip
            return

        do_install = Confirm.ask(
            f"  是否立即安装? (pip install {' '.join(missing_pip)})", default=True
        )
        if not do_install:
            self._channel_deps_ok = False
            self._channel_deps_missing = missing_pip
            extra = CHANNEL_EXTRAS.get(self._selected_channel, "")
            if extra:
                console.print(
                    f"  [dim]稍后可运行: pip install openakita[{extra}][/dim]"
                )
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                f"Installing {', '.join(missing_pip)}...", total=None
            )
            try:
                cmd = [
                    sys.executable, "-m", "pip", "install",
                    "--prefer-binary",
                    *missing_pip,
                ]
                extra_kw: dict = {}
                if sys.platform == "win32":
                    extra_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                    **extra_kw,
                )
                if result.returncode == 0:
                    importlib.invalidate_caches()
                    still_missing = []
                    for import_name, pip_name in deps:
                        try:
                            importlib.import_module(import_name)
                        except ImportError:
                            still_missing.append(pip_name)
                    if not still_missing:
                        progress.update(
                            task,
                            description="[green]✓ 依赖安装成功![/green]",
                        )
                        self._channel_deps_ok = True
                        self._channel_deps_missing = []
                    else:
                        progress.update(
                            task,
                            description=f"[yellow]⚠ 安装后仍缺少: {', '.join(still_missing)}[/yellow]",
                        )
                        self._channel_deps_ok = False
                        self._channel_deps_missing = still_missing
                else:
                    err_tail = (result.stderr or result.stdout or "").strip()[-200:]
                    progress.update(
                        task,
                        description=f"[red]✗ 安装失败 (exit {result.returncode})[/red]",
                    )
                    if err_tail:
                        console.print(f"  [dim]{err_tail}[/dim]")
                    self._channel_deps_ok = False
                    self._channel_deps_missing = missing_pip
            except subprocess.TimeoutExpired:
                progress.update(
                    task, description="[red]✗ 安装超时 (120s)[/red]"
                )
                self._channel_deps_ok = False
                self._channel_deps_missing = missing_pip
            except Exception as e:
                progress.update(
                    task,
                    description=f"[red]✗ 安装异常: {e}[/red]",
                )
                self._channel_deps_ok = False
                self._channel_deps_missing = missing_pip

        console.print()

    def _verify_channel_credentials(self):
        """对已选通道做轻量凭证/连通性验证（可选）。"""
        if not self._selected_channel:
            return
        if not self._channel_deps_ok and self._selected_channel != "telegram":
            console.print(
                "  [dim]跳过通道连通性测试（依赖未就绪）[/dim]\n"
            )
            return

        # 仅对有简易验证 API 的通道提供测试
        verifiers: dict[str, tuple[str, callable]] = {
            "dingtalk": ("DingTalk", self._verify_dingtalk),
            "feishu": ("Feishu", self._verify_feishu),
            "telegram": ("Telegram", self._verify_telegram),
        }

        entry = verifiers.get(self._selected_channel)
        if entry is None:
            return

        display_name, verify_fn = entry
        do_test = Confirm.ask(
            f"  Test {display_name} credentials now?", default=True
        )
        if not do_test:
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"Verifying {display_name} credentials...", total=None
            )
            try:
                ok, detail = verify_fn()
                if ok:
                    progress.update(
                        task,
                        description=f"[green]✓ {display_name} credentials valid! {detail}[/green]",
                    )
                else:
                    progress.update(
                        task,
                        description=f"[red]✗ {display_name} verification failed: {detail}[/red]",
                    )
            except Exception as e:
                progress.update(
                    task,
                    description=f"[yellow]! Could not verify: {e}[/yellow]",
                )
        console.print()

    def _verify_dingtalk(self) -> tuple[bool, str]:
        """验证钉钉凭证：请求 access_token。"""
        import httpx

        client_id = self.config.get("DINGTALK_CLIENT_ID", "")
        client_secret = self.config.get("DINGTALK_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            return False, "Client ID or Secret is empty"

        with httpx.Client(timeout=10) as client:
            resp = client.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={"appKey": client_id, "appSecret": client_secret},
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("accessToken"):
                return True, ""
            return False, data.get("message", "No accessToken in response")
        return False, f"HTTP {resp.status_code}"

    def _verify_feishu(self) -> tuple[bool, str]:
        """验证飞书凭证：请求 tenant_access_token。"""
        import httpx

        app_id = self.config.get("FEISHU_APP_ID", "")
        app_secret = self.config.get("FEISHU_APP_SECRET", "")
        if not app_id or not app_secret:
            return False, "App ID or Secret is empty"

        with httpx.Client(timeout=10) as client:
            resp = client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                return True, ""
            return False, data.get("msg", f"code={data.get('code')}")
        return False, f"HTTP {resp.status_code}"

    def _verify_telegram(self) -> tuple[bool, str]:
        """验证 Telegram Bot Token：调用 getMe。"""
        import httpx

        token = self.config.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return False, "Bot token is empty"

        proxy = self.config.get("TELEGRAM_PROXY", "") or None
        with httpx.Client(timeout=10, proxy=proxy) as client:
            resp = client.get(f"https://api.telegram.org/bot{token}/getMe")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                bot_name = data.get("result", {}).get("username", "")
                return True, f"@{bot_name}" if bot_name else ""
            return False, data.get("description", "Unknown error")
        return False, f"HTTP {resp.status_code}"

    def _test_connection(self):
        """测试 API 连接"""
        self._step_screen(11, "Testing Connection")

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

                first_ep = self._llm_endpoints[0] if self._llm_endpoints else {}
                api_key_env = first_ep.get("api_key_env", "ANTHROPIC_API_KEY")
                api_key = self.config.get(api_key_env, self.config.get("ANTHROPIC_API_KEY", ""))
                base_url = first_ep.get("base_url", self.config.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
                model = first_ep.get("model", self.config.get("DEFAULT_MODEL", "claude-sonnet-4-20250514"))
                is_anthropic = first_ep.get("api_type", "openai") == "anthropic"

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
                    from openakita.llm.types import normalize_base_url

                    url = f"{normalize_base_url(base_url)}/chat/completions"
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
                            description=f"[yellow]! API returned status {response.status_code}[/yellow]",
                        )

            except Exception as e:
                progress.update(task, description=f"[yellow]! Could not test: {e}[/yellow]")

        console.print()

    def _show_completion(self):
        """显示完成信息"""
        console.clear()
        parts = [
            "# Setup Complete!",
            "",
            "OpenAkita has been configured successfully.",
            "",
            "## Quick Start",
            "",
            "**Start the CLI:**",
            "```bash",
            "openakita",
            "```",
            "",
            "**Or run as service (Telegram/IM):**",
            "```bash",
            "openakita serve",
            "```",
            "",
            "## Configuration Files",
            "",
            "- `.env` - Environment variables",
            "- `identity/SOUL.md` - Agent personality",
            "- `data/` - Database and cache",
        ]

        # 若 IM 通道依赖未安装成功，动态追加提示
        if self._selected_channel and not self._channel_deps_ok and self._channel_deps_missing:
            from openakita.channels.deps import CHANNEL_EXTRAS

            extra = CHANNEL_EXTRAS.get(self._selected_channel, "")
            parts.append("")
            parts.append("## IM Channel Dependencies (action required)")
            parts.append("")
            parts.append(
                f"The **{self._selected_channel}** channel requires additional "
                f"dependencies that are not yet installed:"
            )
            parts.append("")
            for pkg in self._channel_deps_missing:
                parts.append(f"- `{pkg}`")
            parts.append("")
            if extra:
                parts.append(f"Install with: `pip install openakita[{extra}]`")
            else:
                parts.append(
                    f"Install with: `pip install {' '.join(self._channel_deps_missing)}`"
                )
            parts.append("")
            parts.append(
                "Without these dependencies the IM channel will **not start**."
            )

        parts.extend([
            "",
            "## Next Steps",
            "",
            "1. Customize `identity/SOUL.md` to personalize your agent",
            "2. Run `openakita` to start chatting",
            "3. Check `openakita --help` for all commands",
            "",
            "## Documentation",
            "",
            "- GitHub: https://github.com/openakita/openakita",
            "- Docs: https://github.com/openakita/openakita/tree/main/docs",
            "",
            "Enjoy your loyal AI companion!",
        ])

        console.print(
            Panel(
                Markdown("\n".join(parts)),
                title="Setup Complete",
                border_style="green",
            )
        )


def run_wizard(project_dir: str | None = None, *, quick: bool = False):
    """运行安装向导的入口函数"""
    path = Path(project_dir) if project_dir else Path.cwd()
    wizard = SetupWizard(path)
    return wizard.run(quick=quick)
