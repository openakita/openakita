"""
消息网关

统一消息入口/出口:
- 消息路由
- 会话管理集成
- 媒体预处理（图片、语音、视频）
- Agent 调用
- 消息中断机制（支持在工具调用间隙插入新消息）
- 系统级命令拦截（模型切换等）
"""

import asyncio
import base64
import contextlib
import logging
import os
import random
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..sessions import Session, SessionManager
from .base import ChannelAdapter
from .group_response import GroupResponseMode, SmartModeThrottle
from .types import OutgoingMessage, UnifiedMessage


def _notify_im_event(event: str, data: dict | None = None) -> None:
    """Fire-and-forget WS broadcast for IM events."""
    try:
        from openakita.api.routes.websocket import broadcast_event
        asyncio.ensure_future(broadcast_event(event, data))
    except Exception:
        pass

if TYPE_CHECKING:
    from ..core.brain import Brain
    from ..llm.stt_client import STTClient

logger = logging.getLogger(__name__)

# Agent 处理函数类型
AgentHandler = Callable[[Session, str], Awaitable[str]]


class InterruptPriority(Enum):
    """中断优先级"""

    NORMAL = 0  # 普通消息，排队等待
    HIGH = 1  # 高优先级，在工具间隙插入
    URGENT = 2  # 紧急，尝试立即中断


@dataclass
class InterruptMessage:
    """中断消息封装"""

    message: UnifiedMessage
    priority: InterruptPriority = InterruptPriority.HIGH
    timestamp: datetime = field(default_factory=datetime.now)

    def __lt__(self, other: "InterruptMessage") -> bool:
        """优先级队列比较：优先级高的先处理，同优先级按时间"""
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value
        return self.timestamp < other.timestamp


# ==================== 模型切换命令处理 ====================


@dataclass
class ModelSwitchSession:
    """模型切换交互会话"""

    session_key: str
    mode: str  # "switch" | "priority" | "restore"
    step: str  # "select" | "confirm"
    selected_model: str | None = None
    selected_priority: list[str] | None = None
    started_at: datetime = field(default_factory=datetime.now)
    timeout_minutes: int = 5

    @property
    def is_expired(self) -> bool:
        """检查会话是否已超时"""
        return datetime.now() > self.started_at + timedelta(minutes=self.timeout_minutes)


class ModelCommandHandler:
    """
    模型命令处理器

    系统级命令拦截，不经过大模型处理，确保即使模型崩溃也能切换。

    支持的命令:
    - /model: 显示当前模型和可用列表
    - /switch [模型名]: 临时切换模型（12小时）
    - /priority: 调整模型优先级（永久）
    - /restore: 恢复默认模型
    - /cancel: 取消当前操作
    """

    # 命令列表
    MODEL_COMMANDS = {"/model", "/switch", "/priority", "/restore", "/cancel"}

    def __init__(self, brain: Optional["Brain"] = None):
        self._brain: Brain | None = brain
        # 进行中的切换会话 {session_key: ModelSwitchSession}
        self._switch_sessions: dict[str, ModelSwitchSession] = {}

    def set_brain(self, brain: "Brain") -> None:
        """设置 Brain 实例"""
        self._brain = brain

    def is_model_command(self, text: str) -> bool:
        """检查是否是模型相关命令"""
        if not text:
            return False
        text_lower = text.lower().strip()
        # 完整命令或带参数的命令
        for cmd in self.MODEL_COMMANDS:
            if text_lower == cmd or text_lower.startswith(cmd + " "):
                return True
        return False

    def is_in_session(self, session_key: str) -> bool:
        """检查是否在交互会话中"""
        if session_key not in self._switch_sessions:
            return False
        session = self._switch_sessions[session_key]
        if session.is_expired:
            del self._switch_sessions[session_key]
            return False
        return True

    async def handle_command(self, session_key: str, text: str) -> str | None:
        """
        处理模型命令

        Args:
            session_key: 会话标识
            text: 用户输入

        Returns:
            响应文本，如果不是命令返回 None
        """
        if not self._brain:
            return "❌ 模型管理功能未初始化"

        text = text.strip()
        text_lower = text.lower()

        # /model - 显示当前模型状态
        if text_lower == "/model":
            return self._format_model_status()

        # /switch - 切换模型
        if text_lower == "/switch":
            return self._start_switch_session(session_key)

        if text_lower.startswith("/switch "):
            model_name = text[8:].strip()
            return self._start_switch_session(session_key, model_name)

        # /priority - 调整优先级
        if text_lower == "/priority":
            return self._start_priority_session(session_key)

        # /restore - 恢复默认
        if text_lower == "/restore":
            return self._start_restore_session(session_key)

        # /cancel - 取消操作
        if text_lower == "/cancel":
            return self._cancel_session(session_key)

        return None

    async def handle_input(self, session_key: str, text: str) -> str:
        """
        处理交互会话中的用户输入

        Args:
            session_key: 会话标识
            text: 用户输入

        Returns:
            响应文本
        """
        if not self._brain:
            return "❌ 模型管理功能未初始化"

        # 检查是否取消
        if text.lower().strip() == "/cancel":
            return self._cancel_session(session_key)

        session = self._switch_sessions.get(session_key)
        if not session:
            return "会话已结束"

        if session.is_expired:
            del self._switch_sessions[session_key]
            return "⏰ 操作超时（5分钟），已自动取消"

        # 根据模式和步骤处理
        if session.mode == "switch":
            return self._handle_switch_input(session_key, session, text)
        elif session.mode == "priority":
            return self._handle_priority_input(session_key, session, text)
        elif session.mode == "restore":
            return self._handle_restore_input(session_key, session, text)

        return "未知操作"

    def _format_model_status(self) -> str:
        """格式化模型状态信息"""
        models = self._brain.list_available_models()
        override = self._brain.get_override_status()

        lines = ["📋 **模型状态**\n"]

        for i, m in enumerate(models):
            status = ""
            if m["is_current"]:
                status = " ⬅️ 当前（临时）" if m["is_override"] else " ⬅️ 当前"
            health = "✅" if m["is_healthy"] else "❌"
            lines.append(f"{i + 1}. {health} **{m['name']}** ({m['model']}){status}")

        if override:
            lines.append(f"\n⏱️ 临时切换剩余: {override['remaining_hours']:.1f} 小时")
            lines.append(f"   到期时间: {override['expires_at']}")

        lines.append("\n💡 命令: /switch 切换 | /priority 调整优先级 | /restore 恢复默认")

        return "\n".join(lines)

    def _start_switch_session(self, session_key: str, model_name: str = "") -> str:
        """开始切换会话"""
        models = self._brain.list_available_models()

        # 如果指定了模型名，跳到确认步骤
        if model_name:
            # 查找模型
            target = None
            for m in models:
                if (
                    m["name"].lower() == model_name.lower()
                    or m["model"].lower() == model_name.lower()
                ):
                    target = m
                    break

            if not target:
                # 尝试数字索引
                try:
                    idx = int(model_name) - 1
                    if 0 <= idx < len(models):
                        target = models[idx]
                except ValueError:
                    pass

            if not target:
                available = ", ".join(m["name"] for m in models)
                return f"❌ 未找到模型 '{model_name}'\n可用模型: {available}"

            # 创建会话并进入确认步骤
            self._switch_sessions[session_key] = ModelSwitchSession(
                session_key=session_key,
                mode="switch",
                step="confirm",
                selected_model=target["name"],
            )

            return (
                f"⚠️ 确认切换到 **{target['name']}** ({target['model']})?\n\n"
                f"临时切换有效期: 12小时\n"
                f"输入 **yes** 确认，其他任意内容取消"
            )

        # 没有指定模型，显示选择列表
        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="switch",
            step="select",
        )

        lines = ["📋 **可用模型**\n"]
        for i, m in enumerate(models):
            status = " ⬅️ 当前" if m["is_current"] else ""
            health = "✅" if m["is_healthy"] else "❌"
            lines.append(f"{i + 1}. {health} **{m['name']}** ({m['model']}){status}")

        lines.append("\n请输入数字或模型名称选择，/cancel 取消")

        return "\n".join(lines)

    def _start_priority_session(self, session_key: str) -> str:
        """开始优先级调整会话"""
        models = self._brain.list_available_models()

        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="priority",
            step="select",
        )

        lines = ["📋 **当前优先级** (数字越小越优先)\n"]
        for i, m in enumerate(models):
            lines.append(f"{i}. {m['name']}")

        lines.append("\n请按顺序输入模型名称，用空格分隔")
        lines.append("例如: claude kimi dashscope minimax")
        lines.append("/cancel 取消")

        return "\n".join(lines)

    def _start_restore_session(self, session_key: str) -> str:
        """开始恢复默认会话"""
        override = self._brain.get_override_status()

        if not override:
            return "当前没有临时切换，已在使用默认模型"

        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="restore",
            step="confirm",
        )

        return (
            f"⚠️ 确认恢复默认模型?\n\n"
            f"当前临时使用: {override['endpoint_name']}\n"
            f"剩余时间: {override['remaining_hours']:.1f} 小时\n\n"
            f"输入 **yes** 确认，其他任意内容取消"
        )

    def _cancel_session(self, session_key: str) -> str:
        """取消当前会话"""
        if session_key in self._switch_sessions:
            del self._switch_sessions[session_key]
            return "✅ 操作已取消"
        return "没有进行中的操作"

    def _handle_switch_input(self, session_key: str, session: ModelSwitchSession, text: str) -> str:
        """处理切换会话的输入"""
        text = text.strip()

        if session.step == "select":
            models = self._brain.list_available_models()
            target = None

            # 尝试数字索引
            try:
                idx = int(text) - 1
                if 0 <= idx < len(models):
                    target = models[idx]
            except ValueError:
                # 尝试名称匹配
                for m in models:
                    if m["name"].lower() == text.lower() or m["model"].lower() == text.lower():
                        target = m
                        break

            if not target:
                return f"❌ 未找到模型 '{text}'，请重新输入或 /cancel 取消"

            # 进入确认步骤
            session.selected_model = target["name"]
            session.step = "confirm"

            return (
                f"⚠️ 确认切换到 **{target['name']}** ({target['model']})?\n\n"
                f"临时切换有效期: 12小时\n"
                f"输入 **yes** 确认，其他任意内容取消"
            )

        elif session.step == "confirm":
            if text.lower() == "yes":
                # 执行切换
                success, msg = self._brain.switch_model(
                    session.selected_model, conversation_id=session_key
                )
                del self._switch_sessions[session_key]

                if success:
                    return f"✅ {msg}\n\n发送 /model 查看状态"
                else:
                    return f"❌ 切换失败: {msg}"
            else:
                del self._switch_sessions[session_key]
                return "✅ 操作已取消"

        return "未知步骤"

    def _handle_priority_input(
        self, session_key: str, session: ModelSwitchSession, text: str
    ) -> str:
        """处理优先级调整的输入"""
        text = text.strip()

        if session.step == "select":
            models = self._brain.list_available_models()
            model_names = {m["name"].lower(): m["name"] for m in models}

            # 解析用户输入
            input_names = text.split()
            priority_order = []

            for name in input_names:
                name_lower = name.lower()
                if name_lower in model_names:
                    priority_order.append(model_names[name_lower])
                else:
                    return f"❌ 未找到模型 '{name}'，请重新输入或 /cancel 取消"

            if len(priority_order) != len(models):
                return f"❌ 请输入所有 {len(models)} 个模型的顺序"

            # 进入确认步骤
            session.selected_priority = priority_order
            session.step = "confirm"

            lines = ["⚠️ 确认调整优先级为:\n"]
            for i, name in enumerate(priority_order):
                lines.append(f"{i}. {name}")
            lines.append("\n**这是永久更改！** 输入 **yes** 确认")

            return "\n".join(lines)

        elif session.step == "confirm":
            if text.lower() == "yes":
                # 执行优先级更新
                success, msg = self._brain.update_model_priority(session.selected_priority)
                del self._switch_sessions[session_key]

                if success:
                    return f"✅ {msg}"
                else:
                    return f"❌ 更新失败: {msg}"
            else:
                del self._switch_sessions[session_key]
                return "✅ 操作已取消"

        return "未知步骤"

    def _handle_restore_input(
        self, session_key: str, session: ModelSwitchSession, text: str
    ) -> str:
        """处理恢复默认的输入"""
        if text.lower() == "yes":
            success, msg = self._brain.restore_default_model(conversation_id=session_key)
            del self._switch_sessions[session_key]

            if success:
                return f"✅ {msg}"
            else:
                return f"❌ {msg}"
        else:
            del self._switch_sessions[session_key]
            return "✅ 操作已取消"


# ==================== 思考模式命令处理 ====================


class ThinkingCommandHandler:
    """
    思考模式命令处理器

    系统级命令拦截，不经过大模型处理。

    支持的命令:
    - /thinking [on|off|auto]: 切换思考模式
    - /thinking_depth [low|medium|high]: 设置思考深度
    - /chain [on|off]: 开关思维链进度推送（默认关闭）
    """

    THINKING_COMMANDS = {"/thinking", "/thinking_depth", "/chain"}

    VALID_MODES = {"on", "off", "auto"}
    VALID_DEPTHS = {"low", "medium", "high"}

    DEPTH_LABELS = {
        "low": "低（快速响应）",
        "medium": "中（平衡）",
        "high": "高（深度推理）",
    }

    def __init__(self, session_manager: "SessionManager"):
        self._session_manager = session_manager

    def is_thinking_command(self, text: str) -> bool:
        """检查是否是思考模式相关命令"""
        if not text:
            return False
        text_lower = text.lower().strip()
        for cmd in self.THINKING_COMMANDS:
            if text_lower == cmd or text_lower.startswith(cmd + " "):
                return True
        return False

    async def handle_command(self, session_key: str, text: str, session: "Session") -> str | None:
        """
        处理思考模式命令

        Args:
            session_key: 会话标识
            text: 用户输入
            session: 当前会话对象

        Returns:
            响应文本
        """
        text = text.strip()
        text_lower = text.lower()

        # /chain - 查看或设置思维链推送开关
        if text_lower == "/chain":
            return self._format_chain_status(session)

        if text_lower.startswith("/chain "):
            value = text_lower.split(None, 1)[1].strip()
            if value not in {"on", "off"}:
                return f"❌ 无效的参数: `{value}`\n可选: `on`（开启推送）| `off`（关闭推送）"
            enabled = value == "on"
            session.set_metadata("chain_push", enabled)
            label = "开启" if enabled else "关闭"
            return f"✅ 思维链进度推送已 **{label}**"

        # /thinking - 查看或设置思考模式
        if text_lower == "/thinking":
            return self._format_thinking_status(session)

        if text_lower.startswith("/thinking ") and not text_lower.startswith("/thinking_depth"):
            mode = text_lower.split(None, 1)[1].strip()
            if mode not in self.VALID_MODES:
                return f"❌ 无效的思考模式: `{mode}`\n可选: `on`（开启）| `off`（关闭）| `auto`（自动）"
            session.set_metadata("thinking_mode", mode if mode != "auto" else None)
            mode_label = {"on": "开启", "off": "关闭", "auto": "自动（系统决定）"}
            return f"✅ 思考模式已设置为: **{mode_label[mode]}**"

        # /thinking_depth - 查看或设置思考深度
        if text_lower == "/thinking_depth":
            return self._format_depth_status(session)

        if text_lower.startswith("/thinking_depth "):
            depth = text_lower.split(None, 1)[1].strip()
            if depth not in self.VALID_DEPTHS:
                return f"❌ 无效的思考深度: `{depth}`\n可选: `low`（低）| `medium`（中）| `high`（高）"
            session.set_metadata("thinking_depth", depth)
            return f"✅ 思考深度已设置为: **{self.DEPTH_LABELS[depth]}**"

        return None

    def _format_chain_status(self, session: "Session") -> str:
        """格式化思维链推送状态"""
        from openakita.config import settings

        current = session.get_metadata("chain_push")
        if current is None:
            current = settings.im_chain_push
            source = "（跟随全局默认）"
        else:
            source = "（会话级设置）"

        label = "开启" if current else "关闭"

        lines = [
            "📡 **思维链进度推送**\n",
            f"当前状态: **{label}** {source}\n",
            "开启后，处理消息时会实时推送思考过程、工具调用进度等中间状态。",
            "关闭不影响内部推理和数据保存，仅减少消息推送。\n",
            "**可用命令:**",
            "`/chain on` — 开启进度推送",
            "`/chain off` — 关闭进度推送",
        ]
        return "\n".join(lines)

    def _format_thinking_status(self, session: "Session") -> str:
        """格式化思考模式状态"""
        current_mode = session.get_metadata("thinking_mode")
        current_depth = session.get_metadata("thinking_depth")

        mode_label = "自动（系统决定）"
        if current_mode == "on":
            mode_label = "开启"
        elif current_mode == "off":
            mode_label = "关闭"

        depth_label = self.DEPTH_LABELS.get(current_depth or "medium", "中（平衡）")

        lines = [
            "🧠 **思考模式设置**\n",
            f"当前模式: **{mode_label}**",
            f"思考深度: **{depth_label}**\n",
            "**可用命令:**",
            "`/thinking on` — 强制开启深度思考",
            "`/thinking off` — 关闭深度思考",
            "`/thinking auto` — 自动决定（默认）",
            "`/thinking_depth low|medium|high` — 设置思考深度",
        ]
        return "\n".join(lines)

    def _format_depth_status(self, session: "Session") -> str:
        """格式化思考深度状态"""
        current_depth = session.get_metadata("thinking_depth")
        depth_label = self.DEPTH_LABELS.get(current_depth or "medium", "中（平衡）")

        lines = [
            "📊 **思考深度设置**\n",
            f"当前深度: **{depth_label}**\n",
        ]
        for key, label in self.DEPTH_LABELS.items():
            marker = " ⬅️" if key == (current_depth or "medium") else ""
            lines.append(f"• `{key}` — {label}{marker}")
        lines.append("\n用法: `/thinking_depth low|medium|high`")
        return "\n".join(lines)


# ==================== 终极重启命令处理 ====================


@dataclass
class RestartSession:
    """重启确认会话"""

    session_key: str
    confirm_code: str
    message: UnifiedMessage
    started_at: datetime = field(default_factory=datetime.now)
    timeout_seconds: int = 60

    @property
    def is_expired(self) -> bool:
        return datetime.now() > self.started_at + timedelta(seconds=self.timeout_seconds)

    @property
    def remaining_seconds(self) -> int:
        elapsed = (datetime.now() - self.started_at).total_seconds()
        return max(0, int(self.timeout_seconds - elapsed))


class RestartCommandHandler:
    """
    终极重启命令处理器

    在 _on_message 最早期拦截，确保即使系统卡死也能响应。
    流程：/restart → 生成确认码 → 用户回传确认码 → 触发重启。
    支持倒计时自动取消和手动取消。
    """

    RESTART_COMMANDS = {"/restart", "/重启"}
    CANCEL_COMMANDS = {"/cancel_restart", "/取消重启"}
    CONFIRM_TIMEOUT = 60

    def __init__(self) -> None:
        self._pending: dict[str, RestartSession] = {}
        self._timeout_tasks: dict[str, asyncio.Task] = {}
        # 由 MessageGateway 注入
        self._send_feedback_fn: Callable[
            [UnifiedMessage, str], Awaitable[None]
        ] | None = None
        self._shutdown_event: asyncio.Event | None = None

    # ---------- 命令识别 ----------

    def is_restart_command(self, text: str) -> bool:
        return text.strip().lower() in self.RESTART_COMMANDS

    def is_cancel_command(self, text: str) -> bool:
        return text.strip().lower() in self.CANCEL_COMMANDS

    def has_pending_session(self, session_key: str) -> bool:
        """检查该用户是否有待确认的重启会话"""
        session = self._pending.get(session_key)
        if session is None:
            return False
        if session.is_expired:
            self._cleanup(session_key)
            return False
        return True

    def is_confirm_code(self, session_key: str, text: str) -> bool:
        """检查文本是否可能是重启确认码（纯6位数字）"""
        session = self._pending.get(session_key)
        if session is None:
            return False
        return text.strip().isdigit() and len(text.strip()) == 6

    # ---------- 核心流程 ----------

    async def handle_restart_command(
        self, session_key: str, message: UnifiedMessage,
    ) -> None:
        """处理 /restart 命令：生成确认码并发送给用户"""
        if session_key in self._pending:
            old = self._pending[session_key]
            await self._send(
                message,
                f"⚠️ 已有一个待确认的重启请求（确认码 **{old.confirm_code}**，"
                f"剩余 {old.remaining_seconds}s）。\n"
                f"发送确认码以确认，或 /cancel_restart 取消。",
            )
            return

        code = f"{random.randint(0, 999999):06d}"
        session = RestartSession(
            session_key=session_key,
            confirm_code=code,
            message=message,
            timeout_seconds=self.CONFIRM_TIMEOUT,
        )
        self._pending[session_key] = session

        timeout_task = asyncio.create_task(self._timeout_handler(session_key))
        self._timeout_tasks[session_key] = timeout_task

        logger.warning(
            f"[Restart] Restart requested by {session_key}, "
            f"confirm_code={code}, timeout={self.CONFIRM_TIMEOUT}s"
        )

        await self._send(
            message,
            f"🔄 **服务重启确认**\n\n"
            f"确认码: `{code}`\n\n"
            f"请在 **{self.CONFIRM_TIMEOUT} 秒** 内回复此确认码以执行重启。\n"
            f"发送 `/cancel_restart` 取消重启。",
        )

    async def handle_pending_input(
        self, session_key: str, message: UnifiedMessage,
    ) -> bool:
        """
        处理待确认会话中的用户输入。

        Returns:
            True  — 输入已被消费（调用方应 return，不继续处理）
            False — 输入与重启无关，调用方应放行给正常流程
        """
        text = (message.plain_text or "").strip()
        session = self._pending.get(session_key)
        if session is None:
            return False

        # 取消
        if text.lower() in self.CANCEL_COMMANDS or text.lower() == "/cancel":
            self._cleanup(session_key)
            logger.info(f"[Restart] Cancelled by user: {session_key}")
            await self._send(message, "❌ 重启已取消。")
            return True

        # 验证确认码
        if text == session.confirm_code:
            self._cleanup(session_key)
            logger.warning(f"[Restart] Confirmed by {session_key}, triggering restart...")
            await self._send(message, "✅ 确认码正确，服务将在 3 秒后重启…")
            await asyncio.sleep(3)
            await self._trigger_restart()
            return True

        # 6位数字但不匹配 → 提示错误
        if text.isdigit() and len(text) == 6:
            await self._send(
                message,
                f"❌ 确认码不正确（剩余 {session.remaining_seconds}s）。\n"
                f"请发送 `{session.confirm_code}` 或 `/cancel_restart` 取消。",
            )
            return True

        # 非数字输入 → 不消费，放行给正常流程（避免误拦截普通消息）
        return False

    # ---------- 超时处理 ----------

    async def _timeout_handler(self, session_key: str) -> None:
        session = self._pending.get(session_key)
        if session is None:
            return
        try:
            await asyncio.sleep(session.timeout_seconds)
        except asyncio.CancelledError:
            return

        if session_key in self._pending:
            msg = self._pending[session_key].message
            self._cleanup(session_key)
            logger.info(f"[Restart] Timed out for {session_key}")
            await self._send(msg, "⏰ 重启确认已超时，已自动取消。")

    # ---------- 重启触发 ----------

    async def _trigger_restart(self) -> None:
        from openakita import config as cfg

        cfg._restart_requested = True
        if self._shutdown_event is not None:
            logger.warning("[Restart] Setting shutdown_event for graceful restart")
            self._shutdown_event.set()
        else:
            logger.error("[Restart] No shutdown_event available, restart may not work")

    # ---------- 辅助 ----------

    def _cleanup(self, session_key: str) -> None:
        self._pending.pop(session_key, None)
        task = self._timeout_tasks.pop(session_key, None)
        if task and not task.done():
            task.cancel()

    async def _send(self, message: UnifiedMessage, text: str) -> None:
        if self._send_feedback_fn:
            await self._send_feedback_fn(message, text)
        else:
            logger.warning(f"[Restart] No feedback function, cannot send: {text}")


class MessageGateway:
    """
    统一消息网关

    职责:
    - 管理多个通道适配器
    - 将收到的消息路由到会话
    - 调用 Agent 处理
    - 将回复发送回通道
    """

    # 支持 .en 专用模型的 Whisper 尺寸（large 无 .en 变体）
    _EN_MODEL_SIZES = {"tiny", "base", "small", "medium"}

    def __init__(
        self,
        session_manager: SessionManager,
        agent_handler: AgentHandler | None = None,
        whisper_model: str = "base",
        whisper_language: str = "zh",
        stt_client: "STTClient | None" = None,
    ):
        """
        Args:
            session_manager: 会话管理器
            agent_handler: Agent 处理函数 (session, message) -> response
            whisper_model: Whisper 模型大小 (tiny, base, small, medium, large)，默认 base
            whisper_language: 语音识别语言 (zh/en/auto/其他语言代码)
            stt_client: 在线 STT 客户端（可选，用于替代本地 Whisper）
        """
        self.session_manager = session_manager
        self.agent_handler = agent_handler
        self.agent_handler_stream = None  # set by main.py for streaming IM support
        self.stt_client = stt_client

        from .bot_config import BotConfigStore
        self.bot_config = BotConfigStore()

        from .chat_aliases import ChatAliasStore
        self.chat_aliases = ChatAliasStore()

        # 注册的适配器 {channel_name: adapter}
        self._adapters: dict[str, ChannelAdapter] = {}

        # 消息处理队列
        self._message_queue: asyncio.Queue[UnifiedMessage] = asyncio.Queue()

        # 处理任务
        self._processing_task: asyncio.Task | None = None
        self._running = False
        self._accepting = True  # False = drain 模式，拒绝新消息
        self._started_adapters: list[str] = []
        self._failed_adapters: list[str] = []

        # 中间件
        self._pre_process_hooks: list[Callable[[UnifiedMessage], Awaitable[UnifiedMessage]]] = []
        self._post_process_hooks: list[Callable[[UnifiedMessage, str], Awaitable[str]]] = []

        # Whisper 语音识别模型（延迟加载或启动时预加载）
        self._whisper_language = whisper_language.lower().strip()
        # 英语且模型尺寸有 .en 变体时，自动切换到更小更快的 .en 模型
        if self._whisper_language == "en" and whisper_model in self._EN_MODEL_SIZES:
            self._whisper_model_name = f"{whisper_model}.en"
            logger.info(
                f"Whisper language=en → auto-selected English-only model: "
                f"{self._whisper_model_name}"
            )
        else:
            self._whisper_model_name = whisper_model
        self._whisper = None
        self._whisper_loaded = False
        self._whisper_unavailable = False  # ImportError → 本进程内不再重试

        # ==================== 消息中断机制 ====================
        # 会话级中断队列 {session_key: asyncio.PriorityQueue[InterruptMessage]}
        self._interrupt_queues: dict[str, asyncio.PriorityQueue] = {}

        # 正在处理的会话 {session_key: bool}
        self._processing_sessions: dict[str, bool] = {}

        # 并发会话控制
        _max_concurrent = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "5"))
        self._concurrency_sem = asyncio.Semaphore(_max_concurrent)
        self._session_tasks: dict[str, asyncio.Task] = {}

        # 中断锁（防止并发修改）
        self._interrupt_lock = asyncio.Lock()

        # 中断处理回调（由 Agent 设置）
        self._interrupt_callbacks: dict[str, Callable[[], Awaitable[str | None]]] = {}

        # 模型命令处理器（系统级命令拦截）
        self._model_cmd_handler: ModelCommandHandler = ModelCommandHandler()

        # 思考模式命令处理器
        self._thinking_cmd_handler: ThinkingCommandHandler = ThinkingCommandHandler(session_manager)

        # 终极重启命令处理器（在 _on_message 最早期拦截，不经过队列/Agent）
        self._restart_cmd_handler: RestartCommandHandler = RestartCommandHandler()
        self._restart_cmd_handler._send_feedback_fn = self._send_feedback

        # 外部注入的 shutdown_event（由 main.py 调用 set_shutdown_event 设置）
        self._shutdown_event: asyncio.Event | None = None

        # ==================== 进度事件流（Plan/Deliver 等）====================
        # 目标：把“执行过程进度展示”下沉到网关侧，避免模型/工具刷屏。
        self._progress_buffers: dict[str, list[str]] = {}  # session_key -> [lines]
        self._progress_flush_tasks: dict[str, asyncio.Task] = {}  # session_key -> flush task
        self._progress_throttle_seconds: float = 2.0  # 默认节流窗口
        self._progress_card_accum: dict[str, list[str]] = {}  # session_key -> accumulated progress lines (for card PATCH)

        # ==================== 群聊响应策略 ====================
        self._smart_throttle = SmartModeThrottle()

    async def _handle_feishu_command(self, cmd: str, message: "UnifiedMessage") -> str | None:
        """Handle ``/feishu start|auth|help``."""
        parts = cmd.split()
        sub = parts[1] if len(parts) > 1 else ""

        adapter = self._adapters.get(message.channel)

        if sub == "start":
            if adapter and hasattr(adapter, "get_status_info"):
                info = adapter.get_status_info()
                lines = [
                    f"OpenAkita Feishu Adapter v{info['version']}",
                    f"App ID: {info['app_id']}",
                    f"Connected: {'Yes' if info['connected'] else 'No'}",
                    f"Streaming: {'ON' if info['streaming_enabled'] else 'OFF'}"
                    + (f" (group: {'ON' if info['group_streaming'] else 'OFF'})" if info['streaming_enabled'] else ""),
                    f"Group mode: {info['group_response_mode']}",
                ]
                return "\n".join(lines)
            return "Feishu adapter not available"

        if sub == "auth":
            if adapter and hasattr(adapter, "get_auth_url"):
                url = adapter.get_auth_url()
                return f"请在浏览器中打开以下链接完成飞书用户授权：\n{url}"
            return "Feishu adapter not available"

        # /feishu 或 /feishu help
        return (
            "/feishu start — 查看适配器状态与版本\n"
            "/feishu auth  — 获取飞书用户授权链接\n"
            "/feishu help  — 显示本帮助"
        )

    async def _handle_mode_command(self, user_text: str) -> str:
        """
        处理 /模式 或 /mode 命令：查看和切换单/多Agent模式。

        用法:
          /模式           — 查看当前模式
          /模式 开启      — 开启多Agent模式
          /模式 关闭      — 关闭多Agent模式
          /mode on|off   — 同上英文版
        """
        from ..config import runtime_state, settings

        parts = user_text.strip().split(None, 1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""

        ON_ARGS = {"开启", "on", "true", "1", "multi"}
        OFF_ARGS = {"关闭", "off", "false", "0", "single"}

        if arg in ON_ARGS:
            if settings.multi_agent_enabled:
                return "ℹ️ 多Agent模式 (Beta) 已经是开启状态。"
            settings.multi_agent_enabled = True
            runtime_state.save()
            logger.info("[Mode] multi_agent_enabled toggled ON via IM command")
            # Deploy system presets on first enable
            try:
                from openakita.agents.presets import ensure_presets_on_mode_enable
                ensure_presets_on_mode_enable(settings.data_dir / "agents")
            except Exception as e:
                logger.warning(f"[Gateway] Failed to deploy presets: {e}")
            # Initialize orchestrator — rollback on failure
            try:
                from openakita.main import _init_orchestrator
                await _init_orchestrator()
            except Exception as e:
                logger.error(f"[Gateway] Failed to init orchestrator, rolling back: {e}")
                settings.multi_agent_enabled = False
                runtime_state.save()
                return f"❌ 多Agent模式开启失败（Orchestrator 初始化出错: {e}）\n已回滚到单Agent模式。"
            return "✅ 已切换到 **多Agent模式 (Beta)**\n新消息将通过多Agent系统处理。"

        if arg in OFF_ARGS:
            if not settings.multi_agent_enabled:
                return "ℹ️ 当前已经是单Agent模式。"
            settings.multi_agent_enabled = False
            runtime_state.save()
            logger.info("[Mode] multi_agent_enabled toggled OFF via IM command")
            return "✅ 已切换到 **单Agent模式**\n新消息将由默认Agent直接处理。"

        current = settings.multi_agent_enabled
        mode_label = "多Agent模式 (Beta)" if current else "单Agent模式"
        lines = [
            f"🔧 **当前模式: {mode_label}**\n",
            "用法:",
            "  `/模式 开启` — 切换到多Agent模式 (Beta)",
            "  `/模式 关闭` — 切换到单Agent模式",
        ]
        return "\n".join(lines)

    def _is_agent_command(self, text: str) -> bool:
        """检查是否是多Agent相关命令"""
        if not text:
            return False
        t = text.strip().lower()
        if t in ("/help", "/帮助", "/状态", "/status", "/重置", "/agent_reset"):
            return True
        if t in ("/切换", "/switch") or t.startswith(("/切换 ", "/switch ")):
            return True
        return False

    async def _handle_agent_command(
        self, message: UnifiedMessage, user_text: str
    ) -> str | None:
        """
        处理多Agent相关命令。仅当 multi_agent_enabled 时执行；否则返回提示。

        支持: /切换 /switch /help /帮助 /状态 /status /重置 /agent_reset
        """
        from ..config import settings

        if not settings.multi_agent_enabled:
            t = user_text.strip().lower()
            # Don't intercept generic commands in single-agent mode
            if t in ("/help", "/帮助", "/状态", "/status"):
                return None
            return "多Agent模式未开启。发送 `/模式 开启` 开启。"

        session = self.session_manager.get_session(
            channel=message.channel,
            chat_id=message.chat_id,
            user_id=message.user_id,
            thread_id=message.thread_id,
        )
        if not session:
            return "❌ 无法获取会话"

        self._apply_bot_agent_profile(session, message.channel)

        t = user_text.strip().lower()

        # /切换 或 /switch [agent_id]
        if t in ("/切换", "/switch") or t.startswith(("/切换 ", "/switch ")):
            return await self._handle_agent_switch(session, t)

        # /help 或 /帮助
        if t in ("/help", "/帮助"):
            return self._format_agent_help()

        # /状态 或 /status
        if t in ("/状态", "/status"):
            return self._format_agent_status(session)

        # /重置 或 /agent_reset
        if t in ("/重置", "/agent_reset"):
            return self._handle_agent_reset(session)

        return None

    async def _handle_agent_switch(self, session: Session, user_text: str) -> str:
        """处理 /切换 [agent_id] 或 /switch [agent_id]"""
        from datetime import datetime

        from openakita.agents.presets import SYSTEM_PRESETS
        from openakita.agents.profile import get_profile_store

        all_profiles = list(SYSTEM_PRESETS)
        try:
            store = get_profile_store()
            preset_ids = {p.id for p in SYSTEM_PRESETS}
            all_profiles.extend(p for p in store.list_all() if p.id not in preset_ids)
        except Exception:
            pass

        parts = user_text.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        if not arg:
            # 无参数：列出可用 Agent
            lines = ["📋 **可用 Agent**\n"]
            current_id = session.context.agent_profile_id
            for p in all_profiles:
                marker = " ⬅️ 当前" if p.id == current_id else ""
                lines.append(f"• `{p.id}` — {p.icon} {p.name}: {p.description}{marker}")
            lines.append("\n用法: `/切换 <agent_id>` 或 `/switch <agent_id>`")
            return "\n".join(lines)

        # 有参数：切换
        agent_id = arg.lower()
        profile_map = {p.id.lower(): p for p in all_profiles}
        if agent_id not in profile_map:
            available = ", ".join(p.id for p in all_profiles)
            return f"❌ 未找到 Agent `{agent_id}`\n可用: {available}"

        ctx = session.context
        p = profile_map[agent_id]
        old_id = ctx.agent_profile_id
        if old_id.lower() == agent_id:
            return f"ℹ️ 当前已是 **{p.icon} {p.name}**"

        ctx.agent_switch_history.append({
            "from": old_id,
            "to": p.id,
            "at": datetime.now().isoformat(),
        })
        ctx.agent_profile_id = p.id
        self.session_manager.mark_dirty()
        logger.info(f"[IM] Agent switched: {old_id!r} -> {agent_id!r} for {session.session_key}")

        return f"✅ 已切换到 **{p.icon} {p.name}** ({p.description})"

    def _format_agent_help(self) -> str:
        """格式化 /help 输出"""
        lines = [
            "📖 **可用命令**\n",
            "**模式:**",
            "  `/模式` / `/mode` — 查看或切换单/多Agent模式",
            "  `/模式 开启` — 开启多Agent模式",
            "  `/模式 关闭` — 关闭多Agent模式",
            "",
            "**多Agent（需先开启多Agent模式）:**",
            "  `/切换` / `/switch` — 列出可用 Agent",
            "  `/切换 <id>` / `/switch <id>` — 切换当前 Agent",
            "  `/状态` / `/status` — 查看当前 Agent 信息",
            "  `/重置` / `/agent_reset` — 重置为默认 Agent",
            "",
            "**其他:**",
            "  `/new` / `/新话题` — 开启新话题",
            "  `/model` — 模型状态",
            "  `/thinking` — 思考模式",
            "  `/chain` — 思维链进度推送开关",
        ]
        return "\n".join(lines)

    def _format_agent_status(self, session: Session) -> str:
        """格式化 /状态 输出"""
        from openakita.agents.presets import SYSTEM_PRESETS
        from openakita.agents.profile import get_profile_store

        all_profiles = list(SYSTEM_PRESETS)
        try:
            store = get_profile_store()
            preset_ids = {p.id for p in SYSTEM_PRESETS}
            all_profiles.extend(p for p in store.list_all() if p.id not in preset_ids)
        except Exception:
            pass

        current_id = session.context.agent_profile_id
        profile_map = {p.id.lower(): p for p in all_profiles}
        p = profile_map.get(current_id.lower())

        if p:
            return (
                f"🤖 **当前 Agent**\n\n"
                f"**{p.icon} {p.name}** (`{p.id}`)\n"
                f"{p.description}"
            )
        return f"🤖 **当前 Agent**\n\nID: `{current_id}`"

    def _handle_agent_reset(self, session: Session) -> str:
        """处理 /重置：重置为该 bot 绑定的默认 agent（或 "default"）"""
        from datetime import datetime

        reset_target = session.get_metadata("_bot_default_agent") or "default"

        ctx = session.context
        old_id = ctx.agent_profile_id
        if old_id == reset_target:
            label = "默认 Agent" if reset_target == "default" else f"**{reset_target}**"
            return f"ℹ️ 当前已是{label}"

        ctx.agent_switch_history.append({
            "from": old_id,
            "to": reset_target,
            "at": datetime.now().isoformat(),
        })
        ctx.agent_profile_id = reset_target
        self.session_manager.mark_dirty()
        logger.info(f"[IM] Agent reset to {reset_target} for {session.session_key}")

        if reset_target == "default":
            return "✅ 已重置为默认 Agent"
        return f"✅ 已重置为 **{reset_target}**"

    def _get_bot_default_agent(self, channel: str) -> str:
        """Return the agent_profile_id configured on the adapter for *channel*."""
        adapter = self._adapters.get(channel)
        if adapter and hasattr(adapter, "agent_profile_id"):
            return adapter.agent_profile_id
        return "default"

    def _apply_bot_agent_profile(self, session: Session, channel: str) -> None:
        """For multi-bot setups, apply the adapter's bound agent_profile_id
        to a newly-created session so the orchestrator routes to the correct agent.
        Only runs once per session (guard: ``_bot_default_agent`` metadata).
        """
        if session.get_metadata("_bot_default_agent") is not None:
            return
        bot_agent = self._get_bot_default_agent(channel)
        session.set_metadata("_bot_default_agent", bot_agent)
        if bot_agent != "default" and not session.context.agent_switch_history:
            session.context.agent_profile_id = bot_agent
            self.session_manager.mark_dirty()
            logger.info(
                f"[IM] Applied bot default agent: {bot_agent} "
                f"for {session.session_key}"
            )

    # ==================== 自然语言意图检测 ====================

    import re as _re

    _NL_MODE_ON = _re.compile(
        r"^(?:帮我|请)?(?:开启|打开|启用|启动|开|打开一下)[\s]*"
        r"(?:多\s*[Aa]gent|多智能体|multi[\s\-]?agent)[\s]*(?:模式)?$",
    )
    _NL_MODE_OFF = _re.compile(
        r"^(?:帮我|请)?(?:关闭|关掉|停用|停止|关)[\s]*"
        r"(?:多\s*[Aa]gent|多智能体|multi[\s\-]?agent)[\s]*(?:模式)?$",
    )
    _NL_SWITCH = _re.compile(
        r"^(?:帮我|请)?(?:切换到|换成|使用|用|切换为|改为|改成)[\s]*(.+?)[\s]*(?:agent|助手|机器人)?$",
        _re.IGNORECASE,
    )

    def _detect_agent_natural_language(self, text: str) -> tuple[str, str] | None:
        """Detect natural-language intent for multi-agent operations.

        Returns (action, arg) or None:
        - ("mode_on", "")
        - ("mode_off", "")
        - ("switch", "<agent_id>")
        """
        t = text.strip()
        if len(t) > 60 or len(t) < 4:
            return None
        if self._NL_MODE_ON.search(t):
            return ("mode_on", "")
        if self._NL_MODE_OFF.search(t):
            return ("mode_off", "")
        m = self._NL_SWITCH.search(t)
        if m:
            target = m.group(1).strip().strip("\"'`")
            if target:
                return ("switch", target)
        return None

    def _get_group_response_mode(
        self, channel: str, chat_id: str = "", user_id: str = "*"
    ) -> GroupResponseMode:
        """获取群聊响应模式。

        优先级: per-chat bot_config > per-bot adapter > 全局 settings > 默认 MENTION_ONLY
        """
        if chat_id and hasattr(self, "bot_config"):
            per_chat = self.bot_config.get_response_mode(channel, chat_id, user_id)
            if per_chat:
                try:
                    return GroupResponseMode(per_chat)
                except ValueError:
                    pass
        adapter = self._adapters.get(channel)
        per_bot = getattr(adapter, "_group_response_mode", None)
        if per_bot:
            try:
                return GroupResponseMode(per_bot)
            except ValueError:
                pass
        from ..config import settings
        raw = settings.group_response_mode
        try:
            return GroupResponseMode(raw)
        except ValueError:
            return GroupResponseMode.MENTION_ONLY

    def _get_group_allowlist(self, channel: str) -> set[str]:
        """获取群聊白名单（Per-Bot 配置 > 全局配置）"""
        adapter = self._adapters.get(channel)
        per_bot = getattr(adapter, "_group_allowlist", None)
        if per_bot:
            return set(per_bot) if not isinstance(per_bot, set) else per_bot
        from ..config import settings
        raw = getattr(settings, "group_allowlist", None)
        if raw:
            return set(raw) if not isinstance(raw, set) else raw
        return set()

    def _apply_persisted_group_policy(self) -> None:
        """Load persisted group policy from JSON and apply to adapters."""
        from pathlib import Path
        import json
        policy_path = Path("data/sessions/group_policy.json")
        if not policy_path.exists():
            return
        try:
            data = json.loads(policy_path.read_text(encoding="utf-8"))
            for channel, cfg in data.items():
                adapter = self._adapters.get(channel)
                if adapter is None:
                    continue
                mode = cfg.get("mode")
                allowlist = cfg.get("allowlist", [])
                if mode:
                    adapter._group_response_mode = mode
                if allowlist:
                    adapter._group_allowlist = set(allowlist)
            logger.info(f"[Gateway] Applied persisted group policy for {len(data)} channel(s)")
        except Exception as e:
            logger.warning(f"[Gateway] Failed to load group policy: {e}")

    async def start(self) -> None:
        """启动网关"""
        self._running = True
        self._accepting = True

        # 预加载 Whisper 语音识别模型（在后台线程中执行，不阻塞启动）
        asyncio.create_task(self._preload_whisper_async())

        # 启动所有适配器
        started = []
        failed = []
        for name, adapter in self._adapters.items():
            try:
                await adapter.start()
                started.append(name)
                logger.info(f"Started adapter: {name}")
            except Exception as e:
                failed.append(name)
                adapter._running = False
                logger.error(f"Failed to start adapter {name}: {e}")

        self._started_adapters = started
        self._failed_adapters = failed

        self._apply_persisted_group_policy()

        _notify_im_event("im:channel_status", {"started": started, "failed": failed})

        # 启动消息处理循环
        self._processing_task = asyncio.create_task(self._process_loop())

        # 启动 per-session 字典清理任务（每 10 分钟清理不活跃的 session 条目）
        self._session_dict_cleanup_task = asyncio.create_task(self._session_dict_cleanup_loop())

        if failed:
            logger.info(
                f"MessageGateway started with {len(started)}/{len(self._adapters)} adapters"
                f" (failed: {', '.join(failed)})"
            )
        else:
            logger.info(f"MessageGateway started with {len(started)} adapters")

    def get_started_adapters(self) -> list[str]:
        """获取启动成功的适配器列表。"""
        return list(self._started_adapters)

    def get_failed_adapters(self) -> list[str]:
        """获取启动失败的适配器列表。"""
        return list(self._failed_adapters)

    async def _preload_whisper_async(self) -> None:
        """异步预加载 Whisper 模型"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_whisper_model)
        except Exception as e:
            logger.warning(f"Failed to preload Whisper model: {e}")

    def _ensure_ffmpeg(self) -> None:
        """确保 ffmpeg 可用（优先使用系统已有的，否则自动下载静态版本）"""
        import shutil

        if shutil.which("ffmpeg"):
            logger.debug("ffmpeg found in system PATH")
            return

        try:
            import static_ffmpeg

            static_ffmpeg.add_paths(weak=True)  # weak=True: 不覆盖已有
            logger.info("ffmpeg auto-configured via static-ffmpeg")
        except ImportError as e:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("static_ffmpeg")
            logger.warning(f"ffmpeg 不可用: {hint}")
            logger.warning(f"static_ffmpeg ImportError 详情: {e}", exc_info=True)

    async def _extract_video_keyframes(
        self, video_path: str, max_frames: int = 6, interval_seconds: int = 10
    ) -> list[tuple[str, str]]:
        """从视频中截取关键帧（使用 ffmpeg）

        Args:
            video_path: 视频文件路径
            max_frames: 最多截取的帧数
            interval_seconds: 每隔多少秒截取一帧

        Returns:
            [(base64_data, media_type), ...] 列表
        """
        import asyncio
        import shutil
        import tempfile

        self._ensure_ffmpeg()
        if not shutil.which("ffmpeg"):
            logger.warning("ffmpeg not available, cannot extract keyframes")
            return []

        def _do_extract():
            results = []
            with tempfile.TemporaryDirectory() as tmpdir:
                output_pattern = str(Path(tmpdir) / "frame_%03d.jpg")
                cmd = [
                    "ffmpeg", "-i", video_path,
                    "-vf", f"fps=1/{interval_seconds}",
                    "-frames:v", str(max_frames),
                    "-q:v", "2",
                    "-y", output_pattern,
                ]
                import subprocess
                import sys as _sys
                try:
                    _kw: dict = {}
                    if _sys.platform == "win32":
                        _kw["creationflags"] = subprocess.CREATE_NO_WINDOW
                    subprocess.run(
                        cmd, capture_output=True, timeout=60,
                        check=False, **_kw,
                    )
                except Exception as e:
                    logger.error(f"ffmpeg keyframe extraction failed: {e}")
                    return results

                frame_files = sorted(Path(tmpdir).glob("frame_*.jpg"))
                for fp in frame_files[:max_frames]:
                    try:
                        data = base64.b64encode(fp.read_bytes()).decode("utf-8")
                        results.append((data, "image/jpeg"))
                    except Exception as e:
                        logger.error(f"Failed to read keyframe {fp}: {e}")
            return results

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_extract)

    def _load_whisper_model(self) -> None:
        """加载 Whisper 模型（在线程池中执行）"""
        if self._whisper_loaded or self._whisper_unavailable:
            return

        # 模块可能在服务运行期间安装，路径尚未注入 sys.path。
        # 在导入前尝试刷新一次（idempotent，不会重复添加已有路径）。
        # 必须在 _ensure_ffmpeg 之前执行，因为 static_ffmpeg 也在 whisper 模块中。
        if "whisper" not in sys.modules:
            try:
                from openakita.runtime_env import inject_module_paths_runtime
                inject_module_paths_runtime()
            except Exception:
                pass

        # 确保 ffmpeg 可用（Whisper 依赖 ffmpeg 解码音频）
        self._ensure_ffmpeg()

        try:
            import hashlib
            import os

            import whisper
            from whisper import _MODELS

            model_name = self._whisper_model_name

            # 获取模型缓存路径
            cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "whisper")
            model_file = os.path.join(cache_dir, f"{model_name}.pt")

            # 检查本地模型 hash（仅提醒，不阻塞）
            if os.path.exists(model_file) and os.path.getsize(model_file) > 1000000:
                model_url = _MODELS.get(model_name, "")
                if model_url:
                    url_parts = model_url.split("/")
                    expected_hash = url_parts[-2] if len(url_parts) >= 2 else ""

                    if expected_hash and len(expected_hash) > 5:
                        sha256 = hashlib.sha256()
                        with open(model_file, "rb") as f:
                            for chunk in iter(lambda: f.read(65536), b""):
                                sha256.update(chunk)
                        local_hash = sha256.hexdigest()

                        if not local_hash.startswith(expected_hash):
                            logger.info(
                                f"Whisper model '{model_name}' may have updates available. "
                                f"Delete {model_file} to re-download if needed."
                            )

            # 正常加载
            logger.info(f"Loading Whisper model '{model_name}'...")
            self._whisper = whisper.load_model(model_name)
            self._whisper_loaded = True
            logger.info(f"Whisper model '{model_name}' loaded successfully")

        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("whisper")
            logger.warning(f"Whisper 不可用（本进程内不再重试）: {hint}")
            self._whisper_unavailable = True
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}", exc_info=True)

    async def drain(self, timeout: float = 30.0) -> None:
        """
        优雅排空：停止接收新消息，等待进行中任务完成后再停止。

        Args:
            timeout: 等待进行中任务的最大秒数，超时后强制停止
        """
        self._accepting = False
        logger.info("[Shutdown] Gateway entering drain mode, no longer accepting new messages")

        active = {k for k, v in self._processing_sessions.items() if v}
        if not active:
            logger.info("[Shutdown] No in-flight tasks, proceeding to stop")
            await self.stop()
            return

        logger.info(f"[Shutdown] Waiting for {len(active)} in-flight task(s): {active}")
        deadline = asyncio.get_event_loop().time() + timeout
        poll_interval = 0.5

        while True:
            active = {k for k, v in self._processing_sessions.items() if v}
            if not active:
                logger.info("[Shutdown] All in-flight tasks completed")
                break
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.warning(
                    f"[Shutdown] Drain timeout ({timeout}s), "
                    f"force-stopping with {len(active)} task(s) still active: {active}"
                )
                break
            await asyncio.sleep(min(poll_interval, remaining))

        await self.stop()

    async def stop(self) -> None:
        """停止网关（立即停止，不等待进行中任务）"""
        self._running = False
        self._accepting = False

        # 停止处理循环
        if self._processing_task:
            self._processing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._processing_task

        # 停止 per-session 字典清理任务
        cleanup_task = getattr(self, "_session_dict_cleanup_task", None)
        if cleanup_task:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task

        # 取消所有活跃的 session tasks
        for skey, task in list(self._session_tasks.items()):
            if not task.done():
                task.cancel()
        for skey, task in list(self._session_tasks.items()):
            if not task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._session_tasks.clear()

        # 停止所有适配器
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
                logger.info(f"Stopped adapter: {name}")
            except Exception as e:
                logger.error(f"Failed to stop adapter {name}: {e}")

        logger.info("MessageGateway stopped")

    async def _session_dict_cleanup_loop(self) -> None:
        """定期清理 per-session 字典中不活跃的条目，防止内存泄漏。"""
        while self._running:
            try:
                await asyncio.sleep(600)  # 每 10 分钟清理一次
                self._cleanup_stale_session_dicts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Gateway] Session dict cleanup error: {e}")

    def _cleanup_stale_session_dicts(self) -> None:
        """清理不再活跃的 session 对应的字典条目。

        只清理当前未在处理中的 session_key，保留正在活跃的。
        """
        active_keys = {k for k, v in self._processing_sessions.items() if v}
        cleaned = 0

        # 清理 _interrupt_queues 中空闲且非活跃的条目
        stale = [k for k in self._interrupt_queues if k not in active_keys]
        for k in stale:
            q = self._interrupt_queues[k]
            if q.empty():
                del self._interrupt_queues[k]
                cleaned += 1

        # 清理 _processing_sessions 中 False 值的条目
        stale = [k for k, v in self._processing_sessions.items() if not v]
        for k in stale:
            del self._processing_sessions[k]
            cleaned += 1

        # 清理 _interrupt_callbacks 中非活跃的条目
        stale = [k for k in self._interrupt_callbacks if k not in active_keys]
        for k in stale:
            del self._interrupt_callbacks[k]
            cleaned += 1

        # 清理 _progress_buffers 中空的条目
        stale = [k for k, v in self._progress_buffers.items() if not v]
        for k in stale:
            del self._progress_buffers[k]
            cleaned += 1

        # 清理 _progress_flush_tasks 中已完成的条目
        stale = [k for k, t in self._progress_flush_tasks.items() if t.done()]
        for k in stale:
            del self._progress_flush_tasks[k]
            cleaned += 1

        # 清理 _progress_card_accum 中非活跃的条目
        stale = [k for k in self._progress_card_accum if k not in active_keys]
        for k in stale:
            del self._progress_card_accum[k]
            cleaned += 1

        # 清理 _session_tasks 中已完成的条目
        stale = [k for k, t in self._session_tasks.items() if t.done()]
        for k in stale:
            del self._session_tasks[k]
            cleaned += 1

        # 清理 ModelCommandHandler 中过期的切换会话
        stale = [
            k for k, s in self._model_cmd_handler._switch_sessions.items()
            if s.is_expired
        ]
        for k in stale:
            del self._model_cmd_handler._switch_sessions[k]
            cleaned += 1

        if cleaned:
            logger.debug(f"[Gateway] Cleaned {cleaned} stale session dict entries")

    def set_brain(self, brain: "Brain") -> None:
        """
        设置 Brain 实例（用于模型切换命令）

        Args:
            brain: Brain 实例
        """
        self._model_cmd_handler.set_brain(brain)
        logger.info("ModelCommandHandler brain set")

    def set_shutdown_event(self, event: asyncio.Event) -> None:
        """注入 shutdown_event（供终极重启指令使用）"""
        self._shutdown_event = event
        self._restart_cmd_handler._shutdown_event = event
        logger.debug("RestartCommandHandler shutdown_event set")

    # ==================== 适配器管理 ====================

    async def register_adapter(self, adapter: ChannelAdapter) -> None:
        """
        注册适配器

        Args:
            adapter: 通道适配器
        """
        name = adapter.channel_name

        if name in self._adapters:
            logger.warning(f"Adapter {name} already registered, replacing")
            await self._adapters[name].stop()

        # 设置消息回调
        adapter.on_message(self._on_message)

        self._adapters[name] = adapter
        logger.info(f"Registered adapter: {name}")

        # 如果网关已运行，启动适配器
        if self._running:
            await adapter.start()

    async def unregister_adapter(self, name: str) -> bool:
        """
        注销并停止指定适配器。

        Args:
            name: 适配器的 channel_name

        Returns:
            True 表示成功注销，False 表示未找到该适配器
        """
        adapter = self._adapters.pop(name, None)
        if adapter is None:
            logger.warning(f"Adapter {name} not found, cannot unregister")
            return False
        try:
            await adapter.stop()
        except Exception as e:
            logger.error(f"Error stopping adapter {name} during unregister: {e}")
        logger.info(f"Unregistered adapter: {name}")
        return True

    def get_adapter(self, channel: str) -> ChannelAdapter | None:
        """获取适配器"""
        return self._adapters.get(channel)

    def list_adapters(self) -> list[str]:
        """列出所有适配器"""
        return list(self._adapters.keys())

    # ==================== 消息处理 ====================

    async def _on_message(self, message: UnifiedMessage) -> None:
        """
        消息回调（由适配器调用）

        如果该会话正在处理中，根据消息类型做不同处理：
        - STOP: 触发全局任务取消（cancel_event）
        - SKIP: 触发当前步骤跳过（skip_event），不终止任务
        - INSERT: 将用户消息注入任务上下文，让 LLM 决策如何处理
        """
        if not self._accepting:
            logger.debug(f"[Shutdown] Message rejected (drain mode): {message.channel}/{message.user_id}")
            return

        session_key = self._get_session_key(message)
        _raw_text = (message.plain_text or "").strip()

        # ==================== 终极重启指令拦截 ====================
        # 在所有逻辑之前拦截，确保即使系统卡死也能响应。
        # 不经过消息队列、不进入 Agent、不污染会话上下文。
        if self._restart_cmd_handler.has_pending_session(session_key):
            consumed = await self._restart_cmd_handler.handle_pending_input(
                session_key, message,
            )
            if consumed:
                return

        if self._restart_cmd_handler.is_restart_command(_raw_text):
            await self._restart_cmd_handler.handle_restart_command(session_key, message)
            return
        # ==================== /终极重启指令拦截 ====================

        # ==================== 中断快路径（无锁检测） ====================
        # 在获取 interrupt_lock 之前做低成本文本检测，减少锁竞争
        if self._processing_sessions.get(session_key, False) and self._is_abort_text(_raw_text):
            await self._cancel_session(session_key, message, _raw_text)
            return

        async with self._interrupt_lock:
            if self._processing_sessions.get(session_key, False):
                # 会话正在处理中
                user_text = (message.plain_text or "").strip()

                # 群聊响应模式过滤（防止未 @ 的群消息通过中断路径注入上下文）
                if message.chat_type == "group" and not message.is_direct_message:
                    _irq_mode = self._get_group_response_mode(message.channel, message.chat_id, message.user_id)
                    if _irq_mode == GroupResponseMode.MENTION_ONLY and not message.is_mentioned:
                        _is_stop_or_skip = self.agent_handler and self.agent_handler.classify_interrupt(user_text) in ("stop", "skip")
                        if not _is_stop_or_skip:
                            logger.debug(
                                f"[Interrupt] Group message ignored in interrupt path "
                                f"(mention_only, not mentioned): {user_text[:50]}"
                            )
                            return

                # 会话隔离校验：只有当 agent 正在处理本会话的任务时，
                # cancel/skip/insert 操作才应生效（防止 A 用户误杀 B 用户的任务）
                _agent_ref = getattr(self.agent_handler, "_agent_ref", None) if self.agent_handler else None
                _resolved_sid = self._resolve_task_session_id(session_key, _agent_ref)
                _session_matches = _resolved_sid is not None

                logger.debug(
                    f"[Interrupt] Session check: resolved_sid={_resolved_sid!r}, "
                    f"interrupt_key={session_key!r}, matches={_session_matches}"
                )

                if self.agent_handler and _session_matches:
                    msg_type = self.agent_handler.classify_interrupt(user_text)

                    if msg_type == "stop":
                        if _resolved_sid:
                            self.agent_handler.cancel_current_task(
                                f"用户发送停止指令: {user_text}",
                                session_id=_resolved_sid,
                            )
                        else:
                            logger.warning(
                                f"[Interrupt] Could not resolve task for {session_key}, "
                                f"cancelling current_task as fallback"
                            )
                            self.agent_handler.cancel_current_task(
                                f"用户发送停止指令: {user_text}",
                            )
                        logger.info(
                            f"[Interrupt] STOP command, cancelling task for {session_key} "
                            f"(resolved={_resolved_sid}): {user_text}"
                        )
                        await self._send_feedback(message, "✅ 收到，正在停止当前任务…")
                    elif msg_type == "skip":
                        ok = self.agent_handler.skip_current_step(
                            f"用户发送跳过指令: {user_text}", session_id=_resolved_sid,
                        )
                        if ok:
                            await self._send_feedback(message, "⏭️ 收到，正在跳过当前步骤…")
                        else:
                            await self._send_feedback(message, "⚠️ 当前没有可跳过的步骤。")
                        logger.info(
                            f"[Interrupt] SKIP handled directly (not queued) for {session_key}: {user_text}"
                        )
                    else:
                        # 补录到 session 历史（INSERT 路径原本不写历史，
                        # 导致桌面端 IM 界面看不到这条消息）
                        _ins_session = self.session_manager.get_session(
                            channel=message.channel,
                            chat_id=message.chat_id,
                            user_id=message.user_id,
                            thread_id=message.thread_id,
                        )
                        if _ins_session:
                            _ins_session.add_message(
                                role="user",
                                content=user_text,
                                message_id=message.id,
                                channel_message_id=message.channel_message_id,
                                is_interrupt=True,
                            )
                            self.session_manager.mark_dirty()
                            _notify_im_event("im:new_message", {
                                "channel": message.channel, "role": "user",
                                "session_id": _ins_session.session_key,
                                "chat_type": _ins_session.chat_type,
                                "display_name": _ins_session.display_name,
                            })

                        try:
                            ok = await self.agent_handler.insert_user_message(
                                user_text, session_id=_resolved_sid,
                            )
                            if ok:
                                await self._send_feedback(message, "💬 收到，已将消息注入当前任务。")
                            else:
                                await self._send_feedback(message, "⚠️ 当前没有正在执行的任务，消息未能注入。")
                        except Exception as e:
                            logger.error(f"[Interrupt] INSERT failed for {session_key}: {e}")
                            await self._send_feedback(message, "❌ 消息注入失败，请稍后再试。")
                        logger.info(
                            f"[Interrupt] INSERT handled for {session_key}: {user_text[:50]}"
                        )
                elif self.agent_handler and not _session_matches:
                    # Agent 不在处理当前用户的任务（可能空闲或在处理其他用户）
                    await self._add_interrupt_message(session_key, message)
                    logger.info(
                        f"[Interrupt] Session mismatch: resolved_sid={_resolved_sid!r}, "
                        f"interrupt_key={session_key!r}, agent_ref={'present' if _agent_ref else 'None'}, "
                        f"queued for later: {user_text[:50]}"
                    )
                else:
                    # agent_handler 不可用时，fallback 入中断队列
                    await self._add_interrupt_message(session_key, message)
                    logger.warning(
                        f"[Interrupt] No agent_handler, queued as interrupt for {session_key}: {user_text[:50]}"
                    )
                return

        # 正常入队
        await self._message_queue.put(message)

    # ==================== 中断快路径 ====================

    _ABORT_TRIGGERS = frozenset({
        "停止", "停", "stop", "停止执行", "取消", "取消任务",
        "算了", "不用了", "别做了", "停下", "halt", "abort", "cancel",
        "やめて", "중지",
    })

    @classmethod
    def _normalize_abort_text(cls, text: str) -> str:
        """Strip @mentions and whitespace for abort detection"""
        import re
        return re.sub(r"@\S+\s*", "", text).strip().lower()

    @classmethod
    def _is_abort_text(cls, raw_text: str) -> bool:
        """Low-cost check: is this text an abort trigger?"""
        normalized = cls._normalize_abort_text(raw_text)
        return normalized in cls._ABORT_TRIGGERS

    async def _cancel_session(
        self, session_key: str, message: UnifiedMessage, user_text: str,
    ) -> None:
        """Fast-path: cancel the running task for a session and send feedback"""
        _agent_ref = getattr(self.agent_handler, "_agent_ref", None) if self.agent_handler else None
        _resolved_sid = self._resolve_task_session_id(session_key, _agent_ref)

        if self.agent_handler:
            if _resolved_sid:
                self.agent_handler.cancel_current_task(
                    f"用户发送停止指令(fast-path): {user_text}",
                    session_id=_resolved_sid,
                )
            else:
                self.agent_handler.cancel_current_task(
                    f"用户发送停止指令(fast-path): {user_text}",
                )

        # Cancel the asyncio task if it exists
        task = self._session_tasks.get(session_key)
        if task and not task.done():
            task.cancel()

        logger.info(
            f"[Abort-FastPath] Session {session_key} cancelled: {user_text}"
        )
        await self._send_feedback(message, "✅ 收到，正在停止当前任务…")

    # ==================== 中断机制 ====================

    async def _add_interrupt_message(
        self,
        session_key: str,
        message: UnifiedMessage,
        priority: InterruptPriority = InterruptPriority.HIGH,
    ) -> None:
        """
        添加中断消息到会话队列

        Args:
            session_key: 会话标识
            message: 消息
            priority: 优先级
        """
        if session_key not in self._interrupt_queues:
            self._interrupt_queues[session_key] = asyncio.PriorityQueue()

        interrupt_msg = InterruptMessage(message=message, priority=priority)
        await self._interrupt_queues[session_key].put(interrupt_msg)

        logger.debug(f"[Interrupt] Added to queue: {session_key}, priority={priority.name}")

    def _get_session_key(self, message: UnifiedMessage) -> str:
        """获取会话标识（话题消息会追加 thread_id 实现话题级隔离）"""
        key = f"{message.channel}:{message.chat_id}:{message.user_id}"
        if message.thread_id:
            key += f":{message.thread_id}"
        return key

    @staticmethod
    def _resolve_task_session_id(session_key: str, agent_ref: object) -> str | None:
        """
        根据 gateway session_key 找到 AgentState._tasks 中匹配的 task session_id。

        session_key 格式:
          三段式: "telegram:1241684312:tg_1241684312"  (channel:chat_id:user_id)
          四段式: "telegram:1241684312:tg_1241684312:thread_abc"  (channel:chat_id:user_id:thread_id)

        task key 可能是两种格式（取决于 _resolve_conversation_id 的返回）:
          a) session.id 格式: "telegram_1241684312_20260219031213_xxx"（下划线分隔）
          b) gateway session_key 格式: "telegram:1241684312:tg_1241684312[:thread_id]"（冒号分隔）
        """
        if not agent_ref:
            return None
        agent_state = getattr(agent_ref, "agent_state", None)
        if not agent_state:
            return None
        parts = session_key.split(":")
        channel = parts[0] if parts else ""
        chat_id = parts[1] if len(parts) >= 2 else ""
        thread_id = parts[3] if len(parts) >= 4 else ""
        if not channel or not chat_id:
            return None

        tasks = getattr(agent_state, "_tasks", {})

        if session_key in tasks:
            return session_key

        prefix_underscore = f"{channel}_"
        chat_id_seg_underscore = f"_{chat_id}_"
        prefix_colon = f"{channel}:"
        chat_id_seg_colon = f":{chat_id}:"

        def _match_key(key: str) -> bool:
            base_matched = (
                (key.startswith(prefix_underscore) and chat_id_seg_underscore in key)
                or (key.startswith(prefix_colon) and chat_id_seg_colon in key)
            )
            if not base_matched:
                return False
            if thread_id:
                return thread_id in key
            return True

        for key in tasks:
            task = tasks[key]
            if _match_key(key) and task.is_active:
                return key
        for key in tasks:
            if _match_key(key):
                return key
        return None

    def _mark_session_processing(self, session_key: str, processing: bool) -> None:
        """标记会话处理状态"""
        self._processing_sessions[session_key] = processing
        if not processing and session_key in self._interrupt_callbacks:
            del self._interrupt_callbacks[session_key]

    async def check_interrupt(self, session_key: str) -> UnifiedMessage | None:
        """
        检查会话是否有待处理的中断消息

        Args:
            session_key: 会话标识

        Returns:
            待处理的消息，如果没有则返回 None
        """
        queue = self._interrupt_queues.get(session_key)
        if not queue or queue.empty():
            return None

        try:
            interrupt_msg = queue.get_nowait()
            logger.info(
                f"[Interrupt] Retrieved message for {session_key}: {interrupt_msg.message.plain_text}"
            )
            return interrupt_msg.message
        except asyncio.QueueEmpty:
            return None

    def has_pending_interrupt(self, session_key: str) -> bool:
        """
        检查会话是否有待处理的中断消息

        Args:
            session_key: 会话标识

        Returns:
            是否有待处理消息
        """
        queue = self._interrupt_queues.get(session_key)
        return queue is not None and not queue.empty()

    def get_interrupt_count(self, session_key: str) -> int:
        """
        获取待处理的中断消息数量

        Args:
            session_key: 会话标识

        Returns:
            待处理消息数量
        """
        queue = self._interrupt_queues.get(session_key)
        return queue.qsize() if queue else 0

    def register_interrupt_callback(
        self,
        session_key: str,
        callback: Callable[[], Awaitable[str | None]],
    ) -> None:
        """
        注册中断检查回调（由 Agent 调用）

        当工具调用间隙，Agent 会调用此回调检查是否需要处理新消息

        Args:
            session_key: 会话标识
            callback: 回调函数，返回需要插入的消息文本或 None
        """
        self._interrupt_callbacks[session_key] = callback
        logger.debug(f"[Interrupt] Registered callback for {session_key}")

    async def _process_loop(self) -> None:
        """消息处理循环（per-session-key 并发调度）

        不同 session_key 的消息并发处理（受 MAX_CONCURRENT_SESSIONS 限制），
        同一 session_key 内的消息由中断机制保证顺序。
        """
        while self._running:
            try:
                message = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                session_key = self._get_session_key(message)

                old_task = self._session_tasks.get(session_key)
                if old_task and old_task.done():
                    del self._session_tasks[session_key]
                    old_task = None

                if old_task and not old_task.done():
                    logger.info(
                        f"[ProcessLoop] Session {session_key} has in-flight task, "
                        "routing new message to interrupt queue"
                    )
                    await self._add_interrupt_message(session_key, message)
                else:
                    task = asyncio.create_task(self._session_dispatch(message))
                    self._session_tasks[session_key] = task

            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in process_loop dispatch: {e}", exc_info=True)

    async def _session_dispatch(self, message: UnifiedMessage) -> None:
        """带并发控制的单条消息处理"""
        async with self._concurrency_sem:
            try:
                await self._handle_message(message)
            except Exception as e:
                logger.error(f"Error handling message: {e}", exc_info=True)

    async def _handle_message(self, message: UnifiedMessage) -> None:
        """
        处理单条消息
        """
        session_key = self._get_session_key(message)
        user_text = message.plain_text.strip() if message.plain_text else ""

        logger.info(
            f"[IM] <<< 收到消息: channel={message.channel}, user={message.user_id}, "
            f"text=\"{user_text[:100]}\""
        )

        typing_task: asyncio.Task | None = None
        session = None
        try:
            # ==================== 群聊响应过滤 ====================
            if message.chat_type == "group" and not message.is_direct_message:
                mode = self._get_group_response_mode(message.channel, message.chat_id, message.user_id)

                if mode == GroupResponseMode.DISABLED:
                    logger.debug(f"[IM] Group message ignored (disabled): {user_text[:50]}")
                    return

                if mode == GroupResponseMode.ALLOWLIST:
                    from .policy import check_group_policy, GroupPolicyConfig, GroupPolicyType
                    gp_config = GroupPolicyConfig(
                        policy=GroupPolicyType.ALLOWLIST,
                        allowlist=self._get_group_allowlist(message.channel),
                    )
                    gp_result = check_group_policy(message.chat_id, gp_config)
                    if not gp_result.allowed:
                        logger.debug(
                            f"[IM] Group message ignored (allowlist, "
                            f"chat_id={message.chat_id[:20]}): {user_text[:50]}"
                        )
                        return

                if mode == GroupResponseMode.MENTION_ONLY and not message.is_mentioned:
                    logger.debug(f"[IM] Group message ignored (mention_only): {user_text[:50]}")
                    return

                if mode == GroupResponseMode.SMART and not message.is_mentioned:
                    if not self._smart_throttle.should_process(message.chat_id):
                        logger.debug(f"[IM] Group message throttled (smart): {user_text[:50]}")
                        return
                    self._smart_throttle.record_process(message.chat_id)
                    message.metadata["group_smart_mode"] = True

            # 标记会话开始处理
            async with self._interrupt_lock:
                self._mark_session_processing(session_key, True)

            # ==================== 系统级命令拦截 ====================
            # 在处理 Agent 之前，检查是否是模型切换相关命令
            # 这确保即使大模型崩溃也能执行切换操作

            # 检查是否在模型切换交互会话中
            if self._model_cmd_handler.is_in_session(session_key):
                response_text = await self._model_cmd_handler.handle_input(session_key, user_text)
                await self._send_response(message, response_text)
                return

            # 检查是否是模型相关命令
            if self._model_cmd_handler.is_model_command(user_text):
                response_text = await self._model_cmd_handler.handle_command(session_key, user_text)
                if response_text:
                    await self._send_response(message, response_text)
                    return

            # 检查是否是思考模式相关命令
            if self._thinking_cmd_handler.is_thinking_command(user_text):
                # 需要获取 session 来读写 thinking 设置
                _thinking_session = self.session_manager.get_session(
                    channel=message.channel,
                    chat_id=message.chat_id,
                    user_id=message.user_id,
                    thread_id=message.thread_id,
                )
                response_text = await self._thinking_cmd_handler.handle_command(
                    session_key, user_text, _thinking_session,
                )
                if response_text:
                    await self._send_response(message, response_text)
                    return

            # 检查是否是模式切换命令（/模式 始终可用，不受 multi_agent_enabled 影响）
            _cmd_lower = user_text.lower().strip()
            if _cmd_lower in ("/模式", "/mode") or _cmd_lower.startswith(("/模式 ", "/mode ")):
                response_text = await self._handle_mode_command(user_text)
                await self._send_response(message, response_text)
                return

            # /feishu 命令族（仅飞书渠道生效）
            if _cmd_lower.startswith("/feishu") and message.channel.split(":")[0] == "feishu":
                feishu_resp = await self._handle_feishu_command(_cmd_lower, message)
                if feishu_resp is not None:
                    await self._send_response(message, feishu_resp)
                    return

            # 检查是否是多Agent相关命令（/切换 /switch /help /帮助 /状态 /status /重置 /agent_reset）
            if self._is_agent_command(user_text):
                response_text = await self._handle_agent_command(message, user_text)
                if response_text is not None:
                    await self._send_response(message, response_text)
                    return

            # 自然语言切换多Agent模式 / 切换Agent
            _nlu = self._detect_agent_natural_language(user_text)
            if _nlu is not None:
                action, arg = _nlu
                if action == "mode_on":
                    resp = await self._handle_mode_command("/模式 开启")
                elif action == "mode_off":
                    resp = await self._handle_mode_command("/模式 关闭")
                elif action == "switch":
                    _switch_session = self.session_manager.get_session(
                        channel=message.channel,
                        chat_id=message.chat_id,
                        user_id=message.user_id,
                        thread_id=message.thread_id,
                    )
                    resp = await self._handle_agent_switch(
                        _switch_session, f"/切换 {arg}"
                    )
                else:
                    resp = None
                if resp:
                    await self._send_response(message, resp)
                    return

            # 检查是否是上下文重置命令（开启新话题）
            _CONTEXT_RESET_COMMANDS = {"/new", "/reset", "/clear", "/新话题", "/新任务", "新对话"}
            _user_cmd = user_text.strip()
            if _user_cmd in _CONTEXT_RESET_COMMANDS or _user_cmd.lower() in _CONTEXT_RESET_COMMANDS:
                _reset_session = self.session_manager.get_session(
                    channel=message.channel,
                    chat_id=message.chat_id,
                    user_id=message.user_id,
                    thread_id=message.thread_id,
                )
                if _reset_session:
                    _old_count = len(_reset_session.context.messages)
                    _reset_session.context.clear_messages()
                    _reset_session.context.current_task = None
                    _reset_session.context.summary = None
                    _reset_session.context.variables.pop("task_description", None)
                    _reset_session.context.variables.pop("task_status", None)
                    self.session_manager.mark_dirty()
                    # 同步清理 SQLite 中的 conversation_turns，防止 getChatHistory 兜底加载旧数据
                    try:
                        _agent_ref = getattr(self.agent_handler, "_agent_ref", None) if self.agent_handler else None
                        _mm = getattr(_agent_ref, "memory_manager", None) if _agent_ref else None
                        if _mm and hasattr(_mm, "store"):
                            _mm.store.delete_turns_for_session(_reset_session.id)
                    except Exception as _e:
                        logger.warning(f"[IM] Failed to clear SQLite turns on reset: {_e}")
                    logger.info(
                        f"[IM] Context reset for {session_key}: "
                        f"cleared {_old_count} messages"
                    )
                await self._send_response(
                    message, "好的，已开启新话题。之前的对话上下文已清除，请说说你的新需求吧~"
                )
                return

            # ==================== 正常消息处理流程 ====================

            # 0. Bot 开关检查（必须在 typing 之前，避免禁用会话触发 typing）
            if not self.bot_config.is_enabled(message.channel, message.chat_id, message.user_id):
                logger.debug(
                    f"[Gateway] Bot disabled for {message.channel}:{message.chat_id}:{message.user_id}, skipping"
                )
                return

            # 1. 启动持续 typing 状态（覆盖预处理 + Agent 全流程）
            typing_task = asyncio.create_task(self._keep_typing(message))

            # 2. 预处理钩子
            for hook in self._pre_process_hooks:
                try:
                    message = await hook(message)
                except Exception as hook_err:
                    logger.warning(f"[Gateway] Pre-process hook {hook.__qualname__} failed: {hook_err}")

            # 3. 媒体预处理（下载图片、语音转文字）
            await self._preprocess_media(message)

            # 4. 获取或创建会话
            _msg_sender_name = (message.metadata or {}).get("sender_name", "")
            _msg_chat_name = (message.metadata or {}).get("chat_name", "")
            session = self.session_manager.get_session(
                channel=message.channel,
                chat_id=message.chat_id,
                user_id=message.user_id,
                thread_id=message.thread_id,
                chat_type=message.chat_type or "private",
                display_name=_msg_sender_name,
                chat_name=_msg_chat_name,
            )

            # 4.0.1 惰性更新 chat_type / display_name / chat_name（已有 session 可能缺失）
            if message.chat_type and session.chat_type != message.chat_type:
                session.chat_type = message.chat_type
            if _msg_sender_name and not session.display_name:
                session.display_name = _msg_sender_name
            if _msg_chat_name and session.chat_name != _msg_chat_name:
                session.chat_name = _msg_chat_name

            # 4.1 多Bot绑定：将 adapter 配置的 agent_profile_id 写入新 session
            self._apply_bot_agent_profile(session, message.channel)

            # 4.2 注入 IM 环境上下文（平台、聊天类型、机器人身份、能力列表）
            adapter = self._adapters.get(message.channel)
            if adapter:
                im_env = {
                    "platform": message.channel,
                    "chat_type": message.chat_type,
                    "chat_id": message.chat_id,
                    "thread_id": message.thread_id,
                    "bot_id": getattr(adapter, "_bot_open_id", None),
                    "capabilities": getattr(adapter, "_capabilities", []),
                }
                session.set_metadata("_im_environment", im_env)
                session.set_metadata("chat_type", message.chat_type)

            # 4.5 推送未送达的自检报告（每天第一条消息时触发，最多一次）
            await self._maybe_deliver_pending_selfcheck_report(message)

            # 4.6 时间间隔自动上下文边界标记
            # 如果距离上一条消息超过阈值，插入边界标记帮助 LLM 区分新旧话题
            _CONTEXT_BOUNDARY_MINUTES = 30
            if session.context.messages:
                _last_ts_str = session.context.messages[-1].get("timestamp")
                if _last_ts_str:
                    try:
                        _last_ts = datetime.fromisoformat(_last_ts_str)
                        _elapsed_min = (datetime.now() - _last_ts).total_seconds() / 60
                        if _elapsed_min > _CONTEXT_BOUNDARY_MINUTES:
                            _hours = _elapsed_min / 60
                            if _hours >= 1:
                                _time_desc = f"{_hours:.1f} 小时"
                            else:
                                _time_desc = f"{int(_elapsed_min)} 分钟"
                            session.context.add_message(
                                "system",
                                f"[上下文边界] 距上次对话已过去 {_time_desc}，"
                                f"以下是新的对话，可能是新话题。"
                                f"请优先关注边界之后的内容。",
                            )
                            session.context.mark_topic_boundary()
                            logger.info(
                                f"[IM] Inserted context boundary for {session_key} "
                                f"(idle {_time_desc})"
                            )
                    except (ValueError, TypeError):
                        pass

            # 4.8 注入待处理的关键事件（@所有人、群公告变更等）
            if adapter:
                pending_events = adapter.get_pending_events(message.chat_id)
                if pending_events:
                    event_lines = []
                    for evt in pending_events:
                        evt_type = evt.get("type", "unknown")
                        if evt_type == "at_all":
                            event_lines.append(f"- @所有人消息: {evt.get('text', '')[:100]}")
                        elif evt_type == "chat_updated":
                            changes = evt.get("changes", {})
                            event_lines.append(f"- 群聊信息更新: {changes}")
                        elif evt_type == "bot_added":
                            event_lines.append("- 机器人已被添加到群聊")
                        elif evt_type == "bot_removed":
                            event_lines.append("- 机器人已被移出群聊")
                        else:
                            event_lines.append(f"- 事件: {evt_type}")
                    if event_lines:
                        event_text = (
                            "[系统提示] 以下是最近发生的重要事件，请注意：\n"
                            + "\n".join(event_lines)
                        )
                        session.context.add_message("system", event_text)

            # 5. 记录消息到会话
            session.add_message(
                role="user",
                content=message.plain_text,
                message_id=message.id,
                channel_message_id=message.channel_message_id,
            )
            self.session_manager.mark_dirty()  # 触发保存
            _notify_im_event("im:new_message", {
                "channel": message.channel, "role": "user",
                "session_id": session.session_key,
                "chat_type": session.chat_type,
                "display_name": session.display_name,
            })

            # 6. 调用 Agent 处理（支持中断检查 + 流式输出）
            response_text, streamed_ok = await self._call_agent(session, message)

            # 7. 后处理钩子
            for hook in self._post_process_hooks:
                try:
                    response_text = await hook(message, response_text)
                except Exception as hook_err:
                    logger.warning(f"[Gateway] Post-process hook {hook.__qualname__} failed: {hook_err}")

            # 7.5 空回复保护
            if not response_text or not response_text.strip():
                logger.warning(
                    f"[IM] Agent returned empty response for message {message.id} "
                    f"(channel={message.channel}, user={message.user_id}), "
                    f"raw={response_text!r}"
                )
                response_text = "⚠️ 处理完成，但未生成有效回复。请重试。"
                streamed_ok = False

            # 8. 记录响应到会话（含思维链摘要 + 工具执行摘要）
            _chain_summary = None
            try:
                _chain_summary = session.get_metadata("_last_chain_summary")
                session.set_metadata("_last_chain_summary", None)
            except Exception:
                pass
            _tool_summary = None
            try:
                _agent_obj = getattr(self.agent_handler, "_agent_ref", None)
                if _agent_obj and hasattr(_agent_obj, "build_tool_trace_summary"):
                    _tool_summary = _agent_obj.build_tool_trace_summary() or None
                    if _tool_summary:
                        logger.debug(f"[Gateway] Tool trace summary ({len(_tool_summary)} chars)")
            except Exception:
                pass
            _msg_meta: dict = {}
            if _chain_summary:
                _msg_meta["chain_summary"] = _chain_summary
            if _tool_summary:
                _msg_meta["tool_summary"] = _tool_summary
            session.add_message(role="assistant", content=response_text, **_msg_meta)
            self.session_manager.mark_dirty()
            self.session_manager.flush()
            _notify_im_event("im:new_message", {
                "channel": message.channel, "role": "assistant",
                "session_id": session.session_key,
                "chat_type": session.chat_type,
                "display_name": session.display_name,
            })

            # 9. 发送响应（流式已通过卡片 PATCH 送达则跳过）
            logger.info(
                f"[IM] >>> 回复完成: channel={message.channel}, user={message.user_id}, "
                f"len={len(response_text)}, streamed={streamed_ok}, "
                f"preview=\"{response_text[:80]}\""
            )
            if not streamed_ok:
                # For adapters that render <think> natively, extract ALL
                # accumulated progress lines and wrap them in a <think> block
                # so WeCom renders them as a collapsible thinking section
                # within the same message bubble.
                _adapter = self._adapters.get(message.channel)
                if _adapter and getattr(_adapter, "_THINK_TAG_NATIVE", False):
                    _buf = self._progress_buffers.get(session.session_key, [])
                    if _buf:
                        _all_lines = [ln.strip() for ln in _buf if ln.strip()]
                        _buf[:] = []
                        if _all_lines:
                            _think_text = "\n".join(_all_lines)
                            response_text = f"<think>\n{_think_text}\n</think>\n{response_text}"

                _had_progress = bool(self._progress_buffers.get(session.session_key))
                await self.flush_progress(session)

                _card_used = bool(self._progress_card_accum.get(session.session_key))
                _adapter = self._adapters.get(message.channel)

                if _had_progress and not _card_used:
                    _cp = session.get_metadata("chain_push")
                    if _cp is None:
                        from ..config import settings as _s
                        _cp = _s.im_chain_push
                    if _cp and _adapter:
                        with contextlib.suppress(Exception):
                            await _adapter.clear_typing(
                                message.chat_id, thread_id=message.thread_id,
                            )

                self._progress_card_accum.pop(session.session_key, None)

                await self._send_response(message, response_text)

            # 10. 处理剩余的中断消息
            await self._process_pending_interrupts(session_key, session)

        except Exception as e:
            logger.error(
                f"Error handling message {message.id} "
                f"(channel={message.channel}, user={message.user_id}): {e}",
                exc_info=True,
            )
            # 补录 assistant 错误响应，防止会话中出现孤立 user 消息
            # (孤立 user 消息会导致下一轮连续同角色 → 模型混乱 / 工具重复执行)
            try:
                if session and session.context.messages:
                    _last = session.context.messages[-1]
                    if _last.get("role") == "user":
                        session.add_message(
                            role="assistant",
                            content=f"[处理出错: {str(e)[:200]}]",
                        )
                        self.session_manager.mark_dirty()
            except Exception:
                pass
            # 发送错误提示
            await self._send_error(message, str(e))
        finally:
            if typing_task is not None:
                typing_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await typing_task
            if session:
                self._progress_card_accum.pop(session.session_key, None)
            _adapter = self._adapters.get(message.channel)
            if _adapter:
                with contextlib.suppress(Exception):
                    await _adapter.clear_typing(message.chat_id, thread_id=message.thread_id)
                if hasattr(_adapter, "_streaming_buffers") and hasattr(_adapter, "_make_session_key"):
                    _adapter._streaming_buffers.pop(
                        _adapter._make_session_key(message.chat_id, message.thread_id), None,
                    )
            # 标记会话处理完成
            async with self._interrupt_lock:
                self._mark_session_processing(session_key, False)

    _MAX_INTERRUPT_ITERATIONS = 20

    async def _process_pending_interrupts(self, session_key: str, session: Session) -> None:
        """
        处理会话中剩余的中断消息

        在当前消息处理完成后，继续处理排队的中断消息
        """
        iterations = 0
        while self.has_pending_interrupt(session_key):
            iterations += 1
            if iterations > self._MAX_INTERRUPT_ITERATIONS:
                logger.warning(
                    f"[Interrupt] {session_key}: exceeded {self._MAX_INTERRUPT_ITERATIONS} iterations, "
                    "deferring remaining interrupts"
                )
                break
            interrupt_msg = await self.check_interrupt(session_key)
            if not interrupt_msg:
                break

            logger.info(f"[Interrupt] Processing pending message for {session_key}")

            try:
                # 预处理媒体
                await self._preprocess_media(interrupt_msg)

                # 记录到会话
                session.add_message(
                    role="user",
                    content=interrupt_msg.plain_text,
                    message_id=interrupt_msg.id,
                    channel_message_id=interrupt_msg.channel_message_id,
                    is_interrupt=True,  # 标记为中断消息
                )
                self.session_manager.mark_dirty()  # 触发保存

                # 调用 Agent 处理（typing 由外层 typing_task 覆盖，中断不走流式）
                response_text, _ = await self._call_agent(
                    session, interrupt_msg, allow_streaming=False,
                )

                # 后处理钩子
                for hook in self._post_process_hooks:
                    try:
                        response_text = await hook(interrupt_msg, response_text)
                    except Exception as hook_err:
                        logger.warning(f"[Gateway] Post-process hook {hook.__qualname__} failed: {hook_err}")

                # 记录响应（含思维链摘要 + 工具执行摘要）
                _int_chain = None
                try:
                    _int_chain = session.get_metadata("_last_chain_summary")
                    session.set_metadata("_last_chain_summary", None)
                except Exception:
                    pass
                _int_tool_summary = None
                try:
                    _int_agent = getattr(self.agent_handler, "_agent_ref", None)
                    if _int_agent and hasattr(_int_agent, "build_tool_trace_summary"):
                        _int_tool_summary = _int_agent.build_tool_trace_summary() or None
                except Exception:
                    pass
                _int_meta: dict = {}
                if _int_chain:
                    _int_meta["chain_summary"] = _int_chain
                if _int_tool_summary:
                    _int_meta["tool_summary"] = _int_tool_summary
                session.add_message(role="assistant", content=response_text, **_int_meta)
                self.session_manager.mark_dirty()  # 触发保存

                # 发送响应
                await self._send_response(interrupt_msg, response_text)

            except Exception as e:
                logger.error(f"Error processing interrupt message: {e}", exc_info=True)
                await self._send_error(interrupt_msg, str(e))

    async def _preprocess_media(self, message: UnifiedMessage) -> None:
        """
        预处理媒体文件（下载语音、图片到本地，语音自动转文字）
        """
        adapter = self._adapters.get(message.channel)
        if not adapter:
            return

        import asyncio

        # 并发下载/转写（避免多媒体消息逐个串行导致延迟叠加）
        sem = asyncio.Semaphore(4)

        async def _process_voice(voice) -> None:
            try:
                async with sem:
                    if not voice.local_path:
                        local_path = await asyncio.wait_for(
                            adapter.download_media(voice), timeout=60
                        )
                        voice.local_path = str(local_path)
                        logger.info(f"Voice downloaded: {voice.local_path}")

                if voice.local_path and not voice.transcription:
                    transcription = await asyncio.wait_for(
                        self._transcribe_voice_local(voice.local_path), timeout=120
                    )
                    if transcription:
                        voice.transcription = transcription
                        logger.info(f"Voice transcribed: {transcription}")
                    else:
                        voice.transcription = "[语音识别失败]"
            except TimeoutError:
                logger.error(f"Voice processing timed out: {voice.filename}")
                voice.transcription = "[语音处理超时]"
            except Exception as e:
                logger.error(f"Failed to process voice: {e}")
                voice.transcription = "[语音处理失败]"

        async def _process_image(img) -> None:
            try:
                if img.local_path:
                    return
                async with sem:
                    local_path = await adapter.download_media(img)
                    img.local_path = str(local_path)
                    logger.info(f"Image downloaded: {img.local_path}")
            except Exception as e:
                logger.error(f"Failed to download image: {e}")

        async def _process_video(vid) -> None:
            try:
                if vid.local_path:
                    return
                async with sem:
                    local_path = await adapter.download_media(vid)
                    vid.local_path = str(local_path)
                    logger.info(f"Video downloaded: {vid.local_path}")
            except Exception as e:
                logger.error(f"Failed to download video: {e}")

        async def _process_file(fil) -> None:
            try:
                if fil.local_path:
                    return
                async with sem:
                    local_path = await adapter.download_media(fil)
                    fil.local_path = str(local_path)
                    logger.info(f"File downloaded: {fil.local_path}")
            except Exception as e:
                logger.error(f"Failed to download file: {e}")

        tasks = []
        for voice in getattr(message.content, "voices", []) or []:
            tasks.append(_process_voice(voice))
        for img in getattr(message.content, "images", []) or []:
            tasks.append(_process_image(img))
        for vid in getattr(message.content, "videos", []) or []:
            tasks.append(_process_video(vid))
        for fil in getattr(message.content, "files", []) or []:
            tasks.append(_process_file(fil))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _transcribe_voice_local(self, audio_path: str) -> str | None:
        """
        使用本地 Whisper 进行语音转文字

        使用预加载的模型，避免每次都重新加载
        """
        import asyncio

        try:
            # 检查文件是否存在
            if not Path(audio_path).exists():
                logger.error(f"Audio file not found: {audio_path}")
                return None

            # 确保模型已加载
            if not self._whisper_loaded and not self._whisper_unavailable:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._load_whisper_model)

            if self._whisper is None:
                if not self._whisper_unavailable:
                    logger.error("Whisper model not available")
                return None

            # 在线程池中运行转写（避免阻塞事件循环）
            whisper_lang = self._whisper_language

            def transcribe():
                from openakita.channels.media.audio_utils import (
                    ensure_whisper_compatible,
                    load_wav_as_numpy,
                )

                compatible_path = ensure_whisper_compatible(audio_path)

                kwargs = {}
                if whisper_lang and whisper_lang != "auto":
                    kwargs["language"] = whisper_lang

                # 对已转换的 WAV 尝试直接 numpy 加载，绕过 ffmpeg 依赖
                if compatible_path.endswith(".wav"):
                    audio_array = load_wav_as_numpy(compatible_path)
                    if audio_array is not None:
                        result = self._whisper.transcribe(audio_array, **kwargs)
                        return result["text"].strip()

                result = self._whisper.transcribe(compatible_path, **kwargs)
                return result["text"].strip()

            # 异步执行
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, transcribe)

            return text if text else None

        except Exception as e:
            logger.error(f"Voice transcription failed: {e}", exc_info=True)
            return None

    async def _send_typing(self, message: UnifiedMessage) -> None:
        """发送正在输入状态"""
        adapter = self._adapters.get(message.channel)
        if adapter and hasattr(adapter, "send_typing"):
            try:
                await adapter.send_typing(message.chat_id, thread_id=message.thread_id)
            except Exception:
                pass  # 忽略 typing 发送失败

    async def _send_feedback(self, message: UnifiedMessage, text: str) -> None:
        """向 IM 用户发送轻量反馈消息（中断操作确认等）"""
        adapter = self._adapters.get(message.channel)
        if adapter and hasattr(adapter, "send_text"):
            try:
                _meta = {"is_group": (message.metadata or {}).get("is_group", message.chat_type == "group")}
                await adapter.send_text(
                    chat_id=message.chat_id,
                    text=text,
                    reply_to=message.channel_message_id,
                    metadata=_meta,
                )
            except Exception as e:
                logger.warning(f"[Feedback] Failed to send feedback to {message.channel}: {e}")

    async def _call_agent_with_typing(self, session: Session, message: UnifiedMessage) -> tuple[str, bool]:
        """调用 Agent 处理消息，期间持续发送 typing 状态"""
        import asyncio

        typing_task = asyncio.create_task(self._keep_typing(message))

        try:
            return await self._call_agent(session, message)
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    async def _keep_typing(self, message: UnifiedMessage) -> None:
        """持续发送 typing 状态（每 4 秒一次）"""
        import asyncio

        while True:
            await self._send_typing(message)
            await asyncio.sleep(4)  # Telegram typing 状态持续约 5 秒

    async def _call_agent(
        self, session: Session, message: UnifiedMessage, *, allow_streaming: bool = True,
    ) -> tuple[str, bool]:
        """
        调用 Agent 处理消息（支持多模态：图片、语音）

        Returns:
            (response_text, streamed_ok) — streamed_ok=True 表示已通过流式卡片
            发送给用户，调用方应跳过 _send_response。
        """
        if not self.agent_handler:
            return ("Agent handler not configured", False)

        try:
            # 构建输入（文本 + 图片 + 语音）
            input_text = message.plain_text
            _has_voice = bool(message.content.voices)

            # 处理语音文件 - 双路策略：保留原始音频 + Whisper 转写
            audio_data_list = []
            for voice in message.content.voices:
                # 双路保留：始终存储原始音频路径到 pending_audio
                if voice.local_path and Path(voice.local_path).exists():
                    audio_data_list.append({
                        "local_path": voice.local_path,
                        "mime_type": voice.mime_type or "audio/wav",
                        "duration": voice.duration,
                        "transcription": voice.transcription if voice.transcription not in (None, "", "[语音识别失败]") else None,
                    })

                if voice.transcription and voice.transcription not in ("[语音识别失败]", ""):
                    # 语音已转写，用转写文字作为输入（保底）
                    if not input_text.strip() or "[语音:" in input_text:
                        input_text = f"[来源:语音转写] {voice.transcription}"
                        logger.info(f"Using voice transcription as input: {input_text}")
                    else:
                        input_text = f"{input_text}\n\n[语音内容: {voice.transcription}]"
                elif voice.local_path:
                    # 语音未转写成功，保存路径供 Agent 手动处理
                    session.set_metadata(
                        "pending_voices",
                        [
                            {
                                "local_path": voice.local_path,
                                "duration": voice.duration,
                            }
                        ],
                    )
                    if not input_text.strip() or "[语音:" in input_text:
                        input_text = (
                            f"[用户发送了语音消息，但自动识别失败。文件路径: {voice.local_path}]"
                        )
                    logger.info(f"Voice transcription failed, file: {voice.local_path}")

            # 存储原始音频数据到 session（供 Agent 做三级决策）
            if audio_data_list:
                session.set_metadata("pending_audio", audio_data_list)
                logger.info(f"Stored {len(audio_data_list)} raw audio files for Agent decision")

            # 处理图片文件 - 多模态输入
            images_data = []
            for img in message.content.images:
                if img.local_path and Path(img.local_path).exists():
                    try:
                        with open(img.local_path, "rb") as f:
                            image_data = base64.b64encode(f.read()).decode("utf-8")
                            images_data.append(
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": img.mime_type or "image/jpeg",
                                        "data": image_data,
                                    },
                                    "local_path": img.local_path,  # 也保存路径
                                }
                            )
                    except Exception as e:
                        logger.error(f"Failed to read image: {e}")

            # 如果有图片，构建多模态输入
            if images_data:
                # 存储图片数据到 session，供 Agent 使用
                session.set_metadata("pending_images", images_data)
                if not input_text.strip():
                    input_text = "[用户发送了图片]"
                logger.info(f"Processing multimodal message with {len(images_data)} images")

            # 处理视频文件 - 多模态输入
            videos_data = []
            VIDEO_SIZE_LIMIT = 7 * 1024 * 1024  # 7MB (base64 后 ~9.3MB，低于 DashScope 10MB data-uri 限制)
            for vid in message.content.videos:
                if vid.local_path and Path(vid.local_path).exists():
                    try:
                        file_size = Path(vid.local_path).stat().st_size
                        if file_size <= VIDEO_SIZE_LIMIT:
                            with open(vid.local_path, "rb") as f:
                                video_data = base64.b64encode(f.read()).decode("utf-8")
                                videos_data.append(
                                    {
                                        "type": "video",
                                        "source": {
                                            "type": "base64",
                                            "media_type": vid.mime_type or "video/mp4",
                                            "data": video_data,
                                        },
                                        "local_path": vid.local_path,
                                    }
                                )
                            logger.info(f"Video encoded as base64: {vid.local_path} ({file_size / 1024 / 1024:.1f}MB)")
                        else:
                            # 视频超过大小限制，用 ffmpeg 截取关键帧降级为图片
                            logger.info(
                                f"Video too large ({file_size / 1024 / 1024:.1f}MB > 7MB), "
                                f"extracting keyframes: {vid.local_path}"
                            )
                            keyframes = await self._extract_video_keyframes(vid.local_path)
                            if keyframes:
                                for kf_data, kf_mime in keyframes:
                                    images_data.append(
                                        {
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": kf_mime,
                                                "data": kf_data,
                                            },
                                            "local_path": vid.local_path,
                                        }
                                    )
                                # 更新 pending_images
                                session.set_metadata("pending_images", images_data)
                                logger.info(f"Extracted {len(keyframes)} keyframes from video")
                            else:
                                logger.warning(f"Failed to extract keyframes from: {vid.local_path}")
                    except Exception as e:
                        logger.error(f"Failed to process video: {e}")

            if videos_data:
                session.set_metadata("pending_videos", videos_data)
                if not input_text.strip():
                    input_text = "[用户发送了视频]"
                logger.info(f"Processing multimodal message with {len(videos_data)} videos")

            # 处理文件 - PDF 等文档的多模态输入
            files_data = []
            for fil in message.content.files:
                if fil.local_path and Path(fil.local_path).exists():
                    try:
                        mime = fil.mime_type or ""
                        suffix = Path(fil.local_path).suffix.lower()
                        if suffix == ".pdf" or "pdf" in mime:
                            file_data = base64.b64encode(
                                Path(fil.local_path).read_bytes()
                            ).decode("utf-8")
                            files_data.append({
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": file_data,
                                },
                                "filename": fil.filename or Path(fil.local_path).name,
                                "local_path": fil.local_path,
                            })
                            logger.info(f"PDF file encoded: {fil.local_path}")
                        else:
                            # 非 PDF 文件，作为文本描述
                            input_text += f"\n[附件: {fil.filename or Path(fil.local_path).name} ({mime or suffix})]"
                    except Exception as e:
                        logger.error(f"Failed to process file: {e}")

            if files_data:
                session.set_metadata("pending_files", files_data)
                if not input_text.strip():
                    input_text = "[用户发送了文件]"
                logger.info(f"Processing multimodal message with {len(files_data)} files")

            # === 中断机制：传递 gateway 引用和会话标识 ===
            session_key = self._get_session_key(message)
            session.set_metadata("_gateway", self)
            session.set_metadata("_session_key", session_key)
            session.set_metadata("_current_message", message)

            # === 流式 / 非流式分支 ===
            adapter = self._adapters.get(message.channel)
            is_group = message.chat_type == "group"
            from ..config import settings as _cfg
            use_streaming = (
                allow_streaming
                and adapter is not None
                and adapter.has_capability("streaming")
                and hasattr(adapter, "is_streaming_enabled")
                and adapter.is_streaming_enabled(is_group)
                and self.agent_handler_stream is not None
                and not (_cfg.multi_agent_enabled and getattr(self, "_orchestrator_ref", None) is not None)
            )

            streamed_ok = False
            _has_orchestrator = (
                _cfg.multi_agent_enabled
                and getattr(self, "_orchestrator_ref", None) is not None
            )
            if use_streaming:
                response, streamed_ok = await self._call_agent_streaming(
                    session, input_text, message, adapter,
                )
            elif _has_orchestrator:
                # Orchestrator 自带 idle_timeout + hard_timeout 进度监控，
                # 不再套 wait_for 墙钟超时，避免活跃任务被误杀
                response = await self.agent_handler(session, input_text)
            else:
                _AGENT_TIMEOUT = float(
                    os.environ.get("AGENT_HANDLER_TIMEOUT", "1200")
                )
                try:
                    response = await asyncio.wait_for(
                        self.agent_handler(session, input_text),
                        timeout=_AGENT_TIMEOUT,
                    )
                except TimeoutError:
                    logger.error(
                        f"[Gateway] Agent handler timed out after {_AGENT_TIMEOUT}s"
                    )
                    response = (
                        f"⚠️ 处理超时（{int(_AGENT_TIMEOUT)}秒），"
                        f"请稍后重试或简化您的问题。"
                    )

            return (response, streamed_ok)

        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            return (f"处理出错: {str(e)}", False)
        finally:
            session.set_metadata("pending_images", None)
            session.set_metadata("pending_videos", None)
            session.set_metadata("pending_audio", None)
            session.set_metadata("pending_files", None)
            session.set_metadata("pending_voices", None)
            session.set_metadata("_gateway", None)
            session.set_metadata("_session_key", None)
            session.set_metadata("_current_message", None)

    async def _call_agent_streaming(
        self, session: Session, input_text: str,
        message: UnifiedMessage, adapter,
    ) -> tuple[str, bool]:
        """Consume agent_handler_stream, pipe tokens to adapter.stream_token,
        then finalize.  Returns (full_reply, streamed_ok)."""
        reply_text = ""
        is_group = message.chat_type == "group"

        chain_push = session.get_metadata("chain_push")
        if chain_push is None:
            from ..config import settings as _s
            chain_push = _s.im_chain_push
        can_stream_thinking = (
            chain_push
            and hasattr(adapter, "stream_thinking")
        )

        _thinking_buf = ""

        if hasattr(adapter, "_streaming_buffers") and hasattr(adapter, "_make_session_key"):
            _sk = adapter._make_session_key(message.chat_id, message.thread_id)
            adapter._streaming_buffers.setdefault(_sk, "")

        _STREAM_TIMEOUT = float(os.environ.get("AGENT_HANDLER_TIMEOUT", "1200"))

        async def _consume_stream():
            nonlocal reply_text, _thinking_buf
            async for event in self.agent_handler_stream(session, input_text):
                etype = event.get("type")
                if etype == "text_delta":
                    delta = event.get("content", "")
                    reply_text += delta
                    await adapter.stream_token(
                        message.chat_id, delta,
                        thread_id=message.thread_id,
                        is_group=is_group,
                    )
                elif etype == "thinking_delta":
                    _thinking_buf += event.get("content", "")
                    if can_stream_thinking:
                        thinking_content = event.get("content", "")
                        if thinking_content:
                            await adapter.stream_thinking(
                                message.chat_id, thinking_content,
                                thread_id=message.thread_id,
                                is_group=is_group,
                            )
                elif etype == "thinking_end":
                    if can_stream_thinking and hasattr(adapter, "stream_thinking"):
                        dur_ms = event.get("duration_ms", 0)
                        sk = adapter._make_session_key(message.chat_id, message.thread_id) if hasattr(adapter, "_make_session_key") else ""
                        if sk and hasattr(adapter, "_streaming_thinking_ms") and dur_ms:
                            adapter._streaming_thinking_ms[sk] = dur_ms
                    if not can_stream_thinking and chain_push and _thinking_buf:
                        preview = _thinking_buf.strip().replace("\n", " ")[:120]
                        if len(_thinking_buf) > 120:
                            preview += "..."
                        await self.emit_progress_event(session, f"💭 {preview}")
                    _thinking_buf = ""
                elif etype == "chain_text" and chain_push:
                    content = event.get("content", "")
                    if content:
                        if can_stream_thinking and hasattr(adapter, "stream_chain_text"):
                            await adapter.stream_chain_text(
                                message.chat_id, content,
                                thread_id=message.thread_id,
                                is_group=is_group,
                            )
                        else:
                            await self.emit_progress_event(session, content)
                elif etype == "ask_user":
                    if not reply_text:
                        reply_text = event.get("question", "")
                elif etype == "error":
                    err_msg = event.get("message", "")
                    if not reply_text:
                        reply_text = f"处理出错: {err_msg}"
                elif etype == "done":
                    pass

        try:
            await asyncio.wait_for(_consume_stream(), timeout=_STREAM_TIMEOUT)
        except TimeoutError:
            logger.error(f"[IM] Streaming agent timed out after {_STREAM_TIMEOUT}s")
            if not reply_text:
                reply_text = f"⚠️ 处理超时（{int(_STREAM_TIMEOUT)}秒），请稍后重试或简化您的问题。"
        except Exception as e:
            logger.error(f"[IM] Streaming agent error: {e}", exc_info=True)
            if not reply_text:
                reply_text = f"处理出错: {str(e)}"

        if not reply_text or not reply_text.strip():
            return (reply_text, False)

        # For adapters that render <think> natively, extract ALL accumulated
        # progress lines and wrap them in a <think> block.
        if getattr(adapter, "_THINK_TAG_NATIVE", False):
            _buf = self._progress_buffers.get(session.session_key, [])
            if _buf:
                _all_lines = [ln.strip() for ln in _buf if ln.strip()]
                _buf[:] = []
                if _all_lines:
                    _think_text = "\n".join(_all_lines)
                    reply_text = f"<think>\n{_think_text}\n</think>\n{reply_text}"

        await self.flush_progress(session)

        ok = await adapter.finalize_stream(
            message.chat_id, reply_text, thread_id=message.thread_id,
        )
        return (reply_text, ok)

    # 各渠道单条消息最大字符数（留余量）
    # - telegram: API 硬限制 4096，留余量 → 4000
    # - wework:   流式/response_url 模式下 send_message 会覆写而非追加，不应分片
    # - dingtalk:  Webhook 文本/Markdown ≈20000
    # - feishu:    卡片消息 ≈30000
    # - onebot/qqbot: 一般无严格限制
    _CHANNEL_MAX_LENGTH: dict[str, int] = {
        "telegram": 4000,
        "wework":   0,       # 0 = 不分片，整条发送
        "dingtalk":  18000,
        "feishu":    28000,
        "onebot":    20000,
        "qqbot":     20000,
        "wechat":    4000,
    }
    _DEFAULT_MAX_LENGTH = 4000

    # 分片间发送间隔（秒），避免触发平台限流
    _SPLIT_SEND_INTERVAL: dict[str, float] = {
        "telegram": 0.5,
        "wechat":   1.5,
    }
    _DEFAULT_SPLIT_INTERVAL = 0.15

    # 进度消息节流间隔（秒）— 不支持卡片更新的平台需要更高的节流间隔
    _CHANNEL_PROGRESS_THROTTLE: dict[str, float] = {
        "wechat": 8.0,
    }

    @staticmethod
    def _split_text(text: str, max_length: int) -> list[str]:
        """
        将长文本按换行符分割为不超过 max_length 的分片，
        尽量保持段落完整；超长单行会按字符强制切断。
        """
        if max_length <= 0 or len(text) <= max_length:
            return [text]

        chunks: list[str] = []
        current = ""
        for line in text.split("\n"):
            candidate = f"{current}{line}\n" if current else f"{line}\n"
            if len(candidate) <= max_length:
                current = candidate
                continue

            # 当前缓冲区已有内容 → 先入列
            if current:
                chunks.append(current.rstrip())
                current = ""

            # 单行本身就超长 → 按字符强制切断
            if len(line) + 1 > max_length:
                while line:
                    chunks.append(line[:max_length])
                    line = line[max_length:]
            else:
                current = line + "\n"

        if current:
            chunks.append(current.rstrip())
        return chunks

    async def _send_response(self, original: UnifiedMessage, response: str) -> None:
        """
        发送响应（带重试、按渠道分割长消息、分片间限流保护）

        分片失败策略：
        - 首次以 Markdown 分片发送
        - 任一分片 3 次重试仍失败 → 中止剩余分片，改用纯文本整体重发
        - 纯文本重发也失败 → 发送失败通知

        媒体补发：
        - 在发送文本前解析回复中的 ![](path)、MEDIA: 行、裸路径
        - 先发清理后的文本，再逐个补发图片/文件
        """
        import asyncio
        from .media_parser import parse_media_from_text

        adapter = self._adapters.get(original.channel)
        if not adapter:
            logger.error(f"No adapter for channel: {original.channel}")
            return

        # 解析文本中的媒体引用
        media_result = parse_media_from_text(response)
        text_to_send = media_result.cleaned_text

        channel = original.channel
        base_channel = channel.split(":")[0].split("_")[0]

        max_length = self._CHANNEL_MAX_LENGTH.get(
            base_channel, self._DEFAULT_MAX_LENGTH
        )
        from .text_splitter import chunk_markdown_text
        messages = chunk_markdown_text(text_to_send, max_length) if text_to_send else []

        interval = self._SPLIT_SEND_INTERVAL.get(
            base_channel, self._DEFAULT_SPLIT_INTERVAL
        )

        outgoing_meta = dict(original.metadata) if original.metadata else {}
        if original.channel_user_id:
            outgoing_meta["channel_user_id"] = original.channel_user_id

        failed_at = -1

        for i, text in enumerate(messages):
            if i > 0 and interval > 0:
                await asyncio.sleep(interval)

            outgoing = OutgoingMessage.text(
                chat_id=original.chat_id,
                text=text,
                reply_to=original.channel_message_id if i == 0 else None,
                thread_id=original.thread_id,
                parse_mode="markdown",
                metadata=outgoing_meta,
            )

            from .retry import async_with_retry
            try:
                await async_with_retry(
                    adapter.send_message, outgoing,
                    max_retries=2,
                    base_delay=1.0,
                    operation_name=f"send_response[{i + 1}/{len(messages)}]",
                )
            except Exception as e:
                logger.error(
                    f"Failed to send response part {i + 1}/{len(messages)} "
                    f"after retries: {e}"
                )
                failed_at = i
                break

        if failed_at < 0:
            await self._send_extracted_media(adapter, original, media_result, outgoing_meta)
            return

        # 分片发送失败 → 仅将失败及后续分片以纯文本重发，避免已送达的部分重复
        remaining = messages[failed_at:]
        logger.info(
            f"[SendResponse] Split send failed at part {failed_at + 1}/{len(messages)}, "
            f"retrying {len(remaining)} remaining part(s) as plain text"
        )
        for j, plain_text in enumerate(remaining):
            if j > 0 and interval > 0:
                await asyncio.sleep(interval)
            plain_out = OutgoingMessage.text(
                chat_id=original.chat_id,
                text=plain_text,
                reply_to=original.channel_message_id if (failed_at + j) == 0 else None,
                thread_id=original.thread_id,
                parse_mode="none",
                metadata=outgoing_meta,
            )
            try:
                await adapter.send_message(plain_out)
            except Exception as e2:
                logger.error(
                    f"Plain-text fallback also failed for part {failed_at + j + 1}: {e2}"
                )
                with contextlib.suppress(Exception):
                    await adapter.send_text(
                        chat_id=original.chat_id,
                        text="消息发送失败，请稍后重试。",
                        reply_to=original.channel_message_id,
                        thread_id=original.thread_id,
                        metadata=outgoing_meta,
                    )
                return

        await self._send_extracted_media(adapter, original, media_result, outgoing_meta)

    async def _send_extracted_media(
        self,
        adapter: "ChannelAdapter",
        original: UnifiedMessage,
        media_result: "MediaParseResult",
        outgoing_meta: dict,
    ) -> None:
        """补发从回复文本中解析出的图片/文件"""
        reply_to = original.thread_id or original.channel_message_id

        if adapter.has_capability("send_image"):
            for img in media_result.images:
                if img.is_url:
                    continue
                try:
                    await adapter.send_image(
                        original.chat_id, img.path, reply_to=reply_to,
                    )
                except Exception as e:
                    logger.warning(f"[SendResponse] send extracted image failed: {e}")
                    with contextlib.suppress(Exception):
                        fname = Path(img.path).name if img.path else "image"
                        await adapter.send_text(
                            original.chat_id, f"📎 {fname}",
                            reply_to=reply_to, metadata=outgoing_meta,
                        )

        if adapter.has_capability("send_file"):
            all_files = list(media_result.files) + list(media_result.videos)
            for file in all_files:
                if file.is_url:
                    continue
                try:
                    await adapter.send_file(
                        original.chat_id, file.path, reply_to=reply_to,
                    )
                except Exception as e:
                    logger.warning(f"[SendResponse] send extracted file failed: {e}")
                    with contextlib.suppress(Exception):
                        fname = Path(file.path).name if file.path else "file"
                        await adapter.send_text(
                            original.chat_id, f"📎 {fname}",
                            reply_to=reply_to, metadata=outgoing_meta,
                        )

        if adapter.has_capability("send_voice"):
            for audio in media_result.audios:
                if audio.is_url:
                    continue
                try:
                    await adapter.send_voice(
                        original.chat_id, audio.path, reply_to=reply_to,
                    )
                except Exception as e:
                    logger.warning(f"[SendResponse] send extracted audio failed: {e}")
                    with contextlib.suppress(Exception):
                        fname = Path(audio.path).name if audio.path else "audio"
                        await adapter.send_text(
                            original.chat_id, f"📎 {fname}",
                            reply_to=reply_to, metadata=outgoing_meta,
                        )

    async def _send_error(self, original: UnifiedMessage, error: str) -> None:
        """
        发送错误提示
        """
        adapter = self._adapters.get(original.channel)
        if not adapter:
            return

        try:
            _meta = {"is_group": (original.metadata or {}).get("is_group", original.chat_type == "group")}
            await adapter.send_text(
                chat_id=original.chat_id,
                text=f"❌ 处理出错: {error}",
                reply_to=original.thread_id or original.channel_message_id,
                metadata=_meta,
            )
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")

    # ==================== 待推送自检报告 ====================

    async def _maybe_deliver_pending_selfcheck_report(self, message: UnifiedMessage) -> None:
        """
        检查并推送未送达的自检报告

        自检在凌晨 4:00 运行，但此时通常没有活跃会话（30 分钟超时），
        报告会以 reported=false 状态保存在 data/selfcheck/ 目录下。
        当用户发消息时，这里会把未送达的报告补推给用户。

        去重由报告 JSON 的 reported 字段保证，无需额外的日期锁。
        """
        try:
            await self._deliver_pending_selfcheck_report(message)
        except Exception as e:
            logger.error(f"Pending selfcheck report delivery failed: {e}")

    async def _deliver_pending_selfcheck_report(self, message: UnifiedMessage) -> None:
        """
        读取 data/selfcheck/ 中未推送的报告并发送给用户

        检查今天和昨天的报告文件，找到第一个 reported=false 的报告推送。
        直接通过适配器发送，不写入会话上下文（避免污染对话历史）。
        """
        import json
        from datetime import date as date_type

        from ..config import settings

        selfcheck_dir = settings.selfcheck_dir
        if not selfcheck_dir.exists():
            return

        today = date_type.today()
        # 检查今天和昨天的报告（自检在凌晨 4:00 生成当天日期的报告）
        candidates = [
            today.isoformat(),
            (today - timedelta(days=1)).isoformat(),
        ]

        for report_date in candidates:
            json_file = selfcheck_dir / f"{report_date}_report.json"
            md_file = selfcheck_dir / f"{report_date}_report.md"

            if not json_file.exists():
                continue

            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                # 已推送过则跳过
                if data.get("reported"):
                    continue

                if not md_file.exists():
                    continue

                with open(md_file, encoding="utf-8") as f:
                    report_md = f.read()

                if not report_md.strip():
                    continue

                # 通过适配器直接发送（不写入会话上下文）
                adapter = self._adapters.get(message.channel)
                if not adapter or not adapter.is_running:
                    continue

                header = f"📋 每日系统自检报告（{report_date}）\n\n"
                full_text = header + report_md
                _meta = {"is_group": (message.metadata or {}).get("is_group", message.chat_type == "group")}

                # 分段发送（兼容 Telegram 4096 限制）
                max_len = 3500
                text = full_text
                while text:
                    if len(text) <= max_len:
                        await adapter.send_text(message.chat_id, text, metadata=_meta)
                        break
                    cut = text.rfind("\n", 0, max_len)
                    if cut < 1000:
                        cut = max_len
                    await adapter.send_text(message.chat_id, text[:cut].rstrip(), metadata=_meta)
                    text = text[cut:].lstrip()

                # 标记为已推送
                data["reported"] = True
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                logger.info(
                    f"Delivered pending selfcheck report for {report_date} "
                    f"to {message.channel}/{message.chat_id}"
                )
                break  # 只推送最近一份未读报告

            except Exception as e:
                logger.error(f"Failed to deliver pending selfcheck report for {report_date}: {e}")

    # ==================== 主动发送 ====================

    async def send(
        self,
        channel: str,
        chat_id: str,
        text: str,
        record_to_session: bool = True,
        user_id: str = "system",
        **kwargs,
    ) -> str | None:
        """
        主动发送消息

        Args:
            channel: 目标通道
            chat_id: 目标聊天
            text: 消息文本
            record_to_session: 是否记录到会话历史
            user_id: 发送者标识

        Returns:
            消息 ID 或 None
        """
        adapter = self._adapters.get(channel)
        if not adapter:
            logger.error(f"No adapter for channel: {channel}")
            return None

        try:
            result = await adapter.send_text(chat_id, text, **kwargs)

            # 记录到 session 历史
            if record_to_session and self.session_manager:
                try:
                    self.session_manager.add_message(
                        channel=channel,
                        chat_id=chat_id,
                        user_id=user_id,
                        role="system",  # 系统发送的消息
                        content=text,
                        source="gateway.send",
                    )
                except Exception as e:
                    logger.warning(f"Failed to record message to session: {e}")

            return result
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return None

    async def send_to_session(
        self,
        session: Session,
        text: str,
        role: str = "assistant",
        **kwargs,
    ) -> str | None:
        """
        发送消息到会话
        """
        # 话题感知：session 关联了话题且调用者未显式指定 reply_to 时，
        # 自动使用 thread_id 使消息留在话题内（飞书等平台需要 reply 才能定位到话题）
        if session.thread_id and "reply_to" not in kwargs:
            kwargs["reply_to"] = session.thread_id

        result = await self.send(
            channel=session.channel,
            chat_id=session.chat_id,
            text=text,
            record_to_session=False,  # 下面手动记录
            **kwargs,
        )

        # 记录到 session 历史（用指定的 role）；发送失败时不记录，避免上下文不一致
        if self.session_manager and result is not None:
            try:
                session.add_message(role=role, content=text, source="send_to_session")
                self.session_manager.mark_dirty()  # 触发保存
            except Exception as e:
                logger.warning(f"Failed to record message to session: {e}")

        return result

    async def _try_patch_progress_to_card(
        self, session: Session, new_lines: list[str],
    ) -> bool:
        """尝试将进度文本 PATCH 到思考卡片（不消费卡片）。

        非流式路径下，进度消息通过此方法直接更新占位卡片，
        避免发送独立灰色文本消息。卡片由最终回复消费（pop）。

        Returns True if PATCH succeeded.
        """
        if not session:
            return False
        adapter = self._adapters.get(session.channel)
        if (
            not adapter
            or not hasattr(adapter, "_thinking_cards")
            or not hasattr(adapter, "_make_session_key")
            or not hasattr(adapter, "_patch_card_content")
        ):
            return False

        chat_id = session.chat_id
        thread_id = None
        try:
            current_message = session.get_metadata("_current_message")
            if current_message:
                thread_id = getattr(current_message, "thread_id", None)
        except Exception:
            pass

        sk = adapter._make_session_key(chat_id, thread_id)
        card_id = adapter._thinking_cards.get(sk)
        if not card_id:
            return False

        session_key = session.session_key
        accum = self._progress_card_accum.setdefault(session_key, [])
        accum.extend(new_lines)
        if len(accum) > 20:
            accum[:] = accum[-20:]

        display = "\n".join(accum)
        try:
            return await adapter._patch_card_content(card_id, display)
        except Exception:
            return False

    async def emit_progress_event(
        self,
        session: Session,
        text: str,
        *,
        throttle_seconds: float | None = None,
        role: str = "system",
        force: bool = False,
    ) -> None:
        """
        发出“进度事件”并由网关节流/合并后发送。

        - 受 im_chain_push 全局开关和会话级 chain_push 元数据控制。
        - 多条事件会在节流窗口内合并为一条，避免刷屏。
        - 进度消息默认以 system role 记录到 session（不影响模型对话历史）。
        - 传 force=True 可绕过 chain_push 检查（仅用于必须送达的系统通知）。
        """
        if not session or not text:
            return

        # chain_push 开关守卫
        if not force:
            from ..config import settings as _s
            _push = session.get_metadata("chain_push")
            if _push is None:
                _push = _s.im_chain_push
            if not _push:
                return

        session_key = session.session_key
        if throttle_seconds is not None:
            throttle = throttle_seconds
        else:
            base_ch = session.channel.split(":")[0].split("_")[0]
            throttle = self._CHANNEL_PROGRESS_THROTTLE.get(
                base_ch, self._progress_throttle_seconds,
            )

        buf = self._progress_buffers.setdefault(session_key, [])
        if buf and buf[-1] == text:
            return  # 连续相同消息去重
        buf.append(text)

        # For adapters with native <think> support, accumulate only — no
        # intermediate send.  All buffered lines will be extracted and wrapped
        # in <think> tags at reply time (see _send_response / _call_agent_streaming).
        _adapter = self._adapters.get(session.channel)
        if _adapter and getattr(_adapter, "_THINK_TAG_NATIVE", False):
            return

        existing = self._progress_flush_tasks.get(session_key)
        if existing and not existing.done():
            return

        async def _flush() -> None:
            try:
                await asyncio.sleep(max(0.0, float(throttle)))
                lines = self._progress_buffers.get(session_key, [])
                if not lines:
                    return
                self._progress_buffers[session_key] = []

                if await self._try_patch_progress_to_card(session, lines):
                    return

                combined = "\n".join(lines[:20])
                reply_to = None
                try:
                    current_message = session.get_metadata("_current_message")
                    reply_to = (
                        getattr(current_message, "channel_message_id", None)
                        if current_message
                        else None
                    )
                except Exception:
                    reply_to = None

                await self.send(
                    channel=session.channel,
                    chat_id=session.chat_id,
                    text=combined,
                    record_to_session=False,
                    reply_to=reply_to,
                )
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[Progress] flush failed: {e}")

        self._progress_flush_tasks[session_key] = asyncio.create_task(_flush())

    async def flush_progress(self, session: Session) -> None:
        """
        立即 flush 指定 session 的进度缓冲区。

        在最终回答发送前调用，确保思维链消息先于回答到达。
        """
        if not session:
            return

        # _THINK_TAG_NATIVE adapters: buffer will be extracted and wrapped in
        # <think> tags at reply time (F2 in _handle_message / _call_agent_streaming).
        # Do NOT send as a separate message here.
        _adapter = self._adapters.get(session.channel)
        if _adapter and getattr(_adapter, "_THINK_TAG_NATIVE", False):
            return

        session_key = session.session_key

        # 等待已运行的 flush task 完成，确保进度消息在回复前送达
        existing = self._progress_flush_tasks.pop(session_key, None)
        if existing and not existing.done():
            existing.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await existing

        lines = self._progress_buffers.get(session_key, [])
        if not lines:
            return

        self._progress_buffers[session_key] = []

        if await self._try_patch_progress_to_card(session, lines):
            return

        combined = "\n".join(lines[:20])
        reply_to = None
        try:
            current_message = session.get_metadata("_current_message")
            reply_to = (
                getattr(current_message, "channel_message_id", None)
                if current_message
                else None
            )
        except Exception:
            reply_to = None

        try:
            await self.send(
                channel=session.channel,
                chat_id=session.chat_id,
                text=combined,
                record_to_session=False,
                reply_to=reply_to,
            )
        except Exception as e:
            logger.warning(f"[Progress] flush_progress failed: {e}")

    async def broadcast(
        self,
        text: str,
        channels: list[str] | None = None,
        user_ids: list[str] | None = None,
    ) -> dict[str, int]:
        """
        广播消息

        Args:
            text: 消息文本
            channels: 目标通道列表（None 表示所有）
            user_ids: 目标用户列表（None 表示所有）

        Returns:
            {channel: sent_count}
        """
        results = {}

        # 获取目标会话
        sessions = self.session_manager.list_sessions()

        for session in sessions:
            # 过滤通道
            if channels and session.channel not in channels:
                continue

            # 过滤用户
            if user_ids and session.user_id not in user_ids:
                continue

            try:
                await self.send_to_session(session, text)
                results[session.channel] = results.get(session.channel, 0) + 1
            except Exception as e:
                logger.error(f"Broadcast error to {session.id}: {e}")

        return results

    # ==================== 中间件 ====================

    def add_pre_process_hook(
        self,
        hook: Callable[[UnifiedMessage], Awaitable[UnifiedMessage]],
    ) -> None:
        """
        添加预处理钩子

        在消息处理前调用，可以修改消息
        """
        self._pre_process_hooks.append(hook)

    def add_post_process_hook(
        self,
        hook: Callable[[UnifiedMessage, str], Awaitable[str]],
    ) -> None:
        """
        添加后处理钩子

        在 Agent 响应后调用，可以修改响应
        """
        self._post_process_hooks.append(hook)

    # ==================== 统计 ====================

    def get_stats(self) -> dict:
        """获取网关统计"""
        return {
            "running": self._running,
            "adapters": {name: adapter.is_running for name, adapter in self._adapters.items()},
            "queue_size": self._message_queue.qsize(),
            "sessions": self.session_manager.get_session_count(),
        }
