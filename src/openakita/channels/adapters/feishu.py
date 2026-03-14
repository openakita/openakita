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
    verification_token: str | None = None  # 用于 Webhook 验证
    encrypt_key: str | None = None  # 用于消息加解密
    log_level: str = "INFO"  # 日志级别: DEBUG, INFO, WARN, ERROR


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
        self._bot_open_id: str | None = None
        self._capabilities: list[str] = []

        # 消息去重：WebSocket 重连可能导致重复投递
        self._seen_message_ids: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._seen_message_ids_max = 500

        # "思考中..."占位卡片：chat_id → 卡片 message_id
        self._thinking_cards: dict[str, str] = {}
        # 最近一条用户消息 ID：chat_id → user_msg_id（供 send_typing 回复定位）
        self._last_user_msg: dict[str, str] = {}

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
        try:
            import lark_oapi.api.bot.v3 as bot_v3

            for attempt in range(3):
                try:
                    req = bot_v3.GetBotInfoRequest.builder().build()
                    resp = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: self._client.bot.v3.bot_info.get(req)
                    )
                    if resp.success() and resp.data and resp.data.bot:
                        self._bot_open_id = getattr(resp.data.bot, "open_id", None)
                        logger.info(f"Feishu bot open_id: {self._bot_open_id}")
                        break
                    else:
                        logger.warning(
                            f"Feishu: GetBotInfo attempt {attempt + 1}/3 failed: {getattr(resp, 'msg', 'unknown')}"
                        )
                except Exception as e:
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
            except Exception as e:
                logger.warning(f"Feishu: raw HTTP bot info fallback failed: {e}")

        if not self._bot_open_id:
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

        # 探测可用权限/能力
        await self._probe_capabilities()

    async def _probe_capabilities(self) -> None:
        """探测飞书适配器已实现方法对应的权限是否可用

        通过调用 API 并检查响应码判断权限：
        - 权限不足：响应消息通常包含 "permission"/"tenant_access_token"
        - 参数无效/资源不存在：说明权限本身是通过的
        """
        self._capabilities = ["发消息", "发文件", "回复消息"]
        if not self._client:
            return

        _PERMISSION_KEYWORDS = ("permission", "tenant_access_token", "app_access_token", "forbidden")

        def _is_permission_error(resp: Any) -> bool:
            if resp.success():
                return False
            msg = (getattr(resp, "msg", "") or "").lower()
            return any(kw in msg for kw in _PERMISSION_KEYWORDS)

        try:
            import lark_oapi.api.im.v1 as im_v1
            import lark_oapi.api.contact.v3 as contact_v3
        except ImportError:
            logger.warning("lark_oapi submodules not available for capability probing")
            return

        try:
            req = im_v1.GetChatRequest.builder().chat_id("probe_test").build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat.get(req))
            if not _is_permission_error(resp):
                self._capabilities.append("获取群信息")
        except Exception:
            pass

        try:
            req = contact_v3.GetUserRequest.builder().user_id("probe_test").user_id_type("open_id").build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.contact.v3.user.get(req))
            if not _is_permission_error(resp):
                self._capabilities.append("获取用户信息")
        except Exception:
            pass

        try:
            req = im_v1.GetChatMembersRequest.builder().chat_id("probe_test").member_id_type("open_id").build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.chat_members.get(req))
            if not _is_permission_error(resp):
                self._capabilities.append("获取群成员")
        except Exception:
            pass

        try:
            req = im_v1.ListMessageRequest.builder().container_id_type("chat").container_id("probe_test").page_size(1).build()
            resp = await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.im.v1.message.list(req))
            if not _is_permission_error(resp):
                self._capabilities.append("获取消息历史")
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

    async def add_reaction(self, message_id: str, emoji_type: str = "Get") -> None:
        """给消息添加表情回复，用作「已读」回执替代。默认用 [了解] 表示正在处理。"""
        if not self._client:
            return
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
        except Exception as e:
            logger.debug(f"Feishu: add_reaction failed (non-critical): {e}")

    # ==================== 思考状态指示器 ====================

    async def send_typing(self, chat_id: str, thread_id: str | None = None) -> None:
        """发送"思考中..."占位卡片（首次调用时发送，后续调用跳过）。

        Gateway 的 _keep_typing 每 4 秒调用一次，仅第一次生成卡片。
        """
        if chat_id in self._thinking_cards:
            return
        if not self._client:
            return
        reply_to = self._last_user_msg.pop(chat_id, None) or thread_id
        card_msg_id = await self._send_thinking_card(chat_id, reply_to=reply_to)
        if card_msg_id:
            self._thinking_cards[chat_id] = card_msg_id

    async def _send_thinking_card(
        self, chat_id: str, reply_to: str | None = None,
    ) -> str | None:
        """发送"思考中..."交互卡片，返回卡片 message_id。"""
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": "💭 **思考中...**"},
            ],
        }
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
                return response.data.message_id
            logger.debug(f"Feishu: thinking card failed: {response.msg}")
        except Exception as e:
            logger.debug(f"Feishu: _send_thinking_card error: {e}")
        return None

    async def _patch_card_content(self, message_id: str, new_content: str) -> bool:
        """通过 PATCH API 将占位卡片更新为最终回复内容。"""
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": new_content},
            ],
        }
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

            # 发送已读回执（表情回复，fire-and-forget）
            if msg_id:
                asyncio.create_task(self.add_reaction(msg_id))

            # 记录最近用户消息 ID，供 send_typing 回复定位
            chat_id = msg_dict.get("chat_id")
            if chat_id and msg_id:
                self._last_user_msg[chat_id] = msg_id

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

        # 1) 停止 WS 线程的事件循环 → SDK 的 ws_client.start() 会退出阻塞
        ws_loop = self._ws_loop
        if ws_loop is not None:
            try:
                ws_loop.call_soon_threadsafe(ws_loop.stop)
            except Exception:
                pass

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

            if msg_id:
                asyncio.create_task(self.add_reaction(msg_id))

            unified = await self._convert_message(message, sender)
            self._log_message(unified)
            await self._emit_message(unified)

        except Exception as e:
            logger.error(f"Error handling message event: {e}")

    async def _convert_message(self, message: dict, sender: dict) -> UnifiedMessage:
        """将飞书消息转换为统一格式"""
        content = MessageContent()

        msg_type = message.get("message_type")
        msg_content = json.loads(message.get("content", "{}"))

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
            # 富文本
            content.text = self._parse_post_content(msg_content)

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

    def _parse_post_content(self, post: dict) -> str:
        """解析富文本内容

        飞书 post 消息的 content JSON 格式为：
        {"post": {"zh_cn": {"title": "...", "content": [[...]]}}}
        需要先提取语言层再解析具体内容。
        """
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

        if not isinstance(body, dict):
            return str(body) if body else ""

        result = []

        title = body.get("title", "")
        if title:
            result.append(title)

        for paragraph in body.get("content", []):
            line_parts = []
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
                elif tag == "media":
                    line_parts.append(f"[视频:{item.get('file_key', '')}]")
                elif tag == "emotion":
                    line_parts.append(item.get("emoji_type", ""))
            if line_parts:
                result.append("".join(line_parts))

        return "\n".join(result)

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息"""
        if not self._client:
            raise RuntimeError("Feishu client not started")

        # ---- 思考卡片处理：尝试 PATCH 占位卡片为最终回复 ----
        thinking_card_id = self._thinking_cards.pop(message.chat_id, None)
        if thinking_card_id:
            text = message.content.text or ""
            if text and not message.content.has_media:
                try:
                    if await self._patch_card_content(thinking_card_id, text):
                        return thinking_card_id
                except Exception as e:
                    logger.warning(f"Feishu: patch thinking card failed: {e}")
            with contextlib.suppress(Exception):
                await self._delete_feishu_message(thinking_card_id)

        reply_target = message.reply_to or message.thread_id

        # 语音/文件/视频：委托给专用方法，避免 fallthrough 到空文本
        if message.content.voices and message.content.voices[0].local_path:
            return await self.send_voice(
                message.chat_id, message.content.voices[0].local_path,
                message.content.text, reply_to=reply_target,
            )
        if message.content.files and message.content.files[0].local_path:
            return await self.send_file(
                message.chat_id, message.content.files[0].local_path,
                message.content.text, reply_to=reply_target,
            )
        if message.content.videos and message.content.videos[0].local_path:
            return await self.send_file(
                message.chat_id, message.content.videos[0].local_path,
                message.content.text, reply_to=reply_target,
            )

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
            return response.data.message_id

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

        return response.data.message_id

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
        """上传图片"""
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
                None, lambda: self._client.im.v1.image.create(request)
            )

            if not response.success():
                raise RuntimeError(f"Failed to upload image: {response.msg}")

            return response.data.image_key

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

        # 保存文件
        local_path = self.media_dir / media.filename
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

        return response.data.message_id

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

        return response.data.message_id

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

        message_id = response.data.message_id

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent photo to {chat_id}: {photo_path}")
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

        message_id = response.data.message_id

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent file to {chat_id}: {file_path}")
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

        message_id = response.data.message_id

        if caption:
            await self._send_text(chat_id, caption, reply_to=reply_to)

        logger.info(f"Sent voice to {chat_id}: {voice_path}")
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

        return response.data.message_id

    async def _upload_file(self, path: str) -> str:
        """上传文件到飞书"""
        file_name = Path(path).name

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
                None, lambda: self._client.im.v1.file.create(request)
            )

            if not response.success():
                raise RuntimeError(f"Failed to upload file: {response.msg}")

            return response.data.file_key

    def build_simple_card(
        self,
        title: str,
        content: str,
        buttons: list[dict] | None = None,
    ) -> dict:
        """
        构建简单卡片

        Args:
            title: 标题
            content: 内容
            buttons: 按钮列表 [{"text": "按钮文字", "value": "回调值"}]

        Returns:
            卡片 JSON
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
                actions.append(
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": btn["text"]},
                        "type": "primary",
                        "value": {"action": btn.get("value", btn["text"])},
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
