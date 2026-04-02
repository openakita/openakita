"""
OpenAkita CLI 入口

使用 Typer 和 Rich 提供交互式命令行界面
支持同时运行 CLI 和 IM 通道（Telegram、飞书等）
支持多 Agent 协同模式（通过 ORCHESTRATION_ENABLED 配置）
"""

import openakita._ensure_utf8  # noqa: F401  # isort: skip

import asyncio
import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .config import settings
from .core.agent import Agent
from .logging import setup_logging
from .python_compat import patch_simplejson_jsondecodeerror

# MCP stdio 子进程模式：stdout 专属 JSONRPC 协议，禁止一切控制台日志输出
_is_mcp_subprocess = "run-mcp-module" in sys.argv

# 配置日志系统（使用新的日志模块）
setup_logging(
    log_dir=settings.log_dir_path,
    log_level=settings.log_level,
    log_format=settings.log_format,
    log_file_prefix=settings.log_file_prefix,
    log_max_size_mb=settings.log_max_size_mb,
    log_backup_count=settings.log_backup_count,
    log_to_console=settings.log_to_console and not _is_mcp_subprocess,
    log_to_file=settings.log_to_file,
)
logger = logging.getLogger(__name__)

# 初始化追踪系统
def _init_tracing() -> None:
    """根据配置初始化 Agent 追踪系统"""
    from .tracing.exporter import ConsoleExporter, FileExporter
    from .tracing.tracer import AgentTracer, set_tracer

    tracer = AgentTracer(enabled=settings.tracing_enabled)
    if settings.tracing_enabled:
        tracer.add_exporter(FileExporter(settings.tracing_export_dir))
        if settings.tracing_console_export and not _is_mcp_subprocess:
            tracer.add_exporter(ConsoleExporter())
        logger.info("[Tracing] 追踪系统已启用")
    set_tracer(tracer)

_init_tracing()

# Typer 应用
app = typer.Typer(
    name="openakita",
    help="OpenAkita - 全能自进化AI助手",
    add_completion=False,
)

# Rich 控制台
console = Console()

# 全局组件
_agent: Agent | None = None
_orchestrator = None  # AgentOrchestrator（多 Agent 模式）
_desktop_pool = None  # AgentInstancePool — Desktop Chat per-session 隔离
_message_gateway = None
_session_manager = None


def get_agent() -> Agent:
    """获取或创建 Agent 实例（单 Agent 模式）"""
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


async def _init_orchestrator():
    """Initialize the orchestrator (idempotent).

    Safe to call multiple times — skips if already created.
    Binds to ``_message_gateway`` when available; deploys presets.
    """
    global _orchestrator
    if _orchestrator is not None:
        return
    from openakita.agents.orchestrator import AgentOrchestrator
    _orchestrator = AgentOrchestrator()
    if _message_gateway:
        _orchestrator.set_gateway(_message_gateway)
    logger.info("[MultiAgent] AgentOrchestrator initialized")
    try:
        from openakita.agents.presets import ensure_presets_on_mode_enable
        ensure_presets_on_mode_enable(settings.data_dir / "agents")
    except Exception as e:
        logger.warning(f"[Main] Failed to deploy presets on orchestrator init: {e}")


# ==================== IM 通道依赖自动安装 ====================

from .channels.deps import CHANNEL_DEPS as _CHANNEL_DEPS


def _patch_backports_zstd() -> None:
    """Patch incomplete ``backports.zstd`` so that ``urllib3 >= 2.3`` can load.

    Some environments (notably PyInstaller bundles) ship an older
    ``backports-zstd`` that exposes the decompressor but is missing the
    ``ZstdError`` exception class.  ``urllib3.response`` references this
    attribute at *class-definition time* in ``BaseHTTPResponse``, so the
    ``AttributeError`` is raised during ``import urllib3`` — before any
    user code can catch it.

    We add a thin stub so the import succeeds.  The stub inherits from
    ``Exception``, which is the correct base class for ``ZstdError``.
    """
    try:
        import backports.zstd as _bzstd
    except ImportError:
        return

    if hasattr(_bzstd, "ZstdError"):
        return

    class _ZstdError(Exception):
        """Stub ``ZstdError`` for backports.zstd compatibility."""

    _bzstd.ZstdError = _ZstdError
    logger.debug("Patched backports.zstd: added missing ZstdError stub")


def _build_isolated_pip_env(py_path: Path, *, is_frozen: bool) -> dict[str, str]:
    """Build a sanitized subprocess env for ``python -m pip`` execution."""
    pip_env = os.environ.copy()
    for harmful_key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_SHLVL",
        "CONDA_PYTHON_EXE",
        "PIP_INDEX_URL",
        "PIP_TARGET",
        "PIP_PREFIX",
        "PIP_USER",
        "PIP_REQUIRE_VIRTUALENV",
    ):
        pip_env.pop(harmful_key, None)

    if is_frozen and py_path.parent.name == "_internal":
        path_parts = [str(py_path.parent)]
        for sub in ("Lib", "DLLs"):
            p = py_path.parent / sub
            if p.is_dir():
                path_parts.append(str(p))
        pip_env["PYTHONPATH"] = os.pathsep.join(path_parts)

    return pip_env


def _probe_python_runtime(py: str, env: dict[str, str], *, extra: dict) -> tuple[bool, str]:
    """Probe whether a Python executable can import encodings/pip normally."""
    try:
        result = subprocess.run(
            [py, "-c", "import encodings, pip; print('ok')"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            **extra,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if result.returncode == 0:
        return True, ""
    tail = (result.stderr or result.stdout or "").strip()
    return False, tail[-600:]


def _find_bundled_channel_wheels() -> Path | None:
    """Locate bundled offline wheels for IM deps if present."""
    exe = Path(sys.executable).resolve()
    candidates = [
        exe.parent.parent / "modules" / "channel-deps" / "wheels",
        exe.parent / "modules" / "channel-deps" / "wheels",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return None


def _ensure_channel_deps() -> None:
    """
    检查已启用的 IM 通道所需依赖，缺失的自动安装到隔离目录。

    安装策略：使用 ``pip install --target`` 将缺失依赖安装到
    ``~/.openakita/modules/channel-deps/site-packages``，与外部 Python
    环境完全隔离，避免版本冲突。该目录会被 ``inject_module_paths()``
    自动扫描并注入 sys.path。

    Telegram 为核心依赖，始终包含在安装包中，不需检查。
    """
    _patch_backports_zstd()
    patch_simplejson_jsondecodeerror(logger=logger)
    try:
        from openakita.runtime_env import inject_module_paths_runtime
        inject_module_paths_runtime()
    except Exception:
        pass

    enabled_channels: list[str] = []
    if settings.feishu_enabled:
        enabled_channels.append(settings.feishu_domain if settings.feishu_domain == "lark" else "feishu")
    if settings.dingtalk_enabled:
        enabled_channels.append("dingtalk")
    if settings.wework_enabled:
        enabled_channels.append("wework")
    if settings.wework_ws_enabled:
        enabled_channels.append("wework_ws")
    if settings.onebot_enabled:
        enabled_channels.append("onebot")
    if settings.qqbot_enabled:
        enabled_channels.append("qqbot")
    if settings.wechat_enabled:
        enabled_channels.append("wechat")

    # 补充从 im_bots 中提取已启用的通道类型，确保依赖检测覆盖 UI 创建的 Bot
    for bot_cfg in (settings.im_bots or []):
        if bot_cfg.get("enabled", True):
            channel_type = bot_cfg.get("type", "")
            if channel_type and channel_type not in enabled_channels:
                enabled_channels.append(channel_type)

    if not enabled_channels:
        return

    missing: list[str] = []
    failed_import_names: list[str] = []
    for channel in enabled_channels:
        for import_name, pip_name in _CHANNEL_DEPS.get(channel, []):
            try:
                importlib.import_module(import_name)
            except ImportError as exc:
                # requests 在检测到 simplejson 时会导入 JSONDecodeError。
                # 某些旧/损坏的 simplejson 缺失该符号，导致 lark_oapi 导入失败。
                if (
                    import_name == "lark_oapi"
                    and "JSONDecodeError" in str(exc)
                    and "simplejson" in str(exc)
                ):
                    patch_simplejson_jsondecodeerror(logger=logger)
                    try:
                        importlib.import_module(import_name)
                        logger.info(
                            "lark_oapi import recovered after simplejson compatibility patch"
                        )
                        continue
                    except Exception:
                        pass
                if pip_name not in missing:
                    missing.append(pip_name)
                failed_import_names.append(import_name)
            except Exception as e:
                logger.warning(
                    f"Import check for {import_name} ({channel}) hit unexpected error: "
                    f"{type(e).__name__}: {e} — skipping auto-install for this dep"
                )

    if not missing:
        return

    pkg_list = ", ".join(missing)
    logger.info(f"IM 通道依赖自动安装: {pkg_list} ...")

    from openakita.runtime_env import get_channel_deps_dir, get_python_executable, IS_FROZEN

    py = get_python_executable()
    if not py or (IS_FROZEN and py == sys.executable):
        logger.warning("未找到项目自带的 Python，无法自动安装依赖")
        console.print(
            f"[yellow]⚠[/yellow] 未找到 Python 解释器，无法自动安装: [bold]{pkg_list}[/bold]\n"
            f"  请前往「设置中心 → Python 环境」点击「一键修复」"
        )
        return

    target_dir = get_channel_deps_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 国内镜像多源回退（与 Rust 端 pip_install 行为一致）
    # 尊重用户已配置的 PIP_INDEX_URL 环境变量
    _user_index = os.environ.get("PIP_INDEX_URL", "").strip()
    _mirror_sources: list[tuple[str, str]] = []
    if _user_index:
        _host = _user_index.split("//")[1].split("/")[0] if "//" in _user_index else ""
        _mirror_sources.append((_user_index, _host))
    _mirror_sources.extend([
        ("https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
        ("https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn"),
        ("https://pypi.org/simple/", "pypi.org"),
    ])

    extra: dict = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW

    py_path = Path(py)
    pip_env = _build_isolated_pip_env(py_path, is_frozen=IS_FROZEN)

    # _internal/python.exe 在部分用户机器上会因为 PythonHome 未稳定而报
    # "No module named encodings"。先做探测，必要时追加 PYTHONHOME 再重试。
    runtime_ok, probe_err = _probe_python_runtime(py, pip_env, extra=extra)
    if not runtime_ok and IS_FROZEN and py_path.parent.name == "_internal":
        pip_env["PYTHONHOME"] = str(py_path.parent)
        runtime_ok, probe_err = _probe_python_runtime(py, pip_env, extra=extra)
        if runtime_ok:
            logger.info("内置 Python 通过 PYTHONHOME 修正后可用: %s", py)

    if not runtime_ok:
        logger.error("自动安装依赖前的 Python 运行时探测失败: %s", probe_err)
        console.print(
            "[red]✗[/red] Python 运行环境异常，无法安装 IM 依赖。\n"
            "  建议：前往「设置中心 → Python 环境」点击「一键修复」。"
        )
        return

    def _on_install_success(source_label: str) -> None:
        logger.info(f"依赖安装成功 (source={source_label}, target={target_dir}): {pkg_list}")
        console.print(f"[green]✓[/green] 依赖安装成功: {pkg_list}")

        # 清理之前失败的导入在 sys.modules 中留下的残余条目，
        # 确保后续 import 能从新安装的路径加载完整模块。
        stale = [
            k for k in sys.modules
            if any(k == n or k.startswith(n + ".") for n in failed_import_names)
        ]
        for k in stale:
            del sys.modules[k]
        if stale:
            logger.debug(f"Cleared {len(stale)} stale sys.modules entries: {stale[:10]}")

        importlib.invalidate_caches()
        target_str = str(target_dir)
        if target_str not in sys.path:
            sys.path.append(target_str)
            logger.info(f"已注入通道依赖路径: {target_str}")
        try:
            from openakita.runtime_env import inject_module_paths_runtime
            inject_module_paths_runtime()
        except Exception:
            pass

    installed = False

    # 离线优先：若安装包内带了 channel-deps wheels，先走离线安装。
    bundled_wheels = _find_bundled_channel_wheels() if IS_FROZEN else None
    if bundled_wheels is not None:
        console.print(
            f"[yellow]⏳[/yellow] 自动安装 IM 通道依赖: [bold]{pkg_list}[/bold] "
            f"(源: offline wheels)"
        )
        offline_cmd = [
            py, "-m", "pip", "install",
            "--no-index",
            "--find-links", str(bundled_wheels),
            "--target", str(target_dir),
            "--prefer-binary",
            *missing,
        ]
        try:
            offline = subprocess.run(
                offline_cmd,
                env=pip_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                **extra,
            )
            if offline.returncode == 0:
                _on_install_success("offline")
                installed = True
            else:
                err_tail = (offline.stderr or offline.stdout or "").strip()[-400:]
                logger.warning("离线 wheels 安装失败，回退在线镜像: %s", err_tail)
        except Exception as e:
            logger.warning(f"离线 wheels 安装异常，回退在线镜像: {e}")

    def _pip_install_via_mirrors(
        packages: list[str], label_prefix: str = "",
    ) -> bool:
        """Try installing *packages* through all mirror sources. Return True on success."""
        for idx, (index_url, trusted_host) in enumerate(_mirror_sources):
            source_label = trusted_host or index_url
            if idx == 0:
                console.print(
                    f"[yellow]⏳[/yellow] {label_prefix}自动安装 IM 通道依赖: "
                    f"[bold]{', '.join(packages)}[/bold] (源: {source_label}) ..."
                )
            else:
                console.print(
                    f"[yellow]⏳[/yellow] 切换镜像源重试: {source_label} ..."
                )

            pip_cmd = [
                py, "-m", "pip", "install",
                "--target", str(target_dir),
                "-i", index_url,
                "--prefer-binary",
                "--timeout", "60",
                *packages,
            ]
            if trusted_host:
                pip_cmd.extend(["--trusted-host", trusted_host])

            try:
                result = subprocess.run(
                    pip_cmd,
                    env=pip_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                    **extra,
                )
                if result.returncode == 0:
                    _on_install_success(source_label)
                    return True
                else:
                    err_tail = (result.stderr or result.stdout or "").strip()[-300:]
                    logger.warning(f"镜像源 {source_label} 安装失败 (exit {result.returncode}): {err_tail}")
            except subprocess.TimeoutExpired:
                logger.warning(f"镜像源 {source_label} 安装超时")
            except Exception as e:
                logger.warning(f"镜像源 {source_label} 安装异常: {e}")
        return False

    if not installed:
        installed = _pip_install_via_mirrors(missing)

    # 批量安装失败且有多个包时，逐个重试——避免一个 C 扩展编译失败拖垮纯 Python 包
    if not installed and len(missing) > 1:
        logger.info("批量安装失败，尝试逐个安装 ...")
        per_pkg_ok: list[str] = []
        per_pkg_fail: list[str] = []
        for pkg in missing:
            if _pip_install_via_mirrors([pkg], label_prefix=f"[逐个] "):
                per_pkg_ok.append(pkg)
            else:
                per_pkg_fail.append(pkg)
        if per_pkg_ok:
            installed = True
            if per_pkg_fail:
                fail_list = ", ".join(per_pkg_fail)
                logger.warning(f"部分依赖安装失败（不影响已安装的包）: {fail_list}")
                console.print(
                    f"[yellow]⚠[/yellow] 部分依赖安装失败: {fail_list}\n"
                    f"  相关功能（如语音转码）可能不可用，核心 IM 通道不受影响"
                )

    if not installed:
        logger.error(f"所有镜像源均安装失败: {pkg_list}")
        console.print(
            f"[red]✗[/red] 依赖安装失败（已尝试所有镜像源）: {pkg_list}\n"
            f"  请检查网络连接，或前往「设置中心 → Python 环境」点击「一键修复」"
        )
        return

    # 安装后验证：确保模块真正可导入
    still_broken: list[str] = []
    for name in failed_import_names:
        try:
            importlib.import_module(name)
        except Exception as exc:
            logger.error(f"依赖 {name} 安装后仍无法导入: {exc}", exc_info=True)
            still_broken.append(name)
    if still_broken:
        console.print(
            f"[yellow]⚠[/yellow] 以下依赖安装成功但导入失败: {', '.join(still_broken)}\n"
            f"  日志中有详细错误信息，请反馈给开发者"
        )


def _create_bot_adapter(bot_type: str, creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    """Create an IM adapter instance from im_bots config entry.

    Uses the centralized adapter registry instead of if/elif branches.
    """
    from .channels.registry import ADAPTER_REGISTRY

    factory = ADAPTER_REGISTRY.get(bot_type)
    if factory is None:
        logger.warning(f"Unknown bot type: {bot_type}")
        return None

    return factory(
        creds,
        channel_name=channel_name,
        bot_id=bot_id,
        agent_profile_id=agent_profile_id,
    )


def get_message_gateway():
    """返回当前运行的 MessageGateway 实例，如未启动则返回 None。"""
    return _message_gateway


def _bot_channel_name(bot_cfg: dict) -> str:
    """根据 bot 配置计算 channel_name，与 start_im_channels 中的命名规则保持一致。"""
    bot_type = bot_cfg.get("type", "")
    bot_id = bot_cfg.get("id", "")
    return f"{bot_type}:{bot_id}" if bot_id else bot_type


async def apply_im_bot(bot_cfg: dict) -> bool:
    """动态注册或更新单个 Bot 适配器到正在运行的网关中（热重载）。

    如果网关未运行（服务未启动），则不做任何操作，返回 False。
    服务重启后仍会从 runtime_state.json 正常加载，不影响持久化。
    """
    if _message_gateway is None or not _message_gateway._running:
        return False
    bot_type = bot_cfg.get("type", "")
    bot_id = bot_cfg.get("id", "")
    agent_id = bot_cfg.get("agent_profile_id", "default")
    creds = bot_cfg.get("credentials", {})
    channel_name = _bot_channel_name(bot_cfg)
    try:
        adapter = _create_bot_adapter(
            bot_type, creds,
            channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_id,
        )
        if adapter:
            await _message_gateway.register_adapter(adapter)
            logger.info(f"[HotReload] Applied bot adapter: {channel_name}")
            return True
    except Exception as e:
        logger.error(f"[HotReload] Failed to apply bot {channel_name}: {e}")
    return False


async def remove_im_bot(bot_cfg: dict) -> bool:
    """动态从正在运行的网关中注销并停止单个 Bot 适配器（热重载）。

    如果网关未运行，则不做任何操作，返回 False。
    """
    if _message_gateway is None:
        return False
    channel_name = _bot_channel_name(bot_cfg)
    try:
        result = await _message_gateway.unregister_adapter(channel_name)
        if result:
            logger.info(f"[HotReload] Removed bot adapter: {channel_name}")
        return result
    except Exception as e:
        logger.error(f"[HotReload] Failed to remove bot {channel_name}: {e}")
        return False


async def ensure_session_manager():
    """
    确保 SessionManager 已初始化。

    Desktop Chat API 和 IM 通道都依赖 SessionManager 管理对话上下文，
    因此无论是否启用 IM 通道，都需要初始化 SessionManager。
    """
    global _session_manager

    if _session_manager is not None:
        return

    from .sessions import SessionManager

    _session_manager = SessionManager(
        storage_path=settings.project_root / settings.session_storage_path,
    )
    await _session_manager.start()
    logger.info("SessionManager started")


def _setup_session_backfill(agent_or_master):
    """从 SQLite 回填 session 中可能缺失的消息（崩溃恢复）。"""
    _actual_agent = agent_or_master
    if _actual_agent and hasattr(_actual_agent, "memory_manager"):
        _mm = _actual_agent.memory_manager
        if hasattr(_mm, "store") and _session_manager is not None:
            _session_manager.set_turn_loader(
                lambda safe_id: _mm.store.get_recent_turns(safe_id, limit=50)
            )
            backfilled = _session_manager.backfill_sessions_from_store()
            if backfilled:
                logger.info(f"Session backfill: recovered {backfilled} turns from SQLite")


async def init_core_services(agent_or_master):
    """初始化所有模式（Desktop / IM / CLI）共享的核心服务。

    必须在 start_im_channels / start_api_server 之前调用。
    幂等——多次调用安全。
    """
    global _desktop_pool

    await ensure_session_manager()

    if _desktop_pool is None:
        from openakita.agents.factory import AgentFactory, AgentInstancePool
        _desktop_pool = AgentInstancePool(AgentFactory(), idle_timeout=600)
        await _desktop_pool.start()
        logger.info("[Main] Desktop AgentInstancePool initialized (idle_timeout=600s)")

    if settings.multi_agent_enabled:
        await _init_orchestrator()

    _setup_session_backfill(agent_or_master)


async def start_im_channels(agent_or_master):
    """启动配置的 IM 通道。

    仅处理 IM 相关逻辑（MessageGateway、适配器注册）。
    核心服务（SessionManager、AgentPool、Orchestrator）由 init_core_services() 负责。
    """
    global _message_gateway

    any_enabled = (
        settings.telegram_enabled
        or settings.feishu_enabled
        or settings.wework_enabled
        or settings.wework_ws_enabled
        or settings.dingtalk_enabled
        or settings.onebot_enabled
        or settings.qqbot_enabled
        or settings.wechat_enabled
        or any(b.get("enabled", True) for b in (settings.im_bots or []))
    )

    if not any_enabled:
        logger.info("No IM channels enabled, skipping IM initialization")
        return

    # 自动安装缺失的 IM 通道依赖
    try:
        _ensure_channel_deps()
    except Exception as e:
        logger.error(
            f"IM channel dependency check failed ({type(e).__name__}: {e}), "
            "continuing with adapter registration — individual adapters will "
            "report their own import errors if deps are truly missing"
        )

    # 初始化在线 STT 客户端（可选）
    from .llm.config import load_endpoints_config as _load_ep_config
    from .llm.stt_client import STTClient

    stt_client = None
    try:
        _, _, stt_eps, _ = _load_ep_config()
        if stt_eps:
            stt_client = STTClient(endpoints=stt_eps)
    except Exception as e:
        logger.warning(f"Failed to load STT endpoints: {e}")

    # 初始化 MessageGateway (先创建，agent_handler 会引用它)
    from .channels import MessageGateway

    _message_gateway = MessageGateway(
        session_manager=_session_manager,
        agent_handler=None,  # 稍后设置
        stt_client=stt_client,  # 在线 STT 客户端
    )

    if _orchestrator is not None:
        _orchestrator.set_gateway(_message_gateway)

    # 注册启用的适配器
    adapters_started = []

    # Telegram
    if settings.telegram_enabled and settings.telegram_bot_token:
        _tg_dup = any(
            b.get("type") == "telegram"
            and b.get("credentials", {}).get("bot_token") == settings.telegram_bot_token
            and b.get("enabled", True)
            for b in (settings.im_bots or [])
        )
        if _tg_dup:
            logger.info(
                "Telegram adapter skipped: im_bots already contains a telegram bot "
                f"with the same bot_token ({settings.telegram_bot_token[:8]}...)"
            )
        else:
            try:
                from .channels.adapters import TelegramAdapter

                telegram = TelegramAdapter(
                    bot_token=settings.telegram_bot_token,
                    webhook_url=settings.telegram_webhook_url or None,
                    media_dir=settings.project_root / "data" / "media" / "telegram",
                    pairing_code=settings.telegram_pairing_code or None,
                    require_pairing=settings.telegram_require_pairing,
                    proxy=settings.telegram_proxy or None,
                )
                await _message_gateway.register_adapter(telegram)
                adapters_started.append("telegram")
                logger.info("Telegram adapter registered")
            except Exception as e:
                logger.error(f"Failed to start Telegram adapter: {e}")

    # 飞书 / Lark
    if settings.feishu_enabled and settings.feishu_app_id:
        _feishu_domain = settings.feishu_domain if settings.feishu_domain in ("feishu", "lark") else "feishu"
        _feishu_bot_type = _feishu_domain  # "feishu" or "lark"
        _feishu_dup = any(
            b.get("type") in ("feishu", "lark")
            and b.get("credentials", {}).get("app_id") == settings.feishu_app_id
            and b.get("enabled", True)
            for b in (settings.im_bots or [])
        )
        if _feishu_dup:
            logger.info(
                f"{_feishu_bot_type.capitalize()} adapter skipped: im_bots already contains a "
                f"feishu/lark bot with the same app_id ({settings.feishu_app_id[:8]}...)"
            )
        else:
            try:
                from .channels.adapters import FeishuAdapter

                feishu = FeishuAdapter(
                    app_id=settings.feishu_app_id,
                    app_secret=settings.feishu_app_secret,
                    domain=_feishu_domain,
                )
                await _message_gateway.register_adapter(feishu)
                adapters_started.append(_feishu_bot_type)
                logger.info(f"{_feishu_bot_type.capitalize()} adapter registered")
            except Exception as e:
                logger.error(f"Failed to start {_feishu_bot_type.capitalize()} adapter: {e}")

    # 企业微信（智能机器人模式）
    if settings.wework_enabled and settings.wework_corp_id:
        try:
            from .channels.adapters import WeWorkBotAdapter

            wework = WeWorkBotAdapter(
                corp_id=settings.wework_corp_id,
                token=settings.wework_token,
                encoding_aes_key=settings.wework_encoding_aes_key,
                callback_port=settings.wework_callback_port,
                callback_host=settings.wework_callback_host,
            )
            await _message_gateway.register_adapter(wework)
            adapters_started.append("wework")
            logger.info("WeWork Smart Robot adapter registered")
        except Exception as e:
            logger.error(f"Failed to start WeWork adapter: {e}")

    # 企业微信（智能机器人 — WebSocket 长连接模式）
    if settings.wework_ws_enabled and settings.wework_ws_bot_id:
        # 双开警告：HTTP 回调与 WS 长连接同时启用
        if settings.wework_enabled:
            logger.warning(
                "WeWork HTTP callback and WebSocket are both enabled. "
                "If they share the same bot, messages may be processed twice."
            )

        # 重复注册检查：im_bots 中是否已含相同 bot_id 的 wework_ws 条目
        _wework_ws_dup = any(
            b.get("type") == "wework_ws"
            and b.get("credentials", {}).get("bot_id") == settings.wework_ws_bot_id
            and b.get("enabled", True)
            for b in (settings.im_bots or [])
        )
        if _wework_ws_dup:
            logger.info(
                "WeWork WS adapter skipped: im_bots already contains a wework_ws bot "
                f"with the same bot_id ({settings.wework_ws_bot_id[:8]}...)"
            )
        else:
            try:
                from .channels.adapters import WeWorkWsAdapter

                wework_ws = WeWorkWsAdapter(
                    bot_id=settings.wework_ws_bot_id,
                    secret=settings.wework_ws_secret,
                    webhook_url=settings.wework_ws_webhook_url,
                )
                await _message_gateway.register_adapter(wework_ws)
                adapters_started.append("wework_ws")
                logger.info("WeWork WS (WebSocket) adapter registered")
            except Exception as e:
                logger.error(f"Failed to start WeWork WS adapter: {e}")

    # 钉钉
    if settings.dingtalk_enabled and settings.dingtalk_client_id:
        _ding_dup = any(
            b.get("type") == "dingtalk"
            and b.get("credentials", {}).get("client_id") == settings.dingtalk_client_id
            and b.get("enabled", True)
            for b in (settings.im_bots or [])
        )
        if _ding_dup:
            logger.info(
                "DingTalk adapter skipped: im_bots already contains a dingtalk bot "
                f"with the same client_id ({settings.dingtalk_client_id[:8]}...)"
            )
        else:
            try:
                from .channels.adapters import DingTalkAdapter

                dingtalk = DingTalkAdapter(
                    app_key=settings.dingtalk_client_id,
                    app_secret=settings.dingtalk_client_secret,
                )
                await _message_gateway.register_adapter(dingtalk)
                adapters_started.append("dingtalk")
                logger.info("DingTalk adapter registered")
            except Exception as e:
                logger.error(f"Failed to start DingTalk adapter: {e}")

    # OneBot (通用协议)
    if settings.onebot_enabled:
        try:
            from .channels.adapters import OneBotAdapter

            onebot = OneBotAdapter(
                mode=settings.onebot_mode,
                ws_url=settings.onebot_ws_url,
                reverse_host=settings.onebot_reverse_host,
                reverse_port=settings.onebot_reverse_port,
                access_token=settings.onebot_access_token or None,
            )
            await _message_gateway.register_adapter(onebot)
            adapters_started.append("onebot")
            _mode_label = "reverse" if settings.onebot_mode == "reverse" else "forward"
            logger.info(f"OneBot adapter registered (mode={_mode_label})")
        except Exception as e:
            logger.error(f"Failed to start OneBot adapter: {e}")

    # QQ 官方机器人
    if settings.qqbot_enabled and settings.qqbot_app_id:
        _qq_dup = any(
            b.get("type") == "qqbot"
            and b.get("credentials", {}).get("app_id") == settings.qqbot_app_id
            and b.get("enabled", True)
            for b in (settings.im_bots or [])
        )
        if _qq_dup:
            logger.info(
                "QQ Bot adapter skipped: im_bots already contains a qqbot bot "
                f"with the same app_id ({settings.qqbot_app_id[:8]}...)"
            )
        else:
            try:
                from .channels.adapters import QQBotAdapter

                qqbot = QQBotAdapter(
                    app_id=settings.qqbot_app_id,
                    app_secret=settings.qqbot_app_secret,
                    sandbox=settings.qqbot_sandbox,
                    mode=settings.qqbot_mode,
                    webhook_port=settings.qqbot_webhook_port,
                    webhook_path=settings.qqbot_webhook_path,
                )
                await _message_gateway.register_adapter(qqbot)
                adapters_started.append("qqbot")
                logger.info("QQ Official Bot adapter registered")
            except Exception as e:
                logger.error(f"Failed to start QQ Official Bot adapter: {e}")

    # 微信个人号
    if settings.wechat_enabled and settings.wechat_token:
        _wc_dup = any(
            b.get("type") == "wechat"
            and b.get("credentials", {}).get("token") == settings.wechat_token
            and b.get("enabled", True)
            for b in (settings.im_bots or [])
        )
        if _wc_dup:
            logger.info(
                "WeChat adapter skipped: im_bots already contains a wechat bot "
                f"with the same token ({settings.wechat_token[:8]}...)"
            )
        else:
            try:
                from .channels.adapters import WeChatAdapter

                wc = WeChatAdapter(token=settings.wechat_token)
                await _message_gateway.register_adapter(wc)
                adapters_started.append("wechat")
                logger.info("WeChat adapter registered")
            except Exception as e:
                logger.error(f"Failed to start WeChat adapter: {e}")

    # Multi-bot: create additional adapters from im_bots config
    if settings.im_bots:
        for bot_cfg in settings.im_bots:
            if not bot_cfg.get("enabled", True):
                continue
            bot_type = bot_cfg.get("type", "")
            bot_id = bot_cfg.get("id", "")
            agent_id = bot_cfg.get("agent_profile_id", "default")
            creds = bot_cfg.get("credentials", {})
            _channel_name = f"{bot_type}:{bot_id}" if bot_id else bot_type

            try:
                adapter = _create_bot_adapter(
                    bot_type, creds,
                    channel_name=_channel_name, bot_id=bot_id, agent_profile_id=agent_id,
                )
                if adapter:
                    await _message_gateway.register_adapter(adapter)
                    adapters_started.append(_channel_name)
                    logger.info(f"[MultiBot] Registered bot: {_channel_name} -> agent={agent_id}")
            except Exception as e:
                logger.error(f"Failed to create bot {bot_id}: {e}")

    # 设置 Agent 处理函数
    agent = agent_or_master

    async def agent_handler(session, message: str) -> str:
        """通过 Agent 处理消息（运行时检查多Agent模式开关）"""
        from .channels.gateway import format_user_friendly_error

        if settings.multi_agent_enabled and _orchestrator is not None:
            try:
                return await _orchestrator.handle_message(session, message)
            except Exception as e:
                logger.error(f"Orchestrator handler error: {e}", exc_info=True)
                return format_user_friendly_error(str(e))

        try:
            session_messages = session.context.get_messages()
            response = await agent.chat_with_session(
                message=message,
                session_messages=session_messages,
                session_id=session.id,
                session=session,
                gateway=_message_gateway,
            )
            return response
        except Exception as e:
            logger.error(f"Agent handler error: {e}", exc_info=True)
            return format_user_friendly_error(str(e))

    agent_handler._agent_ref = agent
    agent_handler.is_stop_command = agent.is_stop_command
    agent_handler.is_skip_command = agent.is_skip_command
    agent_handler.classify_interrupt = agent.classify_interrupt
    agent_handler.cancel_current_task = agent.cancel_current_task
    agent_handler.skip_current_step = agent.skip_current_step
    agent_handler.insert_user_message = agent.insert_user_message

    async def agent_handler_stream(session, message: str):
        """流式版 agent_handler，yield SSE event dicts（仅单 Agent 模式可用）。"""
        session_messages = session.context.get_messages()
        async for event in agent.chat_with_session_stream(
            message=message,
            session_messages=session_messages,
            session_id=session.id,
            session=session,
            gateway=_message_gateway,
        ):
            yield event

    agent.set_scheduler_gateway(_message_gateway)
    _message_gateway.set_brain(agent.brain)

    _message_gateway.agent_handler = agent_handler
    _message_gateway.agent_handler_stream = agent_handler_stream

    # 设置 turn_loader 用于 session 崩溃恢复回填
    _setup_session_backfill(agent_or_master)

    # 启动网关
    if adapters_started:
        await _message_gateway.start()
        started = _message_gateway.get_started_adapters()
        failed = _message_gateway.get_failed_adapters()
        if failed:
            logger.warning(f"IM adapters failed to start: {', '.join(failed)}")
        logger.info(f"MessageGateway started with adapters: {started}")
        return started

    return []


async def stop_im_channels(*, graceful: bool = True, drain_timeout: float = 30.0):
    """
    停止 IM 通道

    Args:
        graceful: True 时先排空进行中任务再停止，False 时立即停止
        drain_timeout: 排空等待超时秒数
    """
    global _message_gateway, _session_manager, _orchestrator, _desktop_pool

    # Gateway drain MUST happen first — agents and orchestrator must stay alive
    # so in-flight IM responses can finish sending before we tear down the pool.
    if _message_gateway:
        if graceful:
            await _message_gateway.drain(timeout=drain_timeout)
        else:
            await _message_gateway.stop()
        logger.info("MessageGateway stopped")

    if _desktop_pool:
        try:
            await _desktop_pool.stop()
        except Exception as e:
            logger.warning(f"Desktop pool shutdown error: {e}")
        _desktop_pool = None

    if _orchestrator:
        try:
            await _orchestrator.shutdown()
        except Exception as e:
            logger.warning(f"Orchestrator shutdown error: {e}")
        _orchestrator = None

    if _session_manager:
        await _session_manager.stop()
        logger.info("SessionManager stopped")


def print_welcome():
    """打印欢迎信息"""
    welcome_text = """
# OpenAkita - 全能自进化AI助手

基于 **Ralph Wiggum 模式**，永不放弃。

## 核心特性
- 🔄 任务未完成绝不终止
- 🧠 自动学习和进化
- 🔧 动态安装新技能
- 📝 持续记录经验

## 命令
- 直接输入消息与 Agent 对话
- `/help` - 显示帮助
- `/status` - 显示状态
- `/selfcheck` - 运行自检
- `/clear` - 清空对话
- `/exit` 或 `/quit` - 退出
"""
    console.print(Panel(Markdown(welcome_text), title="Welcome", border_style="blue"))


def print_help():
    """打印帮助信息"""
    table = Table(title="可用命令")
    table.add_column("命令", style="cyan")
    table.add_column("描述", style="green")

    commands = [
        ("/help", "显示此帮助信息"),
        ("/status", "显示 Agent 状态"),
        ("/selfcheck", "运行自检"),
        ("/memory", "显示记忆状态"),
        ("/skills", "列出已安装技能"),
        ("/channels", "显示 IM 通道状态"),
        ("/agents", "显示 Agent 协同状态 (协同模式)"),
        ("/clear", "清空对话历史"),
        ("/exit, /quit", "退出程序"),
    ]

    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(table)



def show_channels():
    """显示 IM 通道状态"""
    table = Table(title="IM 通道状态")
    table.add_column("通道", style="cyan")
    table.add_column("启用", style="green")
    table.add_column("状态", style="yellow")

    channels = [
        ("Telegram", settings.telegram_enabled, settings.telegram_bot_token),
        ("飞书" if settings.feishu_domain != "lark" else "Lark", settings.feishu_enabled, settings.feishu_app_id),
        ("企业微信(HTTP)", settings.wework_enabled, settings.wework_corp_id),
        ("企业微信(WS)", settings.wework_ws_enabled, settings.wework_ws_bot_id),
        ("钉钉", settings.dingtalk_enabled, settings.dingtalk_client_id),
        ("OneBot", settings.onebot_enabled,
         settings.onebot_ws_url if settings.onebot_mode == "forward"
         else f"{settings.onebot_reverse_host}:{settings.onebot_reverse_port}"),
        ("QQ 官方机器人", settings.qqbot_enabled, settings.qqbot_app_id),
        ("微信", settings.wechat_enabled, settings.wechat_token),
    ]

    for name, enabled, token in channels:
        enabled_str = "✓" if enabled else "✗"
        if enabled and token:
            status = "已连接" if _message_gateway else "待启动"
        elif enabled:
            status = "缺少配置"
        else:
            status = "-"
        table.add_row(name, enabled_str, status)

    console.print(table)

    if _message_gateway:
        adapters = _message_gateway.list_adapters()
        console.print(f"\n[green]活跃适配器:[/green] {', '.join(adapters) if adapters else '无'}")


async def run_interactive():
    """运行交互式 CLI（同时启动 IM 通道）"""
    import signal as _signal

    print_welcome()

    shutdown_event = asyncio.Event()

    agent = get_agent()

    with console.status("[bold green]正在初始化 Agent...", spinner="dots"):
        await agent.initialize()

    console.print("[green]✓[/green] Agent 已准备就绪")

    agent_or_master = agent
    agent_name = agent.name

    # 初始化核心服务（SessionManager、Agent Pool、Orchestrator）
    with console.status("[bold green]正在初始化核心服务...", spinner="dots"):
        await init_core_services(agent_or_master)

    # 启动 IM 通道（可选）
    im_channels = []
    with console.status("[bold green]正在启动 IM 通道...", spinner="dots"):
        im_channels = await start_im_channels(agent_or_master)

    if im_channels:
        console.print(f"[green]✓[/green] IM 通道已启动: {', '.join(im_channels)}")
    else:
        console.print("[yellow]ℹ[/yellow] 未启用 IM 通道（Desktop Chat 仍可使用）")

    console.print()

    # 注册信号处理器用于优雅关闭
    _shutdown_triggered = False

    def _interactive_signal_handler(signum, frame):
        nonlocal _shutdown_triggered
        if not _shutdown_triggered:
            _shutdown_triggered = True
            console.print("\n[yellow]收到停止信号，正在优雅关闭...[/yellow]")
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(shutdown_event.set)
            except RuntimeError:
                pass

    _signal.signal(_signal.SIGINT, _interactive_signal_handler)
    _signal.signal(_signal.SIGTERM, _interactive_signal_handler)

    try:
        loop = asyncio.get_running_loop()

        while not shutdown_event.is_set():
            try:
                user_input = await loop.run_in_executor(
                    None, Prompt.ask, "[bold blue]You[/bold blue]"
                )

                if not user_input.strip():
                    continue

                # 处理命令
                if user_input.startswith("/"):
                    cmd = user_input.lower().strip()

                    if cmd in ("/exit", "/quit"):
                        console.print("[yellow]再见！[/yellow]")
                        break

                    elif cmd == "/help":
                        print_help()
                        continue

                    elif cmd == "/status":
                        await show_status(agent_or_master)
                        continue

                    elif cmd == "/selfcheck":
                        await run_selfcheck(agent_or_master)
                        continue

                    elif cmd == "/memory":
                        show_memory()
                        continue

                    elif cmd == "/skills":
                        show_skills()
                        continue

                    elif cmd == "/channels":
                        show_channels()
                        continue

                    elif cmd == "/clear":
                        if hasattr(agent_or_master, '_cli_session') and agent_or_master._cli_session:
                            agent_or_master._cli_session.context.clear_messages()
                        agent_or_master._conversation_history.clear()
                        agent_or_master._context.messages.clear()
                        console.print("[green]对话历史已清空[/green]")
                        continue

                    else:
                        console.print(f"[red]未知命令: {cmd}[/red]")
                        print_help()
                        continue

                # 正常对话
                with console.status("[bold green]思考中...", spinner="dots"):
                    response = await agent_or_master.chat(user_input)

                # 显示响应
                console.print()
                console.print(
                    Panel(
                        Markdown(response),
                        title=f"[bold green]{agent_name}[/bold green]",
                        border_style="green",
                    )
                )
                console.print()

            except KeyboardInterrupt:
                console.print("\n[yellow]使用 /exit 退出[/yellow]")
            except Exception as e:
                logger.error(f"Error: {e}", exc_info=True)
                console.print(f"[red]错误: {e}[/red]")
    finally:
        with console.status("[bold yellow]正在停止服务...", spinner="dots"):
            await stop_im_channels(graceful=True, drain_timeout=30.0)
        console.print("[green]✓[/green] 服务已停止")


async def show_status(agent: Agent):
    """显示 Agent 状态"""
    table = Table(title="Agent 状态")
    table.add_column("属性", style="cyan")
    table.add_column("值", style="green")

    table.add_row("名称", agent.name)
    table.add_row("已初始化", "✓" if agent.is_initialized else "✗")
    table.add_row("对话轮数", str(len(agent.conversation_history) // 2))
    table.add_row("模型", settings.default_model)
    table.add_row("最大迭代", str(settings.max_iterations))

    console.print(table)


async def run_selfcheck(agent: Agent):
    """运行自检"""
    console.print("[bold]运行自检...[/bold]\n")

    with console.status("[bold green]检查中...", spinner="dots"):
        results = await agent.self_check()

    # 显示结果
    status_color = "green" if results["status"] == "healthy" else "red"
    console.print(f"状态: [{status_color}]{results['status']}[/{status_color}]")
    console.print()

    table = Table(title="检查项目")
    table.add_column("检查项", style="cyan")
    table.add_column("状态", style="green")
    table.add_column("消息", style="white")

    for name, check in results["checks"].items():
        status_icon = (
            "✓" if check["status"] == "ok" else "⚠" if check["status"] == "warning" else "✗"
        )
        status_style = (
            "green"
            if check["status"] == "ok"
            else "yellow"
            if check["status"] == "warning"
            else "red"
        )
        table.add_row(
            name,
            f"[{status_style}]{status_icon}[/{status_style}]",
            check.get("message", ""),
        )

    console.print(table)


def show_memory():
    """显示记忆状态"""
    try:
        content = settings.memory_path.read_text(encoding="utf-8")
        console.print(
            Panel(
                Markdown(content[:2000] + ("..." if len(content) > 2000 else "")),
                title="MEMORY.md",
                border_style="blue",
            )
        )
    except Exception as e:
        console.print(f"[red]无法读取 MEMORY.md: {e}[/red]")


def show_skills():
    """显示已安装技能（建议 4）"""
    try:
        from .skills.catalog import SkillCatalog

        catalog = SkillCatalog()
        skills_text = catalog.generate_catalog()
        if skills_text and skills_text.strip():
            console.print(
                Panel(
                    Markdown(skills_text),
                    title="已安装技能",
                    border_style="green",
                )
            )
        else:
            console.print("[yellow]暂无已安装技能[/yellow]")
            console.print("使用 install_skill 工具安装技能，或在 skills/ 目录下创建技能")
    except Exception as e:
        console.print(f"[red]无法加载技能列表: {e}[/red]")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="显示版本信息"),
):
    """
    OpenAkita - 全能自进化AI助手

    直接运行进入交互模式
    """
    if version:
        from . import __version__

        console.print(f"OpenAkita v{__version__}")
        raise typer.Exit(0)

    # 如果没有子命令，进入交互模式
    if ctx.invoked_subcommand is None:
        # 检查是否至少有一个可用的 LLM 端点
        from .llm.config import get_default_config_path

        has_endpoint = (
            settings.anthropic_api_key
            or get_default_config_path().exists()
        )
        if not has_endpoint:
            console.print("[red]错误: 未配置任何 LLM 端点[/red]")
            console.print(
                "请设置 ANTHROPIC_API_KEY，或运行 'openakita init' 配置 data/llm_endpoints.json"
            )
            raise typer.Exit(1)

        # 运行交互式 CLI
        asyncio.run(run_interactive())


@app.command()
def init(
    project_dir: str | None = typer.Argument(None, help="项目目录（默认当前目录）"),
):
    """
    初始化 OpenAkita - 交互式配置向导

    运行此命令启动配置向导，引导您完成：
    - LLM API 配置
    - IM 通道配置（可选）
    - 记忆系统配置
    - 目录结构创建

    示例:
        openakita init
        openakita init ./my-project
    """
    from .setup import SetupWizard

    wizard = SetupWizard(project_dir)
    success = wizard.run()

    if success:
        raise typer.Exit(0)
    else:
        raise typer.Exit(1)


@app.command()
def run(
    task: str = typer.Argument(..., help="要执行的任务"),
):
    """执行单个任务"""

    async def _run():
        agent = get_agent()
        await agent.initialize()

        with console.status("[bold green]执行任务中...", spinner="dots"):
            result = await agent.execute_task_from_message(task)

        if result.success:
            console.print(
                Panel(
                    Markdown(str(result.data)),
                    title="[green]任务完成[/green]",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel(
                    f"错误: {result.error}",
                    title="[red]任务失败[/red]",
                    border_style="red",
                )
            )

        # 桌面通知
        from .config import settings
        from .core.desktop_notify import notify_task_completed
        if settings.desktop_notify_enabled:
            notify_task_completed(
                task[:80],
                success=result.success,
                duration_seconds=result.duration_seconds,
                sound=settings.desktop_notify_sound,
            )

    asyncio.run(_run())


@app.command()
def selfcheck(
    full: bool = typer.Option(False, "--full", "-f", help="运行完整自检"),
    fix: bool = typer.Option(False, "--fix", help="自动修复发现的问题"),
):
    """运行自检"""

    async def _selfcheck():
        agent = get_agent()
        await agent.initialize()
        await run_selfcheck(agent)

    asyncio.run(_selfcheck())


@app.command()
def status():
    """显示 Agent 状态"""

    async def _status():
        agent = get_agent()
        await agent.initialize()
        await show_status(agent)

    asyncio.run(_status())


@app.command()
def compile(
    force: bool = typer.Option(False, "--force", "-f", help="强制重新编译"),
):
    """
    编译 identity 文件

    将 AGENT.md, USER.md 编译为精简摘要（SOUL.md 已改为全文注入）。

    编译产物保存在 identity/runtime/ 目录。
    """
    from .prompt.compiler import check_compiled_outdated, compile_all

    identity_dir = settings.identity_path

    # 检查是否需要编译
    if not force and not check_compiled_outdated(identity_dir):
        console.print("[yellow]编译产物已是最新，使用 --force 强制重新编译[/yellow]")
        return

    console.print("[bold]正在编译 identity 文件...[/bold]")

    try:
        results = compile_all(identity_dir)

        # 显示结果
        table = Table(title="编译结果")
        table.add_column("源文件", style="cyan")
        table.add_column("产物", style="green")
        table.add_column("大小", style="yellow")

        for name, path in results.items():
            if path.exists():
                size = len(path.read_text(encoding="utf-8"))
                table.add_row(f"{name}.md", path.name, f"{size} 字符")

        console.print(table)
        console.print(f"\n[green]✓[/green] 编译完成，产物保存在 {identity_dir / 'runtime'}")

    except Exception as e:
        console.print(f"[red]编译失败: {e}[/red]")
        raise typer.Exit(1)


@app.command(name="prompt-debug")
def prompt_debug(
    task: str = typer.Argument("", help="任务描述（用于记忆检索）"),
    compiled: bool = typer.Option(True, "--compiled/--full", help="使用编译版本或全文版本"),
):
    """
    显示 prompt 调试信息

    显示系统提示词的各部分 token 统计，
    帮助调试和优化 prompt。
    """
    from .prompt.budget import estimate_tokens
    from .prompt.builder import get_prompt_debug_info

    async def _debug():
        agent = get_agent()
        await agent.initialize()

        console.print(f"[bold]Prompt 调试信息[/bold] (任务: {task or '无'})")
        console.print()

        if compiled:
            # 使用编译版本
            info = get_prompt_debug_info(
                identity_dir=settings.identity_path,
                tool_catalog=agent.tool_catalog,
                skill_catalog=agent.skill_catalog,
                mcp_catalog=agent.mcp_catalog,
                memory_manager=agent.memory_manager,
                task_description=task,
            )

            # Runtime 产物
            table = Table(title="Runtime 文件")
            table.add_column("文件", style="cyan")
            table.add_column("Tokens", style="green")

            for name, tokens in info["compiled_files"].items():
                table.add_row(name, str(tokens))

            console.print(table)
            console.print()

            # 清单
            table = Table(title="清单")
            table.add_column("类型", style="cyan")
            table.add_column("Tokens", style="green")

            for name, tokens in info["catalogs"].items():
                table.add_row(name, str(tokens))

            console.print(table)
            console.print()

            # 记忆
            console.print(f"记忆: {info['memory']} tokens")
            console.print()

            # 总计
            total = info["total"]
            budget = info["budget"]["total"]
            color = "green" if total <= budget else "red"
            console.print(f"[bold]总计: [{color}]{total}[/{color}] / {budget} tokens[/bold]")

        else:
            # 使用全文版本
            from .core.identity import Identity

            identity = Identity()
            identity.load()

            full_prompt = identity.get_system_prompt()
            full_tokens = estimate_tokens(full_prompt)

            console.print(f"全文版本: {full_tokens} tokens")
            console.print()

            # 对比
            info = get_prompt_debug_info(
                identity_dir=settings.identity_path,
                tool_catalog=agent.tool_catalog,
                skill_catalog=agent.skill_catalog,
                mcp_catalog=agent.mcp_catalog,
                memory_manager=agent.memory_manager,
                task_description=task,
            )
            compiled_total = info["total"]

            savings = full_tokens - compiled_total
            savings_pct = (savings / full_tokens * 100) if full_tokens > 0 else 0

            console.print(f"编译版本: {compiled_total} tokens")
            console.print(f"[green]节省: {savings} tokens ({savings_pct:.1f}%)[/green]")

    asyncio.run(_debug())


def _reset_globals():
    """重置全局组件引用，用于重启时清除旧实例。"""
    global _agent, _orchestrator, _message_gateway, _session_manager, _desktop_pool
    _agent = None
    _orchestrator = None
    _desktop_pool = None
    _message_gateway = None
    _session_manager = None


@app.command()
def serve(
    dev: bool = typer.Option(False, "--dev", help="开发模式：监控 src/ 目录的 .py 文件变化，自动重启服务"),
):
    """
    启动服务模式 (无 CLI，只运行 IM 通道)

    用于后台运行，只处理 IM 消息。
    支持单 Agent 和多 Agent 协同模式。
    支持通过 /api/config/restart 触发优雅重启。
    使用 --dev 启用文件监控热加载（开发模式）。
    """
    import json
    import signal
    import threading
    import time
    import warnings
    from pathlib import Path

    from openakita import config as cfg

    # 压制 Windows asyncio 关闭时的 ResourceWarning
    warnings.filterwarnings("ignore", category=ResourceWarning, module="asyncio")

    # PyInstaller 打包模式 / NO_COLOR 环境：禁用 Rich 颜色渲染和高亮，
    # 避免 legacy_windows_render 产生无法显示的字符。
    # 注：_ensure_utf8 已将 stdout 全局 reconfigure 为 UTF-8，此处额外包装是
    # 为了确保 Rich Console 使用独立的 UTF-8 stream（双保险）。
    global console
    if getattr(sys, "frozen", False) or os.environ.get("NO_COLOR"):
        import io
        console = Console(file=io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace"),
                          force_terminal=False, no_color=True, highlight=False)

    # ── 心跳文件机制 ──
    # 后端进程通过独立守护线程定期写入心跳文件，供 Tauri 侧判断进程真实健康状态。
    # 使用独立线程而非 asyncio task，确保即使 event loop 卡死，心跳也能持续（或停止写入
    # 以表明进程已卡死）。心跳文件位于 {CWD}/data/backend.heartbeat。
    _heartbeat_file = Path.cwd() / "data" / "backend.heartbeat"
    _heartbeat_stop = threading.Event()
    _heartbeat_phase = "starting"  # "starting" | "initializing" | "running" | "restarting"
    _heartbeat_http_ready = False

    def _write_heartbeat():
        """写入一次心跳（原子写入：先写临时文件再重命名）"""
        try:
            _heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            from openakita import __git_hash__, __version__
            data = {
                "pid": os.getpid(),
                "timestamp": time.time(),
                "phase": _heartbeat_phase,
                "http_ready": _heartbeat_http_ready,
                "version": __version__,
                "git_hash": __git_hash__,
            }
            tmp = _heartbeat_file.with_suffix(".heartbeat.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            # 原子重命名（Windows 上 rename 会覆盖目标文件，Python 3.3+）
            tmp.replace(_heartbeat_file)
        except Exception:
            pass  # 心跳写入失败不应影响服务运行

    def _heartbeat_loop():
        """心跳守护线程：每 10 秒写入一次心跳文件"""
        while not _heartbeat_stop.is_set():
            _write_heartbeat()
            _heartbeat_stop.wait(10)  # 等待 10 秒或被唤醒停止

    def _start_heartbeat():
        """启动心跳线程"""
        nonlocal _heartbeat_phase, _heartbeat_http_ready
        _heartbeat_stop.clear()
        _heartbeat_phase = "starting"
        _heartbeat_http_ready = False
        _write_heartbeat()  # 立即写一次
        t = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
        t.start()
        return t

    def _stop_heartbeat():
        """停止心跳并清理心跳文件"""
        _heartbeat_stop.set()
        try:
            if _heartbeat_file.exists():
                _heartbeat_file.unlink()
        except Exception:
            pass

    # 用于优雅关闭的标志
    shutdown_event = None
    agent_or_master = None
    shutdown_triggered = False

    async def _serve():
        nonlocal shutdown_event, agent_or_master, shutdown_triggered
        nonlocal _heartbeat_phase, _heartbeat_http_ready
        shutdown_event = asyncio.Event()
        shutdown_triggered = False
        _heartbeat_phase = "initializing"

        from openakita import get_version_string
        _version_str = get_version_string()
        logger.info(f"OpenAkita {_version_str} starting...")

        console.print(
            Panel(
                f"[bold]OpenAkita 服务模式[/bold]\n\n"
                f"版本: {_version_str}\n"
                "只运行 IM 通道，不启动 CLI 交互。\n"
                "按 Ctrl+C 停止服务。",
                title="Serve Mode",
                border_style="blue",
            )
        )

        agent = get_agent()

        console.print("[bold green]正在初始化 Agent...[/bold green]")
        await agent.initialize()
        console.print(f"[green]✓[/green] Agent 已初始化 (技能: {agent.skill_registry.count})")

        agent_or_master = agent

        # 初始化核心服务（SessionManager、Agent Pool、Orchestrator）
        console.print("[bold green]正在初始化核心服务...[/bold green]")
        await init_core_services(agent_or_master)
        console.print("[green]✓[/green] 核心服务已就绪")

        # 启动 IM 通道（可选）
        console.print("[bold green]正在启动 IM 通道...[/bold green]")
        im_channels = await start_im_channels(agent_or_master)

        if im_channels:
            console.print(f"[green]✓[/green] IM 通道已启动: {', '.join(im_channels)}")
        else:
            console.print("[yellow]ℹ[/yellow] 未启用 IM 通道（HTTP API 仍可使用）")

        # 注入 shutdown_event 到网关（供终极重启指令使用）
        if _message_gateway is not None:
            _message_gateway.set_shutdown_event(shutdown_event)

        # 启动 HTTP API 服务器（供 Setup Center Chat 页面使用）
        api_task = None
        _api_fatal = False
        try:
            from openakita.api.server import start_api_server
            api_task = await start_api_server(
                agent=agent_or_master,
                shutdown_event=shutdown_event,
                session_manager=_session_manager,
                gateway=_message_gateway,
                orchestrator=_orchestrator,
                agent_pool=_desktop_pool,
            )
            console.print("[green]✓[/green] HTTP API 已启动: http://127.0.0.1:18900")
            _heartbeat_phase = "running"
            _heartbeat_http_ready = True
            _write_heartbeat()  # 立即刷新心跳，标记 HTTP 就绪
        except ImportError:
            console.print("[yellow]⚠[/yellow] HTTP API 未启动（缺少 fastapi/uvicorn 依赖）")
        except Exception as e:
            console.print(f"[red]✗[/red] HTTP API 启动失败: {e}")
            logger.error(f"HTTP API server failed to start: {e}", exc_info=True)
            _api_fatal = True

        if _api_fatal:
            # HTTP API 是 Setup Center 的核心依赖，启动失败时应退出进程
            # 让 Tauri 能正确检测到进程退出并报错给用户
            console.print("[red]HTTP API 启动失败，进程即将退出。请检查端口 18900 是否被占用。[/red]")
            shutdown_event.set()

        console.print()
        if dev:
            console.print("[bold]服务运行中 [cyan](dev 模式)[/cyan]...[/bold] 文件变化时自动重启，按 Ctrl+C 停止")
        else:
            console.print("[bold]服务运行中...[/bold] 按 Ctrl+C 停止")

        # ── dev 模式：文件监控自动重启 ──
        _watch_task = None
        if dev:
            async def _file_watcher():
                try:
                    from watchfiles import awatch, Change
                    src_dir = Path(__file__).resolve().parent  # src/openakita/
                    console.print(f"[dim]📂 监控目录: {src_dir}[/dim]")
                    async for changes in awatch(
                        src_dir,
                        watch_filter=lambda change, path: path.endswith(".py"),
                        debounce=1000,
                        step=500,
                    ):
                        changed_files = [Path(p).name for _, p in changes]
                        console.print(f"\n[cyan]🔄 检测到文件变化: {', '.join(changed_files)}，正在重启...[/cyan]")
                        cfg._restart_requested = True
                        shutdown_event.set()
                        return
                except ImportError:
                    console.print("[yellow]⚠ watchfiles 未安装，dev 模式文件监控不可用[/yellow]")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"File watcher error: {e}")

            _watch_task = asyncio.create_task(_file_watcher())

        # 保持运行，使用 Event 来优雅关闭
        try:
            await shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            if _watch_task and not _watch_task.done():
                _watch_task.cancel()
            if not shutdown_triggered:
                shutdown_triggered = True
                is_restart = cfg._restart_requested
                # 更新心跳状态为重启/停止中
                _heartbeat_phase = "restarting" if is_restart else "stopping"
                _heartbeat_http_ready = False
                _write_heartbeat()
                if is_restart:
                    console.print("\n[yellow]正在重启服务...[/yellow]")
                else:
                    console.print("\n[yellow]正在停止服务...[/yellow]")
                try:
                    # 停止 HTTP API 服务器
                    if api_task is not None:
                        api_task.cancel()
                        try:
                            await asyncio.wait_for(api_task, timeout=2.0)
                        except (asyncio.CancelledError, TimeoutError):
                            pass
                    await asyncio.wait_for(
                        stop_im_channels(graceful=True, drain_timeout=30.0),
                        timeout=35.0,
                    )
                except TimeoutError:
                    logger.warning("Shutdown timeout, forcing exit")
                except Exception as e:
                    # 忽略停止过程中的异常（常见于 Windows asyncio）
                    logger.debug(f"Exception during shutdown (ignored): {e}")

                if is_restart:
                    console.print("[cyan]✓[/cyan] 服务已停止，准备重启...")
                else:
                    console.print("[green]✓[/green] 服务已停止")

    def signal_handler(signum, frame):
        """信号处理器，用于优雅关闭"""
        nonlocal shutdown_triggered
        if shutdown_event and not shutdown_triggered:
            shutdown_triggered = True
            # 信号触发的是真正的关闭，不是重启
            cfg._restart_requested = False
            console.print("\n[yellow]收到停止信号，正在优雅关闭...[/yellow]")
            # 使用 call_soon_threadsafe 确保线程安全
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(shutdown_event.set)
            except RuntimeError:
                pass

    # 设置信号处理（所有平台都需要，以支持优雅关闭）
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # ── 主循环：支持重启 ──
    # 首次进入时 _restart_requested 为 False，正常启动。
    # 当 /api/config/restart 设置 _restart_requested=True 并触发 shutdown 后，
    # 循环会重新加载配置、重置全局状态并重新初始化所有组件。
    _start_heartbeat()
    first_run = True
    while first_run or cfg._restart_requested:
        first_run = False
        if cfg._restart_requested:
            console.print("\n[bold cyan]═══ 服务重启中 ═══[/bold cyan]")
            cfg._restart_requested = False
            _reset_globals()
            settings.reload()  # 重新读取 .env 配置

            # 重置心跳状态为重启中
            _heartbeat_phase = "restarting"
            _heartbeat_http_ready = False
            _write_heartbeat()

            # 重新扫描并注入模块路径（模块可能在服务运行期间安装/卸载）
            try:
                from openakita.runtime_env import inject_module_paths_runtime
                n = inject_module_paths_runtime()
                if n > 0:
                    console.print(f"[dim]已注入 {n} 个新模块路径[/dim]")
            except Exception as e:
                logger.debug(f"Module path refresh failed (non-critical): {e}")

            # 等待端口释放（旧 uvicorn 关闭后 TCP socket 可能处于 TIME_WAIT）
            try:
                from openakita.api.server import API_HOST, API_PORT, wait_for_port_free
                _api_port = int(os.environ.get("API_PORT", API_PORT))
                console.print(f"[dim]等待端口 {_api_port} 释放...[/dim]")
                if not wait_for_port_free(API_HOST, _api_port, timeout=15.0):
                    console.print(f"[yellow]⚠[/yellow] 端口 {_api_port} 仍被占用，继续尝试启动...")
                else:
                    console.print(f"[dim]端口 {_api_port} 已就绪[/dim]")
            except Exception as e:
                logger.debug(f"Port wait check failed (non-critical): {e}")

        # 检查重启准备期间是否收到 Ctrl+C（信号处理器可能在 reload 期间触发）
        if shutdown_triggered:
            console.print("\n[yellow]服务已停止（重启被取消）[/yellow]")
            break

        # 在进入 _serve() 前，记录当前 restart flag，
        # _serve() 内部 shutdown 会读取它，但我们需要在 asyncio.run() 返回后仍能判断。
        restart_flag_before = cfg._restart_requested

        try:
            asyncio.run(_serve())
        except KeyboardInterrupt:
            if not shutdown_triggered:
                console.print("\n[yellow]服务已停止[/yellow]")
            break
        except (ConnectionResetError, OSError) as e:
            # 忽略 Windows asyncio 关闭时的已知问题
            # WinError 995: 由于线程退出或应用程序请求，已中止 I/O 操作
            if "995" in str(e):
                if not shutdown_triggered:
                    console.print("\n[yellow]服务已停止[/yellow]")
            else:
                raise
        except asyncio.CancelledError:
            # asyncio.run() 退出时可能抛出 CancelledError（BaseException）
            # 对于重启场景，这是正常的
            if not cfg._restart_requested:
                if not shutdown_triggered:
                    console.print("\n[yellow]服务已停止[/yellow]")
                break
        except Exception as e:
            # 捕获其他异常，检查是否是 InvalidStateError
            if "InvalidState" in str(type(e).__name__) or "invalid state" in str(e).lower():
                if not shutdown_triggered:
                    console.print("\n[yellow]服务已停止[/yellow]")
            else:
                raise

        # 如果是 API 触发的重启（不是 Ctrl+C / 信号触发的关闭），
        # 需要重置 shutdown_triggered 以允许重启循环继续。
        if cfg._restart_requested or restart_flag_before:
            shutdown_triggered = False
            cfg._restart_requested = True  # 确保循环条件成立
            continue

        # 不是重启请求，跳出循环
        break

    # 主循环结束，停止心跳并清理心跳文件
    _stop_heartbeat()


@app.command(name="run-mcp-module", hidden=True)
def run_mcp_module(
    module_path: str = typer.Argument(..., help="Python module path for MCP server"),
):
    """启动内置 MCP 服务器模块（打包模式内部命令）

    PyInstaller 打包环境中，python -m 无法访问冻结模块。
    此命令通过冻结主程序 import 并运行 FastMCP 实例，作为 stdio 子进程替代方案。
    """
    if not module_path.startswith("openakita."):
        print(f"Error: only openakita.* modules allowed, got: {module_path}", file=sys.stderr)
        raise typer.Exit(1)

    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        print(f"Error: cannot import {module_path}: {e}", file=sys.stderr)
        raise typer.Exit(1) from None

    mcp_instance = getattr(mod, "mcp", None)
    if mcp_instance is None:
        print(f"Error: {module_path} has no 'mcp' attribute", file=sys.stderr)
        raise typer.Exit(1)

    # MCP stdio 协议独占 stdout/stdin，移除所有控制台日志 handler 防止协议污染
    _root = logging.getLogger()
    for h in _root.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            _root.removeHandler(h)

    mcp_instance.run()


if __name__ == "__main__":
    app()
