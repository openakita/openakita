"""StepAggregator: state machine for step card aggregation."""
from __future__ import annotations

import asyncio
import logging
import uuid

from .card_builder import CardBuilder
from .seecrab_models import AggregatorState, FilterResult, PendingCard
from .timer_tracker import TimerTracker
from .title_generator import TitleGenerator

logger = logging.getLogger(__name__)


class StepAggregator:
    """Aggregation state machine: IDLE / SKILL_ABSORB / MCP_ABSORB / PLAN_ABSORB."""

    def __init__(
        self,
        title_gen: TitleGenerator,
        card_builder: CardBuilder,
        timer: TimerTracker,
        title_update_queue: asyncio.Queue | None = None,
    ):
        self.title_gen = title_gen
        self.card_builder = card_builder
        self.timer = timer
        self._title_update_queue = title_update_queue
        self.state = AggregatorState.IDLE
        self.pending_card: PendingCard | None = None
        # Plan mode state
        self._plan_steps: list[dict] | None = None
        self._plan_id_to_index: dict[str, int] = {}  # engine "step_1" → numeric 1
        self._current_plan_step: int | None = None
        self._plan_step_card: PendingCard | None = None
        # Independent (whitelist/user_mention) card tracking: tool_id → (step_id, title)
        self._independent_cards: dict[str, tuple[str, str]] = {}
        # Delegation title tasks for cleanup
        self._delegation_title_tasks: list[asyncio.Task] = []

    async def on_tool_call_start(
        self, tool_name: str, args: dict, tool_id: str,
        filter_result: FilterResult,
    ) -> list[dict]:
        """Process tool_call_start. Returns events to emit."""
        # Plan mode absorbs everything
        if self.state == AggregatorState.PLAN_ABSORB:
            if self._plan_step_card:
                self._plan_step_card.absorbed_calls.append(
                    {"tool": tool_name, "args": args, "tool_id": tool_id}
                )
            return []

        # Skill absorb
        if self.state == AggregatorState.SKILL_ABSORB:
            if filter_result == FilterResult.SKILL_TRIGGER:
                # New skill → complete previous, start new
                events = self._complete_pending()
                events += self._start_skill(tool_name, args)
                return events
            # Absorb into current skill
            if self.pending_card:
                self.pending_card.absorbed_calls.append(
                    {"tool": tool_name, "args": args, "tool_id": tool_id}
                )
            return []

        # MCP absorb
        if self.state == AggregatorState.MCP_ABSORB:
            if filter_result == FilterResult.MCP_TRIGGER:
                server = args.get("server", "")
                if server == self.pending_card.mcp_server:
                    # Same server → absorb
                    self.pending_card.absorbed_calls.append(
                        {"tool": tool_name, "args": args, "tool_id": tool_id}
                    )
                    return []
                else:
                    # Different server → complete current, start new
                    events = self._complete_pending()
                    events += self._start_mcp(tool_name, args)
                    return events
            # Non-MCP tool → complete MCP, handle normally
            events = self._complete_pending()
            events += self._handle_idle(tool_name, args, tool_id, filter_result)
            return events

        # IDLE state
        return self._handle_idle(tool_name, args, tool_id, filter_result)

    async def on_tool_call_end(
        self, tool_name: str, tool_id: str, result: str, is_error: bool
    ) -> list[dict]:
        """Process tool_call_end. Returns events to emit."""
        # Check if this is an independent card completion
        if tool_id in self._independent_cards:
            step_id, original_title = self._independent_cards.pop(tool_id)
            duration = self.timer.end_step(step_id)
            status = "failed" if is_error else "completed"
            return [self.card_builder.build_step_card(
                step_id=step_id,
                title=original_title,
                status=status,
                source_type="tool",
                tool_name=tool_name,
                duration=duration,
                output_data=result[:2000] if result else None,
            )]

        # Update absorbed call with result (Plan mode)
        if self.state == AggregatorState.PLAN_ABSORB and self._plan_step_card:
            for call in self._plan_step_card.absorbed_calls:
                if call.get("tool_id") == tool_id:
                    call["result"] = result[:2000]
                    call["is_error"] = is_error
                    break
            return []

        # Update absorbed call with result (Skill/MCP mode)
        if self.pending_card:
            for call in self.pending_card.absorbed_calls:
                if call.get("tool_id") == tool_id:
                    call["result"] = result[:2000]
                    call["is_error"] = is_error
                    break
        return []

    async def on_text_delta(self) -> list[dict]:
        """text_delta arrived — close any active aggregation."""
        if self.state in (AggregatorState.SKILL_ABSORB, AggregatorState.MCP_ABSORB):
            return self._complete_pending()
        return []

    async def on_plan_created(self, plan: dict) -> list[dict]:
        """Enter Plan mode.

        Engine emits steps as: {"id": "step_1", "description": "...", "status": "pending"}
        We normalize to: {"index": 1, "title": "...", "status": "pending"}
        """
        steps = plan.get("steps", [])
        self._plan_steps = []
        self._plan_id_to_index: dict[str, int] = {}  # "step_1" → 1
        for i, s in enumerate(steps):
            idx = i + 1
            step_id_raw = str(s.get("id", f"step_{idx}"))
            title = s.get("description", s.get("title", ""))
            self._plan_steps.append({"index": idx, "title": title, "status": "pending"})
            self._plan_id_to_index[step_id_raw] = idx
        # Complete any active aggregation first
        events = []
        if self.state in (AggregatorState.SKILL_ABSORB, AggregatorState.MCP_ABSORB):
            events += self._complete_pending()
        self.state = AggregatorState.PLAN_ABSORB
        events.append({"type": "plan_checklist", "steps": list(self._plan_steps)})
        return events

    async def on_plan_step_updated(self, step_index: int, status: str) -> list[dict]:
        """Plan step status change."""
        # Guard: if plan_created hasn't been called yet, skip
        if self.state != AggregatorState.PLAN_ABSORB or self._plan_steps is None:
            logger.warning(
                f"[Aggregator] plan_step_updated(step={step_index}, status={status!r}) "
                "received before plan_created, skipping"
            )
            return []

        events = []
        if status == "running":
            # Finalize previous step card if still open (engine skipped its completion)
            if self._plan_step_card:
                events += self._complete_plan_step("completed")
            self._current_plan_step = step_index
            step_id = f"plan_step_{step_index}"
            title = self._get_plan_step_title(step_index)
            self._plan_step_card = PendingCard(
                step_id=step_id, title=title,
                source_type="plan_step", plan_step_index=step_index,
            )
            self.timer.start_step(step_id)
            events.append(self.card_builder.build_step_card(
                step_id=step_id, title=title, status="running",
                source_type="plan_step", tool_name="",
                plan_step_index=step_index,
            ))
            # Update checklist
            self._update_plan_step_status(step_index, "running")
            events.append({"type": "plan_checklist", "steps": list(self._plan_steps)})

        elif status in ("completed", "failed"):
            if self._plan_step_card:
                step_id = self._plan_step_card.step_id
                duration = self.timer.end_step(step_id)
                events.append(self.card_builder.build_step_card(
                    step_id=step_id,
                    title=self._plan_step_card.title,
                    status=status,
                    source_type="plan_step",
                    tool_name="",
                    plan_step_index=step_index,
                    duration=duration,
                    absorbed_calls=self._plan_step_card.absorbed_calls,
                ))
                self._plan_step_card = None
            self._update_plan_step_status(step_index, status)
            events.append({"type": "plan_checklist", "steps": list(self._plan_steps)})

        return events

    async def on_plan_completed(self) -> list[dict]:
        """Exit Plan mode."""
        events = []
        if self._plan_step_card:
            events += self._complete_plan_step("completed")
        self.state = AggregatorState.IDLE
        self._plan_steps = None
        self._plan_id_to_index = {}
        self._current_plan_step = None
        return events

    async def flush(self) -> list[dict]:
        """Flush any pending state (called on stream end)."""
        events = []
        if self.state in (AggregatorState.SKILL_ABSORB, AggregatorState.MCP_ABSORB):
            events += self._complete_pending()
        elif self.state == AggregatorState.PLAN_ABSORB and self._plan_step_card:
            events += self._complete_plan_step("completed")
        # Cancel any pending delegation title tasks
        for task in self._delegation_title_tasks:
            if not task.done():
                task.cancel()
                try:
                    asyncio.ensure_future(self._suppress_cancel(task))
                except RuntimeError:
                    pass
        self._delegation_title_tasks.clear()
        return events

    # ── Private helpers ──

    def _handle_idle(
        self, tool_name: str, args: dict, tool_id: str, fr: FilterResult
    ) -> list[dict]:
        if fr == FilterResult.SKILL_TRIGGER:
            return self._start_skill(tool_name, args)
        if fr == FilterResult.MCP_TRIGGER:
            return self._start_mcp(tool_name, args)
        if fr == FilterResult.AGENT_TRIGGER:
            return self._start_agent_delegation(tool_name, args, tool_id)
        if fr in (FilterResult.WHITELIST, FilterResult.USER_MENTION):
            return self._create_independent_card(tool_name, args, tool_id)
        return []

    def _start_skill(self, tool_name: str, args: dict) -> list[dict]:
        step_id = f"skill_{uuid.uuid4().hex[:8]}"
        placeholder = "\u23f3"
        self.pending_card = PendingCard(
            step_id=step_id, title=placeholder, source_type="skill",
        )
        self.state = AggregatorState.SKILL_ABSORB
        self.timer.start_step(step_id)
        # Fire async LLM title generation
        skill_meta = args if isinstance(args, dict) else {}
        self.pending_card.title_task = asyncio.create_task(
            self._resolve_skill_title(step_id, skill_meta)
        )
        return [self.card_builder.build_step_card(
            step_id=step_id, title=placeholder, status="running",
            source_type="skill", tool_name=tool_name,
        )]

    async def _resolve_skill_title(self, step_id: str, meta: dict) -> None:
        """Async task: generate LLM title, update pending_card, emit title_update."""
        try:
            title = await self.title_gen.generate_skill_title(meta)
        except Exception:
            title = self.title_gen._skill_fallback(meta)
        if self.pending_card and self.pending_card.step_id == step_id:
            self.pending_card.title = title
            # Enqueue title_update for the adapter to pick up
            if self._title_update_queue is not None:
                await self._title_update_queue.put({
                    "type": "step_card", "step_id": step_id,
                    "title": title, "status": "running",
                    "source_type": "skill", "card_type": "default",
                    "duration": None, "plan_step_index": None,
                    "agent_id": self.pending_card.agent_id,
                    "input": None, "output": None, "absorbed_calls": [],
                })

    def _start_mcp(self, tool_name: str, args: dict) -> list[dict]:
        step_id = f"mcp_{uuid.uuid4().hex[:8]}"
        server = args.get("server", "unknown")
        placeholder = "\u23f3"
        self.pending_card = PendingCard(
            step_id=step_id, title=placeholder, source_type="mcp",
            mcp_server=server,
        )
        self.state = AggregatorState.MCP_ABSORB
        self.timer.start_step(step_id)
        # Fire async LLM title generation for MCP
        server_meta = {
            "name": server,
            "description": args.get("server_description", ""),
        }
        tool_meta = {
            "name": args.get("tool", ""),
            "description": args.get("tool_description", ""),
        }
        self.pending_card.title_task = asyncio.create_task(
            self._resolve_mcp_title(step_id, server_meta, tool_meta)
        )
        return [self.card_builder.build_step_card(
            step_id=step_id, title=placeholder, status="running",
            source_type="mcp", tool_name=tool_name,
        )]

    async def _resolve_mcp_title(
        self, step_id: str, server_meta: dict, tool_meta: dict
    ) -> None:
        """Async task: generate LLM title for MCP, update pending_card."""
        try:
            title = await self.title_gen.generate_mcp_title(server_meta, tool_meta)
        except Exception:
            title = self.title_gen._mcp_fallback(server_meta)
        if self.pending_card and self.pending_card.step_id == step_id:
            self.pending_card.title = title
            if self._title_update_queue is not None:
                await self._title_update_queue.put({
                    "type": "step_card", "step_id": step_id,
                    "title": title, "status": "running",
                    "source_type": "mcp", "card_type": "default",
                    "duration": None, "plan_step_index": None,
                    "agent_id": self.pending_card.agent_id,
                    "input": None, "output": None, "absorbed_calls": [],
                })

    def _create_independent_card(
        self, tool_name: str, args: dict, tool_id: str = "",
    ) -> list[dict]:
        step_id = f"tool_{uuid.uuid4().hex[:8]}"
        title = self.title_gen.humanize_tool_title(tool_name, args)
        self.timer.start_step(step_id)
        # Track for on_tool_call_end completion (store title for reuse)
        if tool_id:
            self._independent_cards[tool_id] = (step_id, title)
        return [self.card_builder.build_step_card(
            step_id=step_id, title=title, status="running",
            source_type="tool", tool_name=tool_name,
            input_data=args,
        )]

    def _start_agent_delegation(
        self, tool_name: str, args: dict, tool_id: str,
    ) -> list[dict]:
        """Create delegation card: instant title + async LLM upgrade."""
        step_id = f"agent_{uuid.uuid4().hex[:8]}"
        instant_title = self.title_gen.delegation_instant_title(args)
        self.timer.start_step(step_id)
        # Track for on_tool_call_end completion
        if tool_id:
            self._independent_cards[tool_id] = (step_id, instant_title)
        # Fire async LLM title generation
        agent_meta = {
            "name": args.get("agent_id", ""),
            "description": "",
        }
        task_meta = {
            "message": args.get("message", ""),
            "reason": args.get("reason", ""),
        }
        task = asyncio.create_task(
            self._resolve_delegation_title(
                step_id, agent_meta, task_meta, instant_title,
            )
        )
        self._delegation_title_tasks.append(task)
        return [self.card_builder.build_step_card(
            step_id=step_id, title=instant_title, status="running",
            source_type="tool", tool_name=tool_name, input_data=args,
        )]

    async def _resolve_delegation_title(
        self,
        step_id: str,
        agent_meta: dict,
        task_meta: dict,
        fallback: str,
    ) -> None:
        """Async: generate LLM title for delegation, update tracked card."""
        try:
            title = await self.title_gen.generate_delegation_title(
                agent_meta, task_meta,
            )
        except Exception:
            title = fallback
        # Update tracked card title so on_tool_call_end uses the new title
        for tid, (sid, _) in list(self._independent_cards.items()):
            if sid == step_id:
                self._independent_cards[tid] = (sid, title)
                break
        # Enqueue title update for the adapter to pick up
        if self._title_update_queue is not None:
            await self._title_update_queue.put({
                "type": "step_card", "step_id": step_id,
                "title": title, "status": "running",
                "source_type": "tool", "card_type": "default",
                "duration": None, "plan_step_index": None,
                "agent_id": "main", "input": None,
                "output": None, "absorbed_calls": [],
            })

    def _complete_pending(self) -> list[dict]:
        if self.pending_card is None:
            self.state = AggregatorState.IDLE
            return []
        step_id = self.pending_card.step_id
        duration = self.timer.end_step(step_id)
        # Cancel pending LLM title task if still running
        if self.pending_card.title_task and not self.pending_card.title_task.done():
            self.pending_card.title_task.cancel()
            # Suppress "Task was destroyed but it is pending!" warning
            try:
                asyncio.ensure_future(self._suppress_cancel(self.pending_card.title_task))
            except RuntimeError:
                pass  # no event loop
        # If title is still placeholder, use fallback from source_type
        title = self.pending_card.title
        if title == "\u23f3":
            title = f"\u6267\u884c {self.pending_card.source_type} \u64cd\u4f5c"
        card = self.card_builder.build_step_card(
            step_id=step_id,
            title=title,
            status="completed",
            source_type=self.pending_card.source_type,
            tool_name="",
            duration=duration,
            absorbed_calls=self.pending_card.absorbed_calls,
        )
        self.pending_card = None
        self.state = AggregatorState.IDLE
        return [card]

    def _complete_plan_step(self, status: str) -> list[dict]:
        if not self._plan_step_card:
            return []
        step_id = self._plan_step_card.step_id
        duration = self.timer.end_step(step_id)
        card = self.card_builder.build_step_card(
            step_id=step_id,
            title=self._plan_step_card.title,
            status=status,
            source_type="plan_step",
            tool_name="",
            plan_step_index=self._plan_step_card.plan_step_index,
            duration=duration,
            absorbed_calls=self._plan_step_card.absorbed_calls,
        )
        self._plan_step_card = None
        return [card]

    @staticmethod
    async def _suppress_cancel(task: asyncio.Task) -> None:
        """Await a cancelled task to prevent 'Task was destroyed' warnings."""
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def _get_plan_step_title(self, index: int) -> str:
        if self._plan_steps:
            for s in self._plan_steps:
                if s.get("index") == index:
                    return s.get("title", f"\u6b65\u9aa4 {index}")
        return f"\u6b65\u9aa4 {index}"

    def _update_plan_step_status(self, index: int, status: str) -> None:
        if self._plan_steps:
            for s in self._plan_steps:
                if s.get("index") == index:
                    s["status"] = status
                    break
