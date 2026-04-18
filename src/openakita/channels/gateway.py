"""
Message gateway

Unified message input/output:
- Message routing
- Session-management integration
- Media preprocessing (images, voice, video)
- Agent invocation
- Message-interrupt mechanism (allows new messages to be inserted between tool calls)
- System-level command interception (model switching, etc.)
"""

import asyncio
import base64
import collections
import contextlib
import logging
import os
import random
import sys
import time as _time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..sessions import Session, SessionManager
from .base import ChannelAdapter
from .group_response import GroupResponseMode, SmartModeThrottle
from .types import MediaStatus, OutgoingMessage, UnifiedMessage


def _notify_im_event(event: str, data: dict | None = None) -> None:
    """Fire-and-forget WS broadcast for IM events."""
    try:
        from openakita.api.routes.websocket import broadcast_event

        asyncio.ensure_future(broadcast_event(event, data))
    except Exception:
        pass


from ..utils.errors import format_user_friendly_error as format_user_friendly_error  # re-export

if TYPE_CHECKING:
    from ..core.brain import Brain
    from ..llm.stt_client import STTClient
    from .media_parser import MediaParseResult

logger = logging.getLogger(__name__)

# Agent handler function type
AgentHandler = Callable[[Session, str], Awaitable[str]]


class InterruptPriority(Enum):
    """Interrupt priority"""

    NORMAL = 0  # regular message, queued
    HIGH = 1  # high priority, inserted between tools
    URGENT = 2  # urgent, attempt immediate interrupt


@dataclass
class InterruptMessage:
    """Interrupt-message wrapper"""

    message: UnifiedMessage
    priority: InterruptPriority = InterruptPriority.HIGH
    timestamp: datetime = field(default_factory=datetime.now)

    def __lt__(self, other: "InterruptMessage") -> bool:
        """Priority-queue comparison: higher priority first; same priority ordered by time"""
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value
        return self.timestamp < other.timestamp


# ==================== Model-switch command handling ====================


@dataclass
class ModelSwitchSession:
    """Model-switch interactive session"""

    session_key: str
    mode: str  # "switch" | "priority" | "restore"
    step: str  # "select" | "confirm"
    selected_model: str | None = None
    selected_priority: list[str] | None = None
    started_at: datetime = field(default_factory=datetime.now)
    timeout_minutes: int = 5

    @property
    def is_expired(self) -> bool:
        """Check whether the session has timed out"""
        return datetime.now() > self.started_at + timedelta(minutes=self.timeout_minutes)


class ModelCommandHandler:
    """
    Model-command handler

    System-level command interception that bypasses the LLM, ensuring switching works even if the model crashes.

    Supported commands:
    - /model: show the current model and the available list
    - /switch [model-name]: temporarily switch model (12 hours)
    - /priority: adjust model priority (permanent)
    - /restore: restore the default model
    - /cancel: cancel the current operation
    """

    # Command list
    MODEL_COMMANDS = {"/model", "/switch", "/priority", "/restore", "/cancel"}

    def __init__(self, brain: Optional["Brain"] = None):
        self._brain: Brain | None = brain
        # In-progress switch sessions {session_key: ModelSwitchSession}
        self._switch_sessions: dict[str, ModelSwitchSession] = {}

    def set_brain(self, brain: "Brain") -> None:
        """Set the Brain instance"""
        self._brain = brain

    def is_model_command(self, text: str) -> bool:
        """Check whether it is a model-related command"""
        if not text:
            return False
        text_lower = text.lower().strip()
        # Full command or command with arguments
        for cmd in self.MODEL_COMMANDS:
            if text_lower == cmd or text_lower.startswith(cmd + " "):
                return True
        return False

    def is_in_session(self, session_key: str) -> bool:
        """Check whether an interactive session is in progress"""
        if session_key not in self._switch_sessions:
            return False
        session = self._switch_sessions[session_key]
        if session.is_expired:
            del self._switch_sessions[session_key]
            return False
        return True

    async def handle_command(self, session_key: str, text: str) -> str | None:
        """
        Handle a model command

        Args:
            session_key: session identifier
            text: user input

        Returns:
            Response text, or None if it is not a command
        """
        if not self._brain:
            return "❌ Model management not initialized"

        text = text.strip()
        text_lower = text.lower()

        # /model - show current model status
        if text_lower == "/model":
            return self._format_model_status()

        # /switch - switch model
        if text_lower == "/switch":
            return self._start_switch_session(session_key)

        if text_lower.startswith("/switch "):
            model_name = text[8:].strip()
            return self._start_switch_session(session_key, model_name)

        # /priority - adjust priority
        if text_lower == "/priority":
            return self._start_priority_session(session_key)

        # /restore - restore default
        if text_lower == "/restore":
            return self._start_restore_session(session_key)

        # /cancel - cancel operation
        if text_lower == "/cancel":
            return self._cancel_session(session_key)

        return None

    async def handle_input(self, session_key: str, text: str) -> str:
        """
        Handle user input inside an interactive session

        Args:
            session_key: session identifier
            text: user input

        Returns:
            Response text
        """
        if not self._brain:
            return "❌ Model management not initialized"

        # Check whether cancelled
        if text.lower().strip() == "/cancel":
            return self._cancel_session(session_key)

        session = self._switch_sessions.get(session_key)
        if not session:
            return "Session has ended"

        if session.is_expired:
            del self._switch_sessions[session_key]
            return "⏰ Operation timed out (5 min), auto-cancelled"

        # Handle based on mode and step
        if session.mode == "switch":
            return self._handle_switch_input(session_key, session, text)
        elif session.mode == "priority":
            return self._handle_priority_input(session_key, session, text)
        elif session.mode == "restore":
            return self._handle_restore_input(session_key, session, text)

        return "Unknown operation"

    def _format_model_status(self) -> str:
        """Format model status info"""
        models = self._brain.list_available_models()
        override = self._brain.get_override_status()

        lines = ["📋 **Model Status**\n"]

        for i, m in enumerate(models):
            status = ""
            if m["is_current"]:
                status = " ⬅️ current (temp)" if m["is_override"] else " ⬅️ current"
            health = "✅" if m["is_healthy"] else "❌"
            lines.append(f"{i + 1}. {health} **{m['name']}** ({m['model']}){status}")

        if override:
            lines.append(f"\n⏱️ Temp override expires in: {override['remaining_hours']:.1f} hours")
            lines.append(f"   Expires at: {override['expires_at']}")

        lines.append("\n💡 Commands: /switch [model] | /priority [adjust] | /restore [default]")

        return "\n".join(lines)

    def _start_switch_session(self, session_key: str, model_name: str = "") -> str:
        """Begin a switch session"""
        models = self._brain.list_available_models()

        # If a model name was given, jump to the confirm step
        if model_name:
            # Look up the model
            target = None
            for m in models:
                if (
                    m["name"].lower() == model_name.lower()
                    or m["model"].lower() == model_name.lower()
                ):
                    target = m
                    break

            if not target:
                # Try numeric index
                try:
                    idx = int(model_name) - 1
                    if 0 <= idx < len(models):
                        target = models[idx]
                except ValueError:
                    pass

            if not target:
                available = ", ".join(m["name"] for m in models)
                return f"❌ Model '{model_name}' not found\nAvailable: {available}"

            # Create the session and enter the confirm step
            self._switch_sessions[session_key] = ModelSwitchSession(
                session_key=session_key,
                mode="switch",
                step="confirm",
                selected_model=target["name"],
            )

            return (
                f"⚠️ Confirm switch to **{target['name']}** ({target['model']})?\n\n"
                f"Temp override duration: 12 hours\n"
                f"Type **yes** to confirm, anything else to cancel"
            )

        # No model specified; show the selection list
        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="switch",
            step="select",
        )

        lines = ["📋 **Available Models**\n"]
        for i, m in enumerate(models):
            status = " ⬅️ current" if m["is_current"] else ""
            health = "✅" if m["is_healthy"] else "❌"
            lines.append(f"{i + 1}. {health} **{m['name']}** ({m['model']}){status}")

        lines.append("\nEnter a number or model name, /cancel to abort")

        return "\n".join(lines)

    def _start_priority_session(self, session_key: str) -> str:
        """Begin a priority-adjustment session"""
        models = self._brain.list_available_models()

        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="priority",
            step="select",
        )

        lines = ["📋 **Current Priority** (lower = higher priority)\n"]
        for i, m in enumerate(models):
            lines.append(f"{i}. {m['name']}")

        lines.append("\nEnter model names in priority order, space-separated")
        lines.append("e.g. claude kimi dashscope minimax")
        lines.append("/cancel to abort")

        return "\n".join(lines)

    def _start_restore_session(self, session_key: str) -> str:
        """Begin a restore-default session"""
        override = self._brain.get_override_status()

        if not override:
            return "No active override, using the default model"

        self._switch_sessions[session_key] = ModelSwitchSession(
            session_key=session_key,
            mode="restore",
            step="confirm",
        )

        return (
            f"⚠️ Confirm restore default model?\n\n"
            f"Current override: {override['endpoint_name']}\n"
            f"Time left: {override['remaining_hours']:.1f} hours\n\n"
            f"Type **yes** to confirm, anything else to cancel"
        )

    def _cancel_session(self, session_key: str) -> str:
        """Cancel the current session"""
        if session_key in self._switch_sessions:
            del self._switch_sessions[session_key]
            return "✅ Cancelled"
        return "No active operation"

    def _handle_switch_input(self, session_key: str, session: ModelSwitchSession, text: str) -> str:
        """Handle input for the switch session"""
        text = text.strip()

        if session.step == "select":
            models = self._brain.list_available_models()
            target = None

            # Try numeric index
            try:
                idx = int(text) - 1
                if 0 <= idx < len(models):
                    target = models[idx]
            except ValueError:
                # Try name matching
                for m in models:
                    if m["name"].lower() == text.lower() or m["model"].lower() == text.lower():
                        target = m
                        break

            if not target:
                return f"❌ Model '{text}' not found, try again or /cancel"

            # Enter the confirm step
            session.selected_model = target["name"]
            session.step = "confirm"

            return (
                f"⚠️ Confirm switch to **{target['name']}** ({target['model']})?\n\n"
                f"Temp override duration: 12 hours\n"
                f"Type **yes** to confirm, anything else to cancel"
            )

        elif session.step == "confirm":
            if text.lower() == "yes":
                # Perform the switch
                success, msg = self._brain.switch_model(
                    session.selected_model, conversation_id=session_key
                )
                del self._switch_sessions[session_key]

                if success:
                    return f"✅ {msg}\n\nSend /model to check status"
                else:
                    return f"❌ Switch failed: {msg}"
            else:
                del self._switch_sessions[session_key]
                return "✅ Cancelled"

        return "Unknown step"

    def _handle_priority_input(
        self, session_key: str, session: ModelSwitchSession, text: str
    ) -> str:
        """Handle input for the priority-adjustment session"""
        text = text.strip()

        if session.step == "select":
            models = self._brain.list_available_models()
            model_names = {m["name"].lower(): m["name"] for m in models}

            # Parse user input
            input_names = text.split()
            priority_order = []

            for name in input_names:
                name_lower = name.lower()
                if name_lower in model_names:
                    priority_order.append(model_names[name_lower])
                else:
                    return f"❌ Model '{name}' not found, try again or /cancel"

            if len(priority_order) != len(models):
                return f"❌ Please provide all {len(models)} models in order"

            # Enter the confirm step
            session.selected_priority = priority_order
            session.step = "confirm"

            lines = ["⚠️ Confirm priority order:\n"]
            for i, name in enumerate(priority_order):
                lines.append(f"{i}. {name}")
            lines.append("\n**This is permanent!** Type **yes** to confirm")

            return "\n".join(lines)

        elif session.step == "confirm":
            if text.lower() == "yes":
                # Perform the priority update
                success, msg = self._brain.update_model_priority(session.selected_priority)
                del self._switch_sessions[session_key]

                if success:
                    return f"✅ {msg}"
                else:
                    return f"❌ Update failed: {msg}"
            else:
                del self._switch_sessions[session_key]
                return "✅ Cancelled"

        return "Unknown step"

    def _handle_restore_input(
        self, session_key: str, session: ModelSwitchSession, text: str
    ) -> str:
        """Handle input for the restore-default session"""
        if text.lower() == "yes":
            success, msg = self._brain.restore_default_model(conversation_id=session_key)
            del self._switch_sessions[session_key]

            if success:
                return f"✅ {msg}"
            else:
                return f"❌ {msg}"
        else:
            del self._switch_sessions[session_key]
            return "✅ Cancelled"


# ==================== Thinking-mode command handling ====================


class ThinkingCommandHandler:
    """
    Thinking-mode command handler

    System-level command interception that bypasses the LLM.

    Supported commands:
    - /thinking [on|off|auto]: switch thinking mode
    - /thinking_depth [low|medium|high]: set thinking depth
    - /chain [on|off]: toggle reasoning-chain progress push (off by default)
    """

    THINKING_COMMANDS = {"/thinking", "/thinking_depth", "/chain"}

    VALID_MODES = {"on", "off", "auto"}
    VALID_DEPTHS = {"low", "medium", "high"}

    DEPTH_LABELS = {
        "low": "low (fast)",
        "medium": "medium (balanced)",
        "high": "high (deep reasoning)",
    }

    def __init__(self, session_manager: "SessionManager"):
        self._session_manager = session_manager

    def is_thinking_command(self, text: str) -> bool:
        """Check whether it is a thinking-mode command"""
        if not text:
            return False
        text_lower = text.lower().strip()
        for cmd in self.THINKING_COMMANDS:
            if text_lower == cmd or text_lower.startswith(cmd + " "):
                return True
        return False

    async def handle_command(self, session_key: str, text: str, session: "Session") -> str | None:
        """
        Handle a thinking-mode command

        Args:
            session_key: session identifier
            text: user input
            session: current session object

        Returns:
            Response text
        """
        text = text.strip()
        text_lower = text.lower()

        # /chain - view or set the reasoning-chain push switch
        if text_lower == "/chain":
            return self._format_chain_status(session)

        if text_lower.startswith("/chain "):
            value = text_lower.split(None, 1)[1].strip()
            if value not in {"on", "off"}:
                return f"❌ Invalid value: `{value}`\nOptions: `on` | `off`"
            enabled = value == "on"
            session.set_metadata("chain_push", enabled)
            label = "enabled" if enabled else "disabled"
            return f"✅ Reasoning chain push is now **{label}**"

        # /thinking - view or set the thinking mode
        if text_lower == "/thinking":
            return self._format_thinking_status(session)

        if text_lower.startswith("/thinking ") and not text_lower.startswith("/thinking_depth"):
            mode = text_lower.split(None, 1)[1].strip()
            if mode not in self.VALID_MODES:
                return f"❌ Invalid mode: `{mode}`\nOptions: `on` | `off` | `auto`"
            session.set_metadata("thinking_mode", mode if mode != "auto" else None)
            mode_label = {"on": "on", "off": "off", "auto": "auto (system decides)"}
            return f"✅ Thinking mode set to: **{mode_label[mode]}**"

        # /thinking_depth - view or set the thinking depth
        if text_lower == "/thinking_depth":
            return self._format_depth_status(session)

        if text_lower.startswith("/thinking_depth "):
            depth = text_lower.split(None, 1)[1].strip()
            if depth not in self.VALID_DEPTHS:
                return (
                    f"❌ Invalid depth: `{depth}`\nOptions: `low` | `medium` | `high`"
                )
            session.set_metadata("thinking_depth", depth)
            return f"✅ Thinking depth set to: **{self.DEPTH_LABELS[depth]}**"

        return None

    def _format_chain_status(self, session: "Session") -> str:
        """Format reasoning-chain push state"""
        from openakita.config import settings

        current = session.get_metadata("chain_push")
        if current is None:
            current = settings.im_chain_push
            source = "(global default)"
        else:
            source = "(session setting)"

        label = "enabled" if current else "disabled"

        lines = [
            "📡 **Reasoning Chain Push**\n",
            f"Status: **{label}** {source}\n",
            "When enabled, thinking progress and tool activity are streamed in real-time.",
            "Disabling only reduces message volume — internal reasoning is unaffected.\n",
            "**Commands:**",
            "`/chain on` — enable push",
            "`/chain off` — disable push",
        ]
        return "\n".join(lines)

    def _format_thinking_status(self, session: "Session") -> str:
        """Format thinking-mode state"""
        current_mode = session.get_metadata("thinking_mode")
        current_depth = session.get_metadata("thinking_depth")

        mode_label = "auto (system decides)"
        if current_mode == "on":
            mode_label = "on"
        elif current_mode == "off":
            mode_label = "off"

        depth_label = self.DEPTH_LABELS.get(current_depth or "medium", "medium (balanced)")

        lines = [
            "🧠 **Thinking Mode Settings**\n",
            f"Mode: **{mode_label}**",
            f"Depth: **{depth_label}**\n",
            "**Commands:**",
            "`/thinking on` — force deep thinking",
            "`/thinking off` — disable deep thinking",
            "`/thinking auto` — auto-decide (default)",
            "`/thinking_depth low|medium|high` — set depth",
        ]
        return "\n".join(lines)

    def _format_depth_status(self, session: "Session") -> str:
        """Format thinking-depth state"""
        current_depth = session.get_metadata("thinking_depth")
        depth_label = self.DEPTH_LABELS.get(current_depth or "medium", "medium (balanced)")

        lines = [
            "📊 **Thinking Depth Settings**\n",
            f"Current depth: **{depth_label}**\n",
        ]
        for key, label in self.DEPTH_LABELS.items():
            marker = " ⬅️" if key == (current_depth or "medium") else ""
            lines.append(f"• `{key}` — {label}{marker}")
        lines.append("\nUsage: `/thinking_depth low|medium|high`")
        return "\n".join(lines)


# ==================== Ultimate-restart command handling ====================


@dataclass
class RestartSession:
    """Restart-confirmation session"""

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
    Ultimate-restart command handler

    Intercepted at the earliest point in _on_message, so it responds even if the system is stuck.
    Flow: /restart -> generate confirmation code -> user sends the code back -> trigger restart.
    Supports countdown auto-cancel and manual cancel.
    """

    RESTART_COMMANDS = {"/restart", "/重启"}
    CANCEL_COMMANDS = {"/cancel_restart", "/取消重启"}
    CONFIRM_TIMEOUT = 60

    def __init__(self) -> None:
        self._pending: dict[str, RestartSession] = {}
        self._timeout_tasks: dict[str, asyncio.Task] = {}
        # Injected by MessageGateway
        self._send_feedback_fn: Callable[[UnifiedMessage, str], Awaitable[None]] | None = None
        self._shutdown_event: asyncio.Event | None = None

    # ---------- Command recognition ----------

    def is_restart_command(self, text: str) -> bool:
        return text.strip().lower() in self.RESTART_COMMANDS

    def is_cancel_command(self, text: str) -> bool:
        return text.strip().lower() in self.CANCEL_COMMANDS

    def has_pending_session(self, session_key: str) -> bool:
        """Check whether the user has a pending restart-confirmation session"""
        session = self._pending.get(session_key)
        if session is None:
            return False
        if session.is_expired:
            self._cleanup(session_key)
            return False
        return True

    def is_confirm_code(self, session_key: str, text: str) -> bool:
        """Check whether text could be a restart-confirmation code (exactly 6 digits)"""
        session = self._pending.get(session_key)
        if session is None:
            return False
        return text.strip().isdigit() and len(text.strip()) == 6

    # ---------- Core flow ----------

    async def handle_restart_command(
        self,
        session_key: str,
        message: UnifiedMessage,
    ) -> None:
        """Handle the /restart command: generate a confirmation code and send it to the user"""
        if session_key in self._pending:
            old = self._pending[session_key]
            await self._send(
                message,
                f"⚠️ A restart request is already pending (code **{old.confirm_code}**, "
                f"{old.remaining_seconds}s remaining).\n"
                f"Send the code to confirm, or /cancel_restart to cancel.",
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
            f"🔄 **Restart Confirmation**\n\n"
            f"Code: `{code}`\n\n"
            f"Reply with this code within **{self.CONFIRM_TIMEOUT} seconds** to restart.\n"
            f"Send `/cancel_restart` to cancel.",
        )

    async def handle_pending_input(
        self,
        session_key: str,
        message: UnifiedMessage,
    ) -> bool:
        """
        Handle user input during a pending-confirmation session.

        Returns:
            True  -- input has been consumed (the caller should return and not process further)
            False -- input is unrelated to restart; the caller should pass it to the normal flow
        """
        text = (message.plain_text or "").strip()
        session = self._pending.get(session_key)
        if session is None:
            return False

        # Cancel
        if text.lower() in self.CANCEL_COMMANDS or text.lower() == "/cancel":
            self._cleanup(session_key)
            logger.info(f"[Restart] Cancelled by user: {session_key}")
            await self._send(message, "❌ Restart cancelled.")
            return True

        # Verify confirmation code
        if text == session.confirm_code:
            self._cleanup(session_key)
            logger.warning(f"[Restart] Confirmed by {session_key}, triggering restart...")
            await self._send(message, "✅ Code confirmed. Restarting in 3 seconds…")
            await asyncio.sleep(3)
            await self._trigger_restart()
            return True

        # 6-digit number but no match -> report error
        if text.isdigit() and len(text) == 6:
            await self._send(
                message,
                f"❌ Wrong code ({session.remaining_seconds}s remaining).\n"
                f"Send `{session.confirm_code}` to confirm or `/cancel_restart` to cancel.",
            )
            return True

        # Non-numeric input -> do not consume; pass it to the normal flow (avoid intercepting regular messages)
        return False

    # ---------- Timeout handling ----------

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
            await self._send(msg, "⏰ Restart confirmation timed out, auto-cancelled.")

    # ---------- Restart trigger ----------

    async def _trigger_restart(self) -> None:
        from openakita import config as cfg

        cfg._restart_requested = True
        if self._shutdown_event is not None:
            logger.warning("[Restart] Setting shutdown_event for graceful restart")
            self._shutdown_event.set()
        else:
            logger.error("[Restart] No shutdown_event available, restart may not work")

    # ---------- Helpers ----------

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
    Unified message gateway

    Responsibilities:
    - Manage multiple channel adapters
    - Route incoming messages to sessions
    - Invoke the Agent
    - Send replies back to channels
    """

    # Whisper sizes that support .en-only models (large has no .en variant)
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
            session_manager: session manager
            agent_handler: Agent handler function (session, message) -> response
            whisper_model: Whisper model size (tiny, base, small, medium, large); defaults to base
            whisper_language: STT language (zh/en/auto/other language codes)
            stt_client: online STT client (optional; replaces local Whisper)
        """
        self.session_manager = session_manager
        self.agent_handler = agent_handler
        self.agent_handler_stream = None  # set by main.py for streaming IM support
        self.stt_client = stt_client

        from .bot_config import BotConfigStore

        self.bot_config = BotConfigStore()

        from .chat_aliases import ChatAliasStore

        self.chat_aliases = ChatAliasStore()

        # Registered adapters {channel_name: adapter}
        self._adapters: dict[str, ChannelAdapter] = {}

        # Message processing queue
        self._message_queue: asyncio.Queue[UnifiedMessage] = asyncio.Queue()

        # Processing task
        self._processing_task: asyncio.Task | None = None
        self._running = False
        self._accepting = True  # False = drain mode; reject new messages
        self._started_adapters: list[str] = []
        self._failed_adapters: list[str] = []
        self._failed_adapter_reasons: dict[str, str] = {}
        self._retry_failed_task: asyncio.Task | None = None

        # Middleware
        self._pre_process_hooks: list[Callable[[UnifiedMessage], Awaitable[UnifiedMessage]]] = []
        self._post_process_hooks: list[Callable[[UnifiedMessage, str], Awaitable[str]]] = []

        self._plugin_hooks = None  # set by start_im_channels() in main.py

        # Whisper STT model (lazy-loaded or preloaded at startup)
        self._whisper_language = whisper_language.lower().strip()
        # When language is English and the model size has a .en variant, switch to the smaller, faster .en model automatically
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
        self._whisper_unavailable = False  # ImportError -> do not retry within this process

        # ==================== Message-interrupt mechanism ====================
        # Per-session interrupt queue {session_key: asyncio.PriorityQueue[InterruptMessage]}
        self._interrupt_queues: dict[str, asyncio.PriorityQueue] = {}

        # Sessions currently being processed {session_key: bool}
        self._processing_sessions: dict[str, bool] = {}

        # Concurrent-session control
        _max_concurrent = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "5"))
        self._concurrency_sem = asyncio.Semaphore(_max_concurrent)
        self._session_tasks: dict[str, asyncio.Task] = {}

        # Interrupt lock (prevents concurrent modification)
        self._interrupt_lock = asyncio.Lock()

        # Interrupt-handling callback (set by the Agent)
        self._interrupt_callbacks: dict[str, Callable[[], Awaitable[str | None]]] = {}

        # Model-command handler (system-level command interception)
        self._model_cmd_handler: ModelCommandHandler = ModelCommandHandler()

        # Thinking-mode command handler
        self._thinking_cmd_handler: ThinkingCommandHandler = ThinkingCommandHandler(session_manager)

        # Ultimate-restart command handler (intercepted earliest in _on_message, bypassing queue/Agent)
        self._restart_cmd_handler: RestartCommandHandler = RestartCommandHandler()
        self._restart_cmd_handler._send_feedback_fn = self._send_feedback

        # Externally injected shutdown_event (set by main.py via set_shutdown_event)
        self._shutdown_event: asyncio.Event | None = None

        # ==================== Progress event stream (Plan/Deliver etc.) ====================
        # Goal: push 'execution progress display' down to the gateway side to avoid model/tool spam.
        self._progress_buffers: dict[str, list[str]] = {}  # session_key -> [lines]
        self._progress_flush_tasks: dict[str, asyncio.Task] = {}  # session_key -> flush task
        self._progress_throttle_seconds: float = 2.0  # default throttling window
        self._progress_card_accum: dict[
            str, list[str]
        ] = {}  # session_key -> accumulated progress lines (for card PATCH)

        # ==================== DM Pairing authorization ====================
        self._dm_pairing: "DMPairingManager | None" = None

        # ==================== Group-chat response policy ====================
        self._smart_throttle = SmartModeThrottle()

        # ==================== Group-chat context buffer ====================
        # Cache filtered group-chat messages (when not @'ed) so later @ messages can inject them as context
        # key: "channel:chat_id", value: deque of context entries
        self._group_context_buffer: dict[str, collections.deque] = {}
        self._GROUP_CONTEXT_MAX_ITEMS = 20
        self._GROUP_CONTEXT_TTL = 600  # 10 minutes

    def enable_dm_pairing(self, data_dir: "Path") -> None:
        """Enable DM Pairing authorization."""
        from .dm_pairing import DMPairingManager

        self._dm_pairing = DMPairingManager(data_dir)
        logger.info("DM Pairing enabled")

    async def _handle_pair_command(
        self, cmd: str, message: "UnifiedMessage"
    ) -> str | None:
        """Handle /pair command for DM Pairing."""
        if not self._dm_pairing:
            return "DM Pairing is not enabled."

        parts = cmd.strip().split()
        sub = parts[1] if len(parts) > 1 else "generate"

        if sub == "generate":
            code = self._dm_pairing.generate_code(
                created_by=f"{message.channel}:{message.user_id}"
            )
            return (
                f"🔑 Pairing code: **{code}**\n\n"
                f"Valid for 1 hour. Share this with the user who needs access."
            )
        elif sub == "list":
            authorized = self._dm_pairing.list_authorized()
            if not authorized:
                return "No authorized channels."
            return "Authorized channels:\n" + "\n".join(f"- {a}" for a in authorized)
        elif sub == "revoke" and len(parts) >= 3:
            target = parts[2]
            parts_t = target.split(":", 1)
            if len(parts_t) == 2:
                ok = self._dm_pairing.revoke(parts_t[0], parts_t[1])
                return f"✅ Revoked: {target}" if ok else f"❌ Not found: {target}"
            return "Usage: /pair revoke channel:chat_id"
        else:
            return (
                "/pair generate — generate pairing code\n"
                "/pair list — list authorized channels\n"
                "/pair revoke channel:chat_id — revoke access"
            )

    async def _handle_background_command(
        self, text: str, message: "UnifiedMessage"
    ) -> str | None:
        """
        Handle /background <prompt> — run a task in the background.

        Creates an isolated agent session that runs without blocking the
        current conversation. Results are delivered when complete.
        """
        parts = text.split(None, 1)
        if len(parts) < 2:
            return (
                "Usage: `/background <task description>`\n\n"
                "Examples:\n"
                "- `/background summarize today's meeting notes`\n"
                "- `/bg generate the latest project status report`"
            )

        prompt = parts[1].strip()
        if not prompt:
            return "❌ Please provide a task description."

        session_key = self._get_session_key(message)
        bg_id = f"bg_{session_key}_{int(_time.time())}"

        await self._send_feedback(
            message,
            f"⏳ Background task started: {prompt[:60]}...\nYou'll be notified when it completes."
        )

        async def _run_background():
            try:
                from ..scheduler.executor import TaskExecutor
                from ..scheduler.task import ScheduledTask, TaskType, TriggerType

                executor = TaskExecutor(gateway=self, timeout_seconds=1200)

                task = ScheduledTask(
                    id=bg_id,
                    name=f"background task: {prompt[:30]}",
                    description=prompt,
                    trigger_type=TriggerType.ONCE,
                    trigger_config={},
                    task_type=TaskType.TASK,
                    prompt=prompt,
                    channel_id=message.channel,
                    chat_id=message.chat_id,
                    user_id=message.user_id,
                )

                success, result = await executor.execute(task)

                if message.channel and message.chat_id:
                    status = "✅ Background task complete" if success else "❌ Background task failed"
                    result_text = f"{status}\n\n**Task**: {prompt[:80]}\n\n**Result**:\n{result}"
                    try:
                        await self.send(
                            channel=message.channel,
                            chat_id=message.chat_id,
                            text=result_text,
                        )
                    except Exception as e:
                        logger.error(f"Failed to deliver background result: {e}")

            except Exception as e:
                logger.error(f"Background task {bg_id} failed: {e}", exc_info=True)
                try:
                    await self.send(
                        channel=message.channel,
                        chat_id=message.chat_id,
                        text=f"❌ Background task error: {e}",
                    )
                except Exception:
                    pass

        asyncio.create_task(_run_background())
        return None

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
                    + (
                        f" (group: {'ON' if info['group_streaming'] else 'OFF'})"
                        if info["streaming_enabled"]
                        else ""
                    ),
                    f"Group mode: {info['group_response_mode']}",
                ]
                return "\n".join(lines)
            return "Feishu adapter not available"

        if sub == "auth":
            if adapter and hasattr(adapter, "get_auth_url"):
                url = adapter.get_auth_url()
                return f"Please open the following link in your browser to complete Feishu user auth:\n{url}"
            return "Feishu adapter not available"

        # /feishu or /feishu help
        return (
            "/feishu start — show adapter status and version\n"
            "/feishu auth  — get Feishu user auth link\n"
            "/feishu help  — show this help"
        )

    async def _handle_mode_command(self, user_text: str) -> str:
        """Handle the /模式 or /mode command: multi-Agent mode is now always on by default."""
        return "ℹ️ **Multi-agent mode is always on** and cannot be toggled."

    def _is_agent_command(self, text: str) -> bool:
        """Check whether it is a multi-Agent command"""
        if not text:
            return False
        t = text.strip().lower()
        if t in ("/状态", "/status", "/重置", "/agent_reset"):
            return True
        if t in ("/切换", "/switch") or t.startswith(("/切换 ", "/switch ")):
            return True
        return False

    async def _handle_agent_command(self, message: UnifiedMessage, user_text: str) -> str | None:
        """
        Handle multi-Agent commands.

        Supports: /切换 /switch /status /重置 /agent_reset
        """
        if getattr(self, "_orchestrator_ref", None) is None:
            return "Multi-agent system is initializing, please try again shortly."

        session = self.session_manager.get_session(
            channel=message.channel,
            chat_id=message.chat_id,
            user_id=message.user_id,
            thread_id=message.thread_id,
        )
        if not session:
            return "❌ Unable to get session"

        self._apply_bot_agent_profile(session, message.channel)

        t = user_text.strip().lower()

        # /切换 or /switch [agent_id]
        if t in ("/切换", "/switch") or t.startswith(("/切换 ", "/switch ")):
            return await self._handle_agent_switch(session, t)

        # /状态 or /status
        if t in ("/状态", "/status"):
            return self._format_agent_status(session)

        # /重置 or /agent_reset
        if t in ("/重置", "/agent_reset"):
            return self._handle_agent_reset(session)

        return None

    async def _handle_agent_switch(self, session: Session, user_text: str) -> str:
        """Handle /切换 [agent_id] or /switch [agent_id]"""
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
            # No argument: list available Agents
            lines = ["📋 **Available Agents**\n"]
            current_id = session.context.agent_profile_id
            for p in all_profiles:
                marker = " ⬅️ current" if p.id == current_id else ""
                lines.append(f"• `{p.id}` — {p.icon} {p.name}: {p.description}{marker}")
            lines.append("\nUsage: `/switch <agent_id>`")
            return "\n".join(lines)

        # Argument present: switch
        agent_id = arg.lower()
        profile_map = {p.id.lower(): p for p in all_profiles}
        if agent_id not in profile_map:
            available = ", ".join(p.id for p in all_profiles)
            return f"❌ Agent `{agent_id}` not found\nAvailable: {available}"

        ctx = session.context
        p = profile_map[agent_id]
        old_id = ctx.agent_profile_id
        if old_id.lower() == agent_id:
            return f"ℹ️ Already using **{p.icon} {p.name}**"

        ctx.agent_switch_history.append(
            {
                "from": old_id,
                "to": p.id,
                "at": datetime.now().isoformat(),
            }
        )
        ctx.agent_profile_id = p.id
        self.session_manager.mark_dirty()
        logger.info(f"[IM] Agent switched: {old_id!r} -> {agent_id!r} for {session.session_key}")

        return f"✅ Switched to **{p.icon} {p.name}** ({p.description})"

    def _format_system_help(self) -> str:
        """Format the global /help output (available in all modes) based on the unified command registry"""
        from ..config import settings
        from .slash_commands import format_help

        lines = [
            "📖 **Quick Commands**\n",
            "**Task control:**",
            "  `stop` / `/stop` / `kill` — stop current task",
            "  `skip` / `/skip` — skip current step",
            "  Send a message while task is running — inject into current task",
            "",
        ]

        lines.append(format_help(scope="im"))

        lines.extend(
            [
                "**Multi-agent:**",
                "  `/switch` — list or switch agent",
                "  `/status` — show current agent info",
                "  `/agent_reset` — reset to default agent",
                "",
            ]
        )

        return "\n".join(lines)

    def _format_agent_help(self) -> str:
        """Format the multi-Agent-specific /help output (kept for internal compatibility)"""
        return self._format_system_help()

    def _format_agent_status(self, session: Session) -> str:
        """Format /status output"""
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
            return f"🤖 **Current Agent**\n\n**{p.icon} {p.name}** (`{p.id}`)\n{p.description}"
        return f"🤖 **Current Agent**\n\nID: `{current_id}`"

    def _handle_agent_reset(self, session: Session) -> str:
        """Handle /重置: reset to this bot's bound default agent (or "default")"""
        from datetime import datetime

        reset_target = session.get_metadata("_bot_default_agent") or "default"

        ctx = session.context
        old_id = ctx.agent_profile_id
        if old_id == reset_target:
            label = "the default agent" if reset_target == "default" else f"**{reset_target}**"
            return f"ℹ️ Already using {label}"

        ctx.agent_switch_history.append(
            {
                "from": old_id,
                "to": reset_target,
                "at": datetime.now().isoformat(),
            }
        )
        ctx.agent_profile_id = reset_target
        self.session_manager.mark_dirty()
        logger.info(f"[IM] Agent reset to {reset_target} for {session.session_key}")

        if reset_target == "default":
            return "✅ Reset to default agent"
        return f"✅ Reset to **{reset_target}**"

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
            logger.info(f"[IM] Applied bot default agent: {bot_agent} for {session.session_key}")

    # ==================== Natural-language intent detection ====================

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
        """Get the group-chat response mode.

        Priority: per-chat bot_config > per-bot adapter > global settings > default MENTION_ONLY
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
        """Get the group-chat whitelist (per-bot config > global config)"""
        adapter = self._adapters.get(channel)
        per_bot = getattr(adapter, "_group_allowlist", None)
        if per_bot:
            return set(per_bot) if not isinstance(per_bot, set) else per_bot
        from ..config import settings

        raw = getattr(settings, "group_allowlist", None)
        if raw:
            return set(raw) if not isinstance(raw, set) else raw
        return set()

    # ==================== Group-chat context-buffer methods ====================

    def _buffer_group_context(
        self,
        message: "UnifiedMessage",
        *,
        text: str | None = None,
    ) -> None:
        """Cache filtered group-chat messages into the context buffer.

        The key is ``channel:chat_id`` (group-chat level); each record includes a timestamp, user, and text.
        Old entries beyond TTL or the max-count are evicted automatically.
        """
        buf_key = f"{message.channel}:{message.chat_id}"
        buf = self._group_context_buffer.get(buf_key)
        if buf is None:
            buf = collections.deque(maxlen=self._GROUP_CONTEXT_MAX_ITEMS)
            self._group_context_buffer[buf_key] = buf

        now = _time.time()
        # Evict expired entries
        while buf and (now - buf[0]["ts"]) > self._GROUP_CONTEXT_TTL:
            buf.popleft()

        display = text or message.plain_text or ""
        if not display.strip():
            return

        sender = (message.metadata or {}).get("sender_name", message.user_id or "")
        buf.append(
            {
                "ts": now,
                "user": sender,
                "user_id": message.user_id,
                "text": display[:500],
            }
        )

    def _get_group_context(
        self,
        channel: str,
        chat_id: str,
        *,
        max_items: int = 10,
    ) -> list[dict]:
        """Get recent messages from the group-chat context buffer (expired entries are evicted automatically)."""
        buf_key = f"{channel}:{chat_id}"
        buf = self._group_context_buffer.get(buf_key)
        if not buf:
            return []
        now = _time.time()
        while buf and (now - buf[0]["ts"]) > self._GROUP_CONTEXT_TTL:
            buf.popleft()
        items = list(buf)[-max_items:]
        return items

    @staticmethod
    def _format_group_context(items: list[dict]) -> str:
        """Format buffer entries into text suitable for prompt injection.

        Appends a count-metadata suffix so the AI can naturally mention
        "I noticed the context of the recent N group-chat messages".
        """
        if not items:
            return ""
        n = len(items)
        lines = [
            f"[Group context] The following are the {n} most recent unprocessed messages in this group.\n"
            f"Please briefly note [based on {n} recent group messages] at the end of your response:"
        ]
        for entry in items:
            user = entry.get("user") or entry.get("user_id", "?")
            text = entry.get("text", "")
            lines.append(f"  - {user}: {text}")
        return "\n".join(lines)

    async def _try_smart_reaction(self, message: "UnifiedMessage") -> None:
        """When Smart-mode filters a message, try adding an emoji reaction on the original message.

        Behavior is controlled by the ``SMART_REACTION_ENABLED`` env var (off by default to avoid group-chat spam).
        Only runs when the adapter declares the ``add_reaction`` capability.
        """
        import os

        if os.environ.get("SMART_REACTION_ENABLED", "").lower() not in ("1", "true", "yes"):
            return
        adapter = self._adapters.get(message.channel)
        if not adapter or not adapter.has_capability("add_reaction"):
            return
        msg_id = message.channel_message_id
        if not msg_id:
            return
        try:
            await adapter.add_reaction(msg_id, emoji_type="DONE")
        except Exception as e:
            logger.debug(f"[Smart] Failed to add reaction: {e}")

    def _apply_persisted_group_policy(self) -> None:
        """Load persisted group policy from JSON and apply to adapters."""
        import json
        from pathlib import Path

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
        """Start the gateway"""
        self._running = True
        self._accepting = True

        # Preload the Whisper STT model (in a background thread so startup is not blocked)
        asyncio.create_task(self._preload_whisper_async())

        # Start all adapters
        started = []
        failed = []
        failed_reasons: dict[str, str] = {}
        for name, adapter in self._adapters.items():
            try:
                await adapter.start()
                started.append(name)
                logger.info(f"Started adapter: {name}")
            except Exception as e:
                failed.append(name)
                failed_reasons[name] = str(e)
                adapter._running = False
                logger.error(f"Failed to start adapter {name}: {e}")

        self._started_adapters = started
        self._failed_adapters = failed
        self._failed_adapter_reasons = failed_reasons

        self._apply_persisted_group_policy()

        _notify_im_event(
            "im:channel_status",
            {
                "started": started,
                "failed": failed,
                "failed_reasons": failed_reasons,
            },
        )

        # Start the message-processing loop
        self._processing_task = asyncio.create_task(self._process_loop())

        # Start the per-session dict cleanup task (cleans inactive session entries every 10 minutes)
        self._session_dict_cleanup_task = asyncio.create_task(self._session_dict_cleanup_loop())

        if failed:
            logger.info(
                f"MessageGateway started with {len(started)}/{len(self._adapters)} adapters"
                f" (failed: {', '.join(failed)})"
            )
            self._retry_failed_task = asyncio.create_task(self._retry_failed_adapters_loop())
        else:
            logger.info(f"MessageGateway started with {len(started)} adapters")

    def get_started_adapters(self) -> list[str]:
        """Get the list of successfully-started adapters."""
        return list(self._started_adapters)

    def get_failed_adapters(self) -> list[str]:
        """Get the list of failed-to-start adapters."""
        return list(self._failed_adapters)

    def get_failed_adapter_reasons(self) -> dict[str, str]:
        """Get the failed adapters and their error reasons."""
        return dict(getattr(self, "_failed_adapter_reasons", {}))

    def report_adapter_failure(self, name: str, reason: str) -> None:
        """Called when a background task's adapter suffers a fatal failure; updates state and notifies the frontend."""
        if name not in self._failed_adapters:
            self._failed_adapters.append(name)
        if name in self._started_adapters:
            self._started_adapters.remove(name)
        self._failed_adapter_reasons[name] = reason

        adapter = self._adapters.get(name)
        if adapter:
            adapter._running = False

        _notify_im_event(
            "im:channel_status",
            {
                "started": list(self._started_adapters),
                "failed": list(self._failed_adapters),
                "failed_reasons": dict(self._failed_adapter_reasons),
            },
        )
        logger.warning(f"Adapter {name} reported fatal failure: {reason}")

    async def _retry_failed_adapters_loop(self) -> None:
        """Periodically retry adapters that failed during initial startup.

        Uses exponential backoff: 15s, 30s, 60s, 120s, 240s (max).
        Stops when all failed adapters recover or after 5 consecutive rounds
        with no progress.
        """
        _BACKOFF_BASE = 15
        _BACKOFF_MAX = 240
        _MAX_STALE_ROUNDS = 5

        delay = _BACKOFF_BASE
        stale_rounds = 0

        while self._running and self._failed_adapters:
            await asyncio.sleep(delay)
            if not self._running:
                break

            recovered: list[str] = []
            for name in list(self._failed_adapters):
                adapter = self._adapters.get(name)
                if adapter is None:
                    recovered.append(name)
                    continue
                try:
                    logger.info(f"[RetryAdapter] Retrying startup for {name} ...")
                    await adapter.start()
                    adapter._running = True
                    recovered.append(name)
                    logger.info(f"[RetryAdapter] Adapter {name} started successfully")
                except Exception as e:
                    logger.debug(f"[RetryAdapter] Adapter {name} still failing: {e}")

            for name in recovered:
                if name in self._failed_adapters:
                    self._failed_adapters.remove(name)
                self._failed_adapter_reasons.pop(name, None)
                if name not in self._started_adapters:
                    self._started_adapters.append(name)

            if recovered:
                stale_rounds = 0
                delay = _BACKOFF_BASE
                _notify_im_event(
                    "im:channel_status",
                    {
                        "started": list(self._started_adapters),
                        "failed": list(self._failed_adapters),
                        "failed_reasons": dict(self._failed_adapter_reasons),
                    },
                )
                logger.info(
                    f"[RetryAdapter] Recovered adapters: {recovered}. "
                    f"Still failing: {list(self._failed_adapters) or 'none'}"
                )
            else:
                stale_rounds += 1
                delay = min(delay * 2, _BACKOFF_MAX)
                if stale_rounds >= _MAX_STALE_ROUNDS:
                    logger.warning(
                        f"[RetryAdapter] Giving up after {_MAX_STALE_ROUNDS} rounds "
                        f"with no progress. Still failed: {list(self._failed_adapters)}"
                    )
                    break

    async def _preload_whisper_async(self) -> None:
        """Asynchronously preload the Whisper model"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_whisper_model)
        except Exception as e:
            logger.warning(f"Failed to preload Whisper model: {e}")

    def _ensure_ffmpeg(self) -> None:
        """Ensure ffmpeg is available (prefer the system one; otherwise auto-download a static build)"""
        import shutil

        if shutil.which("ffmpeg"):
            logger.debug("ffmpeg found in system PATH")
            return

        try:
            import static_ffmpeg

            static_ffmpeg.add_paths(weak=True)  # weak=True: do not override existing paths
            logger.info("ffmpeg auto-configured via static-ffmpeg")
        except ImportError as e:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("static_ffmpeg")
            logger.warning(f"ffmpeg unavailable: {hint}")
            logger.warning(f"static_ffmpeg ImportError details: {e}", exc_info=True)

    async def _extract_video_keyframes(
        self, video_path: str, max_frames: int = 6, interval_seconds: int = 10
    ) -> list[tuple[str, str]]:
        """Extract keyframes from a video (using ffmpeg)

        Args:
            video_path: video file path
            max_frames: maximum number of frames to extract
            interval_seconds: extract one frame every N seconds

        Returns:
            List of [(base64_data, media_type), ...]
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
                    "ffmpeg",
                    "-i",
                    video_path,
                    "-vf",
                    f"fps=1/{interval_seconds}",
                    "-frames:v",
                    str(max_frames),
                    "-q:v",
                    "2",
                    "-y",
                    output_pattern,
                ]
                import subprocess
                import sys as _sys

                try:
                    _kw: dict = {}
                    if _sys.platform == "win32":
                        _kw["creationflags"] = subprocess.CREATE_NO_WINDOW
                    subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=60,
                        check=False,
                        **_kw,
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
        """Load the Whisper model (runs on a thread pool)"""
        if self._whisper_loaded or self._whisper_unavailable:
            return

        # The module may have been installed while the service was running; the path may not yet be in sys.path.
        # Refresh once before import (idempotent; will not re-add existing paths).
        # Must run before _ensure_ffmpeg because static_ffmpeg is inside the whisper module too.
        if "whisper" not in sys.modules:
            try:
                from openakita.runtime_env import inject_module_paths_runtime

                inject_module_paths_runtime()
            except Exception:
                pass

        # Ensure ffmpeg is available (Whisper depends on it to decode audio)
        self._ensure_ffmpeg()

        try:
            import hashlib
            import os

            import whisper
            from whisper import _MODELS

            model_name = self._whisper_model_name

            # Get the model cache path
            cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "whisper")
            model_file = os.path.join(cache_dir, f"{model_name}.pt")

            # Check the local model's hash (notification only; does not block)
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

            # Normal load
            logger.info(f"Loading Whisper model '{model_name}'...")
            self._whisper = whisper.load_model(model_name)
            self._whisper_loaded = True
            logger.info(f"Whisper model '{model_name}' loaded successfully")

        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            hint = import_or_hint("whisper")
            logger.warning(f"Whisper unavailable (will not retry within this process): {hint}")
            self._whisper_unavailable = True
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}", exc_info=True)

    async def drain(self, timeout: float = 30.0) -> None:
        """
        Graceful drain: stop accepting new messages and wait for in-progress tasks before stopping.

        Args:
            timeout: maximum seconds to wait for in-progress tasks before force-stopping
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
        """Stop the gateway (stop immediately; do not wait for in-progress tasks)"""
        if self._plugin_hooks:
            try:
                await self._plugin_hooks.dispatch("on_shutdown", gateway=self)
            except Exception as e:
                logger.debug(f"on_shutdown hook error: {e}")

        self._running = False
        self._accepting = False

        # Stop the processing loop
        if self._processing_task:
            self._processing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._processing_task

        # Stop the failed-adapter retry task
        if self._retry_failed_task and not self._retry_failed_task.done():
            self._retry_failed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_failed_task

        # Stop the per-session dict cleanup task
        cleanup_task = getattr(self, "_session_dict_cleanup_task", None)
        if cleanup_task:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task

        # Cancel all active session tasks
        for _skey, task in list(self._session_tasks.items()):
            if not task.done():
                task.cancel()
        for _skey, task in list(self._session_tasks.items()):
            if not task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._session_tasks.clear()

        # Stop all adapters
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
                logger.info(f"Stopped adapter: {name}")
            except Exception as e:
                logger.error(f"Failed to stop adapter {name}: {e}")

        logger.info("MessageGateway stopped")

    async def _session_dict_cleanup_loop(self) -> None:
        """Periodically clean inactive entries in per-session dicts to prevent memory leaks."""
        while self._running:
            try:
                await asyncio.sleep(600)  # clean every 10 minutes
                self._cleanup_stale_session_dicts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Gateway] Session dict cleanup error: {e}")

    def _cleanup_stale_session_dicts(self) -> None:
        """Clean dict entries for sessions that are no longer active.

        Only cleans session_keys that are currently not being processed; keeps active ones.
        """
        active_keys = {k for k, v in self._processing_sessions.items() if v}
        cleaned = 0

        # Clean idle and inactive entries in _interrupt_queues
        stale = [k for k in self._interrupt_queues if k not in active_keys]
        for k in stale:
            q = self._interrupt_queues[k]
            if q.empty():
                del self._interrupt_queues[k]
                cleaned += 1

        # Clean False-valued entries in _processing_sessions
        stale = [k for k, v in self._processing_sessions.items() if not v]
        for k in stale:
            del self._processing_sessions[k]
            cleaned += 1

        # Clean inactive entries in _interrupt_callbacks
        stale = [k for k in self._interrupt_callbacks if k not in active_keys]
        for k in stale:
            del self._interrupt_callbacks[k]
            cleaned += 1

        # Clean empty entries in _progress_buffers
        stale = [k for k, v in self._progress_buffers.items() if not v]
        for k in stale:
            del self._progress_buffers[k]
            cleaned += 1

        # Clean completed entries in _progress_flush_tasks
        stale = [k for k, t in self._progress_flush_tasks.items() if t.done()]
        for k in stale:
            del self._progress_flush_tasks[k]
            cleaned += 1

        # Clean inactive entries in _progress_card_accum
        stale = [k for k in self._progress_card_accum if k not in active_keys]
        for k in stale:
            del self._progress_card_accum[k]
            cleaned += 1

        # Clean completed entries in _session_tasks
        stale = [k for k, t in self._session_tasks.items() if t.done()]
        for k in stale:
            del self._session_tasks[k]
            cleaned += 1

        # Clean expired switch-sessions in ModelCommandHandler
        stale = [k for k, s in self._model_cmd_handler._switch_sessions.items() if s.is_expired]
        for k in stale:
            del self._model_cmd_handler._switch_sessions[k]
            cleaned += 1

        if cleaned:
            logger.debug(f"[Gateway] Cleaned {cleaned} stale session dict entries")

    def set_brain(self, brain: "Brain") -> None:
        """
        Set the Brain instance (used for model-switch commands)

        Args:
            brain: Brain instance
        """
        self._model_cmd_handler.set_brain(brain)
        logger.info("ModelCommandHandler brain set")

    def set_shutdown_event(self, event: asyncio.Event) -> None:
        """Inject shutdown_event (used by the ultimate-restart command)"""
        self._shutdown_event = event
        self._restart_cmd_handler._shutdown_event = event
        logger.debug("RestartCommandHandler shutdown_event set")

    # ==================== Adapter management ====================

    async def register_adapter(self, adapter: ChannelAdapter) -> None:
        """
        Register an adapter

        Args:
            adapter: channel adapter
        """
        name = adapter.channel_name

        if name in self._adapters:
            logger.warning(f"Adapter {name} already registered, replacing")
            await self._adapters[name].stop()

        # Set message callback
        adapter.on_message(self._on_message)
        adapter.on_failure(self.report_adapter_failure)

        self._adapters[name] = adapter
        logger.info(f"Registered adapter: {name}")

        # If the gateway is already running, start the adapter
        if self._running:
            await adapter.start()

    async def unregister_adapter(self, name: str) -> bool:
        """
        Unregister and stop the specified adapter.

        Args:
            name: adapter's channel_name

        Returns:
            True if successfully unregistered, False if the adapter was not found
        """
        adapter = self._adapters.pop(name, None)
        if adapter is None:
            logger.warning(f"Adapter {name} not found, cannot unregister")
            return False
        try:
            await adapter.stop()
        except Exception as e:
            logger.error(f"Error stopping adapter {name} during unregister: {e}")
        adapter._message_callback = None
        adapter._failure_callback = None
        logger.info(f"Unregistered adapter: {name}")
        return True

    def get_adapter(self, channel: str) -> ChannelAdapter | None:
        """Get an adapter"""
        return self._adapters.get(channel)

    def list_adapters(self) -> list[str]:
        """List all adapters"""
        return list(self._adapters.keys())

    # ==================== Message handling ====================

    async def _on_message(self, message: UnifiedMessage) -> None:
        """
        Message callback (invoked by adapters)

        If the session is currently processing, handle by message type:
        - STOP: trigger global task cancellation (cancel_event)
        - SKIP: trigger current-step skip (skip_event) without terminating the task
        - INSERT: inject the user message into the task context and let the LLM decide how to handle it
        """
        if not self._accepting:
            logger.debug(
                f"[Shutdown] Message rejected (drain mode): {message.channel}/{message.user_id}"
            )
            return

        if self._plugin_hooks:
            try:
                await self._plugin_hooks.dispatch("on_message_received", message=message)
            except Exception as e:
                logger.debug(f"on_message_received hook error: {e}")

        session_key = self._get_session_key(message)
        _raw_text = (message.plain_text or "").strip()

        # ==================== Ultimate-restart interception ====================
        # Intercepted before all logic so it responds even when the system is stuck.
        # Bypasses the message queue, does not enter the Agent, does not pollute the session context.
        if self._restart_cmd_handler.has_pending_session(session_key):
            consumed = await self._restart_cmd_handler.handle_pending_input(
                session_key,
                message,
            )
            if consumed:
                return

        if self._restart_cmd_handler.is_restart_command(_raw_text):
            await self._restart_cmd_handler.handle_restart_command(session_key, message)
            return
        # ==================== /end ultimate-restart interception ====================

        # ==================== Interrupt fast path (lock-free detection) ====================
        # Do cheap text detection before acquiring interrupt_lock to reduce lock contention
        if self._processing_sessions.get(session_key, False) and self._is_abort_text(_raw_text):
            await self._cancel_session(session_key, message, _raw_text)
            return

        async with self._interrupt_lock:
            if self._processing_sessions.get(session_key, False):
                # Session is currently processing
                user_text = (message.plain_text or "").strip()

                # Group-chat response-mode filter (prevents non-@'ed group messages from injecting context via the interrupt path)
                if message.chat_type == "group" and not message.is_direct_message:
                    _irq_mode = self._get_group_response_mode(
                        message.channel, message.chat_id, message.user_id
                    )
                    if _irq_mode == GroupResponseMode.MENTION_ONLY and not message.is_mentioned:
                        _is_stop_or_skip = (
                            self.agent_handler
                            and self.agent_handler.classify_interrupt(user_text) in ("stop", "skip")
                        )
                        if not _is_stop_or_skip:
                            with contextlib.suppress(Exception):
                                self._buffer_group_context(message, text=user_text)
                            logger.debug(
                                f"[Interrupt] Group message ignored in interrupt path "
                                f"(mention_only, not mentioned), buffered: {user_text[:50]}"
                            )
                            return

                # session isolation check: cancel/skip/insert only applies when the agent is processing this session's task,
                # preventing user A from accidentally killing user B's task
                _agent_ref = (
                    getattr(self.agent_handler, "_agent_ref", None) if self.agent_handler else None
                )
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
                                f"User sent stop command: {user_text}",
                                session_id=_resolved_sid,
                            )
                        else:
                            logger.warning(
                                f"[Interrupt] Could not resolve task for {session_key}, "
                                f"cancelling current_task as fallback"
                            )
                            self.agent_handler.cancel_current_task(
                                f"User sent stop command: {user_text}",
                            )
                        logger.info(
                            f"[Interrupt] STOP command, cancelling task for {session_key} "
                            f"(resolved={_resolved_sid}): {user_text}"
                        )
                        await self._send_feedback(message, "✅ Got it, stopping current task…")
                    elif msg_type == "skip":
                        ok = self.agent_handler.skip_current_step(
                            f"User sent skip command: {user_text}",
                            session_id=_resolved_sid,
                        )
                        if ok:
                            await self._send_feedback(message, "⏭️ Got it, skipping current step…")
                        else:
                            await self._send_feedback(message, "⚠️ No active step to skip.")
                        logger.info(
                            f"[Interrupt] SKIP handled directly (not queued) for {session_key}: {user_text}"
                        )
                    else:
                        # Also record into session history (the INSERT path normally doesn't write to history,
                        # which causes the desktop IM UI to miss this message)
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
                            _notify_im_event(
                                "im:new_message",
                                {
                                    "channel": message.channel,
                                    "role": "user",
                                    "session_id": _ins_session.session_key,
                                    "chat_type": _ins_session.chat_type,
                                    "display_name": _ins_session.display_name,
                                },
                            )

                        # --- Interrupt path: download media/files and enrich the injected text ---
                        _insert_text = user_text
                        _has_media = bool(
                            getattr(message.content, "files", None)
                            or getattr(message.content, "images", None)
                            or getattr(message.content, "videos", None)
                        )
                        if _has_media:
                            try:
                                await self._preprocess_media(message)
                            except Exception as _dl_err:
                                logger.warning(f"[Interrupt] Media download failed: {_dl_err}")

                            _file_parts: list[str] = []
                            for _fil in getattr(message.content, "files", []) or []:
                                if _fil.local_path and Path(_fil.local_path).exists():
                                    _fname = _fil.filename or Path(_fil.local_path).name
                                    _file_parts.append(
                                        f"[File downloaded: {_fname}, local path: {_fil.local_path}]"
                                    )
                                    logger.info(
                                        f"[Interrupt] File downloaded for insert: {_fil.local_path}"
                                    )
                            for _img in getattr(message.content, "images", []) or []:
                                if _img.local_path and Path(_img.local_path).exists():
                                    _file_parts.append(
                                        f"[Image downloaded: {_img.filename or Path(_img.local_path).name}, "
                                        f"local path: {_img.local_path}]"
                                    )
                            for _vid in getattr(message.content, "videos", []) or []:
                                if _vid.local_path and Path(_vid.local_path).exists():
                                    _file_parts.append(
                                        f"[Video downloaded: {_vid.filename or Path(_vid.local_path).name}, "
                                        f"local path: {_vid.local_path}]"
                                    )
                            if _file_parts:
                                _insert_text = _insert_text + "\n" + "\n".join(_file_parts)

                            # Set pending_files synchronously for the Agent's next iteration to consume
                            if _ins_session:
                                _pf = self._build_pending_files(message)
                                if _pf:
                                    _ins_session.set_metadata("pending_files", _pf)
                                    logger.info(
                                        f"[Interrupt] Set pending_files on session "
                                        f"({len(_pf)} items)"
                                    )

                        try:
                            ok = await self.agent_handler.insert_user_message(
                                _insert_text,
                                session_id=_resolved_sid,
                            )
                            if ok:
                                await self._send_feedback(
                                    message, "💬 Got it, message injected into current task."
                                )
                            else:
                                await self._send_feedback(
                                    message, "⚠️ No active task running, message was not injected."
                                )
                        except Exception as e:
                            logger.error(f"[Interrupt] INSERT failed for {session_key}: {e}")
                            await self._send_feedback(message, "❌ Message injection failed, please try again.")
                        logger.info(
                            f"[Interrupt] INSERT handled for {session_key}: {_insert_text[:80]}"
                        )
                elif self.agent_handler and not _session_matches:
                    # The Agent is not processing the current user's task (it may be idle or processing another user)
                    await self._add_interrupt_message(session_key, message)
                    logger.info(
                        f"[Interrupt] Session mismatch: resolved_sid={_resolved_sid!r}, "
                        f"interrupt_key={session_key!r}, agent_ref={'present' if _agent_ref else 'None'}, "
                        f"queued for later: {user_text[:50]}"
                    )
                else:
                    # If agent_handler is unavailable, fall back to enqueueing on the interrupt queue
                    await self._add_interrupt_message(session_key, message)
                    logger.warning(
                        f"[Interrupt] No agent_handler, queued as interrupt for {session_key}: {user_text[:50]}"
                    )
                return

        # ==================== DM Pairing authorization check ====================
        if self._dm_pairing:
            channel = message.channel
            chat_id = message.chat_id
            if not self._dm_pairing.is_authorized(channel, chat_id):
                stripped = _raw_text.strip()
                is_pair_cmd = stripped.lower().startswith("/pair")
                if is_pair_cmd:
                    pass
                else:
                    result = self._dm_pairing.verify_code(
                        _raw_text, channel, chat_id
                    )
                    if result[0]:
                        await self._send_feedback(message, f"✅ {result[1]}")
                    else:
                        await self._send_feedback(
                            message,
                            f"🔒 Unauthorized. Contact the admin or enter a pairing code. ({result[1]})"
                        )
                    return

        # Normal enqueue
        await self._message_queue.put(message)

    # ==================== Interrupt fast path ====================

    _ABORT_TRIGGERS = frozenset(
        {
            "停止",
            "停",
            "stop",
            "停止执行",
            "取消",
            "取消任务",
            "算了",
            "不用了",
            "别做了",
            "停下",
            "halt",
            "abort",
            "cancel",
            "やめて",
            "중지",
            "/stop",
            "/停止",
            "/取消",
            "/cancel",
            "/abort",
            "kill",
            "kill all",
        }
    )

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
        self,
        session_key: str,
        message: UnifiedMessage,
        user_text: str,
    ) -> None:
        """Fast-path: cancel the running task for a session and send feedback"""
        _agent_ref = getattr(self.agent_handler, "_agent_ref", None) if self.agent_handler else None
        _resolved_sid = self._resolve_task_session_id(session_key, _agent_ref)

        if self.agent_handler:
            if _resolved_sid:
                self.agent_handler.cancel_current_task(
                    f"User sent stop command (fast-path): {user_text}",
                    session_id=_resolved_sid,
                )
            else:
                self.agent_handler.cancel_current_task(
                    f"User sent stop command (fast-path): {user_text}",
                )

        # Cancel the asyncio task if it exists
        task = self._session_tasks.get(session_key)
        if task and not task.done():
            task.cancel()

        logger.info(f"[Abort-FastPath] Session {session_key} cancelled: {user_text}")
        await self._send_feedback(message, "✅ Got it, stopping current task…")

    # ==================== Interrupt mechanism ====================

    async def _add_interrupt_message(
        self,
        session_key: str,
        message: UnifiedMessage,
        priority: InterruptPriority = InterruptPriority.HIGH,
    ) -> None:
        """
        Add an interrupt message to the session queue

        Args:
            session_key: session identifier
            message: message
            priority: priority
        """
        if session_key not in self._interrupt_queues:
            self._interrupt_queues[session_key] = asyncio.PriorityQueue()

        interrupt_msg = InterruptMessage(message=message, priority=priority)
        await self._interrupt_queues[session_key].put(interrupt_msg)

        logger.debug(f"[Interrupt] Added to queue: {session_key}, priority={priority.name}")

    def _get_session_key(self, message: UnifiedMessage) -> str:
        """Get the session identifier (topic messages append thread_id for topic-level isolation)"""
        key = f"{message.channel}:{message.chat_id}:{message.user_id}"
        if message.thread_id:
            key += f":{message.thread_id}"
        return key

    @staticmethod
    def _resolve_task_session_id(session_key: str, agent_ref: object) -> str | None:
        """
        Find the matching task session_id in AgentState._tasks given a gateway session_key.

        session_key format:
          3-part: "telegram:1241684312:tg_1241684312"  (channel:chat_id:user_id)
          4-part: "telegram:1241684312:tg_1241684312:thread_abc"  (channel:chat_id:user_id:thread_id)

        task key format is the return value of _resolve_conversation_id (i.e., the passed-in session_id):
          IM path: session.id format "telegram_1241684312_20260219031213_xxx" (underscore-separated)
          CLI path: "cli_<uuid>" format
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
                key.startswith(prefix_underscore) and chat_id_seg_underscore in key
            ) or (key.startswith(prefix_colon) and chat_id_seg_colon in key)
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
        """Mark the session's processing state"""
        self._processing_sessions[session_key] = processing
        if not processing and session_key in self._interrupt_callbacks:
            del self._interrupt_callbacks[session_key]

    async def check_interrupt(self, session_key: str) -> UnifiedMessage | None:
        """
        Check whether the session has any pending interrupt messages

        Args:
            session_key: session identifier

        Returns:
            The pending message, or None if none
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
        Check whether the session has any pending interrupt messages

        Args:
            session_key: session identifier

        Returns:
            Whether there is a pending message
        """
        queue = self._interrupt_queues.get(session_key)
        return queue is not None and not queue.empty()

    def get_interrupt_count(self, session_key: str) -> int:
        """
        Get the count of pending interrupt messages

        Args:
            session_key: session identifier

        Returns:
            Count of pending messages
        """
        queue = self._interrupt_queues.get(session_key)
        return queue.qsize() if queue else 0

    def register_interrupt_callback(
        self,
        session_key: str,
        callback: Callable[[], Awaitable[str | None]],
    ) -> None:
        """
        Register an interrupt-check callback (invoked by the Agent)

        Between tool calls, the Agent invokes this callback to check whether new messages need handling

        Args:
            session_key: session identifier
            callback: the function that returns the text to insert, or None
        """
        self._interrupt_callbacks[session_key] = callback
        logger.debug(f"[Interrupt] Registered callback for {session_key}")

    async def _process_loop(self) -> None:
        """Message-processing loop (per-session-key concurrent dispatch)

        Messages across different session_keys are processed concurrently (subject to MAX_CONCURRENT_SESSIONS),
        while messages within the same session_key are ordered by the interrupt mechanism.
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

            except (asyncio.TimeoutError, TimeoutError):
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in process_loop dispatch: {e}", exc_info=True)

    async def _session_dispatch(self, message: UnifiedMessage) -> None:
        """Single-message processing with concurrency control"""
        async with self._concurrency_sem:
            try:
                await self._handle_message(message)
            except Exception as e:
                logger.error(f"Error handling message: {e}", exc_info=True)

    async def _handle_message(self, message: UnifiedMessage) -> None:
        """
        Process a single message
        """
        session_key = self._get_session_key(message)
        user_text = message.plain_text.strip() if message.plain_text else ""

        logger.info(
            f"[IM] <<< message received: channel={message.channel}, user={message.user_id}, "
            f'text="{user_text[:100]}"'
        )

        typing_task: asyncio.Task | None = None
        session = None
        try:
            # ==================== Group-chat response filtering ====================
            if message.chat_type == "group" and not message.is_direct_message:
                mode = self._get_group_response_mode(
                    message.channel, message.chat_id, message.user_id
                )

                if mode == GroupResponseMode.DISABLED:
                    logger.debug(f"[IM] Group message ignored (disabled): {user_text[:50]}")
                    return

                if mode == GroupResponseMode.ALLOWLIST:
                    from .policy import GroupPolicyConfig, GroupPolicyType, check_group_policy

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
                    with contextlib.suppress(Exception):
                        self._buffer_group_context(message, text=user_text)
                    logger.debug(
                        f"[IM] Group message ignored (mention_only), buffered: {user_text[:50]}"
                    )
                    return

                if mode == GroupResponseMode.SMART and not message.is_mentioned:
                    if not self._smart_throttle.should_process(message.chat_id):
                        with contextlib.suppress(Exception):
                            self._buffer_group_context(message, text=user_text)
                        # In Smart filter mode, try adding an emoji reaction to mean "received"
                        await self._try_smart_reaction(message)
                        logger.debug(
                            f"[IM] Group message throttled (smart), buffered: {user_text[:50]}"
                        )
                        return
                    self._smart_throttle.record_process(message.chat_id)
                    message.metadata["group_smart_mode"] = True

            # Mark the session as processing
            async with self._interrupt_lock:
                self._mark_session_processing(session_key, True)

            # ==================== System-level command interception ====================
            # Before invoking the Agent, check whether it is a model-switch command
            # This ensures switching works even if the LLM is down

            # Check whether a model-switch interactive session is in progress
            if self._model_cmd_handler.is_in_session(session_key):
                response_text = await self._model_cmd_handler.handle_input(session_key, user_text)
                await self._send_response(message, response_text)
                return

            # Check whether it is a model-related command
            if self._model_cmd_handler.is_model_command(user_text):
                response_text = await self._model_cmd_handler.handle_command(session_key, user_text)
                if response_text:
                    await self._send_response(message, response_text)
                    return

            # Check whether it is a thinking-mode command
            if self._thinking_cmd_handler.is_thinking_command(user_text):
                # Need the session to read/write thinking settings
                _thinking_session = self.session_manager.get_session(
                    channel=message.channel,
                    chat_id=message.chat_id,
                    user_id=message.user_id,
                    thread_id=message.thread_id,
                )
                response_text = await self._thinking_cmd_handler.handle_command(
                    session_key,
                    user_text,
                    _thinking_session,
                )
                if response_text:
                    await self._send_response(message, response_text)
                    return

            # Check whether it is a mode-view command (/模式 is always available)
            _cmd_lower = user_text.lower().strip()
            if _cmd_lower in ("/模式", "/mode") or _cmd_lower.startswith(("/模式 ", "/mode ")):
                response_text = await self._handle_mode_command(user_text)
                await self._send_response(message, response_text)
                return

            # /feishu command family (only active on the Feishu channel)
            if _cmd_lower.startswith("/feishu") and message.channel.split(":")[0] in (
                "feishu",
                "lark",
            ):
                feishu_resp = await self._handle_feishu_command(_cmd_lower, message)
                if feishu_resp is not None:
                    await self._send_response(message, feishu_resp)
                    return

            # /pair command (DM Pairing authorization)
            if _cmd_lower.startswith("/pair"):
                pair_resp = await self._handle_pair_command(_cmd_lower, message)
                if pair_resp is not None:
                    await self._send_response(message, pair_resp)
                    return

            # /background command: run task in the background
            if _cmd_lower.startswith("/background") or _cmd_lower.startswith("/bg"):
                bg_resp = await self._handle_background_command(user_text, message)
                if bg_resp:
                    await self._send_response(message, bg_resp)
                return

            # Global help command (available in all modes)
            if _cmd_lower in ("/help", "/帮助"):
                response_text = self._format_system_help()
                await self._send_response(message, response_text)
                return

            # Check whether it is a multi-Agent command (/切换 /switch /status /重置 /agent_reset)
            if self._is_agent_command(user_text):
                response_text = await self._handle_agent_command(message, user_text)
                if response_text is not None:
                    await self._send_response(message, response_text)
                    return

            # Natural-language multi-Agent mode switching / Agent switching
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
                    resp = await self._handle_agent_switch(_switch_session, f"/切换 {arg}")
                else:
                    resp = None
                if resp:
                    await self._send_response(message, resp)
                    return

            # Check whether it is a context-reset command (start a new topic)
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
                    # Also clean conversation_turns in SQLite, so getChatHistory's fallback does not re-load old data
                    try:
                        _agent_ref = (
                            getattr(self.agent_handler, "_agent_ref", None)
                            if self.agent_handler
                            else None
                        )
                        _mm = getattr(_agent_ref, "memory_manager", None) if _agent_ref else None
                        if _mm and hasattr(_mm, "store"):
                            _mm.store.delete_turns_for_session(_reset_session.id)
                    except Exception as _e:
                        logger.warning(f"[IM] Failed to clear SQLite turns on reset: {_e}")
                    logger.info(
                        f"[IM] Context reset for {session_key}: cleared {_old_count} messages"
                    )
                await self._send_response(
                    message, "New conversation started. Previous context has been cleared."
                )
                return

            # Stop/skip fallback: when these commands arrive outside processing, return a hint directly
            _IDLE_STOP_CMDS = {"/stop", "/停止", "/cancel", "/abort", "/skip", "/跳过"}
            if _cmd_lower in _IDLE_STOP_CMDS:
                await self._send_response(
                    message, "No active task. Send `/help` to see available commands."
                )
                return

            # ==================== Normal message-processing flow ====================

            # 0. Bot-switch check (must happen before typing, so disabled sessions don't trigger typing)
            if not self.bot_config.is_enabled(message.channel, message.chat_id, message.user_id):
                logger.debug(
                    f"[Gateway] Bot disabled for {message.channel}:{message.chat_id}:{message.user_id}, skipping"
                )
                return

            # 1. Start continuous typing state (covers preprocessing + the entire Agent flow)
            typing_task = asyncio.create_task(self._keep_typing(message))

            # 2. Preprocessing hooks
            for hook in self._pre_process_hooks:
                try:
                    message = await hook(message)
                except Exception as hook_err:
                    logger.warning(
                        f"[Gateway] Pre-process hook {hook.__qualname__} failed: {hook_err}"
                    )

            # 3. Media preprocessing (download images, STT voice)
            await self._preprocess_media(message)

            # 4. Get or create the session
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

            # 4.0.1 Lazily update chat_type / display_name / chat_name (an existing session may lack them)
            if message.chat_type and session.chat_type != message.chat_type:
                session.chat_type = message.chat_type
            if _msg_sender_name and not session.display_name:
                session.display_name = _msg_sender_name
            if _msg_chat_name and session.chat_name != _msg_chat_name:
                session.chat_name = _msg_chat_name

            # 4.1 Multi-bot binding: write the adapter-configured agent_profile_id into the new session
            self._apply_bot_agent_profile(session, message.channel)

            # 4.2 Inject the IM environment context (platform, chat type, bot identity, capability list)
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

            # 4.5 Push the undelivered self-check report (triggered on the first message each day, at most once)
            await self._maybe_deliver_pending_selfcheck_report(message)

            # 4.6 Auto-mark context boundaries by time interval
            # If the gap since the last message exceeds the threshold, insert a boundary marker to help the LLM distinguish old/new topics
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
                                _time_desc = f"{_hours:.1f} hours"
                            else:
                                _time_desc = f"{int(_elapsed_min)} minutes"
                            session.context.add_message(
                                "system",
                                f"[Context boundary] {_time_desc} since the last conversation;"
                                f"what follows is a new conversation that may be a new topic."
                                f"Please focus on content after the boundary.",
                            )
                            session.context.mark_topic_boundary()
                            logger.info(
                                f"[IM] Inserted context boundary for {session_key} "
                                f"(idle {_time_desc})"
                            )
                    except (ValueError, TypeError):
                        pass

            # 4.8 Inject pending key events (@everyone, group-announcement changes, etc.)
            if adapter:
                pending_events = adapter.get_pending_events(message.chat_id)
                if pending_events:
                    event_lines = []
                    for evt in pending_events:
                        evt_type = evt.get("type", "unknown")
                        if evt_type == "at_all":
                            event_lines.append(f"- @everyone message: {evt.get('text', '')[:100]}")
                        elif evt_type == "chat_updated":
                            changes = evt.get("changes", {})
                            event_lines.append(f"- Group chat info updated: {changes}")
                        elif evt_type == "bot_added":
                            event_lines.append("- Bot was added to the group chat")
                        elif evt_type == "bot_removed":
                            event_lines.append("- Bot was removed from the group chat")
                        else:
                            event_lines.append(f"- event: {evt_type}")
                    if event_lines:
                        event_text = "[System notice] Important events just occurred; please note:\n" + "\n".join(
                            event_lines
                        )
                        session.context.add_message("system", event_text)

            # 4.9 Group-chat context injection: use recent filtered group messages as context
            # Use the "user" role so history-build does not filter it out (system messages are skipped)
            if message.chat_type == "group" and not message.is_direct_message:
                try:
                    _ctx_items = self._get_group_context(
                        message.channel,
                        message.chat_id,
                        max_items=10,
                    )
                    if _ctx_items:
                        _ctx_text = self._format_group_context(_ctx_items)
                        session.context.add_message("user", _ctx_text, passive=True)
                        logger.debug(
                            f"[IM] Injected {len(_ctx_items)} buffered group context items "
                            f"for {session_key}"
                        )
                        # Clear the buffer after injecting to avoid re-injection
                        _buf_key = f"{message.channel}:{message.chat_id}"
                        self._group_context_buffer.pop(_buf_key, None)
                except Exception as _ctx_err:
                    logger.debug(f"[IM] Group context injection failed (non-critical): {_ctx_err}")

            # 5. Record the message to the session
            session.add_message(
                role="user",
                content=message.plain_text,
                message_id=message.id,
                channel_message_id=message.channel_message_id,
            )
            self.session_manager.mark_dirty()  # trigger save
            _notify_im_event(
                "im:new_message",
                {
                    "channel": message.channel,
                    "role": "user",
                    "session_id": session.session_key,
                    "chat_type": session.chat_type,
                    "display_name": session.display_name,
                },
            )

            # 6. Invoke the Agent (supports interrupt checks + streaming output)
            response_text, streamed_ok = await self._call_agent(session, message)

            # 7. Postprocessing hooks
            for hook in self._post_process_hooks:
                try:
                    response_text = await hook(message, response_text)
                except Exception as hook_err:
                    logger.warning(
                        f"[Gateway] Post-process hook {hook.__qualname__} failed: {hook_err}"
                    )

            # 7.5 Empty-reply protection
            if not response_text or not response_text.strip():
                logger.warning(
                    f"[IM] Agent returned empty response for message {message.id} "
                    f"(channel={message.channel}, user={message.user_id}), "
                    f"raw={response_text!r}"
                )
                response_text = "⚠️ Processing complete but no reply was generated. Please retry."
                streamed_ok = False

            # 8. Record the response into the session (including reasoning-chain summary + tool-execution summary)
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
            _notify_im_event(
                "im:new_message",
                {
                    "channel": message.channel,
                    "role": "assistant",
                    "session_id": session.session_key,
                    "chat_type": session.chat_type,
                    "display_name": session.display_name,
                },
            )

            # 9. Send the response (skip if streaming already delivered it via card PATCH)
            logger.info(
                f"[IM] >>> reply complete: channel={message.channel}, user={message.user_id}, "
                f"len={len(response_text)}, streamed={streamed_ok}, "
                f'preview="{response_text[:80]}"'
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
                                message.chat_id,
                                thread_id=message.thread_id,
                            )

                self._progress_card_accum.pop(session.session_key, None)

                await self._send_response(message, response_text)

            # 10. Handle remaining interrupt messages
            await self._process_pending_interrupts(session_key, session)

        except Exception as e:
            logger.error(
                f"Error handling message {message.id} "
                f"(channel={message.channel}, user={message.user_id}): {e}",
                exc_info=True,
            )
            # Record an assistant-error response to avoid orphan user messages in the session
            # (orphan user messages would cause consecutive same-role messages next turn -> model confusion / repeated tool execution)
            try:
                if session and session.context.messages:
                    _last = session.context.messages[-1]
                    if _last.get("role") == "user":
                        session.add_message(
                            role="assistant",
                            content=f"[Processing error: {str(e)[:200]}]",
                        )
                        self.session_manager.mark_dirty()
            except Exception:
                pass
            # Send an error notice
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
                if hasattr(_adapter, "_streaming_buffers") and hasattr(
                    _adapter, "_make_session_key"
                ):
                    _adapter._streaming_buffers.pop(
                        _adapter._make_session_key(message.chat_id, message.thread_id),
                        None,
                    )
            # Mark the session as done processing
            async with self._interrupt_lock:
                self._mark_session_processing(session_key, False)

    _MAX_INTERRUPT_ITERATIONS = 20

    async def _process_pending_interrupts(self, session_key: str, session: Session) -> None:
        """
        Handle remaining interrupt messages for the session

        After the current message is processed, continue processing the queued interrupt messages
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
                # Preprocess media
                await self._preprocess_media(interrupt_msg)

                # Record to the session
                session.add_message(
                    role="user",
                    content=interrupt_msg.plain_text,
                    message_id=interrupt_msg.id,
                    channel_message_id=interrupt_msg.channel_message_id,
                    is_interrupt=True,  # mark as an interrupt message
                )
                self.session_manager.mark_dirty()  # trigger save

                # Invoke the Agent (typing is covered by the outer typing_task; interrupts skip streaming)
                response_text, _ = await self._call_agent(
                    session,
                    interrupt_msg,
                    allow_streaming=False,
                )

                # Postprocessing hooks
                for hook in self._post_process_hooks:
                    try:
                        response_text = await hook(interrupt_msg, response_text)
                    except Exception as hook_err:
                        logger.warning(
                            f"[Gateway] Post-process hook {hook.__qualname__} failed: {hook_err}"
                        )

                # Record the response (including reasoning-chain summary + tool-execution summary)
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
                self.session_manager.mark_dirty()  # trigger save

                # Send the response
                await self._send_response(interrupt_msg, response_text)

            except Exception as e:
                logger.error(f"Error processing interrupt message: {e}", exc_info=True)
                await self._send_error(interrupt_msg, str(e))

    async def _preprocess_media(self, message: UnifiedMessage) -> None:
        """
        Preprocess media files (download voice, images; auto-STT voice)
        """
        adapter = self._adapters.get(message.channel)
        if not adapter:
            return

        import asyncio

        # Concurrent download/transcription (avoids latency stacking when multiple media arrive serially)
        sem = asyncio.Semaphore(4)

        async def _process_voice(voice) -> None:
            if voice.status == MediaStatus.FAILED:
                return
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
                        voice.transcription = "[STT failed]"
            except (asyncio.TimeoutError, TimeoutError):
                logger.error(f"Voice processing timed out: {voice.filename}")
                voice.transcription = "[Voice processing timed out]"
            except Exception as e:
                logger.error(f"Failed to process voice: {e}")
                voice.transcription = "[Voice processing failed]"

        async def _process_image(img) -> None:
            try:
                if img.local_path or img.status == MediaStatus.FAILED:
                    return
                async with sem:
                    local_path = await adapter.download_media(img)
                    img.local_path = str(local_path)
                    img.status = MediaStatus.READY
                    logger.info(f"Image downloaded: {img.local_path}")
            except Exception as e:
                img.status = MediaStatus.FAILED
                img.description = f"Download failed: {e}"
                logger.error(f"Failed to download image: {e}")

        async def _process_video(vid) -> None:
            try:
                if vid.local_path or vid.status == MediaStatus.FAILED:
                    return
                async with sem:
                    local_path = await adapter.download_media(vid)
                    vid.local_path = str(local_path)
                    vid.status = MediaStatus.READY
                    logger.info(f"Video downloaded: {vid.local_path}")
            except Exception as e:
                vid.status = MediaStatus.FAILED
                vid.description = f"Download failed: {e}"
                logger.error(f"Failed to download video: {e}")

        async def _process_file(fil) -> None:
            try:
                if fil.local_path or fil.status == MediaStatus.FAILED:
                    return
                async with sem:
                    local_path = await adapter.download_media(fil)
                    fil.local_path = str(local_path)
                    fil.status = MediaStatus.READY
                    logger.info(f"File downloaded: {fil.local_path}")
            except Exception as e:
                fil.status = MediaStatus.FAILED
                fil.description = f"Download failed: {e}"
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

    def _build_pending_files(self, message: UnifiedMessage) -> list[dict]:
        """Build the pending_files list from downloaded message attachments (for the Agent to consume)."""
        files_data: list[dict] = []
        for fil in getattr(message.content, "files", []) or []:
            if not (fil.local_path and Path(fil.local_path).exists()):
                continue
            try:
                mime = fil.mime_type or ""
                suffix = Path(fil.local_path).suffix.lower()
                _fname = fil.filename or Path(fil.local_path).name
                if suffix == ".pdf" or "pdf" in mime:
                    file_data = base64.b64encode(Path(fil.local_path).read_bytes()).decode("utf-8")
                    files_data.append(
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": file_data,
                            },
                            "filename": _fname,
                            "local_path": fil.local_path,
                        }
                    )
                else:
                    files_data.append(
                        {
                            "type": "file",
                            "filename": _fname,
                            "local_path": fil.local_path,
                            "mime_type": mime or suffix,
                        }
                    )
            except Exception as e:
                logger.warning(f"[Interrupt] _build_pending_files failed for {fil.local_path}: {e}")
        return files_data

    async def _transcribe_voice_local(self, audio_path: str) -> str | None:
        """
        Use local Whisper to transcribe voice

        Uses the preloaded model to avoid reloading each time
        """
        import asyncio

        try:
            # Check whether the file exists
            if not Path(audio_path).exists():
                logger.error(f"Audio file not found: {audio_path}")
                return None

            # Ensure the model is loaded
            if not self._whisper_loaded and not self._whisper_unavailable:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._load_whisper_model)

            if self._whisper is None:
                if not self._whisper_unavailable:
                    logger.error("Whisper model not available")
                return None

            # Run transcription on a thread pool (to avoid blocking the event loop)
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

                # For pre-converted WAVs, try loading via numpy directly to bypass the ffmpeg dependency
                if compatible_path.endswith(".wav"):
                    audio_array = load_wav_as_numpy(compatible_path)
                    if audio_array is not None:
                        result = self._whisper.transcribe(audio_array, **kwargs)
                        return result["text"].strip()

                result = self._whisper.transcribe(compatible_path, **kwargs)
                return result["text"].strip()

            # Run async
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, transcribe)

            return text if text else None

        except Exception as e:
            logger.error(f"Voice transcription failed: {e}", exc_info=True)
            return None

    async def _send_typing(self, message: UnifiedMessage) -> None:
        """Send a typing state"""
        adapter = self._adapters.get(message.channel)
        if adapter and hasattr(adapter, "send_typing"):
            try:
                await adapter.send_typing(message.chat_id, thread_id=message.thread_id)
            except Exception:
                pass  # ignore typing-send failures

    async def _send_feedback(self, message: UnifiedMessage, text: str) -> None:
        """Send a lightweight feedback message to the IM user (interrupt confirmations, etc.)"""
        adapter = self._adapters.get(message.channel)
        if adapter and hasattr(adapter, "send_text"):
            try:
                _meta = {
                    "is_group": (message.metadata or {}).get(
                        "is_group", message.chat_type == "group"
                    ),
                    "_interim": True,
                }
                await adapter.send_text(
                    chat_id=message.chat_id,
                    text=text,
                    reply_to=message.channel_message_id,
                    metadata=_meta,
                )
            except Exception as e:
                logger.warning(f"[Feedback] Failed to send feedback to {message.channel}: {e}")

    async def _call_agent_with_typing(
        self, session: Session, message: UnifiedMessage
    ) -> tuple[str, bool]:
        """Invoke the Agent to handle the message, continuously sending typing state throughout"""
        import asyncio

        typing_task = asyncio.create_task(self._keep_typing(message))

        try:
            return await self._call_agent(session, message)
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    async def _keep_typing(self, message: UnifiedMessage) -> None:
        """Continuously send typing state (once every 4 seconds)"""
        import asyncio

        while True:
            await self._send_typing(message)
            await asyncio.sleep(4)  # Telegram typing state lasts ~5 seconds

    async def _call_agent(
        self,
        session: Session,
        message: UnifiedMessage,
        *,
        allow_streaming: bool = True,
    ) -> tuple[str, bool]:
        """
        Invoke the Agent to handle the message (supports multimodal: image, voice)

        Returns:
            (response_text, streamed_ok) -- streamed_ok=True means it was already delivered via a streaming card
            to the user; the caller should skip _send_response.
        """
        if not self.agent_handler:
            return ("Agent handler not configured", False)

        try:
            # Build input (text + images + voice)
            input_text = message.plain_text
            _has_voice = bool(message.content.voices)

            # Handle voice files - dual-path strategy: keep raw audio + Whisper transcription
            audio_data_list = []
            for voice in message.content.voices:
                # Dual-path retention: always store raw audio paths in pending_audio
                if voice.local_path and Path(voice.local_path).exists():
                    audio_data_list.append(
                        {
                            "local_path": voice.local_path,
                            "mime_type": voice.mime_type or "audio/wav",
                            "duration": voice.duration,
                            "transcription": voice.transcription
                            if voice.transcription not in (None, "", "[STT failed]")
                            else None,
                            "_media_ref": voice,
                        }
                    )

                if voice.transcription and voice.transcription not in ("[STT failed]", ""):
                    # Voice transcribed; use transcript as input (fallback)
                    if not input_text.strip() or "[语音:" in input_text:
                        input_text = f"[source: voice-transcription] {voice.transcription}"
                        logger.info(f"Using voice transcription as input: {input_text}")
                    else:
                        input_text = f"{input_text}\n\n[Voice content: {voice.transcription}]"
                elif voice.local_path:
                    # Voice was not successfully transcribed; save the path for the Agent to handle manually
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
                            f"[User sent a voice message, but auto-STT failed. File path: {voice.local_path}]"
                        )
                    logger.info(f"Voice transcription failed, file: {voice.local_path}")

            # Store raw audio data in the session (for the Agent's three-tier decision)
            if audio_data_list:
                session.set_metadata("pending_audio", audio_data_list)
                logger.info(f"Stored {len(audio_data_list)} raw audio files for Agent decision")

            # Handle image files - multimodal input
            images_data = []
            for img in message.content.images:
                if img.local_path and Path(img.local_path).exists():
                    try:
                        from .media.image_prep import prepare_image_for_context

                        raw = Path(img.local_path).read_bytes()
                        result = prepare_image_for_context(
                            raw,
                            media_type=img.mime_type or "image/jpeg",
                        )
                        if result:
                            b64_data, media_type, _w, _h = result
                            images_data.append(
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": b64_data,
                                    },
                                    "local_path": img.local_path,
                                }
                            )
                        else:
                            logger.warning(f"Image too large to embed, skipping: {img.local_path}")
                    except Exception as e:
                        logger.error(f"Failed to read image: {e}")

            # Check for image-download failures
            failed_images = [
                img for img in message.content.images if img.status == MediaStatus.FAILED
            ]
            if failed_images:
                reasons = "; ".join(img.description or "unknown reason" for img in failed_images)
                notice = f"[User sent {len(failed_images)} image(s), but download failed: {reasons}]"
                input_text = f"{input_text}\n\n{notice}" if input_text.strip() else notice
                logger.warning(f"Image download failed, notifying agent: {reasons}")

            # If images are present, build multimodal input
            if images_data:
                # Store image data in the session for the Agent to use
                session.set_metadata("pending_images", images_data)
                if not input_text.strip():
                    input_text = "[User sent an image]"
                logger.info(f"Processing multimodal message with {len(images_data)} images")

            # Handle video files - multimodal input
            videos_data = []
            VIDEO_SIZE_LIMIT = (
                7 * 1024 * 1024
            )  # 7MB (after base64 ~9.3MB, under DashScope's 10MB data-uri limit)
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
                            logger.info(
                                f"Video encoded as base64: {vid.local_path} ({file_size / 1024 / 1024:.1f}MB)"
                            )
                        else:
                            # Video exceeds size limit; extract keyframes via ffmpeg as image fallback
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
                                # Update pending_images
                                session.set_metadata("pending_images", images_data)
                                logger.info(f"Extracted {len(keyframes)} keyframes from video")
                            else:
                                logger.warning(
                                    f"Failed to extract keyframes from: {vid.local_path}"
                                )
                    except Exception as e:
                        logger.error(f"Failed to process video: {e}")

            # Check for video-download failures
            failed_videos = [
                vid for vid in message.content.videos if vid.status == MediaStatus.FAILED
            ]
            if failed_videos:
                reasons = "; ".join(vid.description or "unknown reason" for vid in failed_videos)
                notice = (
                    f"[User sent {len(failed_videos)} video(s), but download failed: {reasons}."
                    f"Please tell the user the video download failed and suggest sending a smaller video file.]"
                )
                input_text = f"{input_text}\n\n{notice}" if input_text.strip() else notice
                logger.warning(f"Video download failed, notifying agent: {reasons}")

            if videos_data:
                session.set_metadata("pending_videos", videos_data)
                if not input_text.strip():
                    input_text = "[User sent a video]"
                logger.info(f"Processing multimodal message with {len(videos_data)} videos")

            # Handle files - multimodal input for PDFs and other documents
            files_data = []
            for fil in message.content.files:
                if fil.local_path and Path(fil.local_path).exists():
                    try:
                        mime = fil.mime_type or ""
                        suffix = Path(fil.local_path).suffix.lower()
                        _fname = fil.filename or Path(fil.local_path).name
                        if suffix == ".pdf" or "pdf" in mime:
                            file_data = base64.b64encode(Path(fil.local_path).read_bytes()).decode(
                                "utf-8"
                            )
                            files_data.append(
                                {
                                    "type": "document",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "application/pdf",
                                        "data": file_data,
                                    },
                                    "filename": _fname,
                                    "local_path": fil.local_path,
                                }
                            )
                            logger.info(f"PDF file encoded: {fil.local_path}")
                        elif suffix in (
                            ".md",
                            ".txt",
                            ".csv",
                            ".json",
                            ".jsonl",
                            ".xml",
                            ".yaml",
                            ".yml",
                            ".toml",
                            ".ini",
                            ".cfg",
                            ".log",
                            ".py",
                            ".js",
                            ".ts",
                            ".jsx",
                            ".tsx",
                            ".html",
                            ".htm",
                            ".css",
                            ".sql",
                            ".sh",
                            ".bat",
                            ".ps1",
                            ".java",
                            ".c",
                            ".cpp",
                            ".h",
                            ".hpp",
                            ".go",
                            ".rs",
                            ".rb",
                            ".php",
                            ".lua",
                            ".r",
                            ".swift",
                            ".kt",
                            ".scala",
                            ".conf",
                            ".env",
                            ".gitignore",
                            ".dockerfile",
                            ".makefile",
                        ) or mime.startswith("text/"):
                            _TEXT_FILE_SIZE_LIMIT = 512 * 1024  # 512KB
                            _fpath = Path(fil.local_path)
                            if _fpath.stat().st_size <= _TEXT_FILE_SIZE_LIMIT:
                                _content = _fpath.read_text(
                                    encoding="utf-8",
                                    errors="replace",
                                )
                                input_text += (
                                    f"\n\n--- File: {_fname} ---\n{_content}\n--- End of file ---"
                                )
                                logger.info(
                                    f"Text file injected: {fil.local_path} ({len(_content)} chars)"
                                )
                            else:
                                input_text += (
                                    f"\n[Attachment: {_fname} ({mime or suffix}), "
                                    f"file too large to inline, local path: {fil.local_path}]"
                                )
                                logger.info(
                                    f"Text file too large for inline, "
                                    f"path provided: {fil.local_path}"
                                )
                        else:
                            input_text += (
                                f"\n[Attachment: {_fname} ({mime or suffix}), local path: {fil.local_path}]"
                            )
                    except Exception as e:
                        logger.error(f"Failed to process file: {e}")

            # Check for file-download failures
            failed_files = [
                fil for fil in message.content.files if fil.status == MediaStatus.FAILED
            ]
            if failed_files:
                reasons = "; ".join(fil.description or "unknown reason" for fil in failed_files)
                notice = f"[User sent {len(failed_files)} file(s), but download failed: {reasons}]"
                input_text = f"{input_text}\n\n{notice}" if input_text.strip() else notice
                logger.warning(f"File download failed, notifying agent: {reasons}")

            if files_data:
                session.set_metadata("pending_files", files_data)
                if not input_text.strip():
                    input_text = "[User sent a file]"
                logger.info(f"Processing multimodal message with {len(files_data)} files")

            # === Interrupt mechanism: pass the gateway reference and session identifier ===
            session_key = self._get_session_key(message)
            session.set_metadata("_gateway", self)
            session.set_metadata("_session_key", session_key)
            session.set_metadata("_current_message", message)

            # === Streaming / non-streaming branch ===
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
                and getattr(self, "_orchestrator_ref", None) is None
            )

            streamed_ok = False
            _has_orchestrator = getattr(self, "_orchestrator_ref", None) is not None
            if use_streaming:
                response, streamed_ok = await self._call_agent_streaming(
                    session,
                    input_text,
                    message,
                    adapter,
                )
            elif _has_orchestrator:
                # The Orchestrator has its own idle_timeout + hard_timeout progress monitoring,
                # so we no longer wrap in a wait_for wall-clock timeout that would mistakenly kill active tasks
                response = await self.agent_handler(session, input_text)
            else:
                _AGENT_TIMEOUT = float(os.environ.get("AGENT_HANDLER_TIMEOUT", "1200"))
                try:
                    response = await asyncio.wait_for(
                        self.agent_handler(session, input_text),
                        timeout=_AGENT_TIMEOUT,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    logger.error(f"[Gateway] Agent handler timed out after {_AGENT_TIMEOUT}s")
                    response = f"⚠️ Processing timed out ({int(_AGENT_TIMEOUT)}s), please retry or simplify your request."

            return (response, streamed_ok)

        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            return (format_user_friendly_error(str(e)), False)
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
        self,
        session: Session,
        input_text: str,
        message: UnifiedMessage,
        adapter,
    ) -> tuple[str, bool]:
        """Consume agent_handler_stream, pipe tokens to adapter.stream_token,
        then finalize.  Returns (full_reply, streamed_ok)."""
        reply_text = ""
        is_group = message.chat_type == "group"

        chain_push = session.get_metadata("chain_push")
        if chain_push is None:
            from ..config import settings as _s

            chain_push = _s.im_chain_push
        can_stream_thinking = chain_push and hasattr(adapter, "stream_thinking")

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
                        message.chat_id,
                        delta,
                        thread_id=message.thread_id,
                        is_group=is_group,
                    )
                elif etype == "thinking_delta":
                    _thinking_buf += event.get("content", "")
                    if can_stream_thinking and _thinking_buf:
                        await adapter.stream_thinking(
                            message.chat_id,
                            _thinking_buf,
                            thread_id=message.thread_id,
                            is_group=is_group,
                        )
                elif etype == "thinking_end":
                    if can_stream_thinking and hasattr(adapter, "stream_thinking"):
                        dur_ms = event.get("duration_ms", 0)
                        sk = (
                            adapter._make_session_key(message.chat_id, message.thread_id)
                            if hasattr(adapter, "_make_session_key")
                            else ""
                        )
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
                                message.chat_id,
                                content,
                                thread_id=message.thread_id,
                                is_group=is_group,
                            )
                        else:
                            await self.emit_progress_event(session, content)
                elif etype == "tool_call_start":
                    tool_name = event.get("name", "unknown")
                    if chain_push:
                        await self.emit_progress_event(session, f"Calling tool: {tool_name}")
                elif etype == "tool_call_end":
                    tool_name = event.get("name", "unknown")
                    tool_ok = event.get("success", True)
                    if chain_push:
                        status = "✅" if tool_ok else "❌"
                        await self.emit_progress_event(
                            session, f"{status} tool {tool_name} finished"
                        )
                elif etype == "ask_user":
                    if not reply_text:
                        reply_text = event.get("question", "")
                elif etype == "security_confirm":
                    await self._handle_im_security_confirm(session, event, adapter, message)
                elif etype == "error":
                    err_msg = event.get("message", "")
                    if not reply_text:
                        reply_text = format_user_friendly_error(err_msg)
                elif etype == "done":
                    pass

        try:
            await asyncio.wait_for(_consume_stream(), timeout=_STREAM_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError):
            logger.error(f"[IM] Streaming agent timed out after {_STREAM_TIMEOUT}s")
            if not reply_text:
                reply_text = f"WARNING: processing timed out ({int(_STREAM_TIMEOUT)}s); please retry later or simplify your question."
        except Exception as e:
            logger.error(f"[IM] Streaming agent error: {e}", exc_info=True)
            if not reply_text:
                reply_text = format_user_friendly_error(str(e))

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
            message.chat_id,
            reply_text,
            thread_id=message.thread_id,
        )
        return (reply_text, ok)

    # Maximum characters per message per channel (with headroom)
    # - telegram: API hard limit 4096; leave headroom -> 4000
    # - wework:   in streaming/response_url mode, send_message overwrites instead of appending; do not chunk
    # - dingtalk: Webhook text/Markdown ~20000
    # - feishu: card messages ~30000
    # - onebot/qqbot: generally no strict limit
    _CHANNEL_MAX_LENGTH: dict[str, int] = {
        "telegram": 4000,
        "wework": 0,  # 0 = no chunking, send as one message
        "dingtalk": 18000,
        "feishu": 28000,
        "lark": 28000,
        "onebot": 20000,
        "qqbot": 20000,
        "wechat": 4000,
    }
    _DEFAULT_MAX_LENGTH = 4000

    # Delay (seconds) between chunk sends, to avoid hitting platform rate limits
    _SPLIT_SEND_INTERVAL: dict[str, float] = {
        "telegram": 0.5,
        "wechat": 2.5,
    }
    _DEFAULT_SPLIT_INTERVAL = 0.15

    # Progress-message throttle interval (seconds) -- platforms without card-update support need higher throttling
    # QQ/OneBot gets higher throttling: reduces noise and slows consumption of the msg_id passive-reply window
    _CHANNEL_PROGRESS_THROTTLE: dict[str, float] = {
        "wechat": 12.0,
        "qqbot": 10.0,
        "onebot": 10.0,
    }

    @staticmethod
    def _split_text(text: str, max_length: int) -> list[str]:
        """
        Split long text on newlines into chunks no longer than max_length;
        try to keep paragraphs intact; overlong single lines are hard-cut by characters.
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

            # The buffer already has content -> flush first
            if current:
                chunks.append(current.rstrip())
                current = ""

            # A single line is over-long -> hard-cut by characters
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
        Send the response (with retries, per-channel long-message splitting, and inter-chunk rate protection)

        Chunk-failure strategy:
        - First send as Markdown chunks
        - If any chunk still fails after 3 retries -> abort remaining chunks and resend the whole thing as plain text
        - If plain-text resend also fails -> send a failure notice

        Media follow-ups:
        - Before sending text, parse ![](path), MEDIA: lines, and bare paths from the reply
        - Send the cleaned text first, then send images/files one by one
        """
        import asyncio

        from .media_parser import parse_media_from_text

        if self._plugin_hooks:
            try:
                await self._plugin_hooks.dispatch(
                    "on_message_sending", message=original, response=response
                )
            except Exception as e:
                logger.debug(f"on_message_sending hook error: {e}")

        adapter = self._adapters.get(original.channel)
        if not adapter:
            logger.error(f"No adapter for channel: {original.channel}")
            return

        # Parse media references from the text
        media_result = parse_media_from_text(response)
        text_to_send = media_result.cleaned_text

        channel = original.channel
        base_channel = channel.split(":")[0].split("_")[0]

        max_length = self._CHANNEL_MAX_LENGTH.get(base_channel, self._DEFAULT_MAX_LENGTH)
        from .text_splitter import (
            add_fragment_numbers,
            chunk_markdown_text,
            estimate_number_prefix_len,
        )

        # Reserve space for the chunk ordinal: make a rough estimate (assume at most 10 chunks), then add exact numbering after chunking
        _est_prefix = estimate_number_prefix_len(10)
        _effective_max = (
            max(max_length - _est_prefix, max_length // 2) if max_length > 0 else max_length
        )
        messages = chunk_markdown_text(text_to_send, _effective_max) if text_to_send else []
        messages = add_fragment_numbers(messages)

        interval = self._SPLIT_SEND_INTERVAL.get(base_channel, self._DEFAULT_SPLIT_INTERVAL)

        footer = adapter.format_final_footer(
            original.chat_id,
            thread_id=original.thread_id,
        )
        if footer and messages:
            messages[-1] = messages[-1] + footer

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
                    adapter.send_message,
                    outgoing,
                    max_retries=2,
                    base_delay=1.0,
                    operation_name=f"send_response[{i + 1}/{len(messages)}]",
                )
            except Exception as e:
                logger.error(
                    f"Failed to send response part {i + 1}/{len(messages)} after retries: {e}"
                )
                failed_at = i
                break

        if failed_at < 0:
            await self._send_extracted_media(adapter, original, media_result, outgoing_meta)
            return

        # Chunk-send failed -> only resend the failed chunk and what follows as plain text (avoid duplicating already-delivered parts)
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
                logger.error(f"Plain-text fallback also failed for part {failed_at + j + 1}: {e2}")
                _sent_count = failed_at + j
                _fail_hint = (
                    f"Message send failed ({_sent_count}/{len(messages)} segments delivered); please retry later."
                    if _sent_count > 0
                    else "Message send failed; please retry later."
                )
                with contextlib.suppress(Exception):
                    await adapter.send_text(
                        chat_id=original.chat_id,
                        text=_fail_hint,
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
        """Send images/files parsed from the reply text as follow-ups"""
        reply_to = original.thread_id or original.channel_message_id

        if adapter.has_capability("send_image"):
            for img in media_result.images:
                if img.is_url:
                    continue
                try:
                    await adapter.send_image(
                        original.chat_id,
                        img.path,
                        reply_to=reply_to,
                    )
                except Exception as e:
                    logger.warning(f"[SendResponse] send extracted image failed: {e}")
                    with contextlib.suppress(Exception):
                        fname = Path(img.path).name if img.path else "image"
                        await adapter.send_text(
                            original.chat_id,
                            f"📎 {fname}",
                            reply_to=reply_to,
                            metadata=outgoing_meta,
                        )

        if adapter.has_capability("send_file"):
            all_files = list(media_result.files) + list(media_result.videos)
            for file in all_files:
                if file.is_url:
                    continue
                try:
                    await adapter.send_file(
                        original.chat_id,
                        file.path,
                        reply_to=reply_to,
                    )
                except Exception as e:
                    logger.warning(f"[SendResponse] send extracted file failed: {e}")
                    with contextlib.suppress(Exception):
                        fname = Path(file.path).name if file.path else "file"
                        await adapter.send_text(
                            original.chat_id,
                            f"📎 {fname}",
                            reply_to=reply_to,
                            metadata=outgoing_meta,
                        )

        if adapter.has_capability("send_voice"):
            for audio in media_result.audios:
                if audio.is_url:
                    continue
                try:
                    await adapter.send_voice(
                        original.chat_id,
                        audio.path,
                        reply_to=reply_to,
                    )
                except Exception as e:
                    logger.warning(f"[SendResponse] send extracted audio failed: {e}")
                    with contextlib.suppress(Exception):
                        fname = Path(audio.path).name if audio.path else "audio"
                        await adapter.send_text(
                            original.chat_id,
                            f"📎 {fname}",
                            reply_to=reply_to,
                            metadata=outgoing_meta,
                        )

    async def _send_error(self, original: UnifiedMessage, error: str) -> None:
        """
        Send an error notice (show a friendly message to the user; keep technical details only in logs)
        """
        adapter = self._adapters.get(original.channel)
        if not adapter:
            return

        try:
            _meta = {
                "is_group": (original.metadata or {}).get("is_group", original.chat_type == "group")
            }
            friendly = format_user_friendly_error(error)
            await adapter.send_text(
                chat_id=original.chat_id,
                text=friendly,
                reply_to=original.thread_id or original.channel_message_id,
                metadata=_meta,
            )
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")

    # ==================== Pending self-check report ====================

    async def _maybe_deliver_pending_selfcheck_report(self, message: UnifiedMessage) -> None:
        """
        Check for and push any undelivered self-check reports

        Self-check runs at 4:00 AM, but there's usually no active session at that time (30-minute timeout),
        so reports are saved under data/selfcheck/ with reported=false.
        When the user sends a message, this method pushes the undelivered reports to them.

        Deduplication is guaranteed by the report JSON's reported field; no separate date lock is needed.
        """
        try:
            await self._deliver_pending_selfcheck_report(message)
        except Exception as e:
            logger.error(f"Pending selfcheck report delivery failed: {e}")

    async def _deliver_pending_selfcheck_report(self, message: UnifiedMessage) -> None:
        """
        Read unpushed reports from data/selfcheck/ and send them to the user

        Check today's and yesterday's report files; push the first one with reported=false.
        Send directly via the adapter, without writing to the session context (to avoid polluting conversation history).
        """
        import json
        from datetime import date as date_type

        from ..config import settings

        selfcheck_dir = settings.selfcheck_dir
        if not selfcheck_dir.exists():
            return

        today = date_type.today()
        # Check today's and yesterday's reports (self-check at 4:00 AM produces the current day's report)
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

                # Skip if already pushed
                if data.get("reported"):
                    continue

                if not md_file.exists():
                    continue

                with open(md_file, encoding="utf-8") as f:
                    report_md = f.read()

                if not report_md.strip():
                    continue

                # Send directly via the adapter (no session-context write)
                adapter = self._adapters.get(message.channel)
                if not adapter or not adapter.is_running:
                    continue

                header = f"Daily system self-check report ({report_date})\n\n"
                full_text = header + report_md
                _meta = {
                    "is_group": (message.metadata or {}).get(
                        "is_group", message.chat_type == "group"
                    )
                }

                # Send in segments (compatible with Telegram's 4096 limit)
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

                # Mark as pushed
                data["reported"] = True
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                logger.info(
                    f"Delivered pending selfcheck report for {report_date} "
                    f"to {message.channel}/{message.chat_id}"
                )
                break  # only push the most recent unread report

            except Exception as e:
                logger.error(f"Failed to deliver pending selfcheck report for {report_date}: {e}")

    # ==================== Proactive send ====================

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
        Send a message proactively

        Args:
            channel: target channel
            chat_id: target chat
            text: message text
            record_to_session: whether to record to session history
            user_id: sender identifier

        Returns:
            The message ID or None
        """
        adapter = self._adapters.get(channel)
        if not adapter:
            logger.error(f"No adapter for channel: {channel}")
            return None

        try:
            # Mark as intermediate to prevent Feishu thinking cards from being consumed prematurely
            _meta = kwargs.pop("metadata", None) or {}
            _meta = dict(_meta) if isinstance(_meta, dict) else {}
            _meta.setdefault("_interim", True)
            kwargs["metadata"] = _meta

            result = await adapter.send_text(chat_id, text, **kwargs)

            # Record to session history
            if record_to_session and self.session_manager:
                try:
                    self.session_manager.add_message(
                        channel=channel,
                        chat_id=chat_id,
                        user_id=user_id,
                        role="system",  # message sent by the system
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
        Send a message to a session
        """
        # Topic-aware: if the session is associated with a topic and the caller did not explicitly set reply_to,
        # use thread_id automatically so the message stays within the topic (platforms like Feishu require a reply to locate the topic)
        if session.thread_id and "reply_to" not in kwargs:
            kwargs["reply_to"] = session.thread_id

        result = await self.send(
            channel=session.channel,
            chat_id=session.chat_id,
            text=text,
            record_to_session=False,  # recorded manually below
            **kwargs,
        )

        # Record to session history (with the specified role); do not record on send failure to avoid context inconsistency
        if self.session_manager and result is not None:
            try:
                session.add_message(role=role, content=text, source="send_to_session")
                self.session_manager.mark_dirty()  # trigger save
            except Exception as e:
                logger.warning(f"Failed to record message to session: {e}")

        return result

    async def send_security_confirm(
        self,
        session: "Session",
        tool_name: str,
        reason: str,
        risk_level: str = "HIGH",
    ) -> None:
        """Send a security confirmation request to the IM channel.

        Uses platform-native interactive elements when available
        (Feishu cards, Telegram InlineKeyboard), falls back to plain text.
        """
        adapter = self._adapters.get(session.channel)
        if adapter is None:
            return

        text = (
            f"WARNING: **Safety confirmation**\n\n"
            f"Tool: `{tool_name}`\n"
            f"Risk level: **{risk_level}**\n"
            f"Reason: {reason}\n\n"
            f"Please reply: **allow** / **deny**"
        )

        if hasattr(adapter, "build_simple_card") and hasattr(adapter, "send_card"):
            card = adapter.build_simple_card(
                title=f"WARNING: Safety confirmation — {risk_level}",
                content=(f"**Tool**: {tool_name}\n**Reason**: {reason}"),
                buttons=[
                    {"text": "Allow", "value": "security_allow"},
                    {"text": "Deny", "value": "security_deny"},
                ],
            )
            try:
                chat_id = session.chat_id
                reply_to = session.thread_id
                await adapter.send_card(chat_id, card, reply_to=reply_to)
                return
            except Exception as e:
                logger.warning(f"[Security] Card send failed, falling back to text: {e}")

        try:
            await self.send_to_session(session, text, role="system")
        except Exception as e:
            logger.warning(f"[Security] Failed to send confirmation: {e}")

    async def _handle_im_security_confirm(
        self,
        session: "Session",
        event: dict,
        adapter,
        message: "UnifiedMessage",
    ) -> None:
        """Handle security_confirm events in IM streaming.

        Send a confirmation card/text to the user, then wait for their reply
        via the interrupt queue.
        """
        tool_name = event.get("tool", "")
        reason = event.get("reason", "")
        risk = event.get("risk_level", "HIGH")
        confirm_id = event.get("id", "")

        await self.send_security_confirm(session, tool_name, reason, risk_level=risk)

        # Wait for user reply via interrupt queue (set by adapter callbacks or
        # plain-text keyword matching in _handle_message)
        try:
            reply_msg = await asyncio.wait_for(
                self._wait_for_interrupt(session.session_key),
                timeout=float(session.get_metadata("security_timeout") or 120),
            )
            text = reply_msg.message.text.strip().lower() if reply_msg else ""
        except (asyncio.TimeoutError, TimeoutError):
            text = ""

        decision = "deny"
        if text in ("允许", "allow", "yes", "y", "allow_once"):
            decision = "allow_once"
        elif text in ("始终允许", "allow_always", "always"):
            decision = "allow_always"
        elif text in ("会话允许", "allow_session", "session"):
            decision = "allow_session"
        elif text in ("沙箱", "sandbox"):
            decision = "sandbox"

        try:
            from ..core.policy import get_policy_engine

            get_policy_engine().resolve_ui_confirm(confirm_id, decision)
        except Exception as exc:
            logger.warning(f"[Security] IM confirm resolve failed: {exc}")

    async def _wait_for_interrupt(self, session_key: str) -> "InterruptMessage | None":
        """Block until an interrupt message arrives for the session."""
        queue = self._interrupt_queues.get(session_key)
        if queue is None:
            self._interrupt_queues[session_key] = asyncio.PriorityQueue()
            queue = self._interrupt_queues[session_key]
        return await queue.get()

    async def _try_patch_progress_to_card(
        self,
        session: Session,
        new_lines: list[str],
    ) -> bool:
        """Try PATCHing progress text into the thinking card (without consuming it).

        On the non-streaming path, progress messages update the placeholder card via this method,
        avoiding separate gray text messages. The card is consumed (popped) by the final reply.

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

        if hasattr(adapter, "_typing_status"):
            adapter._typing_status[sk] = "calling tool"

        session_key = session.session_key
        accum = self._progress_card_accum.setdefault(session_key, [])
        accum.extend(new_lines)
        if len(accum) > 20:
            accum[:] = accum[-20:]

        display = "\n".join(accum)
        try:
            return await adapter._patch_card_content(card_id, display, sk)
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
        Emit a 'progress event' which the gateway throttles/merges before sending.

        - Controlled by the global im_chain_push switch and the per-session chain_push metadata.
        - Multiple events are merged into one within the throttling window to avoid noise.
        - Progress messages are recorded into the session with the system role by default (does not affect the model's conversation history).
        - Pass force=True to bypass the chain_push check (only for system notifications that must be delivered).
        """
        if not session or not text:
            return

        # chain_push switch guard
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
                base_ch,
                self._progress_throttle_seconds,
            )

        buf = self._progress_buffers.setdefault(session_key, [])
        if buf and buf[-1] == text:
            return  # de-duplicate consecutive identical messages
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
        Immediately flush the given session's progress buffer.

        Called before the final answer is sent, ensuring reasoning-chain messages arrive before the answer.
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

        # Wait for any already-running flush task to finish, so progress messages arrive before the reply
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
                getattr(current_message, "channel_message_id", None) if current_message else None
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
        Broadcast a message

        Args:
            text: message text
            channels: list of target channels (None = all)
            user_ids: list of target users (None = all)

        Returns:
            {channel: sent_count}
        """
        results = {}

        # Get target sessions
        sessions = self.session_manager.list_sessions()

        for session in sessions:
            # Filter by channel
            if channels and session.channel not in channels:
                continue

            # Filter by user
            if user_ids and session.user_id not in user_ids:
                continue

            try:
                await self.send_to_session(session, text)
                results[session.channel] = results.get(session.channel, 0) + 1
            except Exception as e:
                logger.error(f"Broadcast error to {session.id}: {e}")

        return results

    # ==================== Middleware ====================

    def add_pre_process_hook(
        self,
        hook: Callable[[UnifiedMessage], Awaitable[UnifiedMessage]],
    ) -> None:
        """
        Add a preprocessing hook

        Called before message processing; may modify the message
        """
        self._pre_process_hooks.append(hook)

    def add_post_process_hook(
        self,
        hook: Callable[[UnifiedMessage, str], Awaitable[str]],
    ) -> None:
        """
        Add a postprocessing hook

        Called after the Agent response; may modify the response
        """
        self._post_process_hooks.append(hook)

    # ==================== Statistics ====================

    def get_stats(self) -> dict:
        """Get gateway statistics"""
        return {
            "running": self._running,
            "adapters": {name: adapter.is_running for name, adapter in self._adapters.items()},
            "queue_size": self._message_queue.qsize(),
            "sessions": self.session_manager.get_session_count(),
        }
