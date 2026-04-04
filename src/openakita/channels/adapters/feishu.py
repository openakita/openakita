"""
飞书适配器

基于 lark-oapi 库实现:
- 事件订阅（支持长连接 WebSocket 和 Webhook 两种方式）
- 卡片消息
- 文本/图片/文件收发

参考文档:
- 机器人概述: https://open.feishu.cn/document/client-docs/bot-v3/bot-overview
- Python SDK: https://github.com/larksuite/oapi-sdk-python
- 事件订阅: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events
"""

import asyncio
import collections
import contextlib
import importlib.util
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openakita.python_compat import patch_simplejson_jsondecodeerror

from ..base import ChannelAdapter
from ..types import (
    MediaFile,
    MediaStatus,
    MessageContent,
    OutgoingMessage,
    UnifiedMessage,
)

logger = logging.getLogger(__name__)


def _drain_loop_tasks(loop: asyncio.AbstractEventLoop, timeout: float = 3.0) -> None:
    """Cancel all pending tasks on *loop* and run them to completion.

    Lark SDK spawns internal asyncio tasks (ExpiringCache cron, ping loop,
    receive loop) that are never explicitly cancelled.  If we just close the
    loop, Python emits "Task was destroyed but it is pending!" for each one,
    and — more importantly — the thread may hang waiting for those tasks,
    blocking process shutdown.

    A *timeout* guard prevents indefinite blocking in case any task swallows
    ``CancelledError`` and keeps running (as some third-party SDKs do).
    """
    try:
        pending = asyncio.all_tasks(loop)
    except RuntimeError:
        return
    if not pending:
        return
    for task in pending:
        task.cancel()
    try:
        loop.run_until_complete(
            asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        )
    except Exception:
        pass


# 延迟导入
lark_oapi = None


def _import_lark():
    """延迟导入 lark-oapi 库"""
    global lark_oapi
    if lark_oapi is None:
        try:
            patch_simplejson_jsondecodeerror(logger=logger)
            import lark_oapi as lark

            lark_oapi = lark
        except ImportError as exc:
            logger.error("lark_oapi import failed: %s", exc, exc_info=True)
            if "JSONDecodeError" in str(exc) and "simplejson" in str(exc):
                raise ImportError(
                    "飞书 SDK 依赖冲突：simplejson 缺少 JSONDecodeError。"
                    "请前往「设置中心 → Python 环境」执行一键修复后重启。"
                ) from exc
            from openakita.tools._import_helper import import_or_hint
            raise ImportError(import_or_hint("lark_oapi")) from exc


@dataclass
class FeishuConfig:
    """飞书配置"""

    app_id: str
    app_secret: str
    verification_token: str | None = None
    encrypt_key: str | None = None
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.app_id or not self.app_id.strip():
            raise ValueError("FeishuConfig: app_id is required")
        if not self.app_secret or not self.app_secret.strip():
            raise ValueError("FeishuConfig: app_secret is required")
        self.log_level = self.log_level.upper()
        if self.log_level not in ("DEBUG", "INFO", "WARN", "ERROR"):
            raise ValueError(f"FeishuConfig: invalid log_level '{self.log_level}'")


class FeishuAdapter(ChannelAdapter):
    """
    飞书适配器

    支持:
    - 事件订阅（长连接 WebSocket 或 Webhook）
    - 文本/富文本消息
    - 图片/文件
    - 卡片消息

    使用说明:
    1. 长连接模式（推荐）: start() 会自动启动 WebSocket 连接
    2. Webhook 模式: 使用 handle_event() 处理 HTTP 回调
    """

    channel_name = "feishu"

    capabilities = {
        "streaming": True,
        "send_image": True,
        "send_file": True,
        "send_voice": True,
        "delete_message": False,
        "edit_message": False,
        "get_chat_info": True,
        "get_user_info": True,
        "get_chat_members": True,
        "get_recent_messages": True,
        "markdown": True,
        "add_reaction": True,
    }

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        verification_token: str | None = None,
        encrypt_key: str | None = None,
        media_dir: Path | None = None,
        log_level: str = "INFO",
        *,
        channel_name: str | None = None,
        bot_id: str | None = None,
        agent_profile_id: str = "default",
        streaming_enabled: bool | None = None,
        group_streaming: bool | None = None,
        streaming_throttle_ms: int | None = None,
        group_response_mode: str | None = None,
        footer_elapsed: bool | None = None,
        footer_status: bool | None = None,
    ):
        """
        Args:
            app_id: 飞书应用 App ID（在开发者后台获取）
            app_secret: 飞书应用 App Secret（在开发者后台获取）
            verification_token: 事件订阅验证 Token（Webhook 模式需要）
            encrypt_key: 事件加密密钥（如果配置了加密则需要）
            media_dir: 媒体文件存储目录
            log_level: 日志级别 (DEBUG, INFO, WARN, ERROR)
            channel_name: 通道名称（多Bot时用于区分实例）
            bot_id: Bot 实例唯一标识
            agent_profile_id: 绑定的 agent profile ID
        """
        super().__init__(channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id)

        self.config = FeishuConfig(
            app_id=app_id,
            app_secret=app_secret,
            verification_token=verification_token,
            encrypt_key=encrypt_key,
            log_level=log_level,
        )
        self.media_dir = Path(media_dir) if media_dir else Path("data/media/feishu")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self._client: Any | None = None
        self._ws_client: Any | None = None
        self._event_dispatcher: Any | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_watchdog_task: asyncio.Task | None = None
        self._ws_restart_count: int = 0
        self._bot_open_id: str | None = None
        self._capabilities: list[str] = []

        # 消息去重：WebSocket 重连可能导致重复投递
        self._seen_message_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._seen_message_ids_max = 500

        # 用户名缓存：open_id → display name（避免重复调 Contact API）
        self._user_name_cache: collections.OrderedDict[str, str] = collections.OrderedDict()
        self._user_name_cache_max = 200

        # 群名缓存：chat_id → group name（避免重复调 im.v1.chat.get）
        self._chat_name_cache: collections.OrderedDict[str, str] = collections.OrderedDict()
        self._chat_name_cache_max = 200

        # "思考中..."占位卡片：session_key → 卡片 message_id
        # session_key = chat_id 或 chat_id:thread_id（话题模式）
        self._thinking_cards: dict[str, str] = {}
        # 最近一条用户消息 ID：session_key → user_msg_id（供 send_typing 回复定位）
        self._last_user_msg: dict[str, str] = {}
        # 已消耗过 thinking card 的 session_key 集合，阻止 _keep_typing 重建卡片
        self._typing_suppressed: set[str] = set()

        # 流式输出状态（构造参数优先，None 时 fallback 到 env）
        self._streaming_enabled = streaming_enabled if streaming_enabled is not None else (
            os.environ.get("FEISHU_STREAMING_ENABLED", "false").lower() in ("true", "1", "yes")
        )
        self._group_streaming = group_streaming if group_streaming is not None else (
            os.environ.get("FEISHU_GROUP_STREAMING", "false").lower() in ("true", "1", "yes")
        )
        self._streaming_throttle_ms = streaming_throttle_ms if streaming_throttle_ms is not None else (
            int(os.environ.get("FEISHU_STREAMING_THROTTLE_MS", "800"))
        )
        # session_key → 已累积的流式文本
        self._streaming_buffers: dict[str, str] = {}
        # session_key → 上次 PATCH 时间戳(秒)
        self._streaming_last_patch: dict[str, float] = {}
        # session_key → 是否已 finalize
        self._streaming_finalized: set[str] = set()
        # session_key → 思考内容（流式期间暂存，finalize 后清理）
        self._streaming_thinking: dict[str, str] = {}
        # session_key → 思考耗时(ms)
        self._streaming_thinking_ms: dict[str, int] = {}
        # session_key → 工具调用/结果等 chain 文本行（流式期间追加，finalize 后清理）
        self._streaming_chain: dict[str, list[str]] = {}

        # Bot 已发送消息 ID 追踪：用于识别"回复机器人消息"作为隐式 mention
        self._bot_sent_msg_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._bot_sent_msg_ids_max = 500

        # Per-bot 群聊响应模式（构造参数 > 环境变量 > 全局配置）
        self._group_response_mode: str | None = group_response_mode or (
            os.environ.get("FEISHU_GROUP_RESPONSE_MODE") or None
        )

        # 卡片 footer 配置（显示耗时 / 状态）
        self._footer_elapsed = footer_elapsed if footer_elapsed is not None else (
            os.environ.get("FEISHU_FOOTER_ELAPSED", "true").lower() in ("true", "1", "yes")
        )
        self._footer_status = footer_status if footer_status is not None else (
            os.environ.get("FEISHU_FOOTER_STATUS", "true").lower() in ("true", "1", "yes")
        )
        self._typing_start_time: dict[str, float] = {}
        self._typing_status: dict[str, str] = {}

        # 关键事件缓冲（per-chat_id，上限 _MAX_EVENTS_PER_CHAT 条）
        self._important_events: dict[str, list[dict]] = {}
        self._events_lock = threading.Lock()
        self._MAX_EVENTS_PER_CHAT = 10

    async def start(self) -> None:
        """
        启动飞书客户端并自动建立 WebSocket 长连接

        会自动启动 WebSocket 长连接（非阻塞模式），以便接收消息。
        SDK 会自动管理 access_token，无需手动刷新。
        """
        _import_lark()

        # 创建客户端
        log_level = getattr(lark_oapi.LogLevel, self.config.log_level, lark_oapi.LogLevel.INFO)

        self._client = (
            lark_oapi.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(log_level)
            .build()
        )

        # 记录主事件循环，用于从 WebSocket 线程投递协程
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None
        logger.info("Feishu adapter: client initialized")

        # 尝试获取机器人 open_id（用于精确匹配 @提及）。
        # lark_oapi.api.bot 子模块在部分打包版本中可能缺失，
        # 导入失败不应阻断适配器启动——仅影响群聊 @提及检测。
        _bot_info_error: str | None = None
        try:
            import lark_oapi.api.bot.v3 as bot_v3

            for attempt in range(3):
                try:
                    req = bot_v3.GetBotInfoRequest.builder().build()
                    resp = await asyncio.get_running_loop().run_in_executor(
                        None, lambda _r=req: self._client.bot.v3.bot_info.get(_r)
                    )
                    if resp.success() and resp.data and resp.data.bot:
                        self._bot_open_id = getattr(resp.data.bot, "open_id", None)
                        logger.info(f"Feishu bot open_id: {self._bot_open_id}")
                        _bot_info_error = None
                        break
                    else:
                        _bot_info_error = getattr(resp, "msg", "unknown")
                        logger.warning(
                            f"Feishu: GetBotInfo attempt {attempt + 1}/3 failed: {_bot_info_error}"
                        )
                except Exception as e:
                    _bot_info_error = str(e)
                    logger.warning(f"Feishu: GetBotInfo attempt {attempt + 1}/3 error: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)
        except ImportError:
            logger.warning(
                "lark_oapi.api.bot module not available, trying raw HTTP fallback..."
            )
            try:
                raw_req = (
                    lark_oapi.BaseRequest.builder()
                    .http_method(lark_oapi.HttpMethod.GET)
                    .uri("/open-apis/bot/v3/info")
                    .token_types({lark_oapi.AccessTokenType.TENANT})
                    .build()
                )
                raw_resp = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._client.request(raw_req)
                )
                if raw_resp.success() and raw_resp.raw:
                    _body = json.loads(raw_resp.raw.content)
                    _bot = _body.get("bot") or _body.get("data", {}).get("bot") or {}
                    self._bot_open_id = _bot.get("open_id")
                    if self._bot_open_id:
                        logger.info(
                            f"Feishu bot open_id (raw HTTP): {self._bot_open_id}"
                        )
                        _bot_info_error = None
                    else:
                        _bot_info_error = "raw HTTP 返回中未包含 bot open_id"
                else:
                    _bot_info_error = getattr(raw_resp, "msg", "raw HTTP fallback failed")
            except Exception as e:
                _bot_info_error = str(e)
                logger.warning(f"Feishu: raw HTTP bot info fallback failed: {e}")

        if not self._bot_open_id and _bot_info_error:
            _err_lower = (_bot_info_error or "").lower()
            if any(kw in _err_lower for kw in ("invalid", "app_id", "secret", "token", "auth", "10003")):
                raise ConnectionError(
                    f"飞书 App ID 或 App Secret 无效，请在飞书开放平台检查应用凭据。"
                    f"（错误详情: {_bot_info_error}）"
                )
            if "connect" in _err_lower or "timeout" in _err_lower or "resolve" in _err_lower:
                raise ConnectionError(
                    f"无法连接飞书 API (open.feishu.cn)，请检查网络连接。"
                    f"（错误详情: {_bot_info_error}）"
                )
            logger.warning(
                "Feishu: bot open_id not available. "
                "@mention detection will be disabled (bot will NOT respond to any @mention in groups)."
            )

        # 在启动 WS 之前标记为运行中：
        # - 必须在 client 创建 + lark 导入成功之后（确保绿点不虚标）
        # - 必须在 start_websocket 之前（WS 线程依赖 _running 判断是否记录错误）
        self._running = True

        # 自动启动 WebSocket 长连接（非阻塞模式）
        try:
            self.start_websocket(blocking=False)
            logger.info("Feishu adapter: WebSocket started in background")
        except Exception as e:
            logger.warning(f"Feishu adapter: WebSocket startup failed: {e}")
            logger.warning("Feishu adapter: falling back to webhook-only mode")

        if self._group_response_mode and self._group_response_mode != "mention_only":
            logger.info(
                f"Feishu[{self.channel_name}]: group_response_mode={self._group_response_mode}, "
                f"请确保飞书后台已开启「接收群聊中所有消息」"
            )

        # 探测可用权限/能力
        await self._probe_capabilities()

        # 启动 WebSocket 看门狗（后台任务，周期性检查 WS 线程存活状态）
        if self._ws_thread is not None:
            self._ws_watchdog_task = asyncio.create_task(self._ws_watchdog_loop())

    # ==================== WebSocket 看门狗 ====================

    _WS_WATCHDOG_INTERVAL = 15          # 检查间隔（秒）
    _WS_WATCHDOG_INITIAL_DELAY = 30     # 首次检查前等待（秒）
    _WS_RECONNECT_MIN_INTERVAL = 10     # 最小重连间隔（秒）
    _WS_RECONNECT_MAX_DELAY = 120       # 最大退避延迟（秒）

    _WS_STABLE_THRESHOLD = 300  # 连接稳定 5 分钟后重置重连计数
    _WS_FATAL_RESTART_THRESHOLD = 5  # 连续重启超过此次数且未稳定，视为致命失败

    async def _ws_watchdog_loop(self) -> None:
        """周期性检查 WebSocket 线程是否存活，退出后自动重启。"""
        await asyncio.sleep(self._WS_WATCHDOG_INITIAL_DELAY)
        last_restart_time = 0.0
        stable_since = asyncio.get_running_loop().time()

        while self._running:
            await asyncio.sleep(self._WS_WATCHDOG_INTERVAL)
            if not self._running:
                break

            ws_thread = self._ws_thread
            if ws_thread is not None and ws_thread.is_alive():
                now = asyncio.get_running_loop().time()
                if self._ws_restart_count > 0 and (now - stable_since) >= self._WS_STABLE_THRESHOLD:
                    logger.info("Feishu WS watchdog: connection stable, resetting restart count")
                    self._ws_restart_count = 0
                continue

            # WS 线程已退出，计算退避延迟后重启
            now = asyncio.get_running_loop().time()
            since_last = now - last_restart_time
            if since_last < self._WS_RECONNECT_MIN_INTERVAL:
                continue

            self._ws_restart_count += 1

            if self._ws_restart_count >= self._WS_FATAL_RESTART_THRESHOLD:
                reason = (
                    f"WebSocket 连续 {self._ws_restart_count} 次重启失败，"
                    "请检查飞书 App ID / App Secret 是否有效"
                )
                logger.error(f"Feishu WS watchdog: {reason}")
                self._running = False
                self._report_failure(reason)
                return

            backoff = min(
                self._WS_RECONNECT_MIN_INTERVAL * (2 ** min(self._ws_restart_count - 1, 6)),
                self._WS_RECONNECT_MAX_DELAY,
            )
            logger.warning(
                f"Feishu WS watchdog: thread exited (restart #{self._ws_restart_count}), "
                f"reconnecting in {backoff:.0f}s"
            )
            await asyncio.sleep(backoff)
            if not self._running:
                break

            try:
                self.start_websocket(blocking=False)
                last_restart_time = asyncio.get_running_loop().time()
                stable_since = last_restart_time
                logger.info(
                    f"Feishu WS watchdog: reconnected (restart #{self._ws_restart_count})"
                )
            except Exception as e:
                logger.error(f"Feishu WS watchdog: reconnect failed: {e}")

    async def _probe_capabilities(self) -> None:
        """探测飞书适配器已实现方法对应的权限是否可用

        通过调用 API 并检查响应码判断权限：
        - 权限不足：响应消息通常包含 "permission"/"access denied"/"scope" 等
        - 参数无效/资源不存在：说明权限本身是通过的
        """
        self._capabilities = ["发消息", "发文件", "回复消息"]
        if not self._client:
            return

        try:
            import lark_oapi.api.contact.v3 as contact_v3
            import lark_oapi.api.im.v1 as im_v1
        except ImportError:
            logger.warning("lark_oapi submodules not available for capability probing")
            return

        try:
            req = im_v1.GetChatRequest.builder().chat_id("probe_test").build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat.get(req))
            if not self._is_token_error(resp):
                self._capabilities.append("获取群信息")
        except Exception:
            pass

        try:
            req = contact_v3.GetUserRequest.builder().user_id("probe_test").user_id_type("open_id").build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.contact.v3.user.get(req))
            if not self._is_token_error(resp):
                self._capabilities.append("获取用户信息")
        except Exception:
            pass

        try:
            req = im_v1.GetChatMembersRequest.builder().chat_id("probe_test").member_id_type("open_id").build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat_members.get(req))
            if not self._is_token_error(resp):
                self._capabilities.append("获取群成员")
        except Exception:
            pass

        try:
            req = im_v1.ListMessageRequest.builder().container_id_type("chat").container_id("probe_test").page_size(1).build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.list(req))
            if not self._is_token_error(resp):
                self._capabilities.append("获取消息历史")
        except Exception:
            pass

        # 探测图片上传权限 (im:resource:upload)
        # 发送一个无效 PNG header，不会在飞书侧创建任何资源：
        # - 权限 OK → 返回「图片格式不支持」（非权限错误）
        # - 缺权限 → 返回 "Access denied...scope"
        try:
            import io
            req = im_v1.CreateImageRequest.builder().request_body(
                im_v1.CreateImageRequestBody.builder()
                .image_type("message")
                .image(io.BytesIO(b"\x89PNG\r\n"))
                .build()
            ).build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.image.create(req))
            if not self._is_token_error(resp):
                self._capabilities.append("上传图片")
            else:
                logger.warning(
                    "Feishu: 缺少 im:resource:upload 权限，图片/表情包发送将不可用。"
                    "请在飞书开放平台为机器人开通此权限。"
                )
        except Exception:
            pass

        logger.info(f"Feishu capabilities: {self._capabilities}")

    def start_websocket(self, blocking: bool = True) -> None:
        """
        启动 WebSocket 长连接接收事件（推荐方式）

        注意事项:
        - 仅支持企业自建应用
        - 每个应用最多建立 50 个连接
        - 消息推送为集群模式，同一应用多个客户端只有随机一个会收到消息

        Args:
            blocking: 是否阻塞主线程，默认为 True
        """
        _import_lark()

        if not self._event_dispatcher:
            self._setup_event_dispatcher()

        logger.info("Starting Feishu WebSocket connection...")

        # lark_oapi.ws.client 在模块级保存了一个全局 loop 变量，Client 类的
        # start / _connect / _receive_message_loop 等方法全部直接引用该变量。
        # 多个 FeishuAdapter 实例在不同线程启动时会互相覆盖这个 loop，导致
        # 运行时 create_task 投递到错误的事件循环，消息静默丢失。
        #
        # 解决方案：用 importlib.util 为每个线程创建 lark_oapi.ws.client 模块
        # 的**独立副本**（不修改 sys.modules）。每个副本的 Client 类方法通过
        # __globals__ 引用各自副本的 loop 变量，从根本上消除跨实例污染。

        def _run_ws_in_thread() -> None:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            self._ws_loop = new_loop

            try:
                spec = importlib.util.find_spec("lark_oapi.ws.client")
                ws_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(ws_mod)

                ws_client = ws_mod.Client(
                    self.config.app_id,
                    self.config.app_secret,
                    event_handler=self._event_dispatcher,
                    log_level=getattr(
                        lark_oapi.LogLevel, self.config.log_level, lark_oapi.LogLevel.INFO
                    ),
                )
                self._ws_client = ws_client

                ws_client.start()
            except Exception as e:
                if self._running:
                    logger.error(f"Feishu WebSocket error: {e}", exc_info=True)
            finally:
                _drain_loop_tasks(new_loop)
                self._ws_loop = None
                with contextlib.suppress(Exception):
                    new_loop.close()

        if blocking:
            _run_ws_in_thread()
        else:
            self._ws_thread = threading.Thread(
                target=_run_ws_in_thread,
                daemon=True,
                name=f"FeishuWS-{self.channel_name}",
            )
            self._ws_thread.start()
            logger.info(f"Feishu WebSocket client started in background thread ({self.channel_name})")

    def _setup_event_dispatcher(self) -> None:
        """设置事件分发器"""
        _import_lark()

        # 创建事件分发器
        # verification_token 和 encrypt_key 在长连接模式下必须为空字符串
        builder = (
            lark_oapi.EventDispatcherHandler.builder(
                verification_token="",  # 长连接模式不需要验证
                encrypt_key="",  # 长连接模式不需要加密
            )
            .register_p2_im_message_receive_v1(self._on_message_receive)
        )
        # 注册消息已读事件，避免 SDK 报 "processor not found" ERROR 日志
        try:
            builder = builder.register_p2_im_message_read_v1(self._on_message_read)
        except AttributeError:
            pass
        # 注册机器人进入会话事件
        try:
            builder = builder.register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(
                self._on_bot_chat_entered
            )
        except AttributeError:
            pass
        # 注册群聊更新事件（群公告变更等）
        try:
            builder = builder.register_p2_im_chat_updated_v1(self._on_chat_updated)
        except AttributeError:
            pass
        # 注册机器人入群/被踢事件
        try:
            builder = builder.register_p2_im_chat_member_bot_added_v1(self._on_bot_chat_added)
        except AttributeError:
            pass
        try:
            builder = builder.register_p2_im_chat_member_bot_deleted_v1(self._on_bot_chat_deleted)
        except AttributeError:
            pass
        # 注册表情回复事件，避免 SDK 报 "processor not found" ERROR 日志
        try:
            builder = builder.register_p2_im_message_reaction_created_v1(self._on_reaction_created)
        except AttributeError:
            pass
        try:
            builder = builder.register_p2_im_message_reaction_deleted_v1(self._on_reaction_deleted)
        except AttributeError:
            pass
        # 注册卡片交互回调（card.action.trigger），需要 lark-oapi >= 1.3.0
        try:
            builder = builder.register_p2_card_action_trigger(self._on_card_action)
        except AttributeError:
            logger.warning(
                "Feishu: register_p2_card_action_trigger not available, "
                "card button interactions will not work. "
                "Upgrade lark-oapi to >= 1.3.0."
            )
        self._event_dispatcher = builder.build()

    def _on_message_receive(self, data: Any) -> None:
        """
        处理接收到的消息事件 (im.message.receive_v1)

        注意：此方法在 WebSocket 线程中同步调用
        """
        try:
            event = data.event
            message = event.message
            sender = event.sender

            logger.info(
                f"Feishu[{self.channel_name}]: received message from "
                f"{sender.sender_id.open_id}"
            )

            # 提取 mentions 列表（用于 is_mentioned 检测）
            mentions_raw = []
            if hasattr(message, "mentions") and message.mentions:
                for m in message.mentions:
                    mid = getattr(m, "id", None)
                    mentions_raw.append({
                        "key": getattr(m, "key", ""),
                        "name": getattr(m, "name", ""),
                        "id": {
                            "open_id": getattr(mid, "open_id", "") if mid else "",
                            "user_id": getattr(mid, "user_id", "") if mid else "",
                        },
                    })

            # 构建消息字典
            msg_dict = {
                "message_id": message.message_id,
                "chat_id": message.chat_id,
                "chat_type": message.chat_type,
                "message_type": message.message_type,
                "content": message.content,
                "root_id": getattr(message, "root_id", None),
                "parent_id": getattr(message, "parent_id", None),
                "mentions": mentions_raw,
                "create_time": getattr(message, "create_time", None),
            }

            sender_dict = {
                "sender_id": {
                    "user_id": getattr(sender.sender_id, "user_id", ""),
                    "open_id": getattr(sender.sender_id, "open_id", ""),
                },
            }

            # 从 WebSocket 线程把协程安全投递到主事件循环。
            # 必须使用 run_coroutine_threadsafe：当前线程已有运行中的事件循环（SDK 的 ws loop），
            # 不能使用 asyncio.run()，否则会触发 "asyncio.run() cannot be called from a running event loop" 导致消息丢失。
            if self._main_loop is not None:
                fut = asyncio.run_coroutine_threadsafe(
                    self._handle_message_async(msg_dict, sender_dict),
                    self._main_loop,
                )
                # 添加回调以捕获跨线程投递中的异常，避免静默丢失消息
                def _on_dispatch_done(f: "asyncio.futures.Future") -> None:
                    try:
                        f.result()
                    except Exception as e:
                        logger.error(
                            f"Failed to dispatch Feishu message to main loop: {e}",
                            exc_info=True,
                        )
                fut.add_done_callback(_on_dispatch_done)
            else:
                logger.error(
                    "Main event loop not set (Feishu adapter not started from async context?), "
                    "dropping message to avoid asyncio.run() in WebSocket thread"
                )

        except Exception as e:
            logger.error(f"Error handling message event: {e}", exc_info=True)

    def _on_message_read(self, data: Any) -> None:
        """消息已读事件 (im.message.message_read_v1)，仅需静默消费以避免 SDK 报错"""
        pass

    def _on_reaction_created(self, data: Any) -> None:
        """消息表情回复事件 (im.message.reaction.created_v1)，静默消费以避免 SDK 报错"""
        pass

    def _on_reaction_deleted(self, data: Any) -> None:
        """消息表情回复移除事件 (im.message.reaction.deleted_v1)，静默消费以避免 SDK 报错"""
        pass

    def _on_bot_chat_entered(self, data: Any) -> None:
        """机器人进入会话事件，仅需静默消费以避免 SDK 报错"""
        pass

    def _on_chat_updated(self, data: Any) -> None:
        """群聊信息更新事件 (im.chat.updated_v1)"""
        try:
            event = data.event
            chat_id = getattr(event, "chat_id", "")
            if not chat_id:
                return
            after = getattr(event, "after", None)
            changes = {}
            if after:
                name = getattr(after, "name", None)
                if name:
                    changes["name"] = name
                description = getattr(after, "description", None)
                if description is not None:
                    changes["description"] = description
            if changes:
                self._buffer_event(chat_id, {
                    "type": "chat_updated",
                    "chat_id": chat_id,
                    "changes": changes,
                })
        except Exception as e:
            logger.debug(f"Feishu: failed to handle chat_updated event: {e}")

    def _on_bot_chat_added(self, data: Any) -> None:
        """机器人被添加到群聊事件 (im.chat.member.bot.added_v1)"""
        try:
            event = data.event
            chat_id = getattr(event, "chat_id", "")
            if chat_id:
                self._buffer_event(chat_id, {
                    "type": "bot_added",
                    "chat_id": chat_id,
                })
                logger.info(f"Feishu: bot added to chat {chat_id}")
        except Exception as e:
            logger.debug(f"Feishu: failed to handle bot_added event: {e}")

    def _on_bot_chat_deleted(self, data: Any) -> None:
        """机器人被移出群聊事件 (im.chat.member.bot.deleted_v1)"""
        try:
            event = data.event
            chat_id = getattr(event, "chat_id", "")
            if chat_id:
                self._buffer_event(chat_id, {
                    "type": "bot_removed",
                    "chat_id": chat_id,
                })
                logger.info(f"Feishu: bot removed from chat {chat_id}")
        except Exception as e:
            logger.debug(f"Feishu: failed to handle bot_deleted event: {e}")

    # ==================== 卡片交互回调 ====================

    def _on_card_action(self, data: Any) -> Any:
        """卡片回传交互回调 (card.action.trigger) — WebSocket 长连接模式。

        在 WS 线程中同步调用，必须在 3 秒内返回 P2CardActionTriggerResponse。
        """
        try:
            from lark_oapi.event.callback.model.p2_card_action_trigger import (
                P2CardActionTriggerResponse,
            )
        except ImportError:
            logger.error("P2CardActionTriggerResponse not available, card action ignored")
            return None

        try:
            event = data.event
            action = event.action
            value = action.value
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    value = {"action": value}

            resp_dict = self._dispatch_card_action(value or {})
            return P2CardActionTriggerResponse(resp_dict)

        except Exception as e:
            logger.error(f"Feishu: card action callback error: {e}", exc_info=True)
            return P2CardActionTriggerResponse({
                "toast": {"type": "error", "content": "处理失败，请稍后重试"},
            })

    def _handle_card_action_webhook(self, body: dict) -> dict:
        """卡片回传交互回调 (card.action.trigger) — Webhook 模式。

        直接返回响应 dict 作为 HTTP 响应体。
        """
        try:
            event = body.get("event", {})
            action = event.get("action", {})
            value = action.get("value", {})
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    value = {"action": value}

            return self._dispatch_card_action(value or {})

        except Exception as e:
            logger.error(f"Feishu: card action webhook error: {e}", exc_info=True)
            return {
                "toast": {"type": "error", "content": "处理失败，请稍后重试"},
            }

    def _dispatch_card_action(self, value: dict) -> dict:
        """根据按钮 value 中的 action 字段分发到对应处理逻辑。

        返回飞书卡片回调响应 dict（含可选的 toast / card 字段）。
        """
        action_type = value.get("action", "")

        if action_type == "expand_folder":
            return self._handle_expand_folder(value.get("path", ""))

        if action_type == "collapse_folder":
            return self._handle_collapse_folder(value)

        logger.debug(f"Feishu: unknown card action: {action_type}")
        return {}

    def _handle_expand_folder(self, path: str) -> dict:
        """读取目录内容并返回包含文件树和展开按钮的更新卡片。"""
        if not path:
            return {"toast": {"type": "error", "content": "路径为空"}}

        norm = os.path.normpath(path)
        if ".." in norm.split(os.sep):
            return {"toast": {"type": "error", "content": "不允许的路径"}}

        if not os.path.isdir(norm):
            return {"toast": {"type": "warning", "content": f"目录不存在: {os.path.basename(norm)}"}}

        try:
            entries = os.listdir(norm)
        except PermissionError:
            return {"toast": {"type": "error", "content": "没有权限访问此目录"}}
        except OSError as e:
            return {"toast": {"type": "error", "content": f"读取失败: {e}"}}

        card = self._build_folder_card(norm, entries)
        return {"card": {"type": "raw", "data": card}}

    def _handle_collapse_folder(self, value: dict) -> dict:
        """折叠目录：返回仅含目录名和展开按钮的精简卡片。"""
        path = value.get("path", "")
        parent = value.get("parent", "")
        if not path:
            return {}

        folder_name = os.path.basename(path) or path
        elements = [
            {"tag": "markdown", "content": f"📁 **{folder_name}**"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": f"📂 展开 {folder_name}"},
                        "type": "default",
                        "value": {"action": "expand_folder", "path": path},
                    },
                ],
            },
        ]

        title = os.path.basename(parent) if parent else folder_name
        return {
            "card": {
                "type": "raw",
                "data": {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {"tag": "plain_text", "content": f"📁 {title}"},
                        "template": "blue",
                    },
                    "elements": elements,
                },
            },
        }

    @staticmethod
    def _build_folder_card(dir_path: str, entries: list[str]) -> dict:
        """构建含文件列表和子目录展开按钮的飞书交互卡片（JSON 1.0）。"""
        folder_name = os.path.basename(dir_path) or dir_path

        dirs: list[str] = []
        files: list[str] = []
        for entry in sorted(entries):
            if entry.startswith("."):
                continue
            full = os.path.join(dir_path, entry)
            if os.path.isdir(full):
                dirs.append(entry)
            else:
                files.append(entry)

        _ICON = {
            "dir": "📁", "md": "📝", "txt": "📄", "pdf": "📕",
            "png": "🖼️", "jpg": "🖼️", "jpeg": "🖼️", "gif": "🖼️",
            "mp3": "🎵", "wav": "🎵", "mp4": "🎬",
            "py": "🐍", "js": "📜", "json": "📋", "csv": "📊",
        }

        md_lines: list[str] = []
        for d in dirs:
            md_lines.append(f"📁 **{d}/**")
        for f in files:
            ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
            icon = _ICON.get(ext, "📄")
            md_lines.append(f"{icon} {f}")

        elements: list[dict] = []

        if md_lines:
            MAX_DISPLAY = 50
            if len(md_lines) > MAX_DISPLAY:
                shown = md_lines[:MAX_DISPLAY]
                shown.append(f"\n*...共 {len(md_lines)} 项，已显示前 {MAX_DISPLAY} 项*")
                md_lines = shown
            elements.append({"tag": "markdown", "content": "\n".join(md_lines)})
        else:
            elements.append({"tag": "markdown", "content": "*(空目录)*"})

        if dirs:
            MAX_BUTTONS_PER_ROW = 5
            for i in range(0, min(len(dirs), 20), MAX_BUTTONS_PER_ROW):
                chunk = dirs[i : i + MAX_BUTTONS_PER_ROW]
                actions = []
                for d in chunk:
                    actions.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": f"📂 展开 {d}"},
                        "type": "default",
                        "value": {
                            "action": "expand_folder",
                            "path": os.path.join(dir_path, d),
                        },
                    })
                elements.append({"tag": "action", "actions": actions})

        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📁 折叠"},
                    "type": "default",
                    "value": {
                        "action": "collapse_folder",
                        "path": dir_path,
                        "parent": str(Path(dir_path).parent),
                    },
                },
            ],
        })

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📁 {folder_name}"},
                "template": "blue",
            },
            "elements": elements,
        }

    def _buffer_event(self, chat_id: str, event: dict) -> None:
        """线程安全地缓冲事件"""
        with self._events_lock:
            events = self._important_events.setdefault(chat_id, [])
            if len(events) >= self._MAX_EVENTS_PER_CHAT:
                events.pop(0)
            events.append(event)

    def get_pending_events(self, chat_id: str) -> list[dict]:
        """取出并清空指定群的待处理事件（线程安全）"""
        with self._events_lock:
            return self._important_events.pop(chat_id, [])

    # ==================== Token / 权限辅助 ====================

    @staticmethod
    def _is_token_error(resp: Any) -> bool:
        """判断 API 响应是否为 token/权限类错误"""
        if resp.success():
            return False
        msg = (getattr(resp, "msg", "") or "").lower()
        return any(kw in msg for kw in (
            "permission", "tenant_access_token", "app_access_token",
            "forbidden", "access denied", "scope",
        ))

    def _invalidate_token_cache(self) -> None:
        """将缓存的 tenant_access_token 标记过期，迫使下次请求重新获取。

        lark-oapi SDK 的 ICache 没有 delete 方法，但 set(key, "", 0) 等效：
        expire=0 < time.time()，下次 get 会判定过期并返回空，触发重新请求 token。
        """
        try:
            from lark_oapi.core.token.manager import TokenManager
            cache_key = f"self_tenant_token:{self.config.app_id}"
            TokenManager.cache.set(cache_key, "", 0)
            logger.info(f"Feishu: token cache invalidated ({cache_key})")
        except Exception as e:
            logger.debug(f"Feishu: failed to invalidate token cache: {e}")

    _EMOJI_TO_FEISHU: dict[str, str] = {
        "✅": "DONE",
        "👀": "Get",
        "👍": "THUMBSUP",
        "❤️": "HEART",
        "🎉": "Celebrate",
    }

    async def add_reaction(
        self,
        chat_id: str,
        message_id: str,
        emoji: str = "👀",
    ) -> bool:
        """给消息添加表情回复。飞书使用名称标识而非 Unicode，自动映射常见 emoji。"""
        if not self._client:
            return False
        emoji_type = self._EMOJI_TO_FEISHU.get(emoji, "Get")
        try:
            request = (
                lark_oapi.api.im.v1.CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageReactionRequestBody.builder()
                    .reaction_type(
                        lark_oapi.api.im.v1.Emoji.builder()
                        .emoji_type(emoji_type)
                        .build()
                    )
                    .build()
                )
                .build()
            )
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message_reaction.create(request)
            )
            return True
        except Exception as e:
            logger.debug(f"Feishu: add_reaction failed (non-critical): {e}")
            return False

    # ==================== 会话级 key 辅助 ====================

    @staticmethod
    def _make_session_key(chat_id: str, thread_id: str | None = None) -> str:
        """生成 session 级 key，用于 _thinking_cards / _last_user_msg / streaming 等 dict"""
        return f"{chat_id}:{thread_id}" if thread_id else chat_id

    # ==================== 思考状态指示器 ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """发送"思考中..."占位卡片（首次调用时发送，后续调用跳过）。

        Gateway 的 _keep_typing 每 4 秒调用一次，仅第一次生成卡片。
        """
        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_finalized.discard(sk)
        self._streaming_thinking.pop(sk, None)
        self._streaming_thinking_ms.pop(sk, None)
        self._streaming_chain.pop(sk, None)
        if sk in self._typing_suppressed:
            return
        if sk in self._thinking_cards:
            return
        if not self._client:
            return
        self._typing_start_time[sk] = time.time()
        self._typing_status[sk] = "思考中"
        reply_to = self._last_user_msg.pop(sk, None) or thread_id
        card_msg_id = await self._send_thinking_card(chat_id, reply_to=reply_to, sk=sk)
        if card_msg_id:
            self._thinking_cards[sk] = card_msg_id

    async def clear_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """清理残留的"思考中..."占位卡片（安全网）。

        正常路径下 send_message / finalize_stream 已消费卡片，此方法不会做
        任何事。仅在异常路径或 _keep_typing 重建卡片后未被消费时触发。
        """
        sk = self._make_session_key(chat_id, thread_id)
        self._typing_start_time.pop(sk, None)
        self._typing_status.pop(sk, None)
        self._typing_suppressed.discard(sk)
        card_id = self._thinking_cards.pop(sk, None)
        if card_id:
            logger.debug(f"Feishu: clear_typing removing leftover card {card_id}")
            with contextlib.suppress(Exception):
                await self._delete_feishu_message(card_id)

    def _build_footer_note(self, sk: str, *, final: bool = False) -> dict | None:
        """构建卡片底部 note 元素（显示耗时和/或状态）。"""
        if not self._footer_elapsed and not self._footer_status:
            return None

        start = self._typing_start_time.get(sk)
        elapsed_s = (time.time() - start) if start else 0.0
        status = self._typing_status.get(sk, "")

        parts: list[str] = []
        if final:
            if self._footer_elapsed:
                parts.append(f"⏱ 完成 ({elapsed_s:.1f}s)")
            else:
                parts.append("✅ 完成")
        else:
            if self._footer_elapsed and elapsed_s > 0:
                parts.append(f"⏱ {elapsed_s:.1f}s")
            if self._footer_status and status:
                parts.append(status)

        if not parts:
            return None

        return {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": " · ".join(parts)},
            ],
        }

    def _build_card_json(
        self, content: str, sk: str | None = None, *, final: bool = False,
    ) -> dict:
        """构建飞书卡片 JSON 1.0 结构，含可选 footer note。"""
        elements: list[dict] = [{"tag": "markdown", "content": content}]
        if sk:
            note = self._build_footer_note(sk, final=final)
            if note:
                elements.append(note)
        return {"config": {"wide_screen_mode": True}, "elements": elements}

    async def _send_thinking_card(
        self, chat_id: str, reply_to: str | None = None,
        sk: str | None = None,
    ) -> str | None:
        """发送"思考中..."交互卡片，返回卡片 message_id。"""
        card = self._build_card_json("💭 **思考中...**", sk)
        content = json.dumps(card)
        try:
            if reply_to:
                request = (
                    lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                    .message_id(reply_to)
                    .request_body(
                        lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._client.im.v1.message.reply(request)
                )
            else:
                request = (
                    lark_oapi.api.im.v1.CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("interactive")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._client.im.v1.message.create(request)
                )
            if response.success():
                logger.debug(f"Feishu: thinking card sent to {chat_id}")
                mid = response.data.message_id if response.data else ""
                self._record_bot_msg_id(mid)
                return mid
            logger.debug(f"Feishu: thinking card failed: {response.msg}")
        except Exception as e:
            logger.debug(f"Feishu: _send_thinking_card error: {e}")
        return None

    async def _patch_card_content(
        self, message_id: str, new_content: str,
        sk: str | None = None, *, final: bool = False,
    ) -> bool:
        """通过 PATCH API 将占位卡片更新为最终回复内容。"""
        card = self._build_card_json(new_content, sk, final=final)
        request = (
            lark_oapi.api.im.v1.PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                lark_oapi.api.im.v1.PatchMessageRequestBody.builder()
                .content(json.dumps(card))
                .build()
            )
            .build()
        )
        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.patch(request)
        )
        if response.success():
            logger.debug(f"Feishu: thinking card patched: {message_id}")
            return True
        logger.warning(
            f"Feishu: patch card failed ({message_id}): {response.msg}"
        )
        return False

    async def _delete_feishu_message(self, message_id: str) -> None:
        """删除飞书消息（PATCH 失败时的降级方案，静默忽略错误）。"""
        try:
            request = (
                lark_oapi.api.im.v1.DeleteMessageRequest.builder()
                .message_id(message_id)
                .build()
            )
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.delete(request)
            )
        except Exception as e:
            logger.debug(f"Feishu: delete message failed (non-critical): {e}")

    # ==================== 流式卡片输出 ====================

    def is_streaming_enabled(self, is_group: bool = False) -> bool:
        """检查当前是否启用流式输出"""
        if not self._streaming_enabled:
            return False
        if is_group and not self._group_streaming:
            return False
        return True

    async def stream_thinking(
        self,
        chat_id: str,
        thinking_text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
        duration_ms: int = 0,
    ) -> None:
        """接收思考内容，PATCH 到卡片显示思考过程。"""
        if not self.is_streaming_enabled(is_group):
            return

        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_thinking[sk] = thinking_text
        self._typing_status[sk] = "深度思考"
        if duration_ms:
            self._streaming_thinking_ms[sk] = duration_ms

        card_id = self._thinking_cards.get(sk)
        if not card_id:
            return

        display = self._compose_thinking_display(sk)
        try:
            await self._patch_card_content(card_id, display, sk)
            self._streaming_last_patch[sk] = time.time()
        except Exception as e:
            logger.debug(f"Feishu: stream_thinking patch failed (non-fatal): {e}")

    async def stream_chain_text(
        self,
        chat_id: str,
        text: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """将工具调用描述/结果摘要等 chain 文本追加到流式卡片中。"""
        if not self.is_streaming_enabled(is_group):
            return

        sk = self._make_session_key(chat_id, thread_id)
        self._streaming_chain.setdefault(sk, []).append(text)
        self._typing_status[sk] = "调用工具"

        card_id = self._thinking_cards.get(sk)
        if not card_id:
            return

        now = time.time()
        last_t = self._streaming_last_patch.get(sk, 0.0)
        throttle_s = self._streaming_throttle_ms / 1000.0
        if now - last_t >= throttle_s:
            display = self._compose_thinking_display(sk)
            try:
                await self._patch_card_content(card_id, display, sk)
                self._streaming_last_patch[sk] = now
            except Exception as e:
                logger.debug(f"Feishu: stream_chain_text patch failed (non-fatal): {e}")

    def _compose_thinking_display(self, sk: str) -> str:
        """根据当前 thinking + chain + reply buffer 构建卡片显示内容"""
        thinking = self._streaming_thinking.get(sk, "")
        reply = self._streaming_buffers.get(sk, "")
        dur_ms = self._streaming_thinking_ms.get(sk, 0)
        chain_lines = self._streaming_chain.get(sk, [])

        parts: list[str] = []
        if thinking:
            dur_str = f" ({dur_ms / 1000:.1f}s)" if dur_ms else ""
            preview = thinking.strip()
            if len(preview) > 600:
                preview = preview[:600] + "..."
            parts.append(f"💭 **思考过程**{dur_str}\n> {preview.replace(chr(10), chr(10) + '> ')}")

        if chain_lines:
            visible = chain_lines[-8:]
            parts.append("\n".join(visible))

        if reply:
            if parts:
                parts.append("---")
            parts.append(reply + " ▍")
        elif not thinking and not chain_lines:
            parts.append("思考中...")

        return "\n".join(parts)

    async def stream_token(
        self,
        chat_id: str,
        token: str,
        *,
        thread_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """接收一个流式 token，累积并节流 PATCH 更新卡片

        如果没有 thinking card 或流式未启用，静默忽略。
        """
        if not self.is_streaming_enabled(is_group):
            return

        sk = self._make_session_key(chat_id, thread_id)
        self._typing_status[sk] = "生成回复"

        buf = self._streaming_buffers.get(sk, "")
        buf += token
        self._streaming_buffers[sk] = buf

        card_id = self._thinking_cards.get(sk)
        if not card_id:
            return

        now = time.time()
        last_t = self._streaming_last_patch.get(sk, 0.0)
        throttle_s = self._streaming_throttle_ms / 1000.0

        if now - last_t >= throttle_s:
            has_thinking = sk in self._streaming_thinking
            display_text = self._compose_thinking_display(sk) if has_thinking else (buf + " ▍")
            try:
                await self._patch_card_content(card_id, display_text, sk)
                self._streaming_last_patch[sk] = now
            except Exception as e:
                logger.debug(f"Feishu: streaming patch failed (non-fatal): {e}")

    async def finalize_stream(
        self,
        chat_id: str,
        final_text: str,
        *,
        thread_id: str | None = None,
    ) -> bool:
        """流式结束：用完整文本做最终 PATCH

        Returns:
            True 表示 PATCH 成功（send_message 应跳过重复发送），
            False 表示失败（send_message 走正常发送路径）。
        """
        sk = self._make_session_key(chat_id, thread_id)
        card_id = self._thinking_cards.get(sk)

        self._streaming_buffers.pop(sk, None)
        self._streaming_last_patch.pop(sk, None)
        self._streaming_thinking.pop(sk, None)
        self._streaming_thinking_ms.pop(sk, None)
        self._streaming_chain.pop(sk, None)

        if not card_id:
            return False

        try:
            success = await self._patch_card_content(card_id, final_text, sk, final=True)
            if success:
                self._streaming_finalized.add(sk)
                self._thinking_cards.pop(sk, None)
                self._typing_suppressed.add(sk)
                self._typing_start_time.pop(sk, None)
                self._typing_status.pop(sk, None)
                return True
        except Exception as e:
            logger.warning(f"Feishu: finalize_stream patch failed: {e}")

        # PATCH 失败回退：删除占位卡片，让 send_message 走正常路径
        with contextlib.suppress(Exception):
            await self._delete_feishu_message(card_id)
        self._thinking_cards.pop(sk, None)
        self._typing_suppressed.add(sk)
        self._typing_start_time.pop(sk, None)
        self._typing_status.pop(sk, None)
        return False

    # ── /feishu command helpers ─────────────────────────────────────────

    def get_status_info(self) -> dict:
        """Return adapter status dict for ``/feishu start``."""
        try:
            from openakita import __version__
        except Exception:
            __version__ = "unknown"
        return {
            "version": __version__,
            "app_id": self.config.app_id,
            "connected": self._client is not None,
            "streaming_enabled": self._streaming_enabled,
            "group_streaming": self._group_streaming,
            "group_response_mode": self._group_response_mode or "global",
        }

    def get_auth_url(self, redirect_uri: str = "") -> str:
        """Build Feishu OAuth2 user authorization URL.

        When *redirect_uri* is empty the parameter is omitted so that the
        Feishu platform automatically uses the redirect URI registered in the
        developer console, avoiding error 20029 (redirect URL mismatch).
        """
        base = "https://open.feishu.cn/open-apis/authen/v1/authorize"
        url = f"{base}?app_id={self.config.app_id}&response_type=code"
        if redirect_uri:
            url += f"&redirect_uri={redirect_uri}"
        return url

    _STALE_MESSAGE_THRESHOLD = 120  # 超过此秒数的重投递消息视为陈旧

    async def _handle_message_async(self, msg_dict: dict, sender_dict: dict) -> None:
        """异步处理消息（含去重 + 陈旧消息防护 + 已读回执）"""
        try:
            msg_id = msg_dict.get("message_id")

            # 消息去重（WebSocket 重连可能重复投递）
            if msg_id:
                if msg_id in self._seen_message_ids:
                    logger.debug(f"Feishu: duplicate message ignored: {msg_id}")
                    return
                self._seen_message_ids[msg_id] = None
                while len(self._seen_message_ids) > self._seen_message_ids_max:
                    self._seen_message_ids.popitem(last=False)

            # 陈旧消息防护：系统重启后去重字典为空，飞书 WebSocket 可能
            # 重新投递断连前未确认的旧消息。通过 create_time 检测并丢弃。
            create_time_ms = msg_dict.get("create_time")
            if create_time_ms:
                try:
                    age = time.time() - int(create_time_ms) / 1000
                    if age > self._STALE_MESSAGE_THRESHOLD:
                        logger.warning(
                            f"Feishu[{self.channel_name}]: stale message dropped "
                            f"(age={age:.0f}s > {self._STALE_MESSAGE_THRESHOLD}s): "
                            f"{msg_id}"
                        )
                        return
                except (ValueError, TypeError):
                    pass

            chat_id = msg_dict.get("chat_id")

            # 发送已读回执（表情回复，fire-and-forget）
            if msg_id:
                asyncio.create_task(self.add_reaction(chat_id or "", msg_id))

            # 记录最近用户消息 ID，供 send_typing 回复定位（session_key 级别）
            root_id = msg_dict.get("root_id")
            if chat_id and msg_id:
                sk = self._make_session_key(chat_id, root_id or None)
                self._last_user_msg[sk] = msg_id

            unified = await self._convert_message(msg_dict, sender_dict)
            self._log_message(unified)
            await self._emit_message(unified)
        except Exception as e:
            logger.error(f"Error in message handler: {e}", exc_info=True)

    async def stop(self) -> None:
        """停止飞书客户端，确保旧 WebSocket 连接被完全关闭。

        不关闭旧连接会导致飞书平台在新旧连接间随机分发消息，
        发到旧连接上的消息因 _main_loop 已失效而被静默丢弃。
        """
        self._running = False

        # 0) 取消看门狗任务
        if self._ws_watchdog_task is not None:
            self._ws_watchdog_task.cancel()
            self._ws_watchdog_task = None

        # 1) 在 WS 线程的 loop 上调度 task 取消，然后 stop loop。
        #    先取消 tasks 再 stop 可以让 _run_ws_in_thread 的 finally 块
        #    里的 _drain_loop_tasks 更快完成（大部分 tasks 已经是 cancelled 状态）。
        ws_loop = self._ws_loop
        if ws_loop is not None:
            try:
                def _cancel_and_stop() -> None:
                    for task in asyncio.all_tasks(ws_loop):
                        task.cancel()
                    ws_loop.stop()
                ws_loop.call_soon_threadsafe(_cancel_and_stop)
            except Exception:
                # loop 可能已关闭
                with contextlib.suppress(Exception):
                    ws_loop.call_soon_threadsafe(ws_loop.stop)

        # 2) 等待 WS 线程退出（给 5 秒超时）
        ws_thread = self._ws_thread
        if ws_thread is not None and ws_thread.is_alive():
            ws_thread.join(timeout=5)
            if ws_thread.is_alive():
                logger.warning("Feishu WebSocket thread did not exit within 5s timeout")

        self._ws_client = None
        self._ws_thread = None
        self._ws_loop = None
        self._client = None
        logger.info("Feishu adapter stopped")

    def handle_event(self, body: dict, headers: dict) -> dict:
        """
        处理飞书事件回调（Webhook 模式）

        用于 HTTP 服务器模式，接收飞书推送的事件

        Args:
            body: 请求体
            headers: 请求头

        Returns:
            响应体
        """
        # URL 验证
        if "challenge" in body:
            return {"challenge": body["challenge"]}

        # 验证签名
        if self.config.verification_token:
            token = body.get("token")
            if token != self.config.verification_token:
                logger.warning("Invalid verification token")
                return {"error": "invalid token"}

        # 处理事件
        event_type = body.get("header", {}).get("event_type")
        event = body.get("event", {})

        if event_type == "im.message.receive_v1":
            asyncio.create_task(self._handle_message_event(event))
        elif event_type == "card.action.trigger":
            return self._handle_card_action_webhook(body)

        return {"success": True}

    async def _handle_message_event(self, event: dict) -> None:
        """处理消息事件（Webhook 模式，含去重 + 陈旧消息防护 + 已读回执）"""
        try:
            message = event.get("message", {})
            sender = event.get("sender", {})

            msg_id = message.get("message_id")
            if msg_id:
                if msg_id in self._seen_message_ids:
                    logger.debug(f"Feishu: duplicate message ignored: {msg_id}")
                    return
                self._seen_message_ids[msg_id] = None
                while len(self._seen_message_ids) > self._seen_message_ids_max:
                    self._seen_message_ids.popitem(last=False)

            create_time_ms = message.get("create_time")
            if create_time_ms:
                try:
                    age = time.time() - int(create_time_ms) / 1000
                    if age > self._STALE_MESSAGE_THRESHOLD:
                        logger.warning(
                            f"Feishu[{self.channel_name}]: stale message dropped "
                            f"(age={age:.0f}s): {msg_id}"
                        )
                        return
                except (ValueError, TypeError):
                    pass

            chat_id = message.get("chat_id")
            root_id = message.get("root_id")

            if msg_id:
                asyncio.create_task(self.add_reaction(chat_id or "", msg_id))

            if chat_id and msg_id:
                sk = self._make_session_key(chat_id, root_id or None)
                self._last_user_msg[sk] = msg_id

            unified = await self._convert_message(message, sender)
            self._log_message(unified)
            await self._emit_message(unified)

        except Exception as e:
            logger.error(f"Error handling message event: {e}")

    async def _convert_message(self, message: dict, sender: dict) -> UnifiedMessage:
        """将飞书消息转换为统一格式"""
        content = MessageContent()

        msg_type = message.get("message_type")
        raw_content = message.get("content", "{}")
        if isinstance(raw_content, dict):
            msg_content = raw_content
        else:
            try:
                msg_content = json.loads(raw_content) if raw_content else {}
            except (json.JSONDecodeError, TypeError):
                msg_content = {}

        if msg_type == "text":
            content.text = msg_content.get("text", "")

        elif msg_type == "image":
            image_key = msg_content.get("image_key")
            if image_key:
                media = MediaFile.create(
                    filename=f"{image_key}.png",
                    mime_type="image/png",
                    file_id=image_key,
                )
                media.extra["message_id"] = message.get("message_id", "")
                content.images.append(media)

        elif msg_type == "audio":
            file_key = msg_content.get("file_key")
            if file_key:
                media = MediaFile.create(
                    filename=f"{file_key}.opus",
                    mime_type="audio/opus",
                    file_id=file_key,
                )
                media.duration = msg_content.get("duration", 0) / 1000
                media.extra["message_id"] = message.get("message_id", "")
                content.voices.append(media)

        elif msg_type == "media":
            # 视频消息
            file_key = msg_content.get("file_key")
            if file_key:
                media = MediaFile.create(
                    filename=f"{file_key}.mp4",
                    mime_type="video/mp4",
                    file_id=file_key,
                )
                media.extra["message_id"] = message.get("message_id", "")
                content.videos.append(media)

        elif msg_type == "file":
            file_key = msg_content.get("file_key")
            file_name = msg_content.get("file_name", "file")
            if file_key:
                media = MediaFile.create(
                    filename=file_name,
                    mime_type="application/octet-stream",
                    file_id=file_key,
                )
                media.extra["message_id"] = message.get("message_id", "")
                content.files.append(media)

        elif msg_type == "sticker":
            # 表情包
            file_key = msg_content.get("file_key")
            if file_key:
                media = MediaFile.create(
                    filename=f"{file_key}.png",
                    mime_type="image/png",
                    file_id=file_key,
                )
                media.extra["message_id"] = message.get("message_id", "")
                content.images.append(media)

        elif msg_type == "post":
            # 富文本（同时提取图片/视频 MediaFile）
            msg_id = message.get("message_id", "")
            content.text = self._parse_post_content_with_media(
                msg_content, content, msg_id,
            )

        else:
            # 未知类型
            content.text = f"[不支持的消息类型: {msg_type}]"

        # 确定聊天类型
        raw_chat_type = message.get("chat_type", "p2p")
        is_direct_message = raw_chat_type == "p2p"

        chat_type = raw_chat_type
        if chat_type == "p2p":
            chat_type = "private"
        elif chat_type == "group":
            chat_type = "group"

        # 检测 @机器人 提及：检查 mentions 列表是否包含机器人
        is_mentioned = False
        mentions = message.get("mentions") or []
        if mentions:
            bot_open_id = getattr(self, "_bot_open_id", None)
            if bot_open_id:
                for m in mentions:
                    m_id = m.get("id", {}) if isinstance(m, dict) else {}
                    if m_id.get("open_id") == bot_open_id:
                        is_mentioned = True
                        break
            else:
                # _bot_open_id 缺失时的降级检测：
                # 收集排除发送者后的候选 mention
                sender_open_id = sender.get("sender_id", {}).get("open_id", "")
                candidates = []
                for m in mentions:
                    m_id = m.get("id", {}) if isinstance(m, dict) else {}
                    m_open_id = m_id.get("open_id", "")
                    if m_open_id and m_open_id != sender_open_id:
                        candidates.append(m_open_id)
                if len(candidates) == 1:
                    # 仅一个非发送者 mention → 高概率就是 bot，安全缓存
                    is_mentioned = True
                    self._bot_open_id = candidates[0]
                    logger.info(
                        f"Feishu: auto-discovered bot open_id from mention: {candidates[0]}"
                    )
                elif candidates:
                    # 多个非发送者 mention → 响应但不缓存，避免误存
                    is_mentioned = True
                    logger.info(
                        f"Feishu: multiple non-sender mentions ({len(candidates)}), "
                        "responding without caching bot_open_id"
                    )
                else:
                    logger.warning(
                        "Feishu: _bot_open_id is None, mention detection fallback inconclusive"
                    )

        # 隐式 mention：回复机器人消息视为提及（群聊中用户回复 bot 消息时无显式 @）
        # 仅检查 parent_id（直接回复目标），不检查 root_id（话题根消息），
        # 避免在话题内回复其他用户消息时因 root_id 指向 bot 根消息而误判。
        if not is_mentioned and chat_type == "group":
            parent_id = message.get("parent_id")
            if parent_id and parent_id in self._bot_sent_msg_ids:
                is_mentioned = True
                logger.info(
                    f"Feishu: implicit mention detected (reply to bot message "
                    f"{parent_id[:20]})"
                )

        # 清理 @_user_N 占位符：替换为实际名称或移除
        if content.text and mentions:
            for m in mentions:
                key = m.get("key", "") if isinstance(m, dict) else ""
                name = m.get("name", "") if isinstance(m, dict) else ""
                if key and key in content.text:
                    content.text = content.text.replace(key, f"@{name}" if name else "")
            content.text = content.text.strip()

        # 检测 @所有人 -- 双重检测策略（key == "@_all" 或 key 存在但 open_id 为空）
        metadata: dict[str, Any] = {}
        if mentions:
            for m in mentions:
                m_dict = m if isinstance(m, dict) else {}
                key = m_dict.get("key", "")
                m_id = m_dict.get("id", {})
                open_id = m_id.get("open_id", "") if isinstance(m_id, dict) else ""
                if key == "@_all" or (key and not open_id):
                    chat_id = message.get("chat_id", "")
                    metadata["at_all"] = True
                    logger.info(f"Feishu: detected @all mention in chat {chat_id}: {m_dict}")
                    self._buffer_event(chat_id, {
                        "type": "at_all",
                        "chat_id": chat_id,
                        "message_id": message.get("message_id", ""),
                        "text": (content.text or "")[:200],
                    })
                    break

        sender_id = sender.get("sender_id", {})
        user_id = sender_id.get("user_id") or sender_id.get("open_id", "")

        metadata["is_group"] = chat_type == "group"
        metadata["sender_name"] = await self._resolve_user_name(
            sender_id.get("open_id", "")
        )
        if chat_type == "group":
            metadata["chat_name"] = await self._resolve_chat_name(
                message.get("chat_id", "")
            )

        return UnifiedMessage.create(
            channel=self.channel_name,
            channel_message_id=message.get("message_id", ""),
            user_id=f"fs_{user_id}",
            channel_user_id=user_id,
            chat_id=message.get("chat_id", ""),
            content=content,
            chat_type=chat_type,
            is_mentioned=is_mentioned,
            is_direct_message=is_direct_message,
            thread_id=message.get("root_id"),
            reply_to=message.get("root_id"),
            raw={"message": message, "sender": sender},
            metadata=metadata,
        )

    async def _resolve_user_name(self, open_id: str) -> str:
        """从缓存或 Contact API 获取用户显示名，失败时静默返回空字符串。"""
        if not open_id:
            return ""

        if open_id in self._user_name_cache:
            self._user_name_cache.move_to_end(open_id)
            return self._user_name_cache[open_id]

        if "获取用户信息" not in self._capabilities:
            return ""

        try:
            info = await self.get_user_info(open_id)
            name = (info or {}).get("name", "") if info else ""
        except Exception:
            name = ""

        self._user_name_cache[open_id] = name
        while len(self._user_name_cache) > self._user_name_cache_max:
            self._user_name_cache.popitem(last=False)

        return name

    async def _resolve_chat_name(self, chat_id: str) -> str:
        """从缓存或 im.v1.chat.get API 获取群聊名称，失败时静默返回空字符串。"""
        if not chat_id:
            return ""

        if chat_id in self._chat_name_cache:
            self._chat_name_cache.move_to_end(chat_id)
            return self._chat_name_cache[chat_id]

        try:
            info = await self.get_chat_info(chat_id)
            name = (info or {}).get("name") or "" if info else ""
        except Exception:
            name = ""

        if name:
            self._chat_name_cache[chat_id] = name
            while len(self._chat_name_cache) > self._chat_name_cache_max:
                self._chat_name_cache.popitem(last=False)

        return name

    def _parse_post_content(self, post: dict) -> str:
        """解析富文本内容（纯文本，不提取 MediaFile）

        飞书 post 消息的 content JSON 格式为：
        {"post": {"zh_cn": {"title": "...", "content": [[...]]}}}
        需要先提取语言层再解析具体内容。
        """
        body = self._extract_post_body(post)
        if not isinstance(body, dict):
            return str(body) if body else ""
        return self._render_post_body(body)

    def _parse_post_content_with_media(
        self, post: dict, content: MessageContent, message_id: str = "",
    ) -> str:
        """解析富文本内容，同时提取图片/视频为 MediaFile。

        与 _parse_post_content 相比，遇到 img/media 标签时会创建 MediaFile
        并 append 到 content.images / content.videos，确保多模态数据不丢失。
        """
        body = self._extract_post_body(post)
        if not isinstance(body, dict):
            return str(body) if body else ""
        return self._render_post_body(body, content=content, message_id=message_id)

    @staticmethod
    def _extract_post_body(post: dict) -> dict | str:
        """从 post JSON 中提取语言层 body（zh_cn / en_us / 首个可用语言）。"""
        body = post
        if "post" in post:
            lang_map = post["post"]
            body = lang_map.get("zh_cn") or lang_map.get("en_us") or {}
            if not body and lang_map:
                body = next(iter(lang_map.values()), {})
        elif "title" not in post and "content" not in post:
            for v in post.values():
                if isinstance(v, dict) and ("title" in v or "content" in v):
                    body = v
                    break
        return body

    @staticmethod
    def _render_post_body(
        body: dict,
        content: MessageContent | None = None,
        message_id: str = "",
    ) -> str:
        """将 post body 渲染为纯文本，可选同时提取媒体到 content。"""
        result: list[str] = []

        title = body.get("title", "")
        if title:
            result.append(title)

        for paragraph in body.get("content", []):
            line_parts: list[str] = []
            for item in paragraph:
                tag = item.get("tag", "")
                if tag == "text":
                    line_parts.append(item.get("text", ""))
                elif tag == "a":
                    line_parts.append(f"[{item.get('text', '')}]({item.get('href', '')})")
                elif tag == "at":
                    line_parts.append(f"@{item.get('user_name', item.get('user_id', ''))}")
                elif tag == "img":
                    image_key = item.get("image_key", "")
                    line_parts.append(f"[图片:{image_key}]" if image_key else "[图片]")
                    if image_key and content is not None:
                        media = MediaFile.create(
                            filename=f"{image_key}.png",
                            mime_type="image/png",
                            file_id=image_key,
                        )
                        media.extra["message_id"] = message_id
                        content.images.append(media)
                elif tag == "media":
                    file_key = item.get("file_key", "")
                    line_parts.append(f"[视频:{file_key}]")
                    if file_key and content is not None:
                        media = MediaFile.create(
                            filename=f"{file_key}.mp4",
                            mime_type="video/mp4",
                            file_id=file_key,
                        )
                        media.extra["message_id"] = message_id
                        content.videos.append(media)
                elif tag == "emotion":
                    line_parts.append(item.get("emoji_type", ""))
            if line_parts:
                result.append("".join(line_parts))

        return "\n".join(result)

    def _record_bot_msg_id(self, msg_id: str) -> None:
        """Record a bot-sent message_id for implicit mention detection in group replies."""
        if not msg_id:
            return
        self._bot_sent_msg_ids[msg_id] = None
        while len(self._bot_sent_msg_ids) > self._bot_sent_msg_ids_max:
            self._bot_sent_msg_ids.popitem(last=False)

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息"""
        if not self._client:
            raise RuntimeError("Feishu client not started")

        # ---- 思考卡片处理：尝试 PATCH 占位卡片为最终回复 ----
        sk = self._make_session_key(message.chat_id, message.thread_id)

        # 中间消息（ask_user 问题、提醒、反馈等）不参与卡片状态管理，
        # 保留思考卡片给最终回复使用
        _is_interim = message.metadata.get("_interim", False)

        if not _is_interim:
            # 如果流式已经完成了 finalize，不再重复 PATCH
            if sk in self._streaming_finalized:
                card_id = self._thinking_cards.get(sk)
                self._streaming_finalized.discard(sk)
                self._thinking_cards.pop(sk, None)
                self._typing_suppressed.add(sk)
                self._streaming_buffers.pop(sk, None)
                self._streaming_last_patch.pop(sk, None)
                self._typing_start_time.pop(sk, None)
                self._typing_status.pop(sk, None)
                return card_id or sk
            if sk not in self._streaming_buffers:
                text = message.content.text or ""
                if text and not message.content.has_media:
                    thinking_card_id = self._thinking_cards.pop(sk, None)
                    if thinking_card_id:
                        self._typing_suppressed.add(sk)
                        try:
                            if await self._patch_card_content(thinking_card_id, text, sk, final=True):
                                self._typing_start_time.pop(sk, None)
                                self._typing_status.pop(sk, None)
                                return thinking_card_id
                        except Exception as e:
                            logger.warning(f"Feishu: patch thinking card failed: {e}")
                        with contextlib.suppress(Exception):
                            await self._delete_feishu_message(thinking_card_id)
                        self._typing_start_time.pop(sk, None)
                        self._typing_status.pop(sk, None)

        reply_target = message.reply_to or message.thread_id

        # 语音/文件/视频：循环发送所有条目（首条带 caption 和 reply_to）
        if message.content.voices:
            first_msg_id = None
            for i, voice in enumerate(message.content.voices):
                if voice.local_path:
                    try:
                        mid = await self.send_voice(
                            message.chat_id, voice.local_path,
                            message.content.text if i == 0 else None,
                            reply_to=reply_target if i == 0 else None,
                        )
                        if first_msg_id is None:
                            first_msg_id = mid
                    except Exception as e:
                        logger.warning(f"Feishu: send voice [{i}] failed: {e}")
            return first_msg_id or ""
        if message.content.files:
            first_msg_id = None
            for i, file in enumerate(message.content.files):
                if file.local_path:
                    try:
                        mid = await self.send_file(
                            message.chat_id, file.local_path,
                            message.content.text if i == 0 else None,
                            reply_to=reply_target if i == 0 else None,
                        )
                        if first_msg_id is None:
                            first_msg_id = mid
                    except Exception as e:
                        logger.warning(f"Feishu: send file [{i}] failed: {e}")
            return first_msg_id or ""
        if message.content.videos:
            first_msg_id = None
            for i, video in enumerate(message.content.videos):
                if video.local_path:
                    try:
                        mid = await self.send_file(
                            message.chat_id, video.local_path,
                            message.content.text if i == 0 else None,
                            reply_to=reply_target if i == 0 else None,
                        )
                        if first_msg_id is None:
                            first_msg_id = mid
                    except Exception as e:
                        logger.warning(f"Feishu: send video [{i}] failed: {e}")
            return first_msg_id or ""

        # 构建消息内容
        _pending_caption = None
        if message.content.text and not message.content.has_media:
            text = message.content.text
            # 检测是否包含 markdown 格式
            if self._contains_markdown(text):
                # 使用卡片消息支持 markdown 渲染
                msg_type = "interactive"
                card = {
                    "config": {"wide_screen_mode": True},
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": text,
                        }
                    ],
                }
                content = json.dumps(card)
            else:
                msg_type = "text"
                content = json.dumps({"text": text})
        elif message.content.images:
            image = message.content.images[0]
            if image.local_path:
                image_key = await self._upload_image(image.local_path)
                msg_type = "image"
                content = json.dumps({"image_key": image_key})
                _pending_caption = message.content.text or None
            else:
                msg_type = "text"
                content = json.dumps({"text": message.content.text or "[图片]"})
                _pending_caption = None
        else:
            msg_type = "text"
            content = json.dumps({"text": message.content.text or ""})
            _pending_caption = None

        # 话题回复：有 reply_to 或 thread_id 时使用 ReplyMessageRequest 回到同一话题
        if reply_target:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_target)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
            if not response.success():
                raise RuntimeError(f"Failed to reply message: {response.msg}")
            if _pending_caption:
                await self._send_text(message.chat_id, _pending_caption, reply_to=reply_target)
            for extra_img in message.content.images[1:]:
                if extra_img.local_path:
                    try:
                        await self.send_image(message.chat_id, extra_img.local_path, reply_to=reply_target)
                    except Exception as e:
                        logger.warning(f"Feishu: send extra image failed: {e}")
            mid = response.data.message_id if response.data else ""
            self._record_bot_msg_id(mid)
            return mid

        # 普通发送（在线程池中执行同步调用）
        request = (
            lark_oapi.api.im.v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                .receive_id(message.chat_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )

        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.create(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to send message: {response.msg}")

        if _pending_caption:
            await self._send_text(message.chat_id, _pending_caption, reply_to=reply_target)

        for extra_img in message.content.images[1:]:
            if extra_img.local_path:
                try:
                    await self.send_image(message.chat_id, extra_img.local_path)
                except Exception as e:
                    logger.warning(f"Feishu: send extra image failed: {e}")

        mid = response.data.message_id if response.data else ""
        self._record_bot_msg_id(mid)
        return mid

    # ==================== IM 查询工具方法 ====================

    async def get_chat_info(self, chat_id: str) -> dict | None:
        """获取群聊信息（群名、成员数、群主等）"""
        if not self._client:
            return None
        try:
            import lark_oapi.api.im.v1 as im_v1
            req = im_v1.GetChatRequest.builder().chat_id(chat_id).build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat.get(req)
            )
            if not resp.success():
                logger.debug(f"Feishu get_chat_info failed: {resp.msg}")
                return None
            chat = resp.data.chat
            return {
                "id": chat_id,
                "name": getattr(chat, "name", ""),
                "type": "group",
                "description": getattr(chat, "description", ""),
                "owner_id": getattr(chat, "owner_id", ""),
                "members_count": getattr(chat, "user_count", 0),
            }
        except Exception as e:
            logger.debug(f"Feishu get_chat_info error: {e}")
            return None

    async def get_user_info(self, user_id: str) -> dict | None:
        """获取用户信息（名称、头像等）"""
        if not self._client:
            return None
        try:
            import lark_oapi.api.contact.v3 as contact_v3
            req = (
                contact_v3.GetUserRequest.builder()
                .user_id(user_id)
                .user_id_type("open_id")
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.contact.v3.user.get(req)
            )
            if not resp.success():
                logger.debug(f"Feishu get_user_info failed: {resp.msg}")
                return None
            user = resp.data.user
            avatar = getattr(user, "avatar", None)
            avatar_url = ""
            if avatar and isinstance(avatar, dict):
                avatar_url = avatar.get("avatar_origin", "")
            elif avatar:
                avatar_url = getattr(avatar, "avatar_origin", "")
            return {
                "id": user_id,
                "name": getattr(user, "name", ""),
                "avatar_url": avatar_url,
            }
        except Exception as e:
            logger.debug(f"Feishu get_user_info error: {e}")
            return None

    async def get_chat_members(self, chat_id: str) -> list[dict]:
        """获取群聊成员列表"""
        if not self._client:
            return []
        try:
            import lark_oapi.api.im.v1 as im_v1
            req = (
                im_v1.GetChatMembersRequest.builder()
                .chat_id(chat_id)
                .member_id_type("open_id")
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat_members.get(req)
            )
            if not resp.success():
                logger.debug(f"Feishu get_chat_members failed: {resp.msg}")
                return []
            return [
                {"id": getattr(m, "member_id", ""), "name": getattr(m, "name", "")}
                for m in (resp.data.items or [])
            ]
        except Exception as e:
            logger.debug(f"Feishu get_chat_members error: {e}")
            return []

    async def get_recent_messages(self, chat_id: str, limit: int = 20) -> list[dict]:
        """获取群聊最近消息（话题分层策略第二层）"""
        if not self._client:
            return []
        try:
            import lark_oapi.api.im.v1 as im_v1
            req = (
                im_v1.ListMessageRequest.builder()
                .container_id_type("chat")
                .container_id(chat_id)
                .page_size(limit)
                .build()
            )
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.list(req)
            )
            if not resp.success():
                logger.debug(f"Feishu get_recent_messages failed: {resp.msg}")
                return []
            return [
                {
                    "id": getattr(m, "message_id", ""),
                    "sender": getattr(m, "sender", {}),
                    "content": (lambda b: b.get("content", "") if isinstance(b, dict) else getattr(b, "content", "") if b else "")(getattr(m, "body", None)),
                    "type": getattr(m, "msg_type", ""),
                    "time": getattr(m, "create_time", ""),
                }
                for m in (resp.data.items or [])
            ]
        except Exception as e:
            logger.debug(f"Feishu get_recent_messages error: {e}")
            return []

    def _contains_markdown(self, text: str) -> bool:
        """检测文本是否包含 markdown 格式"""
        import re

        # 常见 markdown 标记模式
        patterns = [
            r"\*\*[^*]+\*\*",  # **bold**
            r"__[^_]+__",  # __bold__
            r"(?<!\*)\*[^*]+\*(?!\*)",  # *italic* (非 **)
            r"(?<!_)_[^_]+_(?!_)",  # _italic_ (非 __)
            r"^#{1,6}\s",  # # heading
            r"\[.+?\]\(.+?\)",  # [link](url)
            r"`[^`]+`",  # `code`
            r"```",  # code block
            r"^[-*+]\s",  # - list item
            r"^\d+\.\s",  # 1. ordered list
            r"^>\s",  # > quote
        ]
        return any(re.search(pattern, text, re.MULTILINE) for pattern in patterns)

    async def _upload_image(self, path: str) -> str:
        """上传图片（含 token 过期自动重试）

        lark-oapi SDK 不会在 401/权限错误时自动刷新 token，
        因此在首次失败且判定为 token/权限类错误时，主动清缓存并重试一次。
        每次重试需重新 open 文件，因为上次请求已消耗文件句柄。
        """
        for attempt in range(2):
            with open(path, "rb") as f:
                request = (
                    lark_oapi.api.im.v1.CreateImageRequest.builder()
                    .request_body(
                        lark_oapi.api.im.v1.CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(f)
                        .build()
                    )
                    .build()
                )
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda _r=request: self._client.im.v1.image.create(_r)
                )

            if response.success():
                return response.data.image_key if response.data else ""

            if attempt == 0 and self._is_token_error(response):
                logger.warning(
                    f"Feishu: image upload permission error ({response.msg}), "
                    "invalidating token cache and retrying..."
                )
                self._invalidate_token_cache()
                await asyncio.sleep(1)
                continue

            raise RuntimeError(f"Failed to upload image: {response.msg}")

    async def download_media(self, media: MediaFile) -> Path:
        """下载媒体文件"""
        if not self._client:
            raise RuntimeError("Feishu client not started")

        if media.local_path and Path(media.local_path).exists():
            return Path(media.local_path)

        if not media.file_id:
            raise ValueError("Media has no file_id")

        # 根据类型选择下载接口
        message_id = media.extra.get("message_id", "")
        if media.is_image and not message_id:
            # 仅用于下载机器人自己上传的图片（无 message_id）
            request = lark_oapi.api.im.v1.GetImageRequest.builder().image_key(media.file_id).build()

            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.image.get(request)
            )
        else:
            # 用户消息中的图片/音频/视频/文件，统一走 MessageResource 接口
            resource_type = "image" if media.is_image else "file"
            request = (
                lark_oapi.api.im.v1.GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(media.file_id)
                .type(resource_type)
                .build()
            )

            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message_resource.get(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to download media: {response.msg}")

        if not getattr(response, "file", None):
            raise RuntimeError(f"Download succeeded but response.file is empty for {media.file_id}")

        # 保存文件（过滤 Windows 非法字符如 : * ? 等）
        from openakita.channels.base import sanitize_filename
        safe_name = sanitize_filename(Path(media.filename).name or "download")
        local_path = self.media_dir / safe_name
        with open(local_path, "wb") as f:
            f.write(response.file.read())

        media.local_path = str(local_path)
        media.status = MediaStatus.READY

        logger.info(f"Downloaded media: {media.filename}")
        return local_path

    async def upload_media(self, path: Path, mime_type: str) -> MediaFile:
        """上传媒体文件"""
        if mime_type.startswith("image/"):
            image_key = await self._upload_image(str(path))
            media = MediaFile.create(
                filename=path.name,
                mime_type=mime_type,
                file_id=image_key,
            )
            media.status = MediaStatus.READY
            return media

        return MediaFile.create(
            filename=path.name,
            mime_type=mime_type,
        )

    async def send_card(
        self, chat_id: str, card: dict, *, reply_to: str | None = None,
    ) -> str:
        """
        发送卡片消息

        Args:
            chat_id: 聊天 ID
            card: 卡片内容（飞书卡片 JSON）
            reply_to: 回复目标消息 ID（用于话题内回复）

        Returns:
            消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        content = json.dumps(card)

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send card: {response.msg}")

        mid = response.data.message_id if response.data else ""
        self._record_bot_msg_id(mid)
        return mid

    async def reply_message(self, message_id: str, text: str, msg_type: str = "text") -> str:
        """
        回复消息

        Args:
            message_id: 要回复的消息 ID
            text: 回复内容
            msg_type: 消息类型

        Returns:
            新消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        content = json.dumps({"text": text}) if msg_type == "text" else text

        request = (
            lark_oapi.api.im.v1.ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )

        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._client.im.v1.message.reply(request)
        )

        if not response.success():
            raise RuntimeError(f"Failed to reply message: {response.msg}")

        mid = response.data.message_id if response.data else ""
        self._record_bot_msg_id(mid)
        return mid

    async def send_photo(
        self, chat_id: str, photo_path: str, caption: str | None = None,
        *, reply_to: str | None = None,
    ) -> str:
        """
        发送图片

        Args:
            chat_id: 聊天 ID
            photo_path: 图片文件路径
            caption: 图片说明文字
            reply_to: 回复目标消息 ID（用于话题内回复）

        Returns:
            消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        image_key = await self._upload_image(photo_path)
        content = json.dumps({"image_key": image_key})

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("image")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("image")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send photo: {response.msg}")

        message_id = response.data.message_id if response.data else ""

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent photo to {chat_id}: {photo_path}")
        self._record_bot_msg_id(message_id)
        return message_id

    async def send_file(
        self, chat_id: str, file_path: str, caption: str | None = None,
        *, reply_to: str | None = None,
    ) -> str:
        """
        发送文件

        Args:
            chat_id: 聊天 ID
            file_path: 文件路径
            caption: 文件说明文字
            reply_to: 回复目标消息 ID（用于话题内回复）

        Returns:
            消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        file_key = await self._upload_file(file_path)
        content = json.dumps({"file_key": file_key})

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("file")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("file")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send file: {response.msg}")

        message_id = response.data.message_id if response.data else ""

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent file to {chat_id}: {file_path}")
        self._record_bot_msg_id(message_id)
        return message_id

    async def send_voice(
        self, chat_id: str, voice_path: str, caption: str | None = None,
        *, reply_to: str | None = None,
    ) -> str:
        """
        发送语音消息

        Args:
            chat_id: 聊天 ID
            voice_path: 语音文件路径
            caption: 语音说明文字
            reply_to: 回复目标消息 ID（用于话题内回复）

        Returns:
            消息 ID
        """
        if not self._client:
            raise RuntimeError("Feishu client not started")

        file_key = await self._upload_file(voice_path)
        content = json.dumps({"file_key": file_key})

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("audio")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("audio")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send voice: {response.msg}")

        message_id = response.data.message_id if response.data else ""

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent voice to {chat_id}: {voice_path}")
        self._record_bot_msg_id(message_id)
        return message_id

    async def _send_text(
        self, chat_id: str, text: str, *, reply_to: str | None = None,
    ) -> str:
        """发送纯文本消息"""
        content = json.dumps({"text": text})

        if reply_to:
            request = (
                lark_oapi.api.im.v1.ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    lark_oapi.api.im.v1.ReplyMessageRequestBody.builder()
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.reply(request)
            )
        else:
            request = (
                lark_oapi.api.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark_oapi.api.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.create(request)
            )

        if not response.success():
            raise RuntimeError(f"Failed to send text: {response.msg}")

        mid = response.data.message_id if response.data else ""
        self._record_bot_msg_id(mid)
        return mid

    async def _upload_file(self, path: str) -> str:
        """上传文件到飞书（含 token 过期自动重试）"""
        file_name = Path(path).name

        for attempt in range(2):
            with open(path, "rb") as f:
                request = (
                    lark_oapi.api.im.v1.CreateFileRequest.builder()
                    .request_body(
                        lark_oapi.api.im.v1.CreateFileRequestBody.builder()
                        .file_type("stream")
                        .file_name(file_name)
                        .file(f)
                        .build()
                    )
                    .build()
                )
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda _r=request: self._client.im.v1.file.create(_r)
                )

            if response.success():
                return response.data.file_key if response.data else ""

            if attempt == 0 and self._is_token_error(response):
                logger.warning(
                    f"Feishu: file upload permission error ({response.msg}), "
                    "invalidating token cache and retrying..."
                )
                self._invalidate_token_cache()
                await asyncio.sleep(1)
                continue

            raise RuntimeError(f"Failed to upload file: {response.msg}")

    def build_simple_card(
        self,
        title: str,
        content: str,
        buttons: list[dict] | None = None,
    ) -> dict:
        """构建简单卡片。

        Args:
            title: 标题
            content: Markdown 内容
            buttons: 按钮列表，每项支持两种格式：
                - ``{"text": "按钮文字", "value": "字符串回调值"}``
                - ``{"text": "按钮文字", "value": {"action": "xxx", ...}}``
                  传 dict 时整体作为按钮 value，便于卡片回调分发。

        Returns:
            飞书卡片 JSON（1.0 结构）
        """
        elements = [
            {
                "tag": "markdown",
                "content": content,
            }
        ]

        if buttons:
            actions = []
            for btn in buttons:
                raw_value = btn.get("value", btn["text"])
                btn_value = raw_value if isinstance(raw_value, dict) else {"action": raw_value}
                actions.append(
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": btn["text"]},
                        "type": btn.get("type", "primary"),
                        "value": btn_value,
                    }
                )

            elements.append(
                {
                    "tag": "action",
                    "actions": actions,
                }
            )

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": elements,
        }
