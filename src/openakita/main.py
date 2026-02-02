"""
OpenAkita CLI 入口

使用 Typer 和 Rich 提供交互式命令行界面
支持同时运行 CLI 和 IM 通道（Telegram、飞书等）
支持多 Agent 协同模式（通过 ORCHESTRATION_ENABLED 配置）
"""

import asyncio
import logging
import sys
from typing import Optional, Union

import typer
from typer import Context
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from .core.agent import Agent
from .config import settings
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

# Typer 应用
app = typer.Typer(
    name="openakita",
    help="OpenAkita - 全能自进化AI助手",
    add_completion=False,
)

# Rich 控制台
console = Console()

# 全局组件
_agent: Optional[Agent] = None
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
    return settings.orchestration_enabled


async def start_im_channels(agent_or_master):
    """
    启动配置的 IM 通道
    
    Args:
        agent_or_master: Agent 实例或 MasterAgent 实例
    """
    global _message_gateway, _session_manager
    
    # 检查是否有任何通道启用
    any_enabled = (
        settings.telegram_enabled or
        settings.feishu_enabled or
        settings.wework_enabled or
        settings.dingtalk_enabled or
        settings.qq_enabled
    )
    
    if not any_enabled:
        logger.info("No IM channels enabled")
        return
    
    # 初始化 SessionManager
    from .sessions import SessionManager
    _session_manager = SessionManager(
        storage_path=settings.project_root / settings.session_storage_path,
    )
    await _session_manager.start()
    logger.info("SessionManager started")
    
    # 初始化 MessageGateway (先创建，agent_handler 会引用它)
    from .channels import MessageGateway
    _message_gateway = MessageGateway(
        session_manager=_session_manager,
        agent_handler=None,  # 稍后设置
        whisper_model=settings.whisper_model,  # 从配置读取 Whisper 模型
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
    
    # 企业微信
    if settings.wework_enabled and settings.wework_corp_id:
        try:
            from .channels.adapters import WeWorkAdapter
            wework = WeWorkAdapter(
                corp_id=settings.wework_corp_id,
                agent_id=settings.wework_agent_id,
                secret=settings.wework_secret,
            )
            await _message_gateway.register_adapter(wework)
            adapters_started.append("wework")
            logger.info("WeWork adapter registered")
        except Exception as e:
            logger.error(f"Failed to start WeWork adapter: {e}")
    
    # 钉钉
    if settings.dingtalk_enabled and settings.dingtalk_app_key:
        try:
            from .channels.adapters import DingTalkAdapter
            dingtalk = DingTalkAdapter(
                app_key=settings.dingtalk_app_key,
                app_secret=settings.dingtalk_app_secret,
            )
            await _message_gateway.register_adapter(dingtalk)
            adapters_started.append("dingtalk")
            logger.info("DingTalk adapter registered")
        except Exception as e:
            logger.error(f"Failed to start DingTalk adapter: {e}")
    
    # QQ
    if settings.qq_enabled and settings.qq_onebot_url:
        try:
            from .channels.adapters import QQAdapter
            qq = QQAdapter(
                onebot_url=settings.qq_onebot_url,
            )
            await _message_gateway.register_adapter(qq)
            adapters_started.append("qq")
            logger.info("QQ adapter registered")
        except Exception as e:
            logger.error(f"Failed to start QQ adapter: {e}")
    
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
                return f"❌ 处理出错: {str(e)[:200]}"
        
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
                return f"❌ 处理出错: {str(e)[:200]}"
        
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
    console.print(f"[bold]Agent 摘要:[/bold] "
                  f"总计 {summary['total_agents']} | "
                  f"空闲 [green]{summary['idle']}[/green] | "
                  f"繁忙 [yellow]{summary['busy']}[/yellow] | "
                  f"故障 [red]{summary['dead']}[/red]")
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
        ("钉钉", settings.dingtalk_enabled, settings.dingtalk_app_key),
        ("QQ", settings.qq_enabled, settings.qq_onebot_url),
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
        
        worker_count = len([
            a for a in master.registry.list_all()
            if a.agent_type == "worker"
        ])
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
        while True:
            try:
                # 获取用户输入
                user_input = Prompt.ask("[bold blue]You[/bold blue]")
                
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
                console.print(Panel(
                    Markdown(response),
                    title=f"[bold green]{agent_name}[/bold green]",
                    border_style="green",
                ))
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
        status_icon = "✓" if check["status"] == "ok" else "⚠" if check["status"] == "warning" else "✗"
        status_style = "green" if check["status"] == "ok" else "yellow" if check["status"] == "warning" else "red"
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
        console.print(Panel(
            Markdown(content[:2000] + ("..." if len(content) > 2000 else "")),
            title="MEMORY.md",
            border_style="blue",
        ))
    except Exception as e:
        console.print(f"[red]无法读取 MEMORY.md: {e}[/red]")


def show_skills():
    """显示已安装技能"""
    console.print("[yellow]技能系统尚未实现[/yellow]")
    # TODO: 实现技能列表


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
        # 检查 API Key
        if not settings.anthropic_api_key:
            console.print("[red]错误: 未设置 ANTHROPIC_API_KEY[/red]")
            console.print("请设置环境变量或在 .env 文件中配置")
            raise typer.Exit(1)
        
        # 运行交互式 CLI
        asyncio.run(run_interactive())


@app.command()
def init(
    project_dir: Optional[str] = typer.Argument(None, help="项目目录（默认当前目录）"),
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
            console.print(Panel(
                Markdown(str(result.data)),
                title="[green]任务完成[/green]",
                border_style="green",
            ))
        else:
            console.print(Panel(
                f"错误: {result.error}",
                title="[red]任务失败[/red]",
                border_style="red",
            ))
    
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
def serve():
    """
    启动服务模式 (无 CLI，只运行 IM 通道)
    
    用于后台运行，只处理 IM 消息。
    支持单 Agent 和多 Agent 协同模式。
    """
    async def _serve():
        mode_text = "多 Agent 协同模式" if is_orchestration_enabled() else "单 Agent 模式"
        console.print(Panel(
            f"[bold]OpenAkita 服务模式[/bold]\n\n"
            f"模式: {mode_text}\n"
            "只运行 IM 通道，不启动 CLI 交互。\n"
            "按 Ctrl+C 停止服务。",
            title="Serve Mode",
            border_style="blue",
        ))
        
        # 根据配置选择模式
        if is_orchestration_enabled():
            master = get_master_agent()
            
            console.print("[bold green]正在启动 MasterAgent...[/bold green]")
            await master.start()
            
            worker_count = len([
                a for a in master.registry.list_all()
                if a.agent_type == "worker"
            ])
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
            console.print("[red]✗[/red] 没有启用任何 IM 通道！")
            console.print("请在 .env 中配置 IM 通道（如 TELEGRAM_ENABLED=true）")
            return
        
        console.print(f"[green]✓[/green] IM 通道已启动: {', '.join(im_channels)}")
        console.print()
        console.print("[bold]服务运行中...[/bold] 按 Ctrl+C 停止")
        
        # 保持运行
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            console.print("\n[yellow]正在停止服务...[/yellow]")
            await stop_im_channels()
            if is_orchestration_enabled():
                await agent_or_master.stop()
            console.print("[green]✓[/green] 服务已停止")
    
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        console.print("\n[yellow]服务已停止[/yellow]")


if __name__ == "__main__":
    app()
