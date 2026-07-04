"""Relay v2 ``StreamBus`` events into IM-readable progress messages.

The v2 supervisor emits typed events on the named channels defined
in ADR-0006 (``progress_ledger`` / ``messages`` / ``lifecycle`` /
``tasks`` / ...). When a canary org's IM dispatch runs through
``Supervisor.run``, the user sitting in the chat needs to see what
is happening. This module translates a subset of those events into
short Chinese sentences and ships them back through the gateway's
existing ``send_message(session_key, text)`` callable.

The bridge is intentionally tiny: it owns no state apart from the
``StreamBus`` subscription, and it stops the moment the subscriber
task is cancelled (closing the subscription cleanly via the
:class:`StreamBus` close semantics). The gateway is expected to run
``relay_to(...)`` as a background task scoped to a single dispatch.

See continuation plan section 2.1 (P-RC-1 commit 3).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from openakita.runtime.stream import StreamBus

__all__ = ["ImStreamBridge"]

logger = logging.getLogger(__name__)

#: Default channel subset the bridge subscribes to. We deliberately
#: skip noisy channels (``values`` / ``updates`` / ``debug``) so the
#: IM surface only sees user-meaningful events.
DEFAULT_CHANNELS: tuple[str, ...] = (
    "progress_ledger",
    "messages",
    "lifecycle",
)


SendMessage = Callable[[str, str], Awaitable[None]]


class ImStreamBridge:
    """Subscribe to a ``StreamBus`` and translate events into IM text.

    Args:
        stream_bus: the runtime bus the supervisor emits on.
        channels: subset to subscribe to. Defaults to
            :data:`DEFAULT_CHANNELS`.
    """

    def __init__(
        self,
        *,
        stream_bus: StreamBus,
        channels: Iterable[str] = DEFAULT_CHANNELS,
    ) -> None:
        self._bus = stream_bus
        self._channels = tuple(channels)
        if not self._channels:
            raise ValueError("ImStreamBridge requires at least one channel")
        self._relayed = 0
        self._suppressed = 0

    @property
    def relayed(self) -> int:
        return self._relayed

    @property
    def suppressed(self) -> int:
        return self._suppressed

    async def relay_to(
        self,
        send_message: SendMessage,
        *,
        session_key: str,
    ) -> None:
        """Forward translated events to ``send_message(session_key, text)``.

        Runs until the bus is closed or the caller cancels this task.
        Translation failures and ``send_message`` exceptions are
        swallowed at debug level; a misbehaving renderer must not
        break the supervisor loop.
        """
        async for event in self._bus.subscribe(*self._channels):
            try:
                text = self._render(event.channel, event.type, event.payload)
            except Exception as exc:  # noqa: BLE001 -- renderer must not crash bus
                logger.debug("[im_stream_bridge] render failed: %s", exc)
                self._suppressed += 1
                continue
            if text is None:
                self._suppressed += 1
                continue
            try:
                await send_message(session_key, text)
            except Exception as exc:  # noqa: BLE001 -- gateway send must not crash bus
                logger.debug("[im_stream_bridge] send_message failed: %s", exc)
                self._suppressed += 1
                continue
            self._relayed += 1

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------

    @staticmethod
    def _render(channel: str, type_: str, payload: dict[str, Any]) -> str | None:
        """Render an event into IM-friendly Chinese text, or ``None`` to skip."""
        if channel == "lifecycle":
            if type_ == "started":
                return "已收到任务，正在思考…"
            if type_ == "done":
                reason = str(payload.get("reason") or "").strip()
                return f"任务已完成。{reason}".rstrip("。 ") + "。" if reason else "任务已完成。"
            if type_ == "cancelled":
                reason = str(payload.get("reason") or "").strip()
                return f"已应你的请求停止任务。{reason}".rstrip("。 ") + "。" if reason else "已应你的请求停止任务。"
            if type_ == "replan_budget_exhausted" or type_ == "failed":
                return "任务失败，详情见日志。"
            return None

        if channel == "messages":
            if type_ in ("delegation", "delegated"):
                speaker = str(payload.get("speaker") or payload.get("next_speaker") or "").strip()
                if speaker:
                    return f"正在交给 {speaker} 处理…"
                return "正在分派子任务…"
            if type_ in ("node_completed", "completed"):
                node = str(payload.get("node") or payload.get("node_id") or "").strip()
                if node:
                    return f"节点 {node} 完成。"
                return None
            return None

        if channel == "progress_ledger":
            if type_ in ("emitted", "ledger_emitted"):
                satisfied = bool(payload.get("is_request_satisfied"))
                in_loop = bool(payload.get("is_in_loop"))
                next_speaker = str(payload.get("next_speaker") or "").strip()
                if satisfied:
                    return "已确认任务完成。"
                if in_loop:
                    return "检测到循环，正在重新规划…"
                if next_speaker:
                    return f"下一步交给 {next_speaker}。"
                return None
            if type_ in ("awaiting_human", "awaiting_review"):
                return "等待用户确认…"
            return None

        return None
