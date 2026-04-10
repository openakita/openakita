"""
流式反馈统一体验层

定义 ``StreamPresenter`` 接口，将各 IM 平台的「流式更新」差异封装为
统一的三段生命周期：``start`` → ``update`` → ``finalize``。

各适配器通过继承并实现具体的平台 API 调用即可接入，网关侧只需
调用 ``presenter.update(text, thinking)`` 而无需关心底层机制。

设计要点：
- 共享节流逻辑（``_min_interval_ms``），避免各适配器重复实现
- thinking 格式化统一：``<think>`` 包裹或平台专属标签
- 不支持流式的平台自动降级为"正在思考..."占位 + 完成替换
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class StreamPresenter(ABC):
    """IM 流式反馈的统一抽象接口。

    Parameters:
        chat_id: 目标聊天 ID
        thread_id: 可选话题/线程 ID
        min_interval_ms: 更新最小间隔（毫秒），防止平台限流
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

    # ── 生命周期（子类实现） ──

    @abstractmethod
    async def _do_start(self) -> None:
        """平台特定的开始操作（如发送占位消息、创建卡片等）。"""
        ...

    @abstractmethod
    async def _do_update(self, text: str, thinking: str) -> None:
        """平台特定的更新操作（如编辑消息、PATCH 卡片等）。

        Args:
            text: 当前完整的回复文本
            thinking: 当前完整的思考内容
        """
        ...

    @abstractmethod
    async def _do_finalize(self, text: str, thinking: str) -> bool:
        """平台特定的完成操作（如最终消息编辑、卡片定稿等）。

        Returns:
            是否成功完成
        """
        ...

    # ── 公开 API ──

    async def start(self) -> None:
        """开始流式反馈。"""
        if self._started:
            return
        self._started = True
        try:
            await self._do_start()
        except Exception as e:
            logger.warning(f"[StreamPresenter] start failed: {e}")
        self._last_update_ts = time.monotonic()

    async def update(self, text_delta: str = "", thinking_delta: str = "") -> None:
        """推送增量内容，内部自动节流。"""
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
        """完成流式反馈，发送最终内容。"""
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

    # ── 内部节流 ──

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
    """不支持流式更新的平台使用的降级实现。

    ``start`` 时发送"正在思考…"占位消息，``finalize`` 时替换为完整回复。
    """

    def __init__(self, adapter, chat_id: str, **kwargs):
        super().__init__(chat_id, **kwargs)
        self._adapter = adapter
        self._placeholder_msg_id: str | None = None

    async def _do_start(self) -> None:
        try:
            self._placeholder_msg_id = await self._adapter.send_text(
                self.chat_id,
                "💭 正在思考…",
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
