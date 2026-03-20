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

_STREAM_DONE = object()


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
        self._aggregators: dict[str, StepAggregator] = {"main": self.aggregator}
        self._active_agent_id = "main"

    async def transform(
        self,
        raw_events: AsyncIterator[dict],
        reply_id: str,
        event_bus: asyncio.Queue | None = None,
    ) -> AsyncIterator[dict]:
        """Consume raw events + title_update_queue, yield refined events."""
        self.timer.start(reply_id)
        yield self.timer.make_event("ttft", "running")

        source = self._merge_sources(raw_events, event_bus) if event_bus else raw_events

        async for event in source:
            for refined in await self._process_event(event):
                yield refined
            # Drain any pending title updates between raw events
            while not self._title_queue.empty():
                try:
                    title_event = self._title_queue.get_nowait()
                    yield title_event
                except asyncio.QueueEmpty:
                    break

        # Flush pending aggregation from all agents
        for agg in self._aggregators.values():
            for e in await agg.flush():
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

        if etype == "agent_header":
            return await self._handle_agent_switch(event)

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

        # Pre-built step_card from BP engine (delegate cards) — pass through
        if etype == "step_card":
            return [event]

        # BP events — flatten data wrapper for frontend consumption
        if etype in ("bp_progress", "bp_subtask_output", "bp_stale"):
            return [{"type": etype, **event.get("data", {})}]

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
            "agent_id": self._active_agent_id,
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
            "agent_id": self._active_agent_id,
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

    async def _merge_sources(self, raw_events, event_bus):
        """Merge raw_events + event_bus into a single async stream.

        When raw_events blocks (during delegation), event_bus items
        are still consumed.  After raw_events finishes, any remaining
        items in event_bus are drained before the stream ends.
        """
        merged = asyncio.Queue()

        async def _feed_raw():
            try:
                async for event in raw_events:
                    await merged.put(event)
                    # Yield control so _feed_bus can forward queued items
                    await asyncio.sleep(0)
            except Exception as e:
                logger.error(f"[SeeCrab] raw_events feeder error: {e}")
            finally:
                await merged.put(_STREAM_DONE)

        async def _feed_bus():
            try:
                while True:
                    event = await event_bus.get()
                    if event is _STREAM_DONE:
                        break
                    await merged.put(event)
            except asyncio.CancelledError:
                pass

        raw_task = asyncio.create_task(_feed_raw())
        bus_task = asyncio.create_task(_feed_bus())

        try:
            while True:
                item = await merged.get()
                if item is _STREAM_DONE:
                    # Drain any remaining event_bus items that arrived
                    # before raw_events finished
                    while not event_bus.empty():
                        try:
                            leftover = event_bus.get_nowait()
                            if leftover is not _STREAM_DONE:
                                yield leftover
                        except asyncio.QueueEmpty:
                            break
                    break
                yield item
        finally:
            bus_task.cancel()
            try:
                await bus_task
            except (asyncio.CancelledError, Exception):
                pass
            if not raw_task.done():
                raw_task.cancel()

    async def _handle_agent_switch(self, event: dict) -> list[dict]:
        """Handle agent switch: flush current aggregator, switch to new agent."""
        agent_id = event.get("agent_id", "main") or "sub_agent"
        logger.debug(f"[SeeCrab] Agent switch: {self._active_agent_id} → {agent_id}")
        events: list[dict] = []
        # Flush current aggregator
        current_agg = self._aggregators.get(self._active_agent_id)
        if current_agg:
            events.extend(await current_agg.flush())
        # Switch aggregator (create if new)
        if agent_id not in self._aggregators:
            self._aggregators[agent_id] = StepAggregator(
                title_gen=self.title_gen,
                card_builder=self.card_builder,
                timer=self.timer,
                title_update_queue=self._title_queue,
                agent_id=agent_id,
            )
        self._active_agent_id = agent_id
        self.aggregator = self._aggregators[agent_id]
        # Pass through agent_header to frontend
        events.append({
            "type": "agent_header",
            "agent_id": agent_id,
            "agent_name": event.get("agent_name", agent_id),
            "agent_description": event.get("agent_description", ""),
        })
        return events
