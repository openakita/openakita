"""
Unified streaming feedback layer.

Defines the ``StreamPresenter`` interface, encapsulating the differences
across IM platforms' streaming updates into a unified three-phase lifecycle:
``start`` -> ``update`` -> ``finalize``.

Adapters can integrate by subclassing and implementing platform-specific API
calls. The gateway side only needs to call
``presenter.update(text, thinking)`` without worrying about underlying
mechanisms.

Design highlights:
- Shared throttling logic (``_min_interval_ms``), avoiding duplicate
  implementations across adapters
- Unified thinking formatting: ``<think>`` wrapper or platform-specific tags
- Platforms without streaming support automatically degrade to a
  "Thinking..." placeholder + final replacement
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class StreamPresenter(ABC):
    """Unified abstract interface for IM streaming feedback.

    Parameters:
        chat_id: Target chat ID
        thread_id: Optional thread/topic ID
        min_interval_ms: Minimum update interval (ms) to prevent rate limiting
    """

    def __init__(
        self,
        chat_id: str,
        *,
        thread_id: str | None = None,
        min_interval_ms: int = 800,
        is_group: bool = False,
    ):
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.min_interval_ms = min_interval_ms
        self.is_group = is_group

        self._started = False
        self._finalized = False
        self._last_update_ts: float = 0
        self._pending_text: str = ""
        self._pending_thinking: str = ""
        self._accumulated_text: str = ""
        self._accumulated_thinking: str = ""
        self._flush_task: asyncio.Task | None = None

    # ── Lifecycle (subclass implementations) ──

    @abstractmethod
    async def _do_start(self) -> None:
        """Platform-specific start action (e.g., send placeholder message, create card)."""
        ...

    @abstractmethod
    async def _do_update(self, text: str, thinking: str) -> None:
        """Platform-specific update action (e.g., edit message, PATCH card).

        Args:
            text: Current complete reply text
            thinking: Current complete thinking content
        """
        ...

    @abstractmethod
    async def _do_finalize(self, text: str, thinking: str) -> bool:
        """Platform-specific finalize action (e.g., final message edit, card finalization).

        Returns:
            Whether finalization succeeded
        """
        ...

    # ── Public API ──

    async def start(self) -> None:
        """Start streaming feedback."""
        if self._started:
            return
        self._started = True
        try:
            await self._do_start()
        except Exception as e:
            logger.warning(f"[StreamPresenter] start failed: {e}")
        self._last_update_ts = time.monotonic()

    async def update(self, text_delta: str = "", thinking_delta: str = "") -> None:
        """Push incremental content with automatic internal throttling."""
        if not self._started or self._finalized:
            return
        self._accumulated_text += text_delta
        self._accumulated_thinking += thinking_delta
        self._pending_text = self._accumulated_text
        self._pending_thinking = self._accumulated_thinking

        now = time.monotonic()
        elapsed_ms = (now - self._last_update_ts) * 1000
        if elapsed_ms >= self.min_interval_ms:
            await self._flush()
        elif not self._flush_task or self._flush_task.done():
            delay = (self.min_interval_ms - elapsed_ms) / 1000
            self._flush_task = asyncio.ensure_future(self._delayed_flush(delay))

    async def finalize(self) -> bool:
        """Finalize streaming feedback and send final content."""
        if self._finalized:
            return True
        self._finalized = True
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        if not self._started:
            await self.start()
        try:
            return await self._do_finalize(
                self._accumulated_text,
                self._accumulated_thinking,
            )
        except Exception as e:
            logger.warning(f"[StreamPresenter] finalize failed: {e}")
            return False

    # ── Internal throttling ──

    async def _flush(self) -> None:
        self._last_update_ts = time.monotonic()
        try:
            await self._do_update(self._pending_text, self._pending_thinking)
        except Exception as e:
            logger.debug(f"[StreamPresenter] update failed: {e}")

    async def _delayed_flush(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if not self._finalized:
            await self._flush()


class NullStreamPresenter(StreamPresenter):
    """Fallback implementation for platforms without streaming support.

    Sends a "Thinking..." placeholder on ``start`` and replaces it with
    the full reply on ``finalize``.
    """

    def __init__(self, adapter, chat_id: str, **kwargs):
        super().__init__(chat_id, **kwargs)
        self._adapter = adapter
        self._placeholder_msg_id: str | None = None

    async def _do_start(self) -> None:
        try:
            self._placeholder_msg_id = await self._adapter.send_text(
                self.chat_id,
                "💭 Thinking...",
                thread_id=self.thread_id,
            )
        except Exception:
            pass

    async def _do_update(self, text: str, thinking: str) -> None:
        pass

    async def _do_finalize(self, text: str, thinking: str) -> bool:
        if self._placeholder_msg_id and self._adapter.has_capability("delete_message"):
            try:
                await self._adapter.delete_message(self.chat_id, self._placeholder_msg_id)
            except Exception:
                pass
        return True
