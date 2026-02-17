"""
OpenAkita CLI 入口

使用 Typer 和 Rich 提供交互式命令行界面
支持同时运行 CLI 和 IM 通道（Telegram、飞书等）
支持多 Agent 协同模式（通过 ORCHESTRATION_ENABLED 配置）
"""

import asyncio
import importlib
import logging
import os
import subprocess
import sys

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .config import settings
from .core.agent import Agent
from .logging import setup_logging

# 配置日志系统（使用新的日志模块）
setup_logging(
    log_dir=settings.log_dir_path,
    log_level=settings.log_level,
    log_format=settings.log_format,
    log_file_prefix=settings.log_file_prefix,
    log_max_size_mb=settings.log_max_size_mb,
    log_backup_count=settings.log_backup_count,
    log_to_console=settings.log_to_console,
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
        if settings.tracing_console_export:
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
_master_agent = None  # MasterAgent（多 Agent 协同模式）
_message_gateway = None
_session_manager = None


def get_agent() -> Agent:
    """获取或创建 Agent 实例（单 Agent 模式）"""
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


def get_master_agent():
    """获取或创建 MasterAgent 实例（多 Agent 协同模式）"""
    global _master_agent
    if _master_agent is None:
        from .orchestration import MasterAgent
        from .orchestration.bus import BusConfig

        bus_config = BusConfig(
            router_address=settings.orchestration_bus_address,
            pub_address=settings.orchestration_pub_address,
        )

        _master_agent = MasterAgent(
            bus_config=bus_config,
            min_workers=settings.orchestration_min_workers,
            max_workers=settings.orchestration_max_workers,
            heartbeat_interval=settings.orchestration_heartbeat_interval,
            health_check_interval=settings.orchestration_health_check_interval,
            data_dir=settings.project_root / "data",
        )
    return _master_agent


def is_orchestration_enabled() -> bool:
    """检查是否启用多 Agent 协同模式"""
    if not settings.orchestration_enabled:
        return False

    # 模块可能在服务运行期间安装，尝试刷新路径
    import sys
    if "zmq" not in sys.modules:
        try:
            from openakita.runtime_env import inject_module_paths_runtime
            inject_module_paths_runtime()
        except Exception:
            pass

    from openakita.tools._import_helper import import_or_hint
    hint = import_or_hint("zmq")
    if hint:
        logger.warning(f"多 Agent 协同模式自动禁用: {hint}")
        return False
    return True


# ==================== IM 通道依赖自动安装 ====================

# 通道名 → [(import_name, pip_package), ...]
_CHANNEL_DEPS: dict[str, list[tuple[str, str]]] = {
    "feishu": [("lark_oapi", "lark-oapi")],
    "dingtalk": [("dingtalk_stream", "dingtalk-stream")],
    "wework": [("aiohttp", "aiohttp"), ("Crypto", "pycryptodome")],
    "onebot": [("websockets", "websockets")],
    "qqbot": [("botpy", "qq-botpy"), ("pilk", "pilk")],
}


def _ensure_channel_deps() -> None:
    """
    检查已启用的 IM 通道所需依赖，缺失的自动 pip install。

    在适配器实例化之前调用，确保依赖就绪。
    Telegram 为核心依赖，始终包含在安装包中，不需检查。
    """
    enabled_channels: list[str] = []
    if settings.feishu_enabled:
        enabled_channels.append("feishu")
    if settings.dingtalk_enabled:
        enabled_channels.append("dingtalk")
    if settings.wework_enabled:
        enabled_channels.append("wework")
    if settings.onebot_enabled:
        enabled_channels.append("onebot")
    if settings.qqbot_enabled:
        enabled_channels.append("qqbot")

    if not enabled_channels:
        return

    # 收集缺失的包
    missing: list[str] = []
    for channel in enabled_channels:
        for import_name, pip_name in _CHANNEL_DEPS.get(channel, []):
            try:
                importlib.import_module(import_name)
            except ImportError:
                if pip_name not in missing:
                    missing.append(pip_name)

    if not missing:
        return

    pkg_list = ", ".join(missing)
    logger.info(f"IM 通道依赖自动安装: {pkg_list} ...")

    # PyInstaller 兼容: 使用 runtime_env 获取正确的 Python 解释器
    from openakita.runtime_env import get_pip_command
    pip_cmd = get_pip_command(missing)
    if not pip_cmd:
        logger.warning("当前环境不支持自动安装依赖，请通过设置中心的模块管理安装")
        console.print(
            f"[yellow]⚠[/yellow] 当前环境不支持自动安装依赖: [bold]{pkg_list}[/bold]\n"
            f"  请通过设置中心的模块管理功能安装，或手动运行: [bold]pip install {' '.join(missing)}[/bold]"
        )
        return

    console.print(
        f"[yellow]⏳[/yellow] 自动安装 IM 通道依赖: [bold]{pkg_list}[/bold] ..."
    )
    try:
        result = subprocess.run(
            pip_cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            logger.info(f"依赖安装成功: {pkg_list}")
            console.print(f"[green]✓[/green] 依赖安装成功: {pkg_list}")
            # 安装后清除 importlib 缓存，让后续 import 能找到新包
            importlib.invalidate_caches()
        else:
            err_tail = (result.stderr or result.stdout or "").strip()[-500:]
            logger.error(f"依赖安装失败 (exit {result.returncode}): {err_tail}")
            console.print(
                f"[red]✗[/red] 依赖安装失败: {pkg_list}\n"
                f"  请手动运行: [bold]pip install {' '.join(missing)}[/bold]"
            )
    except subprocess.TimeoutExpired:
        logger.error("依赖安装超时（180s），请手动运行: pip install " + " ".join(missing))
        console.print(
            f"[red]✗[/red] 依赖安装超时，请手动运行: [bold]pip install {' '.join(missing)}[/bold]"
        )
    except Exception as e:
        logger.error(f"依赖安装异常: {e}")
        console.print(f"[red]✗[/red] 依赖安装异常: {e}")


async def start_im_channels(agent_or_master):
    """
    启动配置的 IM 通道

    Args:
        agent_or_master: Agent 实例或 MasterAgent 实例
    """
    global _message_gateway, _session_manager

    # 检查是否有任何通道启用
    any_enabled = (
        settings.telegram_enabled
        or settings.feishu_enabled
        or settings.wework_enabled
        or settings.dingtalk_enabled
        or settings.onebot_enabled
        or settings.qqbot_enabled
    )

    if not any_enabled:
        logger.info("No IM channels enabled")
        return

    # 自动安装缺失的 IM 通道依赖
    _ensure_channel_deps()

    # 初始化 SessionManager
    from .sessions import SessionManager

    _session_manager = SessionManager(
        storage_path=settings.project_root / settings.session_storage_path,
    )
    await _session_manager.start()
    logger.info("SessionManager started")

    # 初始化在线 STT 客户端（可选）
    from .llm.stt_client import STTClient
    from .llm.config import load_endpoints_config as _load_ep_config

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
        whisper_model=settings.whisper_model,  # 从配置读取 Whisper 模型
        whisper_language=settings.whisper_language,  # 语音识别语言
        stt_client=stt_client,  # 在线 STT 客户端
    )

    # 注册启用的适配器
    adapters_started = []

    # Telegram
    if settings.telegram_enabled and settings.telegram_bot_token:
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

    # 飞书
    if settings.feishu_enabled and settings.feishu_app_id:
        try:
            from .channels.adapters import FeishuAdapter

            feishu = FeishuAdapter(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
            )
            await _message_gateway.register_adapter(feishu)
            adapters_started.append("feishu")
            logger.info("Feishu adapter registered")
        except Exception as e:
            logger.error(f"Failed to start Feishu adapter: {e}")

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

    # 钉钉
    if settings.dingtalk_enabled and settings.dingtalk_client_id:
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
    if settings.onebot_enabled and settings.onebot_ws_url:
        try:
            from .channels.adapters import OneBotAdapter

            onebot = OneBotAdapter(
                ws_url=settings.onebot_ws_url,
                access_token=settings.onebot_access_token or None,
            )
            await _message_gateway.register_adapter(onebot)
            adapters_started.append("onebot")
            logger.info("OneBot adapter registered")
        except Exception as e:
            logger.error(f"Failed to start OneBot adapter: {e}")

    # QQ 官方机器人
    if settings.qqbot_enabled and settings.qqbot_app_id:
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

    # 设置 Agent 处理函数
    # 根据是否启用协同模式选择不同的处理方式
    if is_orchestration_enabled():
        # 多 Agent 协同模式：通过 MasterAgent 路由
        master = agent_or_master

        async def agent_handler(session, message: str) -> str:
            """通过 MasterAgent 处理消息"""
            try:
                session_messages = session.context.get_messages()
                response = await master.handle_request(
                    session_id=session.id,
                    message=message,
                    session_messages=session_messages,
                    session=session,
                    gateway=_message_gateway,
                )
                return response
            except Exception as e:
                logger.error(f"MasterAgent handler error: {e}", exc_info=True)
                return f"❌ 处理出错: {str(e)}"

        # 透传 Agent 的中断控制方法，供 Gateway 中断检测使用
        if master._local_agent:
            _la = master._local_agent
            agent_handler.is_stop_command = _la.is_stop_command
            agent_handler.is_skip_command = _la.is_skip_command
            agent_handler.classify_interrupt = _la.classify_interrupt
            agent_handler.cancel_current_task = _la.cancel_current_task
            agent_handler.skip_current_step = _la.skip_current_step
            agent_handler.insert_user_message = _la.insert_user_message

        # 设置 Brain 到 Gateway（用于模型切换命令）
        # MasterAgent 的 _local_agent 有 brain 属性
        if master._local_agent:
            _message_gateway.set_brain(master._local_agent.brain)
    else:
        # 单 Agent 模式：直接调用 Agent
        agent = agent_or_master

        async def agent_handler(session, message: str) -> str:
            """直接通过 Agent 处理消息"""
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
                return f"❌ 处理出错: {str(e)}"

        # 透传 Agent 的中断控制方法，供 Gateway 中断检测使用
        agent_handler.is_stop_command = agent.is_stop_command
        agent_handler.is_skip_command = agent.is_skip_command
        agent_handler.classify_interrupt = agent.classify_interrupt
        agent_handler.cancel_current_task = agent.cancel_current_task
        agent_handler.skip_current_step = agent.skip_current_step
        agent_handler.insert_user_message = agent.insert_user_message

        # 设置 Agent 的 scheduler gateway
        agent.set_scheduler_gateway(_message_gateway)

        # 设置 Brain 到 Gateway（用于模型切换命令）
        _message_gateway.set_brain(agent.brain)

    _message_gateway.agent_handler = agent_handler

    # 启动网关
    if adapters_started:
        await _message_gateway.start()
        logger.info(f"MessageGateway started with adapters: {adapters_started}")
        return adapters_started

    return []


async def stop_im_channels():
    """停止 IM 通道"""
    global _message_gateway, _session_manager

    if _message_gateway:
        await _message_gateway.stop()
        logger.info("MessageGateway stopped")

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


async def show_orchestration_status(master):
    """显示多 Agent 协同状态"""
    stats = master.get_stats()

    # 基本信息
    table = Table(title="MasterAgent 状态")
    table.add_column("属性", style="cyan")
    table.add_column("值", style="green")

    table.add_row("模式", "多 Agent 协同")
    table.add_row("总任务数", str(stats["tasks_total"]))
    table.add_row("本地处理", str(stats["tasks_local"]))
    table.add_row("分发处理", str(stats["tasks_distributed"]))
    table.add_row("成功", str(stats["tasks_success"]))
    table.add_row("失败", str(stats["tasks_failed"]))
    table.add_row("待处理任务", str(stats["pending_tasks"]))

    console.print(table)
    console.print()

    # Agent 列表
    show_agents(master)


def show_agents(master):
    """显示 Agent 列表"""
    dashboard = master.get_dashboard_data()
    summary = dashboard["summary"]
    agents = dashboard["agents"]

    # 摘要
    console.print(
        f"[bold]Agent 摘要:[/bold] "
        f"总计 {summary['total_agents']} | "
        f"空闲 [green]{summary['idle']}[/green] | "
        f"繁忙 [yellow]{summary['busy']}[/yellow] | "
        f"故障 [red]{summary['dead']}[/red]"
    )
    console.print()

    # Agent 列表
    if agents:
        table = Table(title="活跃 Agent")
        table.add_column("ID", style="cyan")
        table.add_column("类型", style="blue")
        table.add_column("状态", style="green")
        table.add_column("当前任务", style="white")
        table.add_column("完成/失败", style="yellow")
        table.add_column("心跳", style="dim")

        for agent_info in agents:
            status = agent_info["status"]
            status_style = {
                "idle": "[green]空闲[/green]",
                "busy": "[yellow]繁忙[/yellow]",
                "dead": "[red]故障[/red]",
                "stopping": "[dim]停止中[/dim]",
            }.get(status, status)

            table.add_row(
                agent_info["agent_id"],
                agent_info["type"],
                status_style,
                (agent_info["current_task"] or "-")[:30],
                f"{agent_info['tasks_completed']}/{agent_info['tasks_failed']}",
                agent_info["last_heartbeat"],
            )

        console.print(table)
    else:
        console.print("[yellow]没有活跃的 Agent[/yellow]")


def show_channels():
    """显示 IM 通道状态"""
    table = Table(title="IM 通道状态")
    table.add_column("通道", style="cyan")
    table.add_column("启用", style="green")
    table.add_column("状态", style="yellow")

    channels = [
        ("Telegram", settings.telegram_enabled, settings.telegram_bot_token),
        ("飞书", settings.feishu_enabled, settings.feishu_app_id),
        ("企业微信", settings.wework_enabled, settings.wework_corp_id),
        ("钉钉", settings.dingtalk_enabled, settings.dingtalk_client_id),
        ("OneBot", settings.onebot_enabled, settings.onebot_ws_url),
        ("QQ 官方机器人", settings.qqbot_enabled, settings.qqbot_app_id),
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
    print_welcome()

    # 根据配置选择单 Agent 或多 Agent 协同模式
    if is_orchestration_enabled():
        console.print("[cyan]ℹ[/cyan] 多 Agent 协同模式已启用")
        master = get_master_agent()

        # 启动 MasterAgent
        with console.status("[bold green]正在启动 MasterAgent...", spinner="dots"):
            await master.start()

        worker_count = len([a for a in master.registry.list_all() if a.agent_type == "worker"])
        console.print(f"[green]✓[/green] MasterAgent 已启动 (Workers: {worker_count})")

        agent_or_master = master
        agent_name = "OpenAkita (Master)"
    else:
        agent = get_agent()

        # 初始化 Agent
        with console.status("[bold green]正在初始化 Agent...", spinner="dots"):
            await agent.initialize()

        console.print("[green]✓[/green] Agent 已准备就绪")

        agent_or_master = agent
        agent_name = agent.name

    # 启动 IM 通道
    im_channels = []
    with console.status("[bold green]正在启动 IM 通道...", spinner="dots"):
        im_channels = await start_im_channels(agent_or_master)

    if im_channels:
        console.print(f"[green]✓[/green] IM 通道已启动: {', '.join(im_channels)}")
    else:
        console.print("[yellow]ℹ[/yellow] 未启用任何 IM 通道 (可在 .env 中配置)")

    console.print()

    try:
        # 使用 run_in_executor 异步获取用户输入，避免阻塞事件循环
        # 这样 IM 通道（钉钉、飞书等）的消息处理不会被 CLI 等待输入阻塞
        loop = asyncio.get_running_loop()

        while True:
            try:
                # 获取用户输入（在线程池中执行，不阻塞事件循环）
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
                        if is_orchestration_enabled():
                            await show_orchestration_status(agent_or_master)
                        else:
                            await show_status(agent_or_master)
                        continue

                    elif cmd == "/selfcheck":
                        if not is_orchestration_enabled():
                            await run_selfcheck(agent_or_master)
                        else:
                            console.print("[yellow]协同模式下自检功能开发中[/yellow]")
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

                    elif cmd == "/agents":
                        if is_orchestration_enabled():
                            show_agents(agent_or_master)
                        else:
                            console.print("[yellow]单 Agent 模式，无 Worker 列表[/yellow]")
                        continue

                    elif cmd == "/clear":
                        if not is_orchestration_enabled():
                            # 清理 CLI Session 上下文（主要的对话历史）
                            if hasattr(agent_or_master, '_cli_session') and agent_or_master._cli_session:
                                agent_or_master._cli_session.context.clear_messages()
                            # 兼容旧属性
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
                    if is_orchestration_enabled():
                        # 多 Agent 协同模式
                        response = await agent_or_master.handle_request(
                            session_id="cli",
                            message=user_input,
                        )
                    else:
                        # 单 Agent 模式
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
        # 停止服务
        with console.status("[bold yellow]正在停止服务...", spinner="dots"):
            await stop_im_channels()
            if is_orchestration_enabled():
                await agent_or_master.stop()
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

    将 SOUL.md, AGENT.md, USER.md 编译为精简摘要，
    降低约 55% 的 token 消耗。

    编译产物保存在 identity/compiled/ 目录。
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
        console.print(f"\n[green]✓[/green] 编译完成，产物保存在 {identity_dir / 'compiled'}")

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

            # 编译产物
            table = Table(title="编译产物 (v2)")
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


@app.command()
def _reset_globals():
    """重置全局组件引用，用于重启时清除旧实例。"""
    global _agent, _master_agent, _message_gateway, _session_manager
    _agent = None
    _master_agent = None
    _message_gateway = None
    _session_manager = None


@app.command()
def serve():
    """
    启动服务模式 (无 CLI，只运行 IM 通道)

    用于后台运行，只处理 IM 消息。
    支持单 Agent 和多 Agent 协同模式。
    支持通过 /api/config/restart 触发优雅重启。
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

    # PyInstaller 打包模式 + Windows: Rich 的 legacy_windows_render 会因 GBK 编码
    # 无法输出 Unicode 符号（✓、⚠ 等）而崩溃。使用安全的 Console 替代。
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
            data = {
                "pid": os.getpid(),
                "timestamp": time.time(),
                "phase": _heartbeat_phase,
                "http_ready": _heartbeat_http_ready,
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

        mode_text = "多 Agent 协同模式" if is_orchestration_enabled() else "单 Agent 模式"
        console.print(
            Panel(
                f"[bold]OpenAkita 服务模式[/bold]\n\n"
                f"模式: {mode_text}\n"
                "只运行 IM 通道，不启动 CLI 交互。\n"
                "按 Ctrl+C 停止服务。",
                title="Serve Mode",
                border_style="blue",
            )
        )

        # 根据配置选择模式
        if is_orchestration_enabled():
            master = get_master_agent()

            console.print("[bold green]正在启动 MasterAgent...[/bold green]")
            await master.start()

            worker_count = len([a for a in master.registry.list_all() if a.agent_type == "worker"])
            console.print(f"[green]✓[/green] MasterAgent 已启动 (Workers: {worker_count})")

            agent_or_master = master
        else:
            agent = get_agent()

            console.print("[bold green]正在初始化 Agent...[/bold green]")
            await agent.initialize()
            console.print(f"[green]✓[/green] Agent 已初始化 (技能: {agent.skill_registry.count})")

            agent_or_master = agent

        # 启动 IM 通道
        console.print("[bold green]正在启动 IM 通道...[/bold green]")
        im_channels = await start_im_channels(agent_or_master)

        if not im_channels:
            console.print("[yellow]⚠[/yellow] 没有启用任何 IM 通道（HTTP API 仍可使用）")

        if im_channels:
            console.print(f"[green]✓[/green] IM 通道已启动: {', '.join(im_channels)}")

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
        console.print("[bold]服务运行中...[/bold] 按 Ctrl+C 停止")

        # 保持运行，使用 Event 来优雅关闭
        try:
            await shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
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
                    # 使用 asyncio.shield 保护关闭操作
                    await asyncio.wait_for(stop_im_channels(), timeout=5.0)
                    if is_orchestration_enabled() and agent_or_master:
                        await asyncio.wait_for(agent_or_master.stop(), timeout=5.0)
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
    heartbeat_thread = _start_heartbeat()
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


if __name__ == "__main__":
    app()
