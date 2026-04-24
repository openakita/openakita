"""
Task Monitor

Features:
- Track task execution time
- Record iteration counts and tool calls
- Automatically switch models on timeout
- Post-task retrospect analysis
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


# Default configuration
DEFAULT_TIMEOUT_SECONDS = 600  # No-progress timeout threshold (seconds)
DEFAULT_RETROSPECT_THRESHOLD = 180  # Retrospect threshold (seconds)
DEFAULT_RETRY_BEFORE_SWITCH = 2  # Retries before switching models (global cap enforced by upstream MAX_TOTAL_LLM_RETRIES=3)
DEFAULT_RETRY_INTERVAL = 5  # Retry interval (seconds)


class TaskPhase(Enum):
    """Task phase"""

    STARTED = "started"
    TOOL_CALLING = "tool_calling"
    WAITING_LLM = "waiting_llm"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass
class IdleNudge:
    """Structured idle loop intervention.

    Provides escalating interventions when an agent gets stuck in
    consecutive iterations without using tools.
    """

    level: str
    message: str
    should_switch_model: bool = False
    should_terminate: bool = False


@dataclass
class ToolCallRecord:
    """Tool call record"""

    name: str
    input_summary: str
    output_summary: str
    duration_ms: int
    success: bool
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class IterationRecord:
    """Iteration record"""

    iteration: int
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    llm_response_preview: str = ""
    duration_ms: int = 0
    model_used: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class TaskMetrics:
    """Task metrics"""

    task_id: str
    description: str
    session_id: str | None = None

    # Timing
    start_time: float = 0
    end_time: float = 0
    total_duration_seconds: float = 0

    # Iterations
    total_iterations: int = 0
    iterations: list[IterationRecord] = field(default_factory=list)

    # Model
    initial_model: str = ""
    final_model: str = ""
    model_switched: bool = False
    switch_reason: str = ""

    # Retry and switching
    retry_count: int = 0  # Retries before switching
    context_reset_on_switch: bool = False  # Whether to reset context on switch

    # Result
    success: bool = False
    error: str | None = None
    final_response: str = ""

    # Retrospect
    retrospect_needed: bool = False
    retrospect_result: str | None = None

    def to_summary(self) -> str:
        """Generate summary"""
        lines = [
            f"Task: {self.description}",
            f"Duration: {self.total_duration_seconds:.1f}s",
            f"Iterations: {self.total_iterations}",
            f"Result: {'Success' if self.success else 'Failure'}",
        ]
        if self.model_switched:
            lines.append(f"Model Switched: {self.initial_model} → {self.final_model}")
            lines.append(f"Retries before switch: {self.retry_count}")
            if self.context_reset_on_switch:
                lines.append("Context reset")
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)


class TaskMonitor:
    """
    Task monitor.

    Tracks task execution state, timing, iterations, and other information,
    and triggers a model switch on timeout.
    """

    def __init__(
        self,
        task_id: str,
        description: str,
        session_id: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        hard_timeout_seconds: int = 0,
        retrospect_threshold: int = DEFAULT_RETROSPECT_THRESHOLD,
        fallback_model: str = "",
        on_timeout: Callable[["TaskMonitor"], None] | None = None,
        retry_before_switch: int = DEFAULT_RETRY_BEFORE_SWITCH,
    ):
        """
        Initialize the task monitor.

        Args:
            task_id: Task ID
            description: Task description
            session_id: Session ID
            timeout_seconds: Timeout threshold (seconds)
            retrospect_threshold: Retrospect threshold (seconds)
            fallback_model: Fallback model to switch to after timeout
            on_timeout: Timeout callback
            retry_before_switch: Number of retries before switching models
        """
        self.metrics = TaskMetrics(
            task_id=task_id,
            description=description,
            session_id=session_id,
        )

        self.timeout_seconds = timeout_seconds
        self.hard_timeout_seconds = hard_timeout_seconds
        self.retrospect_threshold = retrospect_threshold
        self.fallback_model = fallback_model
        self.on_timeout = on_timeout
        self.retry_before_switch = retry_before_switch

        self._phase = TaskPhase.STARTED
        self._current_iteration: IterationRecord | None = None
        self._current_tool_start: float = 0
        self._timeout_triggered = False

        # Two independent retry counters:
        # 1. LLM error retry count (incremented on LLM call failure, reset on success)
        self._retry_count = 0
        self._last_error: str | None = None

        # 2. Timeout retry count (incremented on timeout detection, not affected by LLM success)
        self._timeout_retry_count = 0
        self._last_progress_time: float = 0.0

        # 3. Idle loop detection: count consecutive iterations with 0 tool calls.
        # After N consecutive zero-tool-call iterations, the agent is likely stuck in a
        # "thinking without acting" loop. The threshold defaults to 3.
        self._consecutive_zero_tool_iterations: int = 0
        self._idle_loop_threshold: int = 3
        self._idle_warned: bool = False

    def start(self, model: str) -> None:
        """Start the task."""
        self.metrics.start_time = time.time()
        self._last_progress_time = self.metrics.start_time
        self.metrics.initial_model = model
        self.metrics.final_model = model
        self._phase = TaskPhase.STARTED
        logger.info(f"[TaskMonitor] Task started: {self.metrics.task_id}")

    def begin_iteration(self, iteration: int, model: str) -> None:
        """Begin a new iteration."""
        self._current_iteration = IterationRecord(
            iteration=iteration,
            model_used=model,
        )
        self._phase = TaskPhase.WAITING_LLM

        # Entering a new iteration counts as progress (the system is still advancing the loop)
        self._touch_progress()

        # Check for a "no-progress timeout" (avoid hard-cutting long-running tasks)
        if self.progress_idle_seconds > self.timeout_seconds and not self._timeout_triggered:
            self._handle_timeout()

        # Optional hard timeout safety net (disabled by default)
        if (
            self.hard_timeout_seconds
            and self.hard_timeout_seconds > 0
            and self.elapsed_seconds > self.hard_timeout_seconds
            and not self._timeout_triggered
        ):
            self._handle_timeout()

    def end_iteration(self, llm_response_preview: str = "") -> None:
        """End the current iteration."""
        if self._current_iteration:
            self._touch_progress()
            # Track consecutive zero-tool-call iterations (idle loop detection)
            tool_count = len(self._current_iteration.tool_calls)
            if tool_count == 0:
                self._consecutive_zero_tool_iterations += 1
                if (
                    self._consecutive_zero_tool_iterations >= self._idle_loop_threshold
                    and not self._idle_warned
                ):
                    self._idle_warned = True
                    logger.warning(
                        f"[TaskMonitor] Idle loop detected: {self._consecutive_zero_tool_iterations} "
                        f"consecutive iterations with 0 tool calls (task: {self.metrics.task_id}). "
                        f"Agent may be stuck in 'thinking without acting'."
                    )
            else:
                # Reset counter when tools are actually used — partial progress still counts
                self._consecutive_zero_tool_iterations = 0
                self._idle_warned = False

            self._current_iteration.llm_response_preview = llm_response_preview
            self._current_iteration.duration_ms = int(
                (time.time() - self.metrics.start_time) * 1000
            )
            self.metrics.iterations.append(self._current_iteration)
            self.metrics.total_iterations += 1
            self._current_iteration = None

    def begin_tool_call(self, tool_name: str, tool_input: dict) -> None:
        """Begin a tool call."""
        self._phase = TaskPhase.TOOL_CALLING
        self._current_tool_start = time.time()
        self._current_tool_name = tool_name
        self._current_tool_input = str(tool_input)
        self._touch_progress()

    def end_tool_call(self, result: str, success: bool = True) -> None:
        """End the current tool call."""
        if self._current_iteration and hasattr(self, "_current_tool_name"):
            duration_ms = int((time.time() - self._current_tool_start) * 1000)
            record = ToolCallRecord(
                name=self._current_tool_name,
                input_summary=self._current_tool_input,
                output_summary=result if result else "",
                duration_ms=duration_ms,
                success=success,
            )
            self._current_iteration.tool_calls.append(record)
        self._phase = TaskPhase.WAITING_LLM
        self._touch_progress()

    def record_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        result: str,
        *,
        success: bool,
        duration_ms: int,
    ) -> None:
        """Record a tool call (parallel-safe).

        Notes:
        - begin_tool_call/end_tool_call rely on the implicit assumption of "only one tool call at a time"
        - When a model returns multiple tool_use items in one turn (parallel tool calls), the agent may execute tools concurrently
        - This method lets the caller measure duration itself and write directly into the current iteration, avoiding shared-state races
        """
        if not self._current_iteration:
            return
        self._touch_progress()
        record = ToolCallRecord(
            name=tool_name,
            input_summary=str(tool_input),
            output_summary=result if result else "",
            duration_ms=int(duration_ms),
            success=success,
        )
        self._current_iteration.tool_calls.append(record)

    def complete(self, success: bool, response: str = "", error: str = "") -> TaskMetrics:
        """Complete the task."""
        self.metrics.end_time = time.time()
        self.metrics.total_duration_seconds = self.metrics.end_time - self.metrics.start_time
        self.metrics.success = success
        self.metrics.final_response = response
        self.metrics.error = error if not success else None
        self._phase = TaskPhase.COMPLETED if success else TaskPhase.FAILED

        # Decide whether a retrospect is needed
        self.metrics.retrospect_needed = (
            self.metrics.total_duration_seconds > self.retrospect_threshold
        )

        logger.info(
            f"[TaskMonitor] Task completed: {self.metrics.task_id}, "
            f"duration={self.metrics.total_duration_seconds:.1f}s, "
            f"iterations={self.metrics.total_iterations}, "
            f"success={success}"
        )

        return self.metrics

    def switch_model(self, new_model: str, reason: str, reset_context: bool = True) -> None:
        """
        Switch the model.

        Args:
            new_model: Name of the new model
            reason: Reason for switching
            reset_context: Whether to reset context (default True)
        """
        old_model = self.metrics.final_model
        self.metrics.final_model = new_model
        self.metrics.model_switched = True
        self.metrics.switch_reason = reason
        self.metrics.context_reset_on_switch = reset_context
        self.metrics.retry_count = self._retry_count
        logger.warning(
            f"[TaskMonitor] Model switched: {old_model} → {new_model}, "
            f"reason: {reason}, context_reset: {reset_context}, retries: {self._retry_count}"
        )
        # Reset the LLM error retry count so the new model gets a full retry budget.
        # Without this reset: after switching, _retry_count is already >= retry_before_switch,
        # so every subsequent error would immediately trigger another switch, causing an infinite loop.
        self._retry_count = 0
        self._last_error = None
        # Reset idle loop counter so the new model starts fresh without inherited warnings.
        self._consecutive_zero_tool_iterations = 0

    def record_error(self, error: str) -> bool:
        """
        Record an error and decide whether to retry.

        Args:
            error: Error message

        Returns:
            True if should retry, False if should switch models
        """
        self._last_error = error
        self._retry_count += 1

        logger.info(
            f"[TaskMonitor] Error recorded (retry {self._retry_count}/{self.retry_before_switch}): {error}"
        )

        if self._retry_count < self.retry_before_switch:
            return True  # Continue retrying
        else:
            return False  # Should switch models

    def reset_retry_count(self) -> None:
        """Reset the LLM error retry count (called after a successful LLM call).

        Note: this only resets the LLM error retry count; it does not affect the timeout retry count.
        Timeout retries are independent and are not reset by a successful LLM call.
        """
        self._retry_count = 0
        self._last_error = None

    @property
    def retry_count(self) -> int:
        """Current LLM error retry count."""
        return self._retry_count

    @property
    def timeout_retry_count(self) -> int:
        """Current timeout retry count (independent counter)."""
        return self._timeout_retry_count

    @property
    def should_retry(self) -> bool:
        """Whether an LLM error should be retried (rather than switching models)."""
        return self._retry_count < self.retry_before_switch

    @property
    def should_retry_timeout(self) -> bool:
        """Whether a timeout should be retried (rather than switching models)."""
        return self._timeout_retry_count < self.retry_before_switch

    @property
    def last_error(self) -> str | None:
        """Most recent error message."""
        return self._last_error

    @property
    def consecutive_zero_tool_iterations(self) -> int:
        """Number of consecutive iterations with 0 tool calls (idle loop indicator)."""
        return self._consecutive_zero_tool_iterations

    @property
    def is_idle_loop_detected(self) -> bool:
        """Whether the agent appears stuck in consecutive iterations with no tool usage."""
        return self._consecutive_zero_tool_iterations >= self._idle_loop_threshold

    def get_idle_loop_nudge(self) -> IdleNudge | None:
        """Return escalating nudge based on consecutive idle iterations.

        Escalation levels (from EscalationThresholds):
        - 3 iterations: soft nudge
        - 5 iterations: force tool directive
        - 7 iterations: trigger model switch
        - 10 iterations: terminate task
        """
        from .health_config import EscalationThresholds

        n = self._consecutive_zero_tool_iterations
        thresholds = EscalationThresholds()
        level = thresholds.level_for_count(n)

        if level is None:
            return None

        if level == "terminate":
            return IdleNudge(
                level="terminate",
                message=(
                    f"CRITICAL: {n} consecutive iterations with no tools. "
                    "Task is being terminated to prevent infinite loop."
                ),
                should_terminate=True,
            )

        if level == "model_switch":
            return IdleNudge(
                level="model_switch",
                message=(
                    f"SEVERE: {n} consecutive iterations with no tools. "
                    "Switching to a different model. You MUST execute a tool immediately."
                ),
                should_switch_model=True,
            )

        if level == "force_tool":
            return IdleNudge(
                level="force_tool",
                message=(
                    f"WARNING: {n} consecutive iterations with no tools. "
                    "You MUST use a tool NOW. Use run_shell to write a script, "
                    "or use read_file to gather info. Stop planning. Execute."
                ),
            )

        # soft_nudge (default at threshold)
        return IdleNudge(
            level="soft_nudge",
            message=(
                f"You have completed {n} consecutive iterations without using any tools. "
                "You are stuck in a thinking-without-acting loop. IMMEDIATELY use a tool "
                "(write_file, run_shell, or read_file) to make real progress."
            ),
        )

    @property
    def idle_loop_count(self) -> int:
        """Alias for consecutive_zero_tool_iterations (for external code)."""
        return self._consecutive_zero_tool_iterations

    def _handle_timeout(self) -> None:
        """
        Handle a timeout.

        Uses an independent timeout retry counter (not affected by successful LLM calls).
        The model is only actually switched once the timeout retries are exhausted.
        """
        self._phase = TaskPhase.TIMEOUT

        # Increment the timeout retry count (independent of LLM error retries)
        self._timeout_retry_count += 1

        logger.warning(
            f"[TaskMonitor] Task timeout: {self.metrics.task_id}, "
            f"idle={self.progress_idle_seconds:.1f}s > {self.timeout_seconds}s, "
            f"timeout_retry={self._timeout_retry_count}/{self.retry_before_switch}"
        )

        if self._timeout_retry_count < self.retry_before_switch:
            # Still have retry budget; log but do not switch
            logger.info(
                f"[TaskMonitor] Timeout retry {self._timeout_retry_count}/{self.retry_before_switch}, "
                f"continuing with current model"
            )
        else:
            # Timeout retries exhausted; switch to the fallback model and reset context
            self._timeout_triggered = True
            self.metrics.retry_count = self._timeout_retry_count
            if not self.fallback_model:
                logger.error(
                    "[TaskMonitor] Timeout retries exhausted but no fallback_model configured; "
                    "cannot switch model."
                )
            else:
                self.switch_model(
                    self.fallback_model,
                    f"Task execution exceeded {self.timeout_seconds} seconds, retried {self.retry_before_switch} times",
                    reset_context=True,  # IMPORTANT: Reset context on switch
                )

            # Trigger the callback
            if self.on_timeout:
                try:
                    self.on_timeout(self)
                except Exception as e:
                    logger.error(f"[TaskMonitor] Timeout callback error: {e}")

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed time since start (seconds)."""
        if self.metrics.start_time == 0:
            return 0
        return time.time() - self.metrics.start_time

    def _touch_progress(self) -> None:
        """Record a progress timestamp (used for no-progress timeout detection)."""
        self._last_progress_time = time.time()

    @property
    def progress_idle_seconds(self) -> float:
        """Idle time since the last progress point (seconds)."""
        if not self._last_progress_time:
            return 0.0
        return time.time() - self._last_progress_time

    @property
    def is_timeout(self) -> bool:
        """Whether a timeout has already been triggered."""
        return self._timeout_triggered

    @property
    def should_switch_model(self) -> bool:
        """
        Whether the model should be switched.

        Switch only when all of the following hold:
        1. Timed out
        2. Timeout retries exhausted
        3. Switch not yet triggered
        """
        if self._timeout_triggered:
            return False
        if self.progress_idle_seconds <= self.timeout_seconds:
            return False
        # Timed out; check whether timeout retries are exhausted
        return self._timeout_retry_count >= self.retry_before_switch

    @property
    def needs_context_reset(self) -> bool:
        """Whether context should be reset when switching models."""
        return self.metrics.context_reset_on_switch

    @property
    def current_model(self) -> str:
        """The model currently in use."""
        return self.metrics.final_model

    def get_retrospect_context(self) -> str:
        """
        Get the retrospect context.

        Returns detailed task execution information for the LLM to analyze.
        """
        lines = [
            "# Task execution retrospect context",
            "",
            "## Basic information",
            f"- Task description: {self.metrics.description}",
            f"- Total duration: {self.metrics.total_duration_seconds:.1f}s",
            f"- Iterations: {self.metrics.total_iterations}",
            f"- Final result: {'Success' if self.metrics.success else 'Failure'}",
        ]

        if self.metrics.model_switched:
            lines.extend(
                [
                    "",
                    "## Model switch",
                    f"- Original model: {self.metrics.initial_model}",
                    f"- Switched to: {self.metrics.final_model}",
                    f"- Switch reason: {self.metrics.switch_reason}",
                ]
            )

        if self.metrics.iterations:
            lines.extend(
                [
                    "",
                    "## Iteration details",
                ]
            )
            for it in self.metrics.iterations[-10:]:  # Show at most the last 10 iterations
                lines.append(f"\n### Iteration {it.iteration}")
                lines.append(f"- Model: {it.model_used}")
                lines.append(f"- Tool calls: {len(it.tool_calls)}")
                for tc in it.tool_calls:
                    status = "OK" if tc.success else "FAIL"
                    lines.append(f"  - {status} {tc.name} ({tc.duration_ms}ms)")
                    if tc.output_summary:
                        lines.append(f"    Output: {tc.output_summary}")
                if it.llm_response_preview:
                    lines.append(f"- LLM response preview: {it.llm_response_preview}")

        if self.metrics.error:
            lines.extend(
                [
                    "",
                    "## Error",
                    f"{self.metrics.error}",
                ]
            )

        return "\n".join(lines)


# Retrospect prompt template
RETROSPECT_PROMPT = """Please analyze the following task execution and identify why it took so long:

{context}

Please analyze across the following dimensions:

1. **Task complexity analysis**
   - Is the task itself complex? How many steps does it require?
   - Was there a reasonable execution plan?

2. **Execution efficiency analysis**
   - Were tool calls efficient? Were there redundant or useless calls?
   - Were there detours? Which steps could be optimized?

3. **Error and retry analysis**
   - Did errors occur? Was error handling appropriate?
   - Were there unnecessary retries?

4. **Improvement suggestions**
   - How could similar tasks be handled more efficiently next time?
   - Are new skills or tools needed?

Please summarize concisely, keeping it under 200 words."""


# ==================== Retrospect result storage ====================


@dataclass
class RetrospectRecord:
    """Retrospect record"""

    task_id: str
    session_id: str | None
    description: str
    duration_seconds: float
    iterations: int
    model_switched: bool
    initial_model: str
    final_model: str
    retrospect_result: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "description": self.description,
            "duration_seconds": self.duration_seconds,
            "iterations": self.iterations,
            "model_switched": self.model_switched,
            "initial_model": self.initial_model,
            "final_model": self.final_model,
            "retrospect_result": self.retrospect_result,
            "timestamp": self.timestamp,
        }


class RetrospectStorage:
    """
    Retrospect result storage.

    Saves retrospect results to files so the daily self-check system can read and summarize them.
    """

    def __init__(self, storage_dir: Path | None = None):
        """
        Initialize storage.

        Args:
            storage_dir: Storage directory, defaults to data/retrospects/
        """
        if storage_dir is None:
            from ..config import settings

            storage_dir = settings.project_root / "data" / "retrospects"

        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save(self, record: RetrospectRecord) -> bool:
        """
        Save a retrospect record.

        Stored by date with one file per day (append mode).

        Args:
            record: Retrospect record

        Returns:
            Whether the save succeeded
        """
        import json

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            file_path = self.storage_dir / f"{today}_retrospects.jsonl"

            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

            logger.info(f"[RetrospectStorage] Saved retrospect: {record.task_id}")
            return True

        except Exception as e:
            logger.error(f"[RetrospectStorage] Failed to save: {e}")
            return False

    def load_today(self) -> list[RetrospectRecord]:
        """Load today's retrospect records."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.load_by_date(today)

    def load_by_date(self, date: str) -> list[RetrospectRecord]:
        """
        Load retrospect records for the given date.

        Args:
            date: Date string (YYYY-MM-DD)

        Returns:
            List of retrospect records
        """
        import json

        file_path = self.storage_dir / f"{date}_retrospects.jsonl"

        if not file_path.exists():
            return []

        records = []
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        records.append(
                            RetrospectRecord(
                                task_id=data.get("task_id", ""),
                                session_id=data.get("session_id"),
                                description=data.get("description", ""),
                                duration_seconds=data.get("duration_seconds", 0),
                                iterations=data.get("iterations", 0),
                                model_switched=data.get("model_switched", False),
                                initial_model=data.get("initial_model", ""),
                                final_model=data.get("final_model", ""),
                                retrospect_result=data.get("retrospect_result", ""),
                                timestamp=data.get("timestamp", ""),
                            )
                        )
        except Exception as e:
            logger.error(f"[RetrospectStorage] Failed to load {date}: {e}")

        return records

    def get_summary(self, date: str | None = None) -> dict:
        """
        Get a retrospect summary.

        Args:
            date: Date, defaults to today

        Returns:
            Summary information
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        records = self.load_by_date(date)

        if not records:
            return {
                "date": date,
                "total_tasks": 0,
                "total_duration": 0,
                "avg_duration": 0,
                "model_switches": 0,
                "common_issues": [],
            }

        total_duration = sum(r.duration_seconds for r in records)
        model_switches = sum(1 for r in records if r.model_switched)

        # Extract common issues (simple keyword counting)
        issue_keywords = ["redundant", "useless", "detour", "error", "timeout", "failure"]
        issue_counts = dict.fromkeys(issue_keywords, 0)

        for record in records:
            for kw in issue_keywords:
                if kw in record.retrospect_result:
                    issue_counts[kw] += 1

        common_issues = [
            {"issue": kw, "count": count} for kw, count in issue_counts.items() if count > 0
        ]
        common_issues.sort(key=lambda x: x["count"], reverse=True)

        return {
            "date": date,
            "total_tasks": len(records),
            "total_duration": total_duration,
            "avg_duration": total_duration / len(records),
            "model_switches": model_switches,
            "common_issues": common_issues[:5],  # At most 5
            "records": [r.to_dict() for r in records],
        }


# Global storage instance
_retrospect_storage: RetrospectStorage | None = None


def get_retrospect_storage() -> RetrospectStorage:
    """Get the retrospect storage singleton."""
    global _retrospect_storage
    if _retrospect_storage is None:
        _retrospect_storage = RetrospectStorage()
    return _retrospect_storage
