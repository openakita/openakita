"""
OpenAkita interactive setup wizard.

One-click entry point that walks the user through all configuration steps.
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
        masked = value[:3] + "*" * (len(value) - 3) if len(value) > 8 else "*" * len(value)
        console.print(f"  [dim]Received: {masked}[/dim]")
    return value


_CHINA_SLUGS = {
    "dashscope",
    "kimi-cn",
    "minimax-cn",
    "siliconflow",
    "volcengine",
    "zhipu-cn",
    "qianfan",
    "hunyuan",
    "yunwu",
    "longcat",
    "iflow",
}


def _load_providers() -> list[dict]:
    """Load provider definitions from the shared providers.json."""
    providers_path = Path(__file__).resolve().parents[1] / "llm" / "registries" / "providers.json"
    return json.loads(providers_path.read_text(encoding="utf-8"))


class SetupWizard:
    """Interactive setup wizard."""

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
        """Run the setup wizard.

        Args:
            quick: quick mode — only Provider + API Key + Model.
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
            console.print("\n\n[yellow]Setup cancelled[/yellow]")
            return False
        except Exception as e:
            console.print(f"\n[red]Setup error: {e}[/red]")
            return False

    def _run_quick(self) -> bool:
        """Quick mode: only Provider + API Key + Model, then write .env and test."""
        console.print(
            Panel(
                "[bold cyan]Quick Setup Mode[/bold cyan]\nThree steps: choose Provider -> enter API Key -> select Model",
                border_style="cyan",
            )
        )
        console.print()

        self._create_directories()
        self._configure_llm()
        self._write_env_file()

        # Test connection (offer a recovery menu on failure)
        while True:
            success = self._test_connection_safe()
            if success:
                break
            console.print()
            choice = Prompt.ask(
                "[bold]Test failed. How do you want to continue?[/bold]\n"
                "  [cyan]1[/cyan] Retry connection test\n"
                "  [cyan]2[/cyan] Modify LLM configuration\n"
                "  [cyan]3[/cyan] Skip test, finish configuration",
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
        """Run the connection test, catching exceptions and returning success/failure."""
        try:
            self._test_connection()
            return True
        except Exception as e:
            console.print(f"[red]Connection test failed: {e}[/red]")
            return False

    def _show_welcome(self):
        """Display the welcome screen."""
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
        """Show the risk acknowledgment and require the user to confirm."""
        console.clear()
        agreement_text = """
## Risk Acknowledgment

OpenAkita is an AI Agent application powered by Large Language Models (LLMs).
Before using it, you must understand and accept the following:

**1. Behavior is not fully predictable**
The behavior of the AI Agent is driven by the underlying LLM, and its outputs are probabilistic
and non-deterministic. Even with identical inputs, the Agent may produce different results,
including but not limited to: performing unintended file operations, sending unintended messages,
or invoking unintended tools.

**2. Supervision is required during use**
You are responsible for supervising the AI Agent's behavior. For tool calls that require
approval (such as file deletion, system command execution, message sending, etc.), please
confirm that the operation is reasonable before approving it. We strongly recommend against
enabling auto-confirm mode (AUTO_CONFIRM) without supervision.

**3. Possible risks**
While performing tasks, the AI Agent may cause:
- Data loss or corruption (e.g. accidental file deletion, overwriting important data)
- Sending inappropriate messages (e.g. posting incorrect content through IM channels)
- Execution of dangerous system commands
- Unintended API calls and associated costs
- Other unforeseen side effects

**4. Disclaimer**
OpenAkita is provided "AS IS" without warranty of any kind, express or implied.
The project maintainers and contributors shall not be liable for any direct, indirect,
incidental, special, or consequential damages arising from the use of this software.
You assume all risk associated with its use.

**5. Data security**
Your conversation content, configuration data, and tool-call records may be sent to
third-party LLM providers. Do not provide sensitive personal information, passwords,
keys, or other confidential data in conversations unless you fully understand and
accept the associated risks.
"""
        console.print(
            Panel(Markdown(agreement_text), title="Risk Acknowledgment", border_style="yellow")
        )
        console.print()

        if not Confirm.ask(
            "[bold]Have you read and agreed to the risk acknowledgment above?[/bold]",
            default=False,
        ):
            console.print("\n[red]Risk acknowledgment not accepted; setup wizard has exited.[/red]")
            console.print("[dim]To continue, rerun openakita init.[/dim]")
            sys.exit(1)
        console.print("\n[green]✓ Confirmed, continuing with the setup wizard.[/green]\n")

    def _check_environment(self):
        """Check the runtime environment."""
        self._step_screen(1, "Checking Environment")

        checks = []

        # Python version
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

        # Check whether we're in a virtual environment
        in_venv = sys.prefix != sys.base_prefix
        checks.append(
            (
                "Virtual Environment",
                "Active" if in_venv else "Not detected",
                True,  # not required
                "Recommended",
            )
        )

        # Check that the directory is writable
        writable = os.access(self.project_dir, os.W_OK)
        checks.append(("Directory Writable", str(self.project_dir), writable, "Required"))

        # Display check results
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
    # Language / region selection — affects all subsequent defaults
    # ------------------------------------------------------------------

    def _detect_locale(self) -> str:
        """Attempt to detect the language from the system locale (used as a default suggestion only)."""
        import locale

        try:
            lang, _ = locale.getdefaultlocale()
            if lang and lang.lower().startswith("zh"):
                return "zh"
        except Exception:
            pass
        return "en"

    def _choose_locale(self):
        """Select language/region and derive sensible defaults for the rest of the setup."""
        self._step_screen(2, "Language & Region")
        console.print(
            "This affects default settings for model downloads, voice recognition, etc.\n"
        )

        detected = self._detect_locale()
        default_choice = "1" if detected == "zh" else "2"

        console.print("  [1] Chinese / Mainland China")
        console.print("  [2] English / International\n")

        choice = Prompt.ask(
            "Select language / region",
            choices=["1", "2"],
            default=default_choice,
        )

        if choice == "1":
            self._locale = "zh"
            # China-region defaults
            self._defaults = {
                "MODEL_DOWNLOAD_SOURCE": "hf-mirror",
                "EMBEDDING_MODEL": "shibing624/text2vec-base-chinese",
                "WHISPER_LANGUAGE": "zh",
                "SCHEDULER_TIMEZONE": "Asia/Shanghai",
            }
            console.print("\n[green]Selected: Chinese / Mainland China[/green]")
            console.print("[dim]Models will download from domestic mirrors by default; voice recognition defaults to Chinese[/dim]\n")
        else:
            self._locale = "en"
            # International defaults
            self._defaults = {
                "MODEL_DOWNLOAD_SOURCE": "huggingface",
                "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
                "WHISPER_LANGUAGE": "en",
                "SCHEDULER_TIMEZONE": "UTC",
            }
            console.print("\n[green]Selected: English / International[/green]")
            console.print(
                "[dim]Models will download from HuggingFace, voice recognition defaults to English[/dim]\n"
            )

    def _create_directories(self):
        """Create the required directory structure."""
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

            # Create .gitkeep
            gitkeep = dir_path / ".gitkeep"
            if not gitkeep.exists():
                gitkeep.touch()

            console.print(f"  [green]✓[/green] {dir_name}/ - {description}")

        console.print("\n[green]Directories created![/green]\n")

    # ------------------------------------------------------------------
    # LLM endpoint configuration (Provider -> Coding Plan -> URL -> Key -> Model)
    # ------------------------------------------------------------------

    def _configure_llm(self):
        """Configure LLM endpoints (supports adding multiple endpoints in a loop)."""
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
            self.config.setdefault(
                "ANTHROPIC_API_KEY", self.config.get(first.get("api_key_env", ""), "")
            )
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
        console.print("\n[green]LLM configuration complete![/green]")
        console.print(f"[dim]Advanced endpoint settings can be edited in {endpoints_path}[/dim]\n")

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

            console.print(
                f"\n[bold]Models (page {page + 1}/{total_pages}, {len(models)} total):[/bold]\n"
            )
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
        """Configure a dedicated model for the Prompt Compiler (optional)."""
        self._step_screen(5, "Configure Prompt Compiler Model (Optional)")

        console.print(
            "The Prompt Compiler uses a fast small model to preprocess user instructions, which can significantly reduce response latency.\n"
            "A low-latency model such as qwen-turbo or gpt-4o-mini is recommended; thinking mode is not needed.\n"
            "If you skip this step, the system will fall back to the main model at runtime (slower).\n"
        )

        configure = Confirm.ask("Configure Prompt Compiler?", default=True)

        if not configure:
            console.print(
                "[dim]Skipping Compiler configuration (will use main model as fallback).[/dim]\n"
            )
            return

        # Select provider
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
            compiler_config["model"] = Prompt.ask("Model name", default="qwen-turbo-latest")
            # Check whether a separate API key needs to be configured
            existing_key = self.config.get("DASHSCOPE_API_KEY") or os.environ.get(
                "DASHSCOPE_API_KEY"
            )
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

        # Optionally add a backup endpoint
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
            # If the env var differs from the primary compiler's, set its key
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
        """Write LLM and Compiler endpoints to data/llm_endpoints.json."""
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
            compiler_endpoints.append(
                {
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
                    "note": "Prompt Compiler primary endpoint (fast model, thinking disabled)",
                }
            )

        backup_cfg = self.config.get("_compiler_backup")
        if backup_cfg:
            compiler_endpoints.append(
                {
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
                    "note": "Prompt Compiler backup endpoint",
                }
            )

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
        """Configure IM channels."""
        self._step_screen(6, "Configure IM Channels (Optional)")

        setup_im = Confirm.ask(
            "Would you like to set up an IM channel (Telegram, etc.)?", default=False
        )

        if not setup_im:
            console.print("[dim]Skipping IM channel configuration.[/dim]\n")
            return

        # Select a channel
        console.print("\nAvailable channels:\n")
        console.print("  [1] Telegram (recommended)")
        console.print("  [2] Feishu (Lark)")
        console.print("  [3] WeCom (WeChat Work)")
        console.print("  [4] DingTalk")
        console.print("  [5] OneBot (NapCat / Lagrange, etc.)")
        console.print("  [6] QQ Official Bot")
        console.print("  [7] Skip\n")

        choice = Prompt.ask(
            "Select channel", choices=["1", "2", "3", "4", "5", "6", "7"], default="7"
        )

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
        """Configure Telegram."""
        console.print("\n[bold]Telegram Bot Configuration[/bold]\n")
        console.print("To create a bot, message @BotFather on Telegram and use /newbot\n")

        token = _ask_secret("Enter your Bot Token")
        self.config["TELEGRAM_ENABLED"] = "true"
        self.config["TELEGRAM_BOT_TOKEN"] = token

        use_pairing = Confirm.ask("Require pairing code for new users?", default=True)
        self.config["TELEGRAM_REQUIRE_PAIRING"] = "true" if use_pairing else "false"

        # Webhook (optional)
        webhook_url = Prompt.ask("Webhook URL (leave empty for long-polling)", default="")
        if webhook_url:
            self.config["TELEGRAM_WEBHOOK_URL"] = webhook_url

        # Proxy configuration (common for users in mainland China)
        use_proxy = Confirm.ask(
            "Use a proxy for Telegram? (recommended in mainland China)", default=False
        )
        if use_proxy:
            proxy = Prompt.ask(
                "Enter proxy URL",
                default="http://127.0.0.1:7890",
            )
            self.config["TELEGRAM_PROXY"] = proxy

    def _configure_feishu(self):
        """Configure Feishu (supports QR-code create / manual input / use existing credentials)."""
        console.print("\n[bold]Feishu (Lark) Configuration[/bold]\n")

        existing_id = self.config.get("FEISHU_APP_ID", "")
        existing_secret = self.config.get("FEISHU_APP_SECRET", "")

        choices = ["1", "2"]
        console.print("  [cyan]1[/cyan]  Create a Feishu bot via QR code (recommended)")
        console.print("  [cyan]2[/cyan]  Enter App ID / App Secret manually")
        if existing_id and existing_secret:
            choices.append("3")
            masked = existing_id[:4] + "****"
            console.print(f"  [cyan]3[/cyan]  Use existing credentials ({masked})")

        mode = Prompt.ask("Select method", choices=choices, default="1")

        if mode == "1":
            self._feishu_qr_onboard()
        elif mode == "2":
            app_id = Prompt.ask("Enter App ID")
            app_secret = _ask_secret("Enter App Secret")
            self.config["FEISHU_APP_ID"] = app_id
            self.config["FEISHU_APP_SECRET"] = app_secret
        else:
            console.print(f"  [dim]Keeping existing credentials: {existing_id[:4]}****[/dim]")

        self.config["FEISHU_ENABLED"] = "true"

        # Streaming output configuration
        console.print()
        streaming = Confirm.ask("Enable streaming card output? (shows AI replies in real time)", default=True)
        self.config["FEISHU_STREAMING_ENABLED"] = "true" if streaming else "false"
        if streaming:
            group_streaming = Confirm.ask("Also enable streaming output in group chats?", default=True)
            self.config["FEISHU_GROUP_STREAMING"] = "true" if group_streaming else "false"

        # Group-chat response mode
        console.print()
        console.print("Group-chat response mode:")
        console.print("  [cyan]1[/cyan]  mention_only — reply only when @mentioned (default)")
        console.print("  [cyan]2[/cyan]  smart — intelligently decide whether to reply")
        console.print("  [cyan]3[/cyan]  always — reply to every message")
        grp_mode = Prompt.ask("Select", choices=["1", "2", "3"], default="1")
        mode_map = {"1": "mention_only", "2": "smart", "3": "always"}
        self.config["FEISHU_GROUP_RESPONSE_MODE"] = mode_map[grp_mode]

    def _feishu_qr_onboard(self):
        """Run the Feishu Device Flow QR-code app creation."""
        import asyncio as _asyncio

        from openakita.setup.feishu_onboard import (
            FeishuOnboard,
            FeishuOnboardError,
            render_qr_terminal,
        )

        domain = Prompt.ask("Feishu edition", choices=["feishu", "lark"], default="feishu")
        ob = FeishuOnboard(domain=domain)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Initializing Device Flow...", total=None)
            try:
                init_data = _asyncio.run(ob.init())
                device_code = init_data["device_code"]
                _asyncio.run(ob.begin(device_code))
            except Exception as e:
                console.print(f"[red]Initialization failed: {e}[/red]")
                console.print("[dim]Please switch to manual entry[/dim]")
                app_id = Prompt.ask("Enter App ID")
                app_secret = _ask_secret("Enter App Secret")
                self.config["FEISHU_APP_ID"] = app_id
                self.config["FEISHU_APP_SECRET"] = app_secret
                return
            progress.remove_task(task)

        verification_uri = init_data.get("verification_uri", "")
        console.print(
            Panel(
                f"Scan the QR code below with the Feishu app to authorize\n\n"
                f"Or open in a browser: [link]{verification_uri}[/link]",
                title="Feishu QR authorization",
                border_style="green",
            )
        )
        render_qr_terminal(verification_uri)

        console.print("\n[dim]Waiting for QR authorization (up to 3 minutes)...[/dim]")
        try:
            result = _asyncio.run(ob.poll_until_done(device_code, interval=3.0, max_attempts=60))
            app_id = result.get("app_id", "")
            app_secret = result.get("app_secret", "")
            if app_id and app_secret:
                self.config["FEISHU_APP_ID"] = app_id
                self.config["FEISHU_APP_SECRET"] = app_secret
                console.print(f"[green]✓ Authorization succeeded! App ID: {app_id[:4]}****[/green]")
            else:
                console.print("[yellow]Incomplete authorization response; please enter manually[/yellow]")
                self.config["FEISHU_APP_ID"] = Prompt.ask("Enter App ID")
                self.config["FEISHU_APP_SECRET"] = _ask_secret("Enter App Secret")
        except FeishuOnboardError as e:
            console.print(f"[red]QR authorization timed out or was rejected: {e}[/red]")
            console.print("[dim]Please switch to manual entry[/dim]")
            self.config["FEISHU_APP_ID"] = Prompt.ask("Enter App ID")
            self.config["FEISHU_APP_SECRET"] = _ask_secret("Enter App Secret")

    def _configure_wework(self):
        """Configure WeCom (WeChat Work)."""
        console.print("\n[bold]WeCom Configuration[/bold]\n")
        console.print("Note: WeCom callback requires a public URL (use ngrok/frp/cpolar)\n")

        corp_id = Prompt.ask("Enter Corp ID")

        self.config["WEWORK_ENABLED"] = "true"
        self.config["WEWORK_CORP_ID"] = corp_id

        # Callback encryption/decryption configuration (required for Smart Bot)
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
        """Configure DingTalk."""
        console.print("\n[bold]DingTalk Configuration[/bold]\n")

        app_key = Prompt.ask("Enter App Key")
        app_secret = _ask_secret("Enter App Secret")

        self.config["DINGTALK_ENABLED"] = "true"
        self.config["DINGTALK_CLIENT_ID"] = app_key
        self.config["DINGTALK_CLIENT_SECRET"] = app_secret

    def _configure_onebot(self):
        """Configure the OneBot-protocol channel."""
        console.print("\n[bold]OneBot Configuration[/bold]\n")
        console.print("The OneBot channel requires deploying a OneBot implementation such as NapCat or Lagrange first.\n")
        console.print("Reference: https://github.com/botuniverse/onebot-11\n")

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
                f"\n[dim]On NapCat, configure a WebSocket client pointing at "
                f"ws://<this-machine-IP>:{reverse_port}[/dim]\n"
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
        """Configure the QQ Official Bot."""
        console.print("\n[bold]QQ Official Bot Configuration[/bold]\n")
        console.print("Create a bot and obtain credentials from the QQ Open Platform (https://q.qq.com).\n")

        app_id = Prompt.ask("Enter AppID")
        app_secret = _ask_secret("Enter AppSecret")

        self.config["QQBOT_ENABLED"] = "true"
        self.config["QQBOT_APP_ID"] = app_id
        self.config["QQBOT_APP_SECRET"] = app_secret

        use_sandbox = Confirm.ask("Enable sandbox mode (test environment)?", default=True)
        self.config["QQBOT_SANDBOX"] = "true" if use_sandbox else "false"

        # Access mode
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
        """Configure the memory system."""
        self._step_screen(7, "Configure Memory System")

        console.print("OpenAkita uses vector embeddings for semantic memory search.\n")

        # Derive default options from the locale
        defaults = getattr(self, "_defaults", {})
        default_embed = defaults.get("EMBEDDING_MODEL", "shibing624/text2vec-base-chinese")
        default_src = defaults.get("MODEL_DOWNLOAD_SOURCE", "auto")

        # Embedding model selection
        models_list = [
            ("1", "shibing624/text2vec-base-chinese", "Chinese optimized (~100MB)"),
            ("2", "sentence-transformers/all-MiniLM-L6-v2", "English optimized (~90MB)"),
            (
                "3",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                "Multilingual (~120MB)",
            ),
        ]
        # Find the index of the default option
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

        # GPU acceleration
        use_gpu = Confirm.ask("Use GPU for embeddings (requires CUDA)?", default=False)
        self.config["EMBEDDING_DEVICE"] = "cuda" if use_gpu else "cpu"

        # Model download source
        src_options = [
            ("1", "auto", "Auto (picks the fastest source automatically)"),
            ("2", "hf-mirror", "hf-mirror (domestic HuggingFace mirror)"),
            ("3", "modelscope", "ModelScope"),
            ("4", "huggingface", "HuggingFace (official source)"),
        ]
        # Derive default option from the locale
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
        """Configure voice recognition (Whisper)."""
        self._step_screen(8, "Voice Recognition (Optional)")

        use_voice = Confirm.ask("Enable local voice recognition (Whisper)?", default=True)
        if not use_voice:
            self.config.setdefault("WHISPER_MODEL", "base")
            self.config.setdefault(
                "WHISPER_LANGUAGE", getattr(self, "_defaults", {}).get("WHISPER_LANGUAGE", "zh")
            )
            console.print(
                "[dim]Voice will be configured with defaults, model downloads on first use.[/dim]\n"
            )
            return

        defaults = getattr(self, "_defaults", {})
        default_lang = defaults.get("WHISPER_LANGUAGE", "zh")

        # Language selection
        console.print("Voice recognition language:\n")
        lang_options = [
            ("1", "zh", "Chinese"),
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

        # Model size selection
        console.print("\nWhisper model size:\n")
        model_options = [
            ("1", "tiny", "Tiny (~39MB)  - fastest, lower accuracy"),
            ("2", "base", "Base (~74MB)  - recommended, balanced"),
            ("3", "small", "Small (~244MB) - good accuracy"),
            ("4", "medium", "Medium (~769MB) - high accuracy"),
            ("5", "large", "Large (~1.5GB) - highest accuracy, resource-heavy"),
        ]
        # For English, the .en models are smaller — hint the user
        if whisper_lang == "en":
            console.print(
                "[dim]  Note: English .en models are auto-selected and are more efficient[/dim]\n"
            )

        model_choice = Prompt.ask(
            "Select model size",
            choices=["1", "2", "3", "4", "5"],
            default="2",
        )
        self.config["WHISPER_MODEL"] = {n: m for n, m, _ in model_options}[model_choice]

        console.print("\n[green]Voice configuration complete![/green]\n")

    def _configure_advanced(self):
        """Advanced configuration."""
        self._step_screen(9, "Advanced Configuration (Optional)")

        configure_advanced = Confirm.ask("Configure advanced options?", default=False)

        if not configure_advanced:
            # Use defaults
            self.config.setdefault("MAX_TOKENS", "0")
            self.config.setdefault("MAX_ITERATIONS", "300")
            self.config.setdefault("LOG_LEVEL", "INFO")
            console.print("[dim]Using default advanced settings.[/dim]\n")
            return

        # Max tokens
        max_tokens = Prompt.ask("Max output tokens (0=unlimited)", default="0")
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
            choices=[
                "default",
                "business",
                "tech_expert",
                "butler",
                "girlfriend",
                "boyfriend",
                "family",
                "jarvis",
            ],
            default="default",
        )
        if persona != "default":
            self.config["PERSONA_NAME"] = persona

        # Sticker
        use_sticker = Confirm.ask("Enable sticker (emoji packs) in IM?", default=True)
        self.config["STICKER_ENABLED"] = "true" if use_sticker else "false"

        # Proactive (living presence)
        use_proactive = Confirm.ask(
            "Enable living-presence mode? (proactive greetings & follow-ups)", default=False
        )
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

        # Scheduler
        console.print("\n[bold]Scheduler Configuration:[/bold]")
        use_scheduler = Confirm.ask("Enable task scheduler? (recommended)", default=True)
        self.config["SCHEDULER_ENABLED"] = "true" if use_scheduler else "false"
        if use_scheduler:
            defaults = getattr(self, "_defaults", {})
            tz = Prompt.ask(
                "  Timezone", default=defaults.get("SCHEDULER_TIMEZONE", "Asia/Shanghai")
            )
            self.config["SCHEDULER_TIMEZONE"] = tz

        # Session
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
        """Write the .env file."""
        self._step_screen(10, "Saving Configuration")

        # Check whether one already exists
        if self.env_path.exists():
            overwrite = Confirm.ask(
                f".env file already exists at {self.env_path}. Overwrite?", default=True
            )
            if not overwrite:
                console.print("  [dim]Keeping existing .env file.[/dim]")
                console.print("  [dim]New configuration saved to .env.new for reference.[/dim]")
                # Write the new configuration to .env.new for reference
                env_content = self._generate_env_content()
                new_path = self.env_path.parent / ".env.new"
                new_path.write_text(env_content, encoding="utf-8")
                console.print(f"  [green]✓[/green] Reference config saved to {new_path}")
                # Still write the llm_endpoints config
                self._write_llm_endpoints()
                return

        # Build the .env content
        env_content = self._generate_env_content()

        # Write the file
        self.env_path.write_text(env_content, encoding="utf-8")
        console.print(f"  [green]✓[/green] Configuration saved to {self.env_path}")

        # Write llm_endpoints.json (main model endpoint + Compiler endpoints)
        self._write_llm_endpoints()

        # Create identity example files
        self._create_identity_examples()

        console.print("\n[green]Configuration saved![/green]\n")

    def _generate_env_content(self) -> str:
        """Generate the .env file content."""
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

        lines.extend(
            [
                "",
                "# ========== Model Configuration ==========",
                f"DEFAULT_MODEL={self.config.get('DEFAULT_MODEL', 'claude-sonnet-4-20250514')}",
                f"MAX_TOKENS={self.config.get('MAX_TOKENS', '0')}",
                f"THINKING_MODE={self.config.get('THINKING_MODE', 'auto')}",
            ]
        )

        lines.extend(
            [
                "",
                "# ========== Agent Configuration ==========",
                "AGENT_NAME=OpenAkita",
                f"MAX_ITERATIONS={self.config.get('MAX_ITERATIONS', '300')}  # Max iterations of the ReAct loop",
                "AUTO_CONFIRM=false  # Whether tool calls are auto-confirmed (no human approval)",
                "SELFCHECK_AUTOFIX=true  # Whether the Agent auto-fixes issues found in self-check",
                "FORCE_TOOL_CALL_MAX_RETRIES=2  # Forced retry count when the LLM returns no tool call",
                "TOOL_MAX_PARALLEL=1  # Max number of parallel tool calls",
                "# ALLOW_PARALLEL_TOOLS_WITH_INTERRUPT_CHECKS=false",
                "",
                "# ========== Timeout ==========",
                "PROGRESS_TIMEOUT_SECONDS=600  # Task no-progress timeout (seconds); 0 = unlimited",
                "HARD_TIMEOUT_SECONDS=0  # Hard task timeout (seconds); 0 = unlimited",
                "",
                "# ========== Paths & Logging ==========",
                "DATABASE_PATH=data/agent.db",
                f"LOG_LEVEL={self.config.get('LOG_LEVEL', 'INFO')}",
                "LOG_DIR=logs  # Log file directory",
                "LOG_FILE_PREFIX=openakita  # Log file name prefix",
                "LOG_MAX_SIZE_MB=10  # Max size of a single log file (MB)",
                "LOG_BACKUP_COUNT=30  # Number of log files retained",
                "LOG_RETENTION_DAYS=30  # Log file retention in days",
                "LOG_TO_CONSOLE=true  # Whether to log to the console",
                "LOG_TO_FILE=true  # Whether to log to file",
                "# LOG_FORMAT=%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "",
                "# ========== Tools ==========",
                "MCP_ENABLED=true  # Enable MCP tool server",
                "DESKTOP_ENABLED=true  # Enable desktop automation (screenshots/keyboard/mouse)",
                "",
            ]
        )

        # Network proxy
        if self.config.get("HTTP_PROXY") or self.config.get("HTTPS_PROXY"):
            lines.extend(
                [
                    "# ========== Network Proxy ==========",
                    f"HTTP_PROXY={self.config.get('HTTP_PROXY', '')}",
                    f"HTTPS_PROXY={self.config.get('HTTPS_PROXY', '')}",
                    "# ALL_PROXY=",
                    "# FORCE_IPV4=false",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "# ========== Network Proxy (optional) ==========",
                    "# HTTP_PROXY=http://127.0.0.1:7890",
                    "# HTTPS_PROXY=http://127.0.0.1:7890",
                    "# ALL_PROXY=socks5://127.0.0.1:1080",
                    "# FORCE_IPV4=false",
                    "",
                ]
            )

        # GitHub Token
        if self.config.get("GITHUB_TOKEN"):
            lines.extend(
                [
                    "# ========== GitHub Token ==========",
                    f"GITHUB_TOKEN={self.config['GITHUB_TOKEN']}",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "# ========== GitHub Token (optional) ==========",
                    "# GITHUB_TOKEN=",
                    "",
                ]
            )

        # Whisper
        whisper_lang = self.config.get("WHISPER_LANGUAGE", "zh")
        lines.extend(
            [
                "# ========== Voice (optional) ==========",
                f"WHISPER_MODEL={self.config.get('WHISPER_MODEL', 'base')}",
                f"WHISPER_LANGUAGE={whisper_lang}",
                "",
            ]
        )

        # IM channel configuration
        lines.append("# ========== IM Channels ==========")

        if self.config.get("TELEGRAM_ENABLED"):
            lines.extend(
                [
                    f"TELEGRAM_ENABLED={self.config.get('TELEGRAM_ENABLED', 'false')}",
                    f"TELEGRAM_BOT_TOKEN={self.config.get('TELEGRAM_BOT_TOKEN', '')}",
                    f"TELEGRAM_REQUIRE_PAIRING={self.config.get('TELEGRAM_REQUIRE_PAIRING', 'true')}",
                ]
            )
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
            lines.extend(
                [
                    "TELEGRAM_ENABLED=false",
                    "# TELEGRAM_BOT_TOKEN=",
                    "# TELEGRAM_WEBHOOK_URL=",
                    "# TELEGRAM_PAIRING_CODE=",
                    "# TELEGRAM_PROXY=",
                ]
            )
        lines.append("")

        if self.config.get("FEISHU_ENABLED"):
            lines.extend(
                [
                    f"FEISHU_ENABLED={self.config.get('FEISHU_ENABLED', 'false')}",
                    f"FEISHU_APP_ID={self.config.get('FEISHU_APP_ID', '')}",
                    f"FEISHU_APP_SECRET={self.config.get('FEISHU_APP_SECRET', '')}",
                    f"FEISHU_STREAMING_ENABLED={self.config.get('FEISHU_STREAMING_ENABLED', 'true')}",
                    f"FEISHU_GROUP_STREAMING={self.config.get('FEISHU_GROUP_STREAMING', 'true')}",
                    f"FEISHU_GROUP_RESPONSE_MODE={self.config.get('FEISHU_GROUP_RESPONSE_MODE', 'mention_only')}",
                ]
            )
        else:
            lines.extend(
                [
                    "FEISHU_ENABLED=false",
                    "# FEISHU_APP_ID=",
                    "# FEISHU_APP_SECRET=",
                    "# FEISHU_STREAMING_ENABLED=true",
                    "# FEISHU_GROUP_STREAMING=true",
                    "# FEISHU_GROUP_RESPONSE_MODE=mention_only",
                ]
            )
        lines.append("")

        if self.config.get("WEWORK_ENABLED"):
            lines.extend(
                [
                    f"WEWORK_ENABLED={self.config.get('WEWORK_ENABLED', 'false')}",
                    f"WEWORK_CORP_ID={self.config.get('WEWORK_CORP_ID', '')}",
                    f"WEWORK_TOKEN={self.config.get('WEWORK_TOKEN', '')}",
                    f"WEWORK_ENCODING_AES_KEY={self.config.get('WEWORK_ENCODING_AES_KEY', '')}",
                    f"WEWORK_CALLBACK_PORT={self.config.get('WEWORK_CALLBACK_PORT', '9880')}",
                    f"WEWORK_CALLBACK_HOST={self.config.get('WEWORK_CALLBACK_HOST', '0.0.0.0')}",
                ]
            )
        else:
            lines.extend(
                [
                    "WEWORK_ENABLED=false",
                    "# WEWORK_CORP_ID=",
                    "# WEWORK_TOKEN=",
                    "# WEWORK_ENCODING_AES_KEY=",
                    "# WEWORK_CALLBACK_PORT=9880",
                    "# WEWORK_CALLBACK_HOST=0.0.0.0",
                ]
            )
        lines.append("")

        if self.config.get("DINGTALK_ENABLED"):
            lines.extend(
                [
                    f"DINGTALK_ENABLED={self.config.get('DINGTALK_ENABLED', 'false')}",
                    f"DINGTALK_CLIENT_ID={self.config.get('DINGTALK_CLIENT_ID', '')}",
                    f"DINGTALK_CLIENT_SECRET={self.config.get('DINGTALK_CLIENT_SECRET', '')}",
                ]
            )
        else:
            lines.extend(
                [
                    "DINGTALK_ENABLED=false",
                    "# DINGTALK_CLIENT_ID=",
                    "# DINGTALK_CLIENT_SECRET=",
                ]
            )
        lines.append("")

        if self.config.get("ONEBOT_ENABLED"):
            onebot_mode = self.config.get("ONEBOT_MODE", "reverse")
            lines.extend(
                [
                    f"ONEBOT_ENABLED={self.config.get('ONEBOT_ENABLED', 'false')}",
                    f"ONEBOT_MODE={onebot_mode}",
                ]
            )
            if onebot_mode == "forward":
                lines.append(
                    f"ONEBOT_WS_URL={self.config.get('ONEBOT_WS_URL', 'ws://127.0.0.1:8080')}"
                )
                lines.append("# ONEBOT_REVERSE_PORT=6700")
                lines.append("# ONEBOT_REVERSE_HOST=0.0.0.0")
            else:
                lines.append(
                    f"ONEBOT_REVERSE_PORT={self.config.get('ONEBOT_REVERSE_PORT', '6700')}"
                )
                lines.append(
                    f"ONEBOT_REVERSE_HOST={self.config.get('ONEBOT_REVERSE_HOST', '0.0.0.0')}"
                )
                lines.append("# ONEBOT_WS_URL=ws://127.0.0.1:8080")
            lines.append(f"ONEBOT_ACCESS_TOKEN={self.config.get('ONEBOT_ACCESS_TOKEN', '')}")
        else:
            lines.extend(
                [
                    "ONEBOT_ENABLED=false",
                    "# ONEBOT_MODE=reverse",
                    "# ONEBOT_WS_URL=ws://127.0.0.1:8080",
                    "# ONEBOT_REVERSE_PORT=6700",
                    "# ONEBOT_REVERSE_HOST=0.0.0.0",
                    "# ONEBOT_ACCESS_TOKEN=",
                ]
            )
        lines.append("")

        if self.config.get("QQBOT_ENABLED"):
            lines.extend(
                [
                    f"QQBOT_ENABLED={self.config.get('QQBOT_ENABLED', 'false')}",
                    f"QQBOT_APP_ID={self.config.get('QQBOT_APP_ID', '')}",
                    f"QQBOT_APP_SECRET={self.config.get('QQBOT_APP_SECRET', '')}",
                    f"QQBOT_SANDBOX={self.config.get('QQBOT_SANDBOX', 'true')}",
                    f"QQBOT_MODE={self.config.get('QQBOT_MODE', 'websocket')}",
                ]
            )
            if self.config.get("QQBOT_MODE") == "webhook":
                lines.append(f"QQBOT_WEBHOOK_PORT={self.config.get('QQBOT_WEBHOOK_PORT', '9890')}")
                lines.append(
                    f"QQBOT_WEBHOOK_PATH={self.config.get('QQBOT_WEBHOOK_PATH', '/qqbot/callback')}"
                )
            else:
                lines.append("# QQBOT_WEBHOOK_PORT=9890")
                lines.append("# QQBOT_WEBHOOK_PATH=/qqbot/callback")
        else:
            lines.extend(
                [
                    "QQBOT_ENABLED=false",
                    "# QQBOT_APP_ID=",
                    "# QQBOT_APP_SECRET=",
                    "# QQBOT_SANDBOX=true",
                    "# QQBOT_MODE=websocket",
                    "# QQBOT_WEBHOOK_PORT=9890",
                    "# QQBOT_WEBHOOK_PATH=/qqbot/callback",
                ]
            )
        lines.append("")

        # Persona system
        lines.extend(
            [
                "# ========== Persona ==========",
                f"PERSONA_NAME={self.config.get('PERSONA_NAME', 'default')}",
                "",
            ]
        )

        # Stickers
        lines.extend(
            [
                "# ========== Sticker ==========",
                f"STICKER_ENABLED={self.config.get('STICKER_ENABLED', 'true')}",
                "# STICKER_DATA_DIR=data/sticker",
                "",
            ]
        )

        # Living-presence mode — once enabled, the Agent proactively sends messages (greetings, follow-ups, small talk) to mimic human interaction pacing
        lines.append("# ========== Proactive (Living Presence) ==========")
        if self.config.get("PROACTIVE_ENABLED") == "true":
            lines.extend(
                [
                    "PROACTIVE_ENABLED=true  # Enable living-presence mode",
                    f"PROACTIVE_MAX_DAILY_MESSAGES={self.config.get('PROACTIVE_MAX_DAILY_MESSAGES', '3')}  # Max proactive messages per day",
                    f"PROACTIVE_MIN_INTERVAL_MINUTES={self.config.get('PROACTIVE_MIN_INTERVAL_MINUTES', '120')}  # Min interval between two proactive messages (minutes)",
                    f"PROACTIVE_QUIET_HOURS_START={self.config.get('PROACTIVE_QUIET_HOURS_START', '23')}  # Quiet hours start (24h)",
                    f"PROACTIVE_QUIET_HOURS_END={self.config.get('PROACTIVE_QUIET_HOURS_END', '7')}  # Quiet hours end (24h)",
                    f"PROACTIVE_IDLE_THRESHOLD_HOURS={self.config.get('PROACTIVE_IDLE_THRESHOLD_HOURS', '3')}  # How long of user idle before triggering proactive greeting (AI adjusts dynamically)",
                ]
            )
        else:
            lines.extend(
                [
                    "PROACTIVE_ENABLED=false  # Enable living-presence mode (proactive greetings/follow-ups/small talk)",
                    "# PROACTIVE_MAX_DAILY_MESSAGES=3  # Max proactive messages per day",
                    "# PROACTIVE_MIN_INTERVAL_MINUTES=120  # Min interval between two proactive messages (minutes)",
                    "# PROACTIVE_QUIET_HOURS_START=23  # Quiet hours start (24h)",
                    "# PROACTIVE_QUIET_HOURS_END=7  # Quiet hours end (24h)",
                    "# PROACTIVE_IDLE_THRESHOLD_HOURS=3  # How long of user idle before triggering proactive greeting (AI adjusts dynamically)",
                ]
            )
        lines.append("")

        # Memory system configuration
        lines.extend(
            [
                "# ========== Memory System ==========",
                f"EMBEDDING_MODEL={self.config.get('EMBEDDING_MODEL', 'shibing624/text2vec-base-chinese')}",
                f"EMBEDDING_DEVICE={self.config.get('EMBEDDING_DEVICE', 'cpu')}  # Embedding model device: cpu / cuda / mps",
                f"MODEL_DOWNLOAD_SOURCE={self.config.get('MODEL_DOWNLOAD_SOURCE', 'auto')}  # Model download source: auto / huggingface / modelscope",
                "MEMORY_HISTORY_DAYS=30  # Memory retention in days",
                "MEMORY_MAX_HISTORY_FILES=1000  # Max history files",
                "MEMORY_MAX_HISTORY_SIZE_MB=500  # Max total size of history files (MB)",
                "",
            ]
        )

        # Scheduler
        lines.extend(
            [
                "# ========== Scheduler ==========",
                f"SCHEDULER_ENABLED={self.config.get('SCHEDULER_ENABLED', 'true')}",
                f"SCHEDULER_TIMEZONE={self.config.get('SCHEDULER_TIMEZONE', 'Asia/Shanghai')}",
                "SCHEDULER_MAX_CONCURRENT=5  # Max concurrent scheduled tasks",
                "SCHEDULER_TASK_TIMEOUT=600  # Timeout per scheduled task (seconds)",
                "",
            ]
        )

        # Session
        lines.extend(
            [
                "# ========== Session ==========",
                f"SESSION_TIMEOUT_MINUTES={self.config.get('SESSION_TIMEOUT_MINUTES', '30')}  # Session timeout (minutes)",
                f"SESSION_MAX_HISTORY={self.config.get('SESSION_MAX_HISTORY', '50')}  # Max messages retained per session",
                "SESSION_STORAGE_PATH=data/sessions  # Session persistence storage path",
                "",
            ]
        )

        # Multi-agent configuration
        lines.append("# ========== Multi-Agent Orchestration ==========")
        if self.config.get("ORCHESTRATION_ENABLED") == "true":
            lines.extend(
                [
                    "ORCHESTRATION_ENABLED=true  # Enable multi-agent collaboration",
                    f"ORCHESTRATION_MODE={self.config.get('ORCHESTRATION_MODE', 'single')}  # Orchestration mode: single / parallel / pipeline",
                    "ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555  # ZeroMQ request bus address",
                    "ORCHESTRATION_PUB_ADDRESS=tcp://127.0.0.1:5556  # ZeroMQ publish address",
                    "ORCHESTRATION_MIN_WORKERS=1  # Min number of workers",
                    "ORCHESTRATION_MAX_WORKERS=5  # Max number of workers",
                ]
            )
        else:
            lines.extend(
                [
                    "ORCHESTRATION_ENABLED=false",
                    "# ORCHESTRATION_MODE=single",
                    "# ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555",
                ]
            )
        lines.append("")

        return "\n".join(lines)

    def _create_identity_examples(self):
        """Create example files in the identity directory."""
        identity_dir = self.project_dir / "identity"
        identity_dir.mkdir(exist_ok=True)

        # SOUL.md — the Agent's core identity
        soul_example = identity_dir / "SOUL.md"
        if not soul_example.exists():
            soul_example.write_text(
                """# Agent Soul

You are OpenAkita, a loyal and reliable AI assistant.

## Core traits
- Never give up — keep trying until you succeed
- Honest and reliable — don't hide problems
- Proactively learn and continuously improve yourself

## Behavioral guidelines
- Prioritize the user's real needs
- When facing difficulties, look for alternative approaches
- Keep communication concise and clear
""",
                encoding="utf-8",
            )
            console.print("  [green]✓[/green] Created identity/SOUL.md")

    def _check_channel_deps(self):
        """Check and install optional dependencies for the selected IM channel."""
        if not self._selected_channel:
            return

        # Telegram is a core dependency; nothing extra to install
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
            console.print(f"  [green]✓[/green] {self._selected_channel} channel dependencies are ready")
            return

        console.print(
            f"\n  [yellow]⚠[/yellow] {self._selected_channel} channel is missing dependencies: "
            f"[bold]{', '.join(missing_display)}[/bold]"
        )

        # Try to install automatically
        if IS_FROZEN:
            # Packaged environment: reuse main.py's _ensure_channel_deps logic (it retries on startup)
            console.print(
                "  [dim]In the packaged environment dependencies are installed automatically on service startup;\n"
                "  if they are still unavailable after startup, go to Settings Center -> Python Environment and click One-Click Fix[/dim]"
            )
            self._channel_deps_ok = False
            self._channel_deps_missing = missing_pip
            return

        do_install = Confirm.ask(
            f"  Install now? (pip install {' '.join(missing_pip)})", default=True
        )
        if not do_install:
            self._channel_deps_ok = False
            self._channel_deps_missing = missing_pip
            extra = CHANNEL_EXTRAS.get(self._selected_channel, "")
            if extra:
                console.print(f"  [dim]You can run later: pip install openakita[{extra}][/dim]")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"Installing {', '.join(missing_pip)}...", total=None)
            try:
                cmd = [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
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
                            description="[green]✓ Dependencies installed successfully![/green]",
                        )
                        self._channel_deps_ok = True
                        self._channel_deps_missing = []
                    else:
                        progress.update(
                            task,
                            description=f"[yellow]⚠ Still missing after install: {', '.join(still_missing)}[/yellow]",
                        )
                        self._channel_deps_ok = False
                        self._channel_deps_missing = still_missing
                else:
                    err_tail = (result.stderr or result.stdout or "").strip()[-200:]
                    progress.update(
                        task,
                        description=f"[red]✗ Install failed (exit {result.returncode})[/red]",
                    )
                    if err_tail:
                        console.print(f"  [dim]{err_tail}[/dim]")
                    self._channel_deps_ok = False
                    self._channel_deps_missing = missing_pip
            except subprocess.TimeoutExpired:
                progress.update(task, description="[red]✗ Install timed out (120s)[/red]")
                self._channel_deps_ok = False
                self._channel_deps_missing = missing_pip
            except Exception as e:
                progress.update(
                    task,
                    description=f"[red]✗ Install error: {e}[/red]",
                )
                self._channel_deps_ok = False
                self._channel_deps_missing = missing_pip

        console.print()

    def _verify_channel_credentials(self):
        """Perform lightweight credential/connectivity verification for the selected channel (optional)."""
        if not self._selected_channel:
            return
        if not self._channel_deps_ok and self._selected_channel != "telegram":
            console.print("  [dim]Skipping channel connectivity test (dependencies not ready)[/dim]\n")
            return

        # Only provide testing for channels that expose a simple verification API
        verifiers: dict[str, tuple[str, callable]] = {
            "dingtalk": ("DingTalk", self._verify_dingtalk),
            "feishu": ("Feishu", self._verify_feishu),
            "telegram": ("Telegram", self._verify_telegram),
        }

        entry = verifiers.get(self._selected_channel)
        if entry is None:
            return

        display_name, verify_fn = entry
        do_test = Confirm.ask(f"  Test {display_name} credentials now?", default=True)
        if not do_test:
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Verifying {display_name} credentials...", total=None)
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
        """Verify DingTalk credentials by requesting an access_token."""
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
        """Verify Feishu credentials by requesting tenant_access_token."""
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
        """Verify the Telegram Bot Token by calling getMe."""
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
        """Test the API connection."""
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
                base_url = first_ep.get(
                    "base_url", self.config.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
                )
                model = first_ep.get(
                    "model", self.config.get("DEFAULT_MODEL", "claude-sonnet-4-20250514")
                )
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
        """Display completion info."""
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

        # If IM channel dependencies failed to install, dynamically append a hint
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
                parts.append(f"Install with: `pip install {' '.join(self._channel_deps_missing)}`")
            parts.append("")
            parts.append("Without these dependencies the IM channel will **not start**.")

        parts.extend(
            [
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
            ]
        )

        console.print(
            Panel(
                Markdown("\n".join(parts)),
                title="Setup Complete",
                border_style="green",
            )
        )


def run_wizard(project_dir: str | None = None, *, quick: bool = False):
    """Entry function that runs the setup wizard."""
    path = Path(project_dir) if project_dir else Path.cwd()
    wizard = SetupWizard(path)
    return wizard.run(quick=quick)
