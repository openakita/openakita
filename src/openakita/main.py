"""
OpenAkita CLI 入口

使用 Typer 和 Rich 提供交互式命令行界面
支持同时运行 CLI 和 IM 通道（Telegram、飞书等）
支持多 Agent 协同模式（通过 ORCHESTRATION_ENABLED 配置）
"""

import openakita._ensure_utf8  # noqa: F401  # isort: skip

import asyncio
import contextlib
import importlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .config import settings
from .core.agent import Agent
from .logging import setup_logging

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


# ── Windows asyncio Proactor 噪音抑制（logging 层兜底）──
# Tauri/uvicorn 在 Windows 上 SSE/WebSocket 客户端断开时会触发
# ``_ProactorBasePipeTransport._call_connection_lost`` 抛 ``ConnectionResetError
# [WinError 10054]``。``_serve()`` 里的 ``loop.set_exception_handler`` 已经
# 处理了同一 loop 内的 callback，但有些路径（跨 loop / 多 worker / 后置
# install 时机错过）仍会冒到 asyncio 模块自己的 logger 里。
# 在 logging 层加一个 filter 是最稳的兜底——只要 record 命中特征就降级，不写
# error.log，前端 BugReport 不再被 60% 噪音占据。
class _WindowsAsyncioPipeFilter(logging.Filter):
    _NEEDLES = (
        "_ProactorBasePipeTransport._call_connection_lost",
        "ConnectionResetError",
        "WinError 10054",
    )

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg) if record.msg else ""
        if any(needle in msg for needle in self._NEEDLES):
            return False
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            if isinstance(exc, ConnectionResetError):
                return False
            if "WinError 10054" in str(exc):
                return False
        return True


if sys.platform == "win32":
    logging.getLogger("asyncio").addFilter(_WindowsAsyncioPipeFilter())


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


def _ensure_channel_deps() -> None:
    """检查已启用 IM 通道依赖，并安装到隔离 channel-deps 目录。"""
    from openakita.runtime_channel_deps import ensure_channel_dependencies

    ensure_channel_dependencies(print_fn=console.print)


def _create_bot_adapter(
    bot_type: str, creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str
):
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
            bot_type,
            creds,
            channel_name=channel_name,
            bot_id=bot_id,
            agent_profile_id=agent_id,
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
    """从 SQLite 回填 session 中可能缺失的消息（崩溃恢复）。

    PR-D3：同时绑定 ``set_turn_writer``，让 ``Session.add_message`` 能在
    用户/助手每条消息落地时同步写一份到 SQLite，进程崩溃也不丢历史。
    """
    _actual_agent = agent_or_master
    if _actual_agent and hasattr(_actual_agent, "memory_manager"):
        _mm = _actual_agent.memory_manager
        if hasattr(_mm, "store") and _session_manager is not None:
            _session_manager.set_turn_loader(
                lambda safe_id: _mm.store.get_recent_turns(safe_id, limit=200)
            )

            def _write_turn(safe_id, turn_index, role, content, metadata):
                try:
                    _mm.store.save_turn(
                        session_id=safe_id,
                        turn_index=turn_index,
                        role=role,
                        content=content if isinstance(content, str) else str(content),
                        timestamp=metadata.get("timestamp"),
                    )
                except Exception as exc:
                    logger.debug(f"[main] turn writer failed: {exc}")

            try:
                _session_manager.set_turn_writer(_write_turn)
            except Exception as exc:
                logger.warning(f"[main] set_turn_writer failed: {exc}")
            backfilled = _session_manager.backfill_sessions_from_store()
            if backfilled:
                logger.info(f"Session backfill: recovered {backfilled} turns from SQLite")


def _web_password_already_set() -> bool:
    """PR-L1: 检查 data/web_access.json 是否已经存了哈希密码。

    用于 lan_mode 开启时的安全闸：只要本机已配置过密码，就允许 0.0.0.0；
    否则拒绝启动，避免无密码裸奔。
    """
    try:
        ws = settings.user_workspace_path
        web_access = Path(ws) / "data" / "web_access.json"
        if not web_access.exists():
            return False
        import json as _json

        data = _json.loads(web_access.read_text(encoding="utf-8"))
        return bool(data.get("password_hash") or data.get("hash"))
    except Exception:
        return False


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
        logger.info("No IM channels enabled, SessionManager is still active for Desktop Chat")
        _setup_session_backfill(agent_or_master)
        if hasattr(agent_or_master, "_plugin_manager") and agent_or_master._plugin_manager:
            if _session_manager is not None:
                _session_manager._plugin_hooks = agent_or_master._plugin_manager.hook_registry
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

    # 飞书
    if settings.feishu_enabled and settings.feishu_app_id:
        _feishu_dup = any(
            b.get("type") == "feishu"
            and b.get("credentials", {}).get("app_id") == settings.feishu_app_id
            and b.get("enabled", True)
            for b in (settings.im_bots or [])
        )
        if _feishu_dup:
            logger.info(
                "Feishu adapter skipped: im_bots already contains a feishu bot "
                f"with the same app_id ({settings.feishu_app_id[:8]}...)"
            )
        else:
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
                    bot_type,
                    creds,
                    channel_name=_channel_name,
                    bot_id=bot_id,
                    agent_profile_id=agent_id,
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
        from .utils.errors import format_user_friendly_error

        if _orchestrator is not None:
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

    if hasattr(agent, "_plugin_manager") and agent._plugin_manager:
        _message_gateway._plugin_hooks = agent._plugin_manager.hook_registry
        agent._plugin_manager._external_host_refs["gateway"] = _message_gateway
        if _session_manager is not None:
            _session_manager._plugin_hooks = agent._plugin_manager.hook_registry

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
        ("飞书", settings.feishu_enabled, settings.feishu_app_id),
        ("企业微信(HTTP)", settings.wework_enabled, settings.wework_corp_id),
        ("企业微信(WS)", settings.wework_ws_enabled, settings.wework_ws_bot_id),
        ("钉钉", settings.dingtalk_enabled, settings.dingtalk_client_id),
        (
            "OneBot",
            settings.onebot_enabled,
            settings.onebot_ws_url
            if settings.onebot_mode == "forward"
            else f"{settings.onebot_reverse_host}:{settings.onebot_reverse_port}",
        ),
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
    init_done = asyncio.Event()
    early_input_queue: list[str] = []

    agent = get_agent()

    async def _background_init():
        """Background: initialize agent, core services, and IM channels."""
        console.print("[dim]正在初始化 Agent...[/dim]")
        try:
            await agent.initialize()
            console.print("[green]✓[/green] Agent 已准备就绪")
        except Exception as e:
            console.print(f"[red]✗ Agent 初始化失败: {e}[/red]")
            shutdown_event.set()
            init_done.set()
            return

        console.print("[dim]正在初始化核心服务...[/dim]")
        try:
            await init_core_services(agent)
        except Exception as e:
            console.print(f"[red]✗ 核心服务初始化失败: {e}[/red]")
            logger.error(f"Core services init failed: {e}", exc_info=True)

        # Session recovery (depends on _session_manager from init_core_services)
        _cli_sf = _cli_session_file
        _cid: str | None = None
        if not _cli_force_new_session and _cli_sf.exists():
            try:
                _cid = json.loads(_cli_sf.read_text(encoding="utf-8")).get("chat_id")
            except Exception:
                _cid = None
        if not _cid:
            _cid = f"cli_{_uuid.uuid4().hex[:12]}"
        nonlocal _cli_chat_id
        _cli_chat_id = _cid
        if _session_manager:
            cs = _session_manager.get_session(
                channel="cli", chat_id=_cid, user_id="cli_user", create_if_missing=True
            )
            if cs:
                # C14 re-audit (D2): mark CLI interactive sessions via the
                # entry classifier so the architectural SoT is consistent
                # (``run_interactive`` is already TTY-gated upstream, so
                # classifier returns ``is_unattended=False`` — no-op behavior
                # but eliminates the "classifier sometimes skipped" pattern).
                try:
                    from .core.policy_v2 import (
                        apply_classification_to_session as _apply_cls,
                    )
                    from .core.policy_v2 import (
                        classify_entry as _classify,
                    )

                    _apply_cls(cs, _classify("cli"))
                except Exception:
                    pass
                agent._cli_session = cs
                cs.context.focus_terms = list(getattr(cs.context, "focus_terms", []) or [])
                cs.context.task_checkpoints = list(getattr(cs.context, "task_checkpoints", []) or [])
                cs.context.delegation_chain = list(getattr(cs.context, "delegation_chain", []) or [])
                if getattr(cs.context, "precompact_snapshot", None):
                    try:
                        agent.memory_manager.save_precompact_snapshot(cs.context.precompact_snapshot)
                    except Exception:
                        logger.debug("[CLI] precompact snapshot hydration skipped", exc_info=True)
                mc = len(cs.context.get_messages())
                if mc > 0 and not _cli_force_new_session:
                    console.print(f"[green]✓[/green] 已恢复上次会话 ({mc} 条消息)")
                _cli_sf.parent.mkdir(parents=True, exist_ok=True)
                _cli_sf.write_text(json.dumps({"chat_id": _cid}), encoding="utf-8")

        async def _start_im_bg():
            try:
                channels = await start_im_channels(agent)
                if channels:
                    console.print(f"[green]✓[/green] IM 通道已启动: {', '.join(channels)}")
            except Exception as e:
                logger.warning(f"IM channel start failed: {e}")

        asyncio.create_task(_start_im_bg())
        init_done.set()

    import uuid as _uuid

    agent_or_master = agent
    agent_name = agent.name
    _cli_chat_id: str | None = None
    _cli_session_file = settings.project_root / "data" / ".cli_last_session"

    _init_task = asyncio.create_task(_background_init())

    console.print("[dim]可以开始输入，初始化完成后将自动处理[/dim]")
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

    from .cli.input import create_cli_session, prompt_input
    from .cli.stream_renderer import render_stream
    from .commands.registry import CommandScope, find_command, get_commands

    _cli_handled = {
        "help",
        "status",
        "selfcheck",
        "memory",
        "skills",
        "channels",
        "clear",
        "sessions",
        "session",
        "exit",
        "quit",
    }
    cli_commands = [
        (f"/{c.name}", c.description)
        for c in get_commands()
        if CommandScope.CLI in c.scope and c.name in _cli_handled
    ]
    pt_session, _completer = create_cli_session(commands=cli_commands)

    async def _process_message(user_input: str):
        """Process a single user message (extracted for early-input replay)."""
        _active_session = getattr(agent_or_master, "_cli_session", None)

        if _active_session:
            _active_session.add_message("user", user_input)
            session_messages = _active_session.context.get_messages()
        elif hasattr(agent_or_master, "_context"):
            session_messages = agent_or_master._context.messages
        else:
            session_messages = []

        _sid = _active_session.id if _active_session else _cli_chat_id
        event_stream = agent_or_master.chat_with_session_stream(
            message=user_input,
            session_messages=session_messages,
            session_id=_sid,
            session=_active_session,
        )
        reply_text = await render_stream(event_stream, console, agent_name=agent_name)

        if _active_session and reply_text:
            _meta: dict = {}
            try:
                _ts = getattr(agent_or_master, "build_tool_trace_summary", None)
                if _ts:
                    _tool_summary = _ts()
                    if _tool_summary:
                        _meta["tool_summary"] = _tool_summary
            except Exception:
                pass
            _active_session.add_message("assistant", reply_text, **_meta)

    try:
        while not shutdown_event.is_set():
            try:
                prompt_prefix = "You> " if init_done.is_set() else "(初始化中) You> "
                user_input = await prompt_input(pt_session, prompt_prefix)

                if not user_input.strip():
                    continue

                # N7: Queue early input if agent is not ready yet
                if not init_done.is_set():
                    if user_input.startswith("/") and user_input.lower().strip() in (
                        "/exit",
                        "/quit",
                    ):
                        console.print("[yellow]再见！[/yellow]")
                        shutdown_event.set()
                        break
                    early_input_queue.append(user_input.strip())
                    console.print(
                        f"[dim]已缓存消息 ({len(early_input_queue)})，Agent 就绪后将自动处理[/dim]"
                    )
                    continue

                # Replay queued messages after initialization
                if early_input_queue:
                    queued = early_input_queue.copy()
                    early_input_queue.clear()
                    console.print(f"[green]正在处理 {len(queued)} 条缓存消息...[/green]")
                    for q in queued:
                        if q.startswith("/"):
                            console.print(f"[dim]跳过缓存命令: {q}[/dim]")
                            continue
                        console.print(f"[dim]>>> {q}[/dim]")
                        await _process_message(q)

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
                        _new_id = f"cli_{_uuid.uuid4().hex[:12]}"
                        if _session_manager:
                            cli_session = _session_manager.get_session(
                                channel="cli",
                                chat_id=_new_id,
                                user_id="cli_user",
                                create_if_missing=True,
                            )
                            if cli_session:
                                agent_or_master._cli_session = cli_session
                        else:
                            if (
                                hasattr(agent_or_master, "_cli_session")
                                and agent_or_master._cli_session
                            ):
                                agent_or_master._cli_session.context.clear_messages()
                        agent_or_master._conversation_history.clear()
                        agent_or_master._context.messages.clear()
                        try:
                            from .prompt.builder import clear_prompt_section_cache

                            clear_prompt_section_cache()
                        except Exception:
                            pass
                        _cli_chat_id = _new_id
                        _cli_session_file.parent.mkdir(parents=True, exist_ok=True)
                        _cli_session_file.write_text(
                            json.dumps({"chat_id": _cli_chat_id}), encoding="utf-8"
                        )
                        console.print("[green]对话历史已清空，已开启新会话[/green]")
                        continue

                    elif cmd == "/sessions":
                        if _session_manager:
                            cli_sessions = sorted(
                                _session_manager.list_sessions(channel="cli"),
                                key=lambda s: getattr(s, "created_at", None) or "",
                                reverse=True,
                            )
                            if not cli_sessions:
                                console.print("[yellow]没有历史 CLI 会话[/yellow]")
                            else:
                                from rich.table import Table as _Tbl

                                tbl = _Tbl(title="CLI 会话列表")
                                tbl.add_column("#", style="cyan", width=4)
                                tbl.add_column("会话 ID", style="green")
                                tbl.add_column("消息数", justify="right")
                                tbl.add_column("创建时间")
                                tbl.add_column("当前", justify="center")
                                for i, s in enumerate(cli_sessions, 1):
                                    is_cur = "✓" if (cli_session and s.id == cli_session.id) else ""
                                    tbl.add_row(
                                        str(i),
                                        s.session_key.split(":")[1][:16],
                                        str(len(s.context.get_messages())),
                                        s.created_at.strftime("%m-%d %H:%M")
                                        if hasattr(s, "created_at") and s.created_at
                                        else "?",
                                        is_cur,
                                    )
                                console.print(tbl)
                                console.print("[dim]输入 /session <#> 切换到对应会话[/dim]")
                        else:
                            console.print("[yellow]SessionManager 未启动[/yellow]")
                        continue

                    elif cmd == "/session":
                        console.print(
                            "[yellow]用法: /session <序号>  (先用 /sessions 查看列表)[/yellow]"
                        )
                        continue

                    elif cmd.startswith("/session "):
                        parts = cmd.split(maxsplit=1)
                        if not _session_manager:
                            console.print("[yellow]SessionManager 未启动[/yellow]")
                        elif len(parts) == 2:
                            try:
                                idx = int(parts[1]) - 1
                                cli_sessions = sorted(
                                    _session_manager.list_sessions(channel="cli"),
                                    key=lambda s: getattr(s, "created_at", None) or "",
                                    reverse=True,
                                )
                                if 0 <= idx < len(cli_sessions):
                                    target = cli_sessions[idx]
                                    cli_session = target
                                    agent_or_master._cli_session = target
                                    _cli_chat_id = target.session_key.split(":")[1]
                                    _cli_session_file.parent.mkdir(parents=True, exist_ok=True)
                                    _cli_session_file.write_text(
                                        json.dumps({"chat_id": _cli_chat_id}), encoding="utf-8"
                                    )
                                    msg_count = len(target.context.get_messages())
                                    console.print(
                                        f"[green]已切换到会话 ({msg_count} 条消息)[/green]"
                                    )
                                else:
                                    console.print("[red]序号超出范围[/red]")
                            except ValueError:
                                console.print("[red]请输入有效的会话序号[/red]")
                        continue

                    else:
                        known = find_command(cmd)
                        if known:
                            console.print(
                                f"[yellow]命令 /{known.name} 暂不支持 CLI，请在 Desktop 中使用[/yellow]"
                            )
                        else:
                            console.print(f"[red]未知命令: {cmd}[/red]")
                            print_help()
                        continue

                # 正常对话 — 流式输出
                await _process_message(user_input)

            except EOFError:
                console.print("\n[yellow]再见！[/yellow]")
                break
            except KeyboardInterrupt:
                console.print("\n[yellow]使用 /exit 退出[/yellow]")
            except Exception as e:
                logger.error(f"Error: {e}", exc_info=True)
                console.print(f"[red]错误: {e}[/red]")
    finally:
        if not _init_task.done():
            _init_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _init_task
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


_cli_force_new_session = False
_cli_permission_mode = "default"


def _apply_auto_confirm_flag(*, enabled: bool) -> None:
    """C18 Phase D — translate ``--auto-confirm`` to the Phase C ENV var.

    Done as a tiny helper (not inlined) so unit tests can exercise it
    without spinning up typer. The flag intentionally feeds the same
    ``OPENAKITA_AUTO_CONFIRM`` override registered in
    ``core.policy_v2.env_overrides``: ENV is the single contract surface
    every subprocess / engine reload re-reads.

    Importantly, **destructive (mutating_global) tools and safety_immune
    paths still require confirm** — that gate is in classifier, not in
    the ConfirmationMode value. So even with ``--auto-confirm``:

    - ``write_file path=/etc/...`` (safety_immune) → CONFIRM
    - ``rm -rf /`` (destructive) → CONFIRM
    - ``read_file`` / non-destructive ``run_shell`` → ALLOW

    This is documented in ``--auto-confirm`` help text + audit row.
    """
    if not enabled:
        return
    import os as _os

    _os.environ["OPENAKITA_AUTO_CONFIRM"] = "1"
    # Loud signal so operators don't forget they enabled auto-confirm.
    # Won't trigger AGAIN at every subsequent engine reload (the
    # underlying ENV var being set is enough; the audit row gets
    # written by global_engine._audit_env_overrides on first load).
    console.print(
        "[yellow]auto-confirm enabled — non-destructive tools will skip confirm. "
        "destructive (mutating_global) and safety_immune paths still require confirm.[/yellow]"
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="显示版本信息"),
    new_session: bool = typer.Option(False, "--new", help="强制开启新 CLI 会话，不恢复上次对话"),
    auto_confirm: bool = typer.Option(
        False,
        "--auto-confirm",
        help=(
            "Skip confirm for non-destructive tools (C18 Phase D). "
            "Equivalent to OPENAKITA_AUTO_CONFIRM=1. Destructive "
            "(mutating_global) and safety_immune paths still require confirm."
        ),
    ),
    permission_mode: str = typer.Option(
        "default",
        "--permission-mode",
        help=(
            "权限模式兼容参数。Policy V2 以 POLICIES.yaml confirmation.mode "
            "为准；自动化场景请优先使用 --auto-confirm。"
        ),
    ),
):
    """
    OpenAkita - 全能自进化AI助手

    直接运行进入交互模式
    """
    global _cli_force_new_session, _cli_permission_mode
    _cli_force_new_session = new_session
    _cli_permission_mode = permission_mode

    # C18 Phase D: translate the CLI flag into the Phase C ENV var.
    # MUST happen before any policy_v2 import in a sub-command, hence we
    # do it in the top-level callback (typer invokes the callback before
    # the sub-command). The classify_entry / engine init below all
    # re-read os.environ, so the flag propagates.
    _apply_auto_confirm_flag(enabled=auto_confirm)

    if version:
        from . import __version__

        console.print(f"OpenAkita v{__version__}")
        raise typer.Exit(0)

    # 如果没有子命令，进入交互模式
    if ctx.invoked_subcommand is None:
        # 检查是否至少有一个可用的 LLM 端点
        from .llm.config import get_default_config_path

        has_endpoint = settings.anthropic_api_key or get_default_config_path().exists()
        if not has_endpoint:
            console.print("[red]错误: 未配置任何 LLM 端点[/red]")
            console.print(
                "请设置 ANTHROPIC_API_KEY，或运行 'openakita init' 配置 data/llm_endpoints.json"
            )
            raise typer.Exit(1)

        # C14 / R4-8: 交互模式需要 TTY 来驱动 prompt_toolkit + Rich 的
        # security_confirm 提示。stdin 为管道时（``cat ... | openakita`` /
        # CI 环境 / launchd plist 等），prompt_toolkit 会立刻在第一次输入
        # 处永久挂死，且 Rich ``Prompt.ask`` 退回原始 ``input()`` 也会同样
        # block。给一个明确指引而不是挂死。
        from .core.policy_v2 import classify_entry

        cli_class = classify_entry("cli")
        if cli_class.is_unattended:
            console.print("[yellow]检测到 stdin 非 TTY（管道输入或非交互环境）[/yellow]")
            console.print(
                "交互式 CLI 需要终端。请改用以下任一非交互入口：\n"
                "  • [bold]openakita run \"<task>\"[/bold] - 单次任务执行（unattended）\n"
                "  • [bold]openakita serve[/bold] - 启动 API 服务并通过 /api/chat 调用"
            )
            raise typer.Exit(1)

        if _cli_permission_mode != "default":
            console.print(
                "[yellow]--permission-mode 是旧 Policy V1 兼容参数；"
                "当前 Policy V2 请通过配置页或 POLICIES.yaml 调整 confirmation.mode。[/yellow]"
            )

        # 运行交互式 CLI
        asyncio.run(run_interactive())


@app.command()
def init(
    project_dir: str | None = typer.Argument(None, help="项目目录（默认当前目录）"),
    quick: bool = typer.Option(
        False, "--quick", "-q", help="快速模式：仅配置 Provider + API Key + Model"
    ),
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
        openakita init --quick
        openakita init ./my-project
    """
    from .setup import SetupWizard

    wizard = SetupWizard(project_dir)
    success = wizard.run(quick=quick)

    if success:
        raise typer.Exit(0)
    else:
        raise typer.Exit(1)


@app.command()
def run(
    task: str = typer.Argument(..., help="要执行的任务"),
):
    """执行单个任务（unattended：CONFIRM 类工具不会等待 TTY 响应）"""

    async def _run():
        agent = get_agent()
        await agent.initialize()

        # C14 / R4-8: ``openakita run`` 是一次性非交互入口 — 即使 stdin
        # 是 TTY 也不应等待 ``security_confirm`` SSE/Prompt。把
        # PolicyContext 显式标记为 unattended，让 PolicyEngineV2 step 11
        # 按 ``unattended_strategy`` 路由（默认 ask_owner），CONFIRM-class
        # 工具走 PendingApproval / DeferredApprovalRequired 路径而非挂死。
        #
        # Re-audit (D1): classifier 是 SoT — 这里通过 ``classify_entry``
        # 拿到完整 (is_unattended, default_strategy) 后再喂给
        # build_policy_context，避免 strategy 经 "全局默认兜底" 路径绕行。
        from .core.policy_v2 import (
            build_policy_context,
            classify_entry,
            reset_current_context,
            set_current_context,
        )

        _cls = classify_entry("cli", force_unattended=True)
        cli_ctx = build_policy_context(
            session_id=f"cli_run_{int(time.time())}",
            channel="cli",
            is_unattended=_cls.is_unattended,
            unattended_strategy=_cls.default_strategy or "",
            user_message=task,
        )
        ctx_token = set_current_context(cli_ctx)

        with console.status("[bold green]执行任务中...", spinner="dots"):
            try:
                result = await agent.execute_task_from_message(task)
            finally:
                reset_current_context(ctx_token)

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


def _install_windows_asyncio_pipe_filter() -> None:
    """Suppress known Windows Proactor pipe-close noise without hiding real errors."""
    if sys.platform != "win32":
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    previous_handler = loop.get_exception_handler()

    def _handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        message = str(context.get("message") or "")
        handle = repr(context.get("handle") or "")
        if (
            "_ProactorBasePipeTransport._call_connection_lost" in message
            or "_ProactorBasePipeTransport._call_connection_lost" in handle
        ):
            exc = context.get("exception")
            logger.debug("Ignored Windows asyncio pipe close callback noise: %r", exc)
            return
        if previous_handler is not None:
            previous_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


@app.command()
def serve(
    dev: bool = typer.Option(
        False, "--dev", help="开发模式：监控 src/ 目录的 .py 文件变化，自动重启服务"
    ),
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

    # ── 最早期心跳：在加载 IM/Skills/Plugins/uvicorn 之前先写一次心跳 ──
    # 让 Tauri 心跳读到 phase=starting/http_ready=false，避免 dual-venv hack
    # 期间（cold start 90~120s）前端因为读不到任何信号而误判 backend 已死。
    # 这一段只用 stdlib，不引入任何新依赖，保证即使后续 import 失败心跳也已落盘。
    #
    # 心跳路径优先用 settings.user_workspace_path（Tauri 启动时通过
    # `--workspace <ws_dir>` 传入，或环境变量 OPENAKITA_USER_WORKSPACE）。
    # 用 Path.cwd() 作 fallback 仅在 CLI 用户从其它目录跑 `openakita serve`
    # 时生效；那种场景 Tauri 不读心跳，所以即使落到 cwd 也不会让前端误判。
    try:
        _hb_root = getattr(settings, "user_workspace_path", None) or Path.cwd()
        _early_hb_path = Path(_hb_root) / "data" / "backend.heartbeat"
        _early_hb_path.parent.mkdir(parents=True, exist_ok=True)
        _early_hb_tmp = _early_hb_path.with_suffix(".heartbeat.tmp")
        _early_hb_tmp.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "timestamp": time.time(),
                    "phase": "starting",
                    "http_ready": False,
                    "im_ready": False,
                    "ready": False,
                }
            ),
            encoding="utf-8",
        )
        _early_hb_tmp.replace(_early_hb_path)
    except Exception:
        pass  # 早期心跳写失败不应阻塞 serve

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

        console = Console(
            file=io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace"),
            force_terminal=False,
            no_color=True,
            highlight=False,
        )

    # ── 心跳文件机制 ──
    # 后端进程通过独立守护线程定期写入心跳文件，供 Tauri 侧判断进程真实健康状态。
    # 使用独立线程而非 asyncio task，确保即使 event loop 卡死，心跳也能持续（或停止写入
    # 以表明进程已卡死）。心跳文件位于 {user_workspace_path}/data/backend.heartbeat
    # （与上方早期心跳路径对齐，避免 CLI 模式下 cwd 漂移导致写入与读取分裂）。
    _heartbeat_file = (
        Path(getattr(settings, "user_workspace_path", None) or Path.cwd())
        / "data"
        / "backend.heartbeat"
    )
    _heartbeat_stop = threading.Event()
    _heartbeat_phase = "starting"  # "starting" | "initializing" | "http_ready" | "starting_im" | "running" | "restarting"
    _heartbeat_http_ready = False
    _heartbeat_im_ready = False
    _heartbeat_ready = False

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
                "im_ready": _heartbeat_im_ready,
                "ready": _heartbeat_ready,
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
        nonlocal _heartbeat_phase, _heartbeat_http_ready, _heartbeat_im_ready, _heartbeat_ready
        _heartbeat_stop.clear()
        _heartbeat_phase = "starting"
        _heartbeat_http_ready = False
        _heartbeat_im_ready = False
        _heartbeat_ready = False
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
        nonlocal _heartbeat_phase, _heartbeat_http_ready, _heartbeat_im_ready, _heartbeat_ready
        _install_windows_asyncio_pipe_filter()
        shutdown_event = asyncio.Event()
        shutdown_triggered = False
        _heartbeat_phase = "initializing"
        _heartbeat_http_ready = False
        _heartbeat_im_ready = False
        _heartbeat_ready = False
        _write_heartbeat()

        from openakita import get_version_string

        _version_str = get_version_string()
        logger.info(f"OpenAkita {_version_str} starting...")
        try:
            from .runtime_env import log_runtime_environment_report

            log_runtime_environment_report()
        except Exception:
            logger.debug("Failed to log runtime environment report", exc_info=True)

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

        # 先启动 HTTP API（供 Setup Center/桌面端使用）。
        # IM 通道依赖安装可能很慢，不能阻塞 /api/health 就绪。
        api_task = None
        _api_fatal = False
        try:
            from openakita.api.server import API_HOST, API_PORT, start_api_server

            # PR-L1: 解析 API host / port，按 settings.api_lan_mode 决定绑定范围。
            # 优先级：环境变量 API_HOST > settings.api_lan_mode 推导 > settings.api_host。
            # 安全闸：开启 lan_mode 必须有 web_access 密码或 api_token，
            # 否则 0.0.0.0 暴露后任何同网段设备无认证即可调 API。
            _api_host = API_HOST  # 已经是 env(API_HOST) > "127.0.0.1"
            _api_port = API_PORT
            try:
                if getattr(settings, "api_lan_mode", False):
                    _has_password = bool(os.environ.get("OPENAKITA_WEB_PASSWORD") or _web_password_already_set())
                    _has_token = bool(getattr(settings, "api_token", "") or "").strip()
                    if not (_has_password or _has_token):
                        raise RuntimeError(
                            "api_lan_mode=True 但既没有 web_access 密码也没有 api_token，"
                            "拒绝启动以避免裸奔暴露。请先在 Setup Center 设置访问密码，"
                            "或在 .env 设置 OPENAKITA_API_TOKEN=<32 字符随机串>。"
                        )
                    if not os.environ.get("API_HOST"):
                        _api_host = "0.0.0.0"
                        console.print(
                            "[yellow]⚠ 已开启 api_lan_mode，HTTP API 将绑定到 0.0.0.0 ——"
                            " 同网段所有设备都能看到这个端口。[/yellow]"
                        )
                else:
                    if not os.environ.get("API_HOST"):
                        _api_host = getattr(settings, "api_host", "127.0.0.1") or "127.0.0.1"
            except RuntimeError:
                raise
            except Exception as _exc:
                logger.warning(f"[main] api host resolution fallback: {_exc}")

            api_task = await start_api_server(
                agent=agent_or_master,
                shutdown_event=shutdown_event,
                session_manager=_session_manager,
                gateway=_message_gateway,
                orchestrator=_orchestrator,
                agent_pool=_desktop_pool,
                host=_api_host,
                port=_api_port,
            )
            _display_host = "127.0.0.1" if _api_host in ("0.0.0.0", "::") else _api_host
            console.print(
                f"[green]✓[/green] HTTP API 已启动: http://{_display_host}:{_api_port}"
                + ("  [dim](lan_mode: 0.0.0.0)[/dim]" if _api_host == "0.0.0.0" else "")
            )
            # HTTP API 已可访问，但 IM 通道、晚绑定 gateway、部分后台服务可能仍在启动。
            # 不要在这里把 phase 标成 running，否则前端会把"HTTP ready"误解为
            # "整个后端已完成启动"，这正是状态面板反复出现假运行/未知 IM 的根因。
            _heartbeat_phase = "http_ready"
            _heartbeat_http_ready = True
            _heartbeat_im_ready = False
            _heartbeat_ready = False
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
            console.print(
                "[red]HTTP API 启动失败，进程即将退出。请检查端口 18900 是否被占用。[/red]"
            )
            shutdown_event.set()

        if not _api_fatal:
            # 启动 IM 通道（可选）。放在 HTTP API 之后，避免首次安装通道依赖时
            # 桌面端长时间无法访问本地健康检查。
            _heartbeat_phase = "starting_im"
            _heartbeat_http_ready = True
            _heartbeat_im_ready = False
            _heartbeat_ready = False
            _write_heartbeat()
            console.print("[bold green]正在启动 IM 通道...[/bold green]")
            im_channels = await start_im_channels(agent_or_master)

            if im_channels:
                console.print(f"[green]✓[/green] IM 通道已启动: {', '.join(im_channels)}")
            else:
                console.print("[yellow]ℹ[/yellow] 未启用 IM 通道（HTTP API 仍可使用）")

            # 注入 shutdown_event 到网关（供终极重启指令使用），并把晚启动的网关
            # 回填给已经运行的 FastAPI app state。
            if _message_gateway is not None:
                _message_gateway.set_shutdown_event(shutdown_event)
            if api_task is not None:
                try:
                    from openakita.api.server import update_runtime_refs

                    update_runtime_refs(
                        api_task,
                        gateway=_message_gateway,
                        startup_phase="running",
                        readiness={
                            "phase": "running",
                            "http_ready": True,
                            "im_ready": True,
                            "ready": True,
                            "started_im_channels": im_channels,
                            "gateway_bound": _message_gateway is not None,
                        },
                    )
                except Exception:
                    logger.debug("Failed to update API runtime readiness", exc_info=True)

            # 到这里才是真正的 serve 启动完成：HTTP API 可访问，IM 启动路径也已收敛
            # （即便没有启用 IM 或某些 adapter 失败，后台服务也已完成启动流程）。
            _heartbeat_phase = "running"
            _heartbeat_http_ready = True
            _heartbeat_im_ready = True
            _heartbeat_ready = True
            _write_heartbeat()

        console.print()
        if dev:
            console.print(
                "[bold]服务运行中 [cyan](dev 模式)[/cyan]...[/bold] 文件变化时自动重启，按 Ctrl+C 停止"
            )
        else:
            console.print("[bold]服务运行中...[/bold] 按 Ctrl+C 停止")

        # ── dev 模式：文件监控自动重启 ──
        _watch_task = None
        if dev:

            async def _file_watcher():
                try:
                    from watchfiles import awatch

                    src_dir = Path(__file__).resolve().parent  # src/openakita/
                    console.print(f"[dim]📂 监控目录: {src_dir}[/dim]")
                    async for changes in awatch(
                        src_dir,
                        watch_filter=lambda change, path: path.endswith(".py"),
                        debounce=1000,
                        step=500,
                    ):
                        changed_files = [Path(p).name for _, p in changes]
                        console.print(
                            f"\n[cyan]🔄 检测到文件变化: {', '.join(changed_files)}，正在重启...[/cyan]"
                        )
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


@app.command(name="plugin-validate")
def plugin_validate(
    path: str = typer.Argument(".", help="插件目录路径（含 plugin.json）"),
    fix: bool = typer.Option(False, "--fix", help="自动修正可修复的问题"),
):
    """校验插件 manifest 是否有效（Pydantic 校验 + 权限检查 + 入口文件检查 + config schema 校验）"""
    from .plugins.manifest import ALL_PERMISSIONS, ManifestError, parse_manifest

    plugin_dir = Path(path).resolve()
    warnings: list[str] = []
    errors: list[str] = []

    # --- 1. 目录检查 ---
    if not plugin_dir.is_dir():
        console.print(f"[bold red]✗[/bold red] 路径不存在或不是目录: {plugin_dir}")
        raise typer.Exit(1)

    manifest_file = plugin_dir / "plugin.json"
    if not manifest_file.is_file():
        console.print(f"[bold red]✗[/bold red] 未找到 plugin.json: {manifest_file}")
        raise typer.Exit(1)

    # --- 2. Manifest 解析（Pydantic 校验）---
    try:
        manifest = parse_manifest(plugin_dir)
    except ManifestError as e:
        console.print("[bold red]✗[/bold red] Manifest 校验失败:")
        for line in str(e).split("\n"):
            console.print(f"  {line}")
        raise typer.Exit(1)

    # --- 3. 入口文件检查 ---
    entry_path = plugin_dir / manifest.entry
    if not entry_path.is_file():
        errors.append(f"入口文件不存在: {manifest.entry}")

    # --- 4. 权限检查 ---
    unknown_perms = [p for p in manifest.permissions if p not in ALL_PERMISSIONS]
    if unknown_perms:
        warnings.append(f"未知权限: {', '.join(unknown_perms)}")

    # --- 5. config_schema.json 校验 ---
    schema_file = plugin_dir / "config_schema.json"
    schema_data: dict | None = None
    if schema_file.is_file():
        try:
            raw_schema = json.loads(schema_file.read_text(encoding="utf-8"))
            if not isinstance(raw_schema, dict):
                errors.append("config_schema.json 不是有效的 JSON 对象")
            else:
                schema_data = raw_schema
                if "type" not in schema_data:
                    warnings.append("config_schema.json 缺少 'type' 字段（建议设为 'object'）")
        except json.JSONDecodeError as e:
            errors.append(f"config_schema.json JSON 解析失败: {e}")

        config_file = plugin_dir / "config.json"
        if config_file.is_file() and schema_data is not None:
            try:
                from jsonschema import ValidationError as JsonSchemaError
                from jsonschema import validate

                config_data = json.loads(config_file.read_text(encoding="utf-8"))
                validate(instance=config_data, schema=schema_data)
            except JsonSchemaError as ve:
                errors.append(f"config.json 不符合 schema: {ve.message}")
            except ImportError:
                warnings.append("jsonschema 未安装，跳过 config.json 校验")
            except Exception as ve:
                warnings.append(f"config.json 校验异常: {ve}")

    # --- 6. README 检查 ---
    readme_candidates = ["README.md", "readme.md", "README.txt", "README"]
    has_readme = any((plugin_dir / f).is_file() for f in readme_candidates)
    if not has_readme:
        warnings.append("缺少 README.md（建议添加使用说明）")

    # --- 7. icon 检查 ---
    if manifest.icon:
        icon_path = plugin_dir / manifest.icon
        if not icon_path.is_file():
            warnings.append(f"icon 文件不存在: {manifest.icon}")
    else:
        warnings.append("未设置 icon（建议添加插件图标）")

    # --- 8. pip 依赖可用性检查 ---
    pip_deps = manifest.requires.get("pip", [])
    if isinstance(pip_deps, str):
        pip_deps = [pip_deps] if pip_deps.strip() else []
    if pip_deps:
        import importlib as _imp

        for dep in pip_deps:
            pkg_name = dep.split(">=")[0].split("==")[0].split("<")[0].split(">")[0].strip()
            pkg_import = pkg_name.replace("-", "_")
            try:
                _imp.import_module(pkg_import)
            except ImportError:
                warnings.append(f"pip 依赖 '{pkg_name}' 当前不可用（安装后会自动解决）")

    # --- 输出结果 ---
    table = Table(title="插件校验报告", show_header=True, header_style="bold cyan")
    table.add_column("属性", style="bold")
    table.add_column("值")
    table.add_row("ID", manifest.id)
    table.add_row("名称", manifest.name)
    table.add_row("版本", manifest.version)
    table.add_row("类型", manifest.plugin_type)
    table.add_row("入口", manifest.entry)
    table.add_row("权限级别", manifest.max_permission_level)
    if manifest.permissions:
        table.add_row("权限", ", ".join(manifest.permissions))
    if manifest.depends:
        table.add_row("依赖", ", ".join(manifest.depends))
    if manifest.description:
        table.add_row("描述", manifest.description)
    if manifest.author:
        table.add_row("作者", manifest.author)
    console.print(table)

    if warnings:
        console.print()
        for w in warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")

    if errors:
        console.print()
        for e in errors:
            console.print(f"  [bold red]✗[/bold red] {e}")
        console.print(
            f"\n[bold red]校验失败[/bold red]（{len(errors)} 个错误，{len(warnings)} 个警告）"
        )
        raise typer.Exit(1)

    if warnings:
        console.print(f"\n[bold green]✓ 校验通过[/bold green]（{len(warnings)} 个警告）")
    else:
        console.print("\n[bold green]✓ 校验通过，一切正常！[/bold green]")


@app.command(name="plugin-scaffold")
def plugin_scaffold(
    name: str = typer.Argument(..., help="Plugin ID (e.g. my-tool)"),
    out: str = typer.Option(".", "--out", "-o", help="Parent directory for the new plugin"),
    ui: bool = typer.Option(False, "--ui", help="Include frontend UI scaffolding (Plugin 2.0)"),
):
    """Generate a new plugin project skeleton."""
    import json as _json

    plugin_dir = Path(out).resolve() / name
    if plugin_dir.exists():
        console.print(f"[bold red]✗[/bold red] Directory already exists: {plugin_dir}")
        raise typer.Exit(1)

    plugin_dir.mkdir(parents=True)

    manifest: dict = {
        "id": name,
        "name": name.replace("-", " ").title(),
        "version": "0.1.0",
        "type": "python",
        "entry": "plugin.py",
        "description": f"OpenAkita plugin: {name}",
        "permissions": ["tools.register", "routes.register"],
        "provides": {"tools": []},
    }

    if ui:
        manifest["ui"] = {
            "entry": "ui/dist/index.html",
            "icon": "",
            "title": manifest["name"],
            "sidebar_group": "apps",
            "permissions": ["theme", "notification", "download"],
        }
        manifest["requires"] = {"plugin_ui_api": "~1"}

    (plugin_dir / "plugin.json").write_text(
        _json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    plugin_py = '''"""Plugin entry point."""
from openakita.plugins.api import PluginAPI, PluginBase


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        api.log("Plugin loaded")

    def on_unload(self) -> None:
        pass
'''
    (plugin_dir / "plugin.py").write_text(plugin_py, encoding="utf-8")

    if ui:
        ui_dist = plugin_dir / "ui" / "dist"
        ui_dist.mkdir(parents=True)
        index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>{manifest['name']}</title>
  <script type="module">
    // Replace with: import {{ PluginBridge }} from "@openakita/plugin-ui-sdk";
    const bridge = {{ init: () => window.parent.postMessage({{ __akita_bridge: true, version: 1, type: "bridge:ready" }}, "*") }};
    bridge.init();
  </script>
</head>
<body>
  <h1>{manifest['name']}</h1>
  <p>Plugin UI scaffold — replace this with your frontend app.</p>
</body>
</html>
"""
        (ui_dist / "index.html").write_text(index_html, encoding="utf-8")

        ui_src = plugin_dir / "ui-src"
        ui_src.mkdir()
        (ui_src / ".gitkeep").write_text("", encoding="utf-8")

    console.print(f"[bold green]✓[/bold green] Plugin scaffolded at: {plugin_dir}")
    if ui:
        console.print("  Includes frontend UI template (ui/dist/index.html)")
    console.print(f"  Next: copy to data/plugins/{name}/ and restart OpenAkita")


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

