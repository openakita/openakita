# src/seeagent/api/adapters/seecrab_adapter.py
"""SeeCrabAdapter: translates raw Agent event stream → refined SSE events."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from .card_builder import CardBuilder
from .step_aggregator import StepAggregator
from .step_filter import StepFilter
from .timer_tracker import TimerTracker
from .title_generator import TitleGenerator

logger = logging.getLogger(__name__)


class SeeCrabAdapter:
    """Core translation layer: raw reason_stream events → refined SSE events."""

    def __init__(self, brain: object | None, user_messages: list[str]):
        self.step_filter = StepFilter()
        self.step_filter.set_user_messages(user_messages)
        self.timer = TimerTracker()
        self.title_gen = TitleGenerator(brain, user_messages)
        self.card_builder = CardBuilder()
        self._title_queue: asyncio.Queue[dict] = asyncio.Queue()
        self.aggregator = StepAggregator(
            title_gen=self.title_gen,
            card_builder=self.card_builder,
            timer=self.timer,
            title_update_queue=self._title_queue,
        )

    async def transform(
        self,
        raw_events: AsyncIterator[dict],
        reply_id: str,
    ) -> AsyncIterator[dict]:
        """Consume raw events + title_update_queue, yield refined events."""
        self.timer.start(reply_id)
        yield self.timer.make_event("ttft", "running")

        async for event in raw_events:
            for refined in await self._process_event(event):
                yield refined
            # Drain any pending title updates between raw events
            while not self._title_queue.empty():
                try:
                    title_event = self._title_queue.get_nowait()
                    yield title_event
                except asyncio.QueueEmpty:
                    break

        # Flush pending aggregation
        for e in await self.aggregator.flush():
            yield e

        # Drain remaining title updates after stream ends
        while not self._title_queue.empty():
            try:
                yield self._title_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Final timing
        yield self.timer.make_event("total", "done")
        yield {"type": "done"}

    async def _process_event(self, event: dict) -> list[dict]:
        """Dispatch a single raw event to handlers."""
        etype = event.get("type", "")

        if etype == "thinking_delta":
            return self._handle_thinking(event)

        if etype == "thinking_start":
            return []  # absorbed, we use delta

        if etype == "thinking_end":
            return []  # timing info only

        if etype == "text_delta":
            return await self._handle_text_delta(event)

        if etype == "tool_call_start":
            return await self._handle_tool_call_start(event)

        if etype == "tool_call_end":
            return await self._handle_tool_call_end(event)

        if etype == "plan_created":
            return await self.aggregator.on_plan_created(event.get("plan", event))

        if etype == "plan_step_updated":
            # Engine sends stepId as string (e.g. "step_1"), normalize to index
            raw_step_id = str(event.get("stepId", event.get("step_index", "")))
            if not raw_step_id:
                logger.warning("[SeeCrab] plan_step_updated with empty stepId, skipping")
                return []
            step_index = self.aggregator._plan_id_to_index.get(raw_step_id, 0)
            if step_index == 0:
                # Fallback 1: parse "step_N" → N
                if "_" in raw_step_id:
                    try:
                        step_index = int(raw_step_id.split("_")[-1])
                    except (ValueError, IndexError):
                        pass
                # Fallback 2: try direct integer parse
                if step_index == 0:
                    try:
                        step_index = int(raw_step_id)
                    except (ValueError, TypeError):
                        pass
                # Fallback 3: try step_index field directly as integer
                if step_index == 0:
                    si = event.get("step_index", event.get("stepIndex", 0))
                    if isinstance(si, int) and si > 0:
                        step_index = si
            if step_index <= 0:
                logger.warning(
                    f"[SeeCrab] plan_step_updated: unknown stepId={raw_step_id!r}, skipping"
                )
                return []
            status = event.get("status", "")
            return await self.aggregator.on_plan_step_updated(step_index, status)

        if etype == "plan_completed":
            return await self.aggregator.on_plan_completed()

        if etype == "ask_user":
            return [self._map_ask_user(event)]

        if etype == "heartbeat":
            return [{"type": "heartbeat"}]

        if etype == "error":
            return [{"type": "error", "message": event.get("message", ""), "code": "agent_error"}]

        # Explicitly ignored event types (from engine, not relevant for SeeCrab):
        # - "done": engine done signal — adapter emits its own done
        # - "iteration_start": internal iteration counter
        # - "context_compressed": context window management
        # - "chain_text": IM-facing internal monologue
        # - "user_insert": IM gateway user injection
        # - "agent_handoff": multi-agent internal routing
        # - "tool_call_skipped": policy-denied tools
        return []

    def _handle_thinking(self, event: dict) -> list[dict]:
        events = []
        ttft = self.timer.check_ttft()
        if ttft:
            events.append(ttft)
            events.append(self.timer.make_event("total", "running"))
        events.append({
            "type": "thinking",
            "content": event.get("content", ""),
            "agent_id": "main",
        })
        return events

    async def _handle_text_delta(self, event: dict) -> list[dict]:
        events = []
        ttft = self.timer.check_ttft()
        if ttft:
            events.append(ttft)
            events.append(self.timer.make_event("total", "running"))
        # Close any active aggregation
        events += await self.aggregator.on_text_delta()
        events.append({
            "type": "ai_text",
            "content": event.get("content", ""),
            "agent_id": "main",
        })
        return events

    async def _handle_tool_call_start(self, event: dict) -> list[dict]:
        tool_name = event.get("tool", "")
        args = event.get("args", {})
        tool_id = event.get("id", "")
        fr = self.step_filter.classify(tool_name, args)
        return await self.aggregator.on_tool_call_start(tool_name, args, tool_id, fr)

    async def _handle_tool_call_end(self, event: dict) -> list[dict]:
        tool_name = event.get("tool", "")
        tool_id = event.get("id", "")
        result = event.get("result", "")
        is_error = event.get("is_error", False)
        events = await self.aggregator.on_tool_call_end(
            tool_name, tool_id, result, is_error
        )
        return events

    @staticmethod
    def _map_ask_user(event: dict) -> dict:
        """Map raw ask_user event (id→value)."""
        options = event.get("options", [])
        mapped = [
            {"label": o.get("label", ""), "value": o.get("id", o.get("value", ""))}
            for o in options
        ]
        return {
            "type": "ask_user",
            "ask_id": event.get("id", event.get("ask_id", "")),
            "question": event.get("question", ""),
            "options": mapped,
        }
