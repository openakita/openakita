"""
BPEngine — BP 子任务执行引擎 (streaming API)。

核心设计:
- advance() async generator 驱动子任务执行，yield SSE events
- auto 模式连续执行，manual 模式执行一轮后暂停
- 首个子任务输入从 initial_input 获取 (M8)
- 执行前检查 input_schema required 字段完整性
- SubAgent 上下文隔离: session_messages=[] (C-1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from seeagent.api.adapters.card_builder import CardBuilder
from seeagent.api.adapters.step_aggregator import StepAggregator
from seeagent.api.adapters.step_filter import StepFilter
from seeagent.api.adapters.timer_tracker import TimerTracker
from seeagent.api.adapters.title_generator import TitleGenerator

from .models import RunMode, SubtaskStatus

if TYPE_CHECKING:
    from .models import BestPracticeConfig, SubtaskConfig
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)


class BPEngine:
    def __init__(
        self,
        state_manager: BPStateManager,
    ) -> None:
        self.state_manager = state_manager
        self._orchestrator = None

    # ── Orchestrator injection ────────────────────────────────

    def set_orchestrator(self, orchestrator) -> None:
        """由 facade 或 server.py 在启动时注入。"""
        self._orchestrator = orchestrator

    def _get_orchestrator(self):
        """获取 orchestrator，fallback 到全局实例。"""
        if self._orchestrator:
            return self._orchestrator
        try:
            import seeagent.main
            return getattr(seeagent.main, "_orchestrator", None)
        except ImportError:
            return None

    def _get_scheduler(self, bp_config, snap):
        """工厂方法: 根据 config 返回合适的 scheduler。"""
        from .scheduler import LinearScheduler
        return LinearScheduler(bp_config, snap)

    def _get_config(self, snap):
        """获取实例对应的 BP 配置。"""
        if snap.bp_config:
            return snap.bp_config
        try:
            from .facade import get_bp_config_loader
            loader = get_bp_config_loader()
            if loader and loader.configs:
                return loader.configs.get(snap.bp_id)
            return None
        except Exception:
            return None

    # ── Core execution (new: async generator) ────────────────────

    async def advance(
        self, instance_id: str, session: Any,
    ) -> AsyncIterator[dict]:
        """Execute the next ready subtask(s) and yield SSE events.

        This is the core async generator that replaces execute_subtask() for
        the new streaming architecture. It does NOT yield a final ``done``
        event (R2).

        Manual mode: executes one subtask then yields ``bp_waiting_next``.
        Auto mode: loops through all remaining subtasks until completion.
        """
        snap = self.state_manager.get(instance_id)
        if not snap:
            yield {"type": "error", "message": f"BP instance {instance_id} not found"}
            return

        bp_config = self._get_config(snap)
        if not bp_config:
            yield {"type": "error", "message": f"BP config not found for {snap.bp_id}"}
            return

        scheduler = self._get_scheduler(bp_config, snap)

        # Gap 1: yield initial progress so TaskProgressCard is visible immediately
        yield self._build_progress_event(instance_id, snap, bp_config)

        while True:
            ready = scheduler.get_ready_tasks()
            if not ready:
                # No tasks ready — might already be done
                if scheduler.is_done():
                    self.state_manager.complete(instance_id)
                    self._persist_state(instance_id, session)
                    yield self._build_bp_complete_event(instance_id, snap, bp_config)
                return

            for subtask in ready:
                # Quick path: check input completeness
                input_data = scheduler.resolve_input(subtask.id)
                missing = self._check_input_completeness(subtask, input_data)
                if missing:
                    self.state_manager.update_subtask_status(
                        instance_id, subtask.id, SubtaskStatus.WAITING_INPUT,
                    )
                    yield {
                        "type": "bp_ask_user",
                        "instance_id": instance_id,
                        "subtask_id": subtask.id,
                        "subtask_name": subtask.name,
                        "missing_fields": missing,
                        "input_schema": subtask.input_schema,
                    }
                    return

                # Mark CURRENT and yield subtask_start
                self.state_manager.update_subtask_status(
                    instance_id, subtask.id, SubtaskStatus.CURRENT,
                )
                yield {
                    "type": "bp_subtask_start",
                    "instance_id": instance_id,
                    "subtask_id": subtask.id,
                    "subtask_name": subtask.name,
                }

                # Gap 5: yield delegate card (running)
                delegate_step_id = f"delegate_{subtask.id}"
                yield {
                    "type": "step_card",
                    "step_id": delegate_step_id,
                    "title": f"委派 {subtask.agent_profile}: {subtask.name}",
                    "status": "running",
                    "source_type": "tool",
                    "card_type": "delegate",
                    "agent_id": "main",
                    "duration": None,
                }
                delegate_start = time.monotonic()

                # Execute via _run_subtask_stream with error handling (R20)
                output = None
                try:
                    async for event in self._run_subtask_stream(
                        instance_id, subtask, input_data, bp_config, session,
                    ):
                        if event.get("type") == "_internal_output":
                            output = event.get("data", {})
                        elif event.get("type") == "bp_ask_user":
                            self.state_manager.update_subtask_status(
                                instance_id, subtask.id, SubtaskStatus.WAITING_INPUT,
                            )
                            yield event
                            return
                        else:
                            # Passthrough other events to the caller
                            yield event
                except Exception as exc:
                    logger.error(
                        f"[BP] Subtask {subtask.id} failed: {exc}", exc_info=True,
                    )
                    self.state_manager.update_subtask_status(
                        instance_id, subtask.id, SubtaskStatus.FAILED,
                    )
                    yield {
                        "type": "bp_error",
                        "instance_id": instance_id,
                        "subtask_id": subtask.id,
                        "error": str(exc),
                    }
                    return

                # Gap 5: yield delegate card (completed)
                delegate_duration = round(time.monotonic() - delegate_start, 1)
                yield {
                    "type": "step_card",
                    "step_id": delegate_step_id,
                    "title": f"委派 {subtask.agent_profile}: {subtask.name}",
                    "status": "completed",
                    "source_type": "tool",
                    "card_type": "delegate",
                    "agent_id": "main",
                    "duration": delegate_duration,
                }

                # Subtask completed successfully
                if output is None:
                    output = {}
                scheduler.complete_task(subtask.id, output)
                self._persist_state(instance_id, session)

                yield {
                    "type": "bp_subtask_complete",
                    "instance_id": instance_id,
                    "subtask_id": subtask.id,
                    "subtask_name": subtask.name,
                    "output": output,
                    "summary": self._extract_summary(output),
                }
                yield self._build_progress_event(instance_id, snap, bp_config)

            # After processing ready tasks, check if done
            if scheduler.is_done():
                self.state_manager.complete(instance_id)
                self._persist_state(instance_id, session)
                yield self._build_bp_complete_event(instance_id, snap, bp_config)
                return

            # Manual mode: stop after one round, yield waiting_next
            if snap.run_mode == RunMode.MANUAL:
                yield {
                    "type": "bp_waiting_next",
                    "instance_id": instance_id,
                    "next_subtask_index": snap.current_subtask_index,
                }
                return

            # Auto mode: continue the while loop to pick up next ready tasks

    # ── advance() helpers ──────────────────────────────────────

    def _build_bp_complete_event(
        self, instance_id: str, snap: Any, bp_config: BestPracticeConfig,
    ) -> dict:
        """Build the bp_complete SSE event dict."""
        return {
            "type": "bp_complete",
            "instance_id": instance_id,
            "bp_id": bp_config.id,
            "bp_name": bp_config.name,
            "outputs": dict(snap.subtask_outputs),
        }

    def _build_progress_event(
        self, instance_id: str, snap: Any, bp_config: BestPracticeConfig,
    ) -> dict:
        """Build a bp_progress SSE event dict."""
        return {
            "type": "bp_progress",
            "instance_id": instance_id,
            "bp_name": bp_config.name,
            "statuses": dict(snap.subtask_statuses),
            "subtasks": [
                {"id": st.id, "name": st.name}
                for st in bp_config.subtasks
            ],
            "current_subtask_index": snap.current_subtask_index,
            "run_mode": snap.run_mode.value if isinstance(snap.run_mode, RunMode) else snap.run_mode,
            "status": snap.status.value if hasattr(snap.status, "value") else str(snap.status),
        }

    def _persist_state(self, instance_id: str, session: Any) -> None:
        """Persist BP state to session metadata (delegates to _persist)."""
        self._persist(instance_id, session)

    @staticmethod
    def _extract_summary(output: dict) -> str:
        """Extract a short summary from subtask output."""
        if not output:
            return ""
        import json as _json
        keys = list(output.keys())
        preview = _json.dumps(output, ensure_ascii=False)[:200]
        return f"fields: {', '.join(keys)} | {preview}"

    # ── Subtask streaming execution ─────────────────────────────

    async def _run_subtask_stream(
        self,
        instance_id: str,
        subtask: SubtaskConfig,
        input_data: dict[str, Any],
        bp_config: BestPracticeConfig,
        session: Any,
    ) -> AsyncIterator[dict]:
        """Execute a single subtask, yield SubAgent streaming events.

        Uses orchestrator.delegate() + temporary event_bus to capture streaming
        events. The final output is yielded as an ``_internal_output`` event.

        R17: delegate_task is exposed on session.context._bp_delegate_task for
        disconnect watcher cancellation.
        """
        orchestrator = self._get_orchestrator()
        if not orchestrator:
            yield {"type": "error", "message": "Orchestrator not available"}
            return

        # Derive output schema for this subtask
        snap = self.state_manager.get(instance_id)
        scheduler = self._get_scheduler(bp_config, snap)
        output_schema = scheduler.derive_output_schema(subtask.id)

        # Build delegation message
        message = self._build_delegation_message(bp_config, subtask, input_data, output_schema)

        # Temporary event_bus to capture SubAgent streaming events
        event_bus: asyncio.Queue = asyncio.Queue()
        old_bus = None
        if hasattr(session, "context"):
            old_bus = getattr(session.context, "_sse_event_bus", None)
            session.context._sse_event_bus = event_bus

        try:
            # Launch SubAgent (non-blocking)
            delegate_task = asyncio.create_task(
                orchestrator.delegate(
                    session=session,
                    from_agent="bp_engine",
                    to_agent=subtask.agent_profile,
                    message=message,
                    reason=f"BP:{bp_config.name} / {subtask.name}",
                    session_messages=[],  # Context isolation
                )
            )
            # R17: expose delegate_task for disconnect watcher cancellation
            if hasattr(session, "context"):
                session.context._bp_delegate_task = delegate_task

            # Initialize step card processing pipeline (reuse adapter components)
            step_filter = StepFilter()
            card_builder = CardBuilder()
            timer = TimerTracker()
            timer.start(f"bp_{instance_id}_{subtask.id}")
            title_gen = TitleGenerator(brain=None, user_messages=[])
            title_queue: asyncio.Queue = asyncio.Queue()
            sub_agent_id = subtask.agent_profile
            aggregator = StepAggregator(
                title_gen=title_gen,
                card_builder=card_builder,
                timer=timer,
                title_update_queue=title_queue,
                agent_id=sub_agent_id,
            )

            while True:
                try:
                    event = await asyncio.wait_for(event_bus.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if delegate_task.done():
                        break
                    continue

                etype = event.get("type")
                if etype == "done":
                    continue

                # Track sub-agent identity
                if etype == "agent_header":
                    aid = event.get("agent_id")
                    if aid and aid != "main":
                        sub_agent_id = aid
                        aggregator._agent_id = aid
                    continue

                # Tool call start → filter + aggregate
                if etype == "tool_call_start":
                    tool_name = event.get("tool", "")
                    args = event.get("args", {})
                    tool_id = event.get("id", f"bp_tool_{id(event)}")
                    fr = step_filter.classify(tool_name, args)
                    for ev in await aggregator.on_tool_call_start(
                        tool_name, args, tool_id, fr
                    ):
                        yield ev
                    # Drain title updates
                    while not title_queue.empty():
                        try:
                            yield title_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    continue

                # Tool call end → update aggregated card
                if etype == "tool_call_end":
                    tool_name = event.get("tool", "")
                    tool_id = event.get("id", "")
                    result = event.get("result", "")
                    is_error = event.get("is_error", False)
                    for ev in await aggregator.on_tool_call_end(
                        tool_name, tool_id, result, is_error
                    ):
                        yield ev
                    continue

                # Text delta → close any active aggregation
                if etype == "text_delta":
                    for ev in await aggregator.on_text_delta():
                        yield ev
                    continue

                # Pass through pre-built step_card events (e.g. from nested delegates)
                if etype == "step_card":
                    yield event
                    continue

                # Skip other raw events (thinking, etc.)

            # Flush any pending aggregation
            for ev in await aggregator.flush():
                yield ev
            while not title_queue.empty():
                try:
                    yield title_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Get final result
            raw_result = await delegate_task
            output = self._parse_output(raw_result)
            yield {"type": "_internal_output", "data": output}

        finally:
            if hasattr(session, "context"):
                session.context._sse_event_bus = old_bus
                session.context._bp_delegate_task = None  # Clean up reference

    # ── Answer (user response to bp_ask_user) ─────────────────

    async def answer(
        self,
        instance_id: str,
        subtask_id: str,
        data: dict,
        session: Any,
    ) -> AsyncIterator[dict]:
        """Handle ask_user answer: merge supplemented data, re-execute subtask."""
        snap = self.state_manager.get(instance_id)
        if not snap:
            yield {"type": "error", "message": "Instance not found"}
            return

        # Merge supplemented data into dedicated field (don't pollute subtask_outputs)
        existing = snap.supplemented_inputs.get(subtask_id, {})
        existing.update(data)
        snap.supplemented_inputs[subtask_id] = existing

        # Reset subtask status to PENDING to allow re-execution
        self.state_manager.update_subtask_status(
            instance_id, subtask_id, SubtaskStatus.PENDING,
        )

        # Reuse advance() flow
        async for event in self.advance(instance_id, session):
            yield event

    # ── Delegation message ──────────────────────────────────────

    def _build_delegation_message(
        self,
        bp_config: BestPracticeConfig,
        subtask: SubtaskConfig,
        input_data: dict[str, Any],
        output_schema: dict[str, Any] | None,
    ) -> str:
        schema_hint = (
            json.dumps(output_schema, ensure_ascii=False, indent=2)
            if output_schema
            else "由你自行决定合适的输出格式"
        )
        return (
            f"## 最佳实践任务: {bp_config.name}\n"
            f"### 当前子任务: {subtask.name}\n"
            f"{subtask.description or ''}\n\n"
            f"### 输入数据\n```json\n"
            f"{json.dumps(input_data, ensure_ascii=False, indent=2)}\n```\n\n"
            f"### 输出格式要求\n\n"
            f"请严格按以下格式输出（先写总结再写 JSON）:\n\n"
            f"**总结**: [用1-2句话简洁描述本子任务的执行结果和关键发现]\n\n"
            f"```json\n{schema_hint}\n```\n\n"
            f"## 限制\n"
            f"- 禁止使用 ask_user 工具，所有信息已在输入数据中提供\n"
            f"- JSON 必须严格符合输出格式要求\n"
            f"- **总结**行必须在 JSON 代码块之前"
        )

    # ── Chat-to-Edit ───────────────────────────────────────────

    def handle_edit_output(
        self,
        instance_id: str,
        subtask_id: str,
        changes: dict[str, Any],
        bp_config: BestPracticeConfig,
    ) -> dict[str, Any]:
        """编辑已完成子任务的输出，触发下游 STALE 标记。"""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return {"success": False, "error": f"instance {instance_id} 不存在"}

        if subtask_id not in snap.subtask_outputs:
            return {"success": False, "error": f"子任务 {subtask_id} 无输出可编辑"}

        # 深度合并
        merged = self.state_manager.merge_subtask_output(instance_id, subtask_id, changes)

        # 标记下游为 STALE
        stale = self.state_manager.mark_downstream_stale(instance_id, subtask_id, bp_config)

        # 软校验
        warning = self._validate_output_soft(merged, subtask_id, bp_config)

        result: dict[str, Any] = {
            "success": True,
            "merged": merged,
            "stale_subtasks": stale,
        }
        if warning:
            result["warning"] = warning
        return result

    # ── Input completeness ─────────────────────────────────────

    def _check_input_completeness(
        self, subtask: SubtaskConfig, input_data: dict[str, Any],
    ) -> list[str]:
        """检查 input_schema.required 字段是否都在 input_data 中。返回缺失字段列表。"""
        schema = subtask.input_schema
        if not schema:
            return []
        required = schema.get("required", [])
        return [field for field in required if field not in input_data]

    # ── Output parsing ─────────────────────────────────────────

    @staticmethod
    def _parse_output(result: str) -> dict[str, Any]:
        """从委派结果中提取 JSON 输出。"""
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            pass
        match = re.search(r"```json\s*(.*?)\s*```", result, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return {"_raw_output": str(result)}

    def _validate_output_soft(
        self, output: dict, subtask_id: str, bp_config: BestPracticeConfig,
    ) -> str | None:
        """宽松校验输出。返回警告文本或 None。"""
        from .scheduler import LinearScheduler
        # Use a minimal snapshot just for schema derivation
        dummy_snap = type("_Snap", (), {
            "subtask_statuses": {},
            "subtask_outputs": {},
            "initial_input": {},
            "current_subtask_index": 0,
            "supplemented_inputs": {},
        })()
        scheduler = LinearScheduler(bp_config, dummy_snap)
        schema = scheduler.derive_output_schema(subtask_id)
        if schema and "required" in schema:
            missing = [f for f in schema["required"] if f not in output]
            if missing:
                return f"输出缺少字段: {missing}"
        return None

    # ── Persistence ────────────────────────────────────────────

    def _persist(self, instance_id: str, session: Any) -> None:
        """持久化 BP 状态到 Session.metadata["bp_state"]。"""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return
        try:
            data = self.state_manager.serialize_for_session(snap.session_id)
            if hasattr(session, "metadata"):
                session.metadata["bp_state"] = data
        except Exception as e:
            logger.warning(f"[BP] Persist failed: {e}")

    # ── SSE Events ─────────────────────────────────────────────

    async def _emit_progress(self, instance_id: str, session: Any) -> None:
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            snap = self.state_manager.get(instance_id)
            if snap:
                bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
                await bus.put({
                    "type": "bp_progress",
                    "data": {
                        "instance_id": instance_id,
                        "bp_name": bp_name,
                        "statuses": dict(snap.subtask_statuses),
                        "subtasks": [
                            {"id": st.id, "name": st.name}
                            for st in snap.bp_config.subtasks
                        ] if snap.bp_config else [],
                        "current_subtask_index": snap.current_subtask_index,
                        "run_mode": snap.run_mode.value,
                        "status": snap.status.value,
                    },
                })
        except Exception:
            pass

    async def _emit_subtask_output(
        self, instance_id: str, subtask_id: str, output: dict, session: Any,
        *, bp_config: BestPracticeConfig | None = None, summary: str | None = None,
    ) -> None:
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            snap = self.state_manager.get(instance_id)
            subtask_name = subtask_id
            output_schema: dict | None = None
            cfg = bp_config or (snap.bp_config if snap else None)
            if cfg:
                for i, st in enumerate(cfg.subtasks):
                    if st.id == subtask_id:
                        subtask_name = st.name
                        if i + 1 < len(cfg.subtasks):
                            output_schema = cfg.subtasks[i + 1].input_schema
                        break

            await bus.put({
                "type": "bp_subtask_output",
                "data": {
                    "instance_id": instance_id,
                    "subtask_id": subtask_id,
                    "subtask_name": subtask_name,
                    "output": output,
                    "output_schema": output_schema,
                    "summary": summary or self._build_summary(output),
                },
            })
        except Exception:
            pass

    @staticmethod
    def _extract_summary_from_result(raw_result: str, output: dict) -> str | None:
        """从 SubAgent 返回文本中提取 **总结** 行作为摘要。"""
        # 匹配 **总结**: ... 或 **总结**： ...
        match = re.search(r"\*\*总结\*\*[：:]\s*(.+?)(?:\n|$)", raw_result)
        if match:
            summary = match.group(1).strip()
            if summary:
                return summary[:300]
        # 尝试提取 JSON 代码块前的说明文本
        json_match = re.search(r"```json", raw_result)
        if json_match:
            text_before = raw_result[:json_match.start()].strip()
            if text_before:
                lines = [
                    line.strip() for line in text_before.split("\n")
                    if line.strip() and not line.startswith("#")
                ]
                if lines:
                    return " ".join(lines)[:300]
        return None

    @staticmethod
    def _build_summary(output: dict) -> str:
        """构建输出摘要：key 列表 + 前 200 字符预览。"""
        if not output:
            return ""
        keys = list(output.keys())
        preview = json.dumps(output, ensure_ascii=False)[:200]
        return f"字段: {', '.join(keys)} | {preview}"

    async def _emit_delegate_card(
        self, step_id: str, subtask: SubtaskConfig, session: Any,
        status: str = "running", duration: float | None = None,
    ) -> None:
        """Emit a step_card for the delegation action itself (parent-level card)."""
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            await bus.put({
                "type": "step_card",
                "step_id": step_id,
                "title": f"委派 {subtask.agent_profile}: {subtask.name}子任务",
                "status": status,
                "source_type": "tool",
                "card_type": "delegate",
                "agent_id": "main",
                "duration": duration,
                "plan_step_index": None,
                "input": None,
                "output": None,
                "absorbed_calls": [],
            })
        except Exception:
            pass

    async def _emit_stale(
        self, instance_id: str, stale_ids: list[str], reason: str, session: Any,
    ) -> None:
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            await bus.put({
                "type": "bp_stale",
                "data": {
                    "instance_id": instance_id,
                    "stale_subtask_ids": stale_ids,
                    "reason": reason,
                },
            })
        except Exception:
            pass
