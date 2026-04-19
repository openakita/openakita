"""
Runtime Supervisor.

Based on Agent Harness design principles, provides runtime behavior pattern detection and tiered intervention.
Consolidates and enhances the supervision capabilities of ReasoningEngine._detect_loops() and TaskMonitor.

Detection capabilities:
- Tool thrashing: same tool fails repeatedly (varying arguments but continued failures).
- Edit thrashing: repeated read-write cycles on the same file.
- Reasoning loop: LLM returns similar content in consecutive turns.
- Token consumption anomaly: single-turn token usage exceeds threshold.
- Plan drift: current operation is unrelated to the Plan step.

Intervention strategies (tiered):
1. Nudge: inject a prompt message to nudge a strategy change.
2. StrategySwitch: force a rollback to a checkpoint + inject a new-strategy prompt.
3. ModelSwitch: switch to a different model.
4. Escalate: pause execution and request user intervention.
5. Terminate: safely terminate and save progress.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class InterventionLevel(IntEnum):
    """Intervention level (increasing severity)."""

    NONE = 0
    NUDGE = 1  # Inject a prompt message
    STRATEGY_SWITCH = 2  # Rollback + change strategy
    MODEL_SWITCH = 3  # Switch model
    ESCALATE = 4  # Request user intervention
    TERMINATE = 5  # Safe termination


class PatternType(StrEnum):
    """Detected problem pattern types."""

    TOOL_THRASHING = "tool_thrashing"
    EDIT_THRASHING = "edit_thrashing"
    REASONING_LOOP = "reasoning_loop"
    TOKEN_ANOMALY = "token_anomaly"
    PLAN_DRIFT = "plan_drift"
    SIGNATURE_REPEAT = "signature_repeat"
    EXTREME_ITERATIONS = "extreme_iterations"
    UNPRODUCTIVE_LOOP = "unproductive_loop"


@dataclass
class SupervisionEvent:
    """Supervision event record."""

    timestamp: float
    pattern: PatternType
    level: InterventionLevel
    detail: str
    iteration: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Intervention:
    """Intervention directive."""

    level: InterventionLevel
    pattern: PatternType
    message: str = ""
    should_inject_prompt: bool = False
    prompt_injection: str = ""
    should_rollback: bool = False
    should_terminate: bool = False
    should_escalate: bool = False
    should_switch_model: bool = False
    throttled_tool_names: list[str] = field(default_factory=list)


# -- Configuration constants --
TOOL_THRASH_WINDOW = 8
TOOL_THRASH_FAIL_THRESHOLD = 3
EDIT_THRASH_WINDOW = 10
EDIT_THRASH_THRESHOLD = 3
REASONING_SIMILARITY_THRESHOLD = 0.80
REASONING_SIMILARITY_WINDOW = 3
TOKEN_ANOMALY_THRESHOLD = 40000
SIGNATURE_REPEAT_WARN = 3
SIGNATURE_REPEAT_STRATEGY_SWITCH = 4
SIGNATURE_REPEAT_TERMINATE = 5
PLAN_DRIFT_WINDOW = 5
EXTREME_ITERATION_THRESHOLD = 50
SELF_CHECK_INTERVAL = 10
UNPRODUCTIVE_WINDOW = 5
UNPRODUCTIVE_ADMIN_TOOLS = frozenset(
    {
        "create_todo",
        "update_todo_step",
        "get_todo_status",
        "complete_todo",
        "search_memory",
        "add_memory",
        "list_directory",
    }
)

# Polling/waiting tools used by org coordinators that are *expected* to be
# called repeatedly while waiting for sub-agents to deliver. Flagging these
# as a "tool dead loop" (the historical default) caused legitimate CMO/CTO
# coordinators to be TERMINATEd. When ``org_supervisor_poll_whitelist`` is
# enabled, signature_repeat checks for these tools:
#   - use a higher repeat threshold (POLL_REPEAT_MULTIPLIER × normal)
#   - cap intervention at NUDGE (never STRATEGY_SWITCH / TERMINATE)
#   - inject a softer prompt suggesting ``org_wait_for_deliverable``
POLL_FRIENDLY_TOOLS = frozenset(
    {
        "org_list_delegated_tasks",
        "org_list_my_tasks",
        "org_get_task_progress",
        "org_get_node_status",
        "org_wait_for_deliverable",
    }
)
POLL_REPEAT_MULTIPLIER = 2  # raise repeat thresholds by this factor


class RuntimeSupervisor:
    """
    Runtime supervisor.

    Acts as an observer of ReasoningEngine: after each iteration, evaluate() is called
    and returns an intervention directive. Does not modify Agent state directly — interventions are executed by the caller.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        tool_thrash_fail_threshold: int = TOOL_THRASH_FAIL_THRESHOLD,
        edit_thrash_threshold: int = EDIT_THRASH_THRESHOLD,
        signature_repeat_warn: int = SIGNATURE_REPEAT_WARN,
        signature_repeat_terminate: int = SIGNATURE_REPEAT_TERMINATE,
        token_anomaly_threshold: int = TOKEN_ANOMALY_THRESHOLD,
        extreme_iteration_threshold: int = EXTREME_ITERATION_THRESHOLD,
        self_check_interval: int = SELF_CHECK_INTERVAL,
    ) -> None:
        self._enabled = enabled

        self._tool_thrash_fail_threshold = tool_thrash_fail_threshold
        self._edit_thrash_threshold = edit_thrash_threshold
        self._signature_repeat_warn = signature_repeat_warn
        self._signature_repeat_terminate = signature_repeat_terminate
        self._token_anomaly_threshold = token_anomaly_threshold
        self._extreme_iteration_threshold = extreme_iteration_threshold
        self._self_check_interval = self_check_interval

        # Observation state (cleared on each reset())
        self._tool_call_history: list[dict[str, Any]] = []
        self._file_access_history: list[dict[str, str]] = []
        self._response_hashes: list[str] = []
        self._signature_history: list[str] = []
        self._token_per_iteration: list[int] = []
        self._events: list[SupervisionEvent] = []
        self._consecutive_tool_rounds: int = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def events(self) -> list[SupervisionEvent]:
        return list(self._events)

    def reset(self) -> None:
        """Reset all observation state (called when a new task starts)."""
        self._tool_call_history.clear()
        self._file_access_history.clear()
        self._response_hashes.clear()
        self._signature_history.clear()
        self._token_per_iteration.clear()
        self._events.clear()
        self._consecutive_tool_rounds = 0

    # ==================== Data recording ====================

    def record_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
        success: bool = True,
        iteration: int = 0,
    ) -> None:
        """Record a tool call."""
        if not self._enabled:
            return
        self._tool_call_history.append(
            {
                "tool_name": tool_name,
                "params": params or {},
                "success": success,
                "iteration": iteration,
                "timestamp": time.time(),
            }
        )
        # File operation tracking
        if tool_name in ("read_file", "write_file", "edit_file", "search_replace"):
            path = ""
            if params:
                path = params.get("path", "") or params.get("file_path", "") or ""
            if path:
                op = (
                    "write"
                    if tool_name in ("write_file", "edit_file", "search_replace")
                    else "read"
                )
                self._file_access_history.append(
                    {"path": path, "op": op, "iteration": str(iteration)}
                )

    def record_tool_signature(self, signature: str) -> None:
        """Record a tool call signature (used for signature-repeat detection)."""
        if not self._enabled:
            return
        self._signature_history.append(signature)
        if len(self._signature_history) > TOOL_THRASH_WINDOW * 4:
            self._signature_history = self._signature_history[-TOOL_THRASH_WINDOW * 3 :]

    def record_response(self, text_content: str) -> None:
        """Record LLM response text (used for reasoning-loop detection)."""
        if not self._enabled or not text_content:
            return
        h = hashlib.md5(text_content.strip()[:2000].encode("utf-8", errors="ignore")).hexdigest()
        self._response_hashes.append(h)
        if len(self._response_hashes) > REASONING_SIMILARITY_WINDOW * 3:
            self._response_hashes = self._response_hashes[-REASONING_SIMILARITY_WINDOW * 2 :]

    def record_token_usage(self, tokens: int) -> None:
        """Record single-turn token consumption."""
        if not self._enabled:
            return
        self._token_per_iteration.append(tokens)

    def record_consecutive_tool_rounds(self, count: int) -> None:
        """Update the count of consecutive tool-call rounds."""
        self._consecutive_tool_rounds = count

    # ==================== Evaluation entry point ====================

    def evaluate(
        self,
        iteration: int,
        *,
        has_active_todo: bool = False,
        plan_current_step: str = "",
    ) -> Intervention | None:
        """
        Comprehensively evaluate the current state and return the most severe intervention directive.

        Called at the end of the OBSERVE phase in each ReasoningEngine iteration.
        Returns None when no intervention is required.
        """
        if not self._enabled:
            return None

        interventions: list[Intervention] = []

        sig_intervention = self._check_signature_repeat(iteration)
        if sig_intervention:
            interventions.append(sig_intervention)

        thrash_intervention = self._check_tool_thrashing(iteration)
        if thrash_intervention:
            interventions.append(thrash_intervention)

        edit_intervention = self._check_edit_thrashing(iteration)
        if edit_intervention:
            interventions.append(edit_intervention)

        loop_intervention = self._check_reasoning_loop(iteration)
        if loop_intervention:
            interventions.append(loop_intervention)

        token_intervention = self._check_token_anomaly(iteration)
        if token_intervention:
            interventions.append(token_intervention)

        extreme_intervention = self._check_extreme_iterations(
            iteration,
            has_active_todo=has_active_todo,
        )
        if extreme_intervention:
            interventions.append(extreme_intervention)

        unproductive_intervention = self._check_unproductive_loop(iteration)
        if unproductive_intervention:
            interventions.append(unproductive_intervention)

        selfcheck_intervention = self._check_self_check_interval(
            iteration,
            has_active_todo,
            plan_current_step,
        )
        if selfcheck_intervention:
            interventions.append(selfcheck_intervention)

        if not interventions:
            return None

        # Return the most severe intervention
        interventions.sort(key=lambda i: i.level, reverse=True)
        chosen = interventions[0]

        self._events.append(
            SupervisionEvent(
                timestamp=time.time(),
                pattern=chosen.pattern,
                level=chosen.level,
                detail=chosen.message,
                iteration=iteration,
            )
        )

        logger.info(
            f"[Supervisor] Iter {iteration} — pattern={chosen.pattern.value} "
            f"level={chosen.level.name}: {chosen.message}"
        )

        # Decision Trace: record the supervision event
        try:
            from ..tracing.tracer import get_tracer

            tracer = get_tracer()
            tracer.record_decision(
                decision_type="supervision",
                reasoning=chosen.message,
                outcome=chosen.level.name,
                pattern=chosen.pattern.value,
                iteration=iteration,
            )
        except Exception:
            pass

        return chosen

    # ==================== Detectors ====================

    @staticmethod
    def _extra_hint_for_tool(tool_name: str) -> str:
        """Add task-semantic guidance when specific tools are in a dead loop.

        Currently targets org_delegate_task: the LLM falls into a self-delegation dead loop when there
        is no legitimate direct subordinate, so it must be explicitly guided to call
        org_submit_deliverable to hand off its current work to its superior.
        """
        if not tool_name:
            return ""
        if tool_name == "org_delegate_task":
            return (
                " [Org orchestration hint] You are repeatedly calling org_delegate_task with an invalid target. "
                "This usually means you are the actual executor of the task — stop delegating immediately "
                "and use org_submit_deliverable to hand the completed work to your superior. "
                "If lateral collaboration is needed, use org_send_message. Do not try org_delegate_task again."
            )
        return ""

    @staticmethod
    def _is_poll_friendly(tool_name: str) -> bool:
        """Return True iff the tool is in POLL_FRIENDLY_TOOLS and the
        ``org_supervisor_poll_whitelist`` flag is enabled.

        Reading config inline (lazy import) so that tests can monkeypatch
        ``openakita.config.settings`` without re-importing the supervisor
        module.
        """
        if not tool_name or tool_name not in POLL_FRIENDLY_TOOLS:
            return False
        try:
            from openakita.config import settings as _s
            return bool(getattr(_s, "org_supervisor_poll_whitelist", True))
        except Exception:
            return True

    def _check_signature_repeat(self, iteration: int) -> Intervention | None:
        """Signature-repeat detection: tool-name granularity takes precedence over exact signature.

        Three-tier intervention: WARN (2x) -> STRATEGY_SWITCH (3x) -> TERMINATE (4x).
        TERMINATE-level checks run first to prevent lower-tier interventions from returning early.

        Org-coordinator poll whitelist (``POLL_FRIENDLY_TOOLS``): for legitimate
        waiting tools such as ``org_list_delegated_tasks``, thresholds are relaxed
        and the maximum intervention is capped at NUDGE, preventing coordinators
        from being incorrectly TERMINATEd while awaiting subordinate deliverables.
        """
        recent = self._signature_history[-TOOL_THRASH_WINDOW:]
        if len(recent) < self._signature_repeat_warn:
            return None

        import re as _re

        _name_pattern = _re.compile(r"\([^)]*\)")
        name_sigs = [_name_pattern.sub("", s) for s in recent]
        name_counts = Counter(name_sigs)
        top_name, top_count = name_counts.most_common(1)[0]

        sig_counts = Counter(recent)
        most_common_sig, most_common_count = sig_counts.most_common(1)[0]

        # Extract the tool name corresponding to most_common_sig, used in the STRATEGY_SWITCH case
        _most_common_tool = (
            most_common_sig.split("(")[0] if "(" in most_common_sig else most_common_sig
        )

        # 白名单豁免：top 工具是 poll-friendly 且未达到放宽后的阈值时直接放行；
        # 达到放宽阈值时强制只发 NUDGE，绝不 STRATEGY_SWITCH/TERMINATE。
        top_is_poll = self._is_poll_friendly(top_name)
        sig_is_poll = self._is_poll_friendly(_most_common_tool)
        # poll-friendly tools 用放宽后的 warn 阈值触发 NUDGE；TERMINATE / STRATEGY_SWITCH
        # 路径直接通过 ``not top_is_poll`` / ``not sig_is_poll`` 兜底跳过，无需单独阈值。
        poll_warn_threshold = (
            self._signature_repeat_warn * POLL_REPEAT_MULTIPLIER
        )

        # --- TERMINATE checks first (highest severity) ---
        if top_count >= self._signature_repeat_terminate and not top_is_poll:
            return Intervention(
                level=InterventionLevel.TERMINATE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=(
                    f"Dead loop: tool '{top_name}' called {top_count} times "
                    f"(exact sig max={most_common_count})"
                ),
                should_terminate=True,
            )

        if most_common_count >= self._signature_repeat_terminate and not sig_is_poll:
            return Intervention(
                level=InterventionLevel.TERMINATE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=f"Dead loop: '{most_common_sig[:60]}' repeated {most_common_count} times",
                should_terminate=True,
            )

        if most_common_count >= SIGNATURE_REPEAT_STRATEGY_SWITCH and not sig_is_poll:
            return Intervention(
                level=InterventionLevel.STRATEGY_SWITCH,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=f"Repeated signature '{most_common_sig[:60]}' ({most_common_count}x) — rollback",
                should_inject_prompt=True,
                should_rollback=True,
                prompt_injection=(
                    "[System notice] Detected 3 consecutive identical tool calls; the system has rolled back. "
                    "If the task is complete, reply to the user with the final result directly and do not call any more tools. "
                    "If you truly need to continue, you must use a completely different tool or arguments. "
                    "Do not call the same tool+arguments combination again."
                    + self._extra_hint_for_tool(_most_common_tool)
                ),
            )

        # Alternating pattern detection: only 1-2 signatures within the window cycle in ping-pong fashion
        if len(set(recent)) <= 2 and len(recent) >= 6:
            transitions = sum(1 for i in range(len(recent) - 1) if recent[i] != recent[i + 1])
            if transitions >= len(recent) // 2:
                return Intervention(
                    level=InterventionLevel.STRATEGY_SWITCH,
                    pattern=PatternType.SIGNATURE_REPEAT,
                    message=f"Alternating tool pattern ({transitions} transitions in {len(recent)} calls)",
                    should_inject_prompt=True,
                    should_rollback=True,
                    prompt_injection=(
                        "[System notice] Detected tool calls alternating between two operations in a loop. "
                        "Stop the current pattern and reply to the user with the result directly."
                    ),
                )

        # --- NUDGE checks (lower severity) ---
        # poll-friendly 路径：threshold 放宽 POLL_REPEAT_MULTIPLIER 倍。
        if top_is_poll and top_count >= poll_warn_threshold:
            return Intervention(
                level=InterventionLevel.NUDGE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=(
                    f"Poll-friendly tool '{top_name}' called {top_count} times — "
                    "suggest org_wait_for_deliverable"
                ),
                should_inject_prompt=True,
                prompt_injection=(
                    f"[系统提示] 你已连续 {top_count} 次调用 {top_name} 轮询下属进度。"
                    "建议改用 org_wait_for_deliverable 阻塞等待下属交付，"
                    "可避免无效轮询。如果下属已全部交付，请直接 org_accept_deliverable "
                    "并向用户输出汇总，不要再轮询。"
                ),
                throttled_tool_names=[top_name],
            )

        if top_count >= self._signature_repeat_warn and not top_is_poll:
            return Intervention(
                level=InterventionLevel.NUDGE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=f"Tool '{top_name}' called {top_count} times with varying args",
                should_inject_prompt=True,
                prompt_injection=(
                    f"[System notice] You have called {top_name} {top_count} times in a row, "
                    "and the tool has already returned results. Stop calling it repeatedly and reply to the user in natural language with the collated results. "
                    "If you need more information, use a different tool or method."
                    + self._extra_hint_for_tool(top_name)
                ),
                throttled_tool_names=[top_name],
            )

        if most_common_count >= self._signature_repeat_warn and not sig_is_poll:
            _sig_tool = most_common_sig.split("(")[0] if "(" in most_common_sig else top_name
            return Intervention(
                level=InterventionLevel.NUDGE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=f"Repeated signature '{most_common_sig[:60]}' ({most_common_count} times)",
                should_inject_prompt=True,
                prompt_injection=(
                    "[System notice] In recent turns you have called the same tool repeatedly with identical arguments. "
                    "Stop calling it and reply to the user in natural language, or use a different tool."
                    + self._extra_hint_for_tool(_sig_tool)
                ),
                throttled_tool_names=[_sig_tool],
            )

        return None

    def _check_tool_thrashing(self, iteration: int) -> Intervention | None:
        """Tool-thrashing detection: the same tool failed multiple times in a row (with varying arguments)."""
        recent = self._tool_call_history[-TOOL_THRASH_WINDOW:]
        if len(recent) < self._tool_thrash_fail_threshold:
            return None

        tool_failures: dict[str, int] = {}
        for entry in recent:
            if not entry["success"]:
                name = entry["tool_name"]
                tool_failures[name] = tool_failures.get(name, 0) + 1

        for tool_name, fail_count in tool_failures.items():
            if fail_count >= self._tool_thrash_fail_threshold:
                return Intervention(
                    level=InterventionLevel.STRATEGY_SWITCH,
                    pattern=PatternType.TOOL_THRASHING,
                    message=(
                        f"Tool '{tool_name}' failed {fail_count} times in last "
                        f"{TOOL_THRASH_WINDOW} calls"
                    ),
                    should_inject_prompt=True,
                    should_rollback=True,
                    prompt_injection=(
                        f"[System notice] Tool '{tool_name}' has failed {fail_count} times in recent calls. "
                        "This indicates the current strategy is not viable. Please:\n"
                        "1. Analyze the cause of failure\n"
                        "2. Pick a completely different approach or tool\n"
                        "3. If the task truly cannot be completed, inform the user of the reason"
                    ),
                )

        return None

    def _check_edit_thrashing(self, iteration: int) -> Intervention | None:
        """Edit-thrashing detection: repeated read-write cycles on the same file."""
        recent = self._file_access_history[-EDIT_THRASH_WINDOW:]
        if len(recent) < self._edit_thrash_threshold * 2:
            return None

        file_cycles: dict[str, int] = {}
        for i in range(1, len(recent)):
            prev, curr = recent[i - 1], recent[i]
            if prev["path"] == curr["path"] and prev["op"] != curr["op"]:
                file_cycles[prev["path"]] = file_cycles.get(prev["path"], 0) + 1

        for path, cycle_count in file_cycles.items():
            if cycle_count >= self._edit_thrash_threshold:
                short_path = (
                    path.rsplit("/", 1)[-1]
                    if "/" in path
                    else path.rsplit("\\", 1)[-1]
                    if "\\" in path
                    else path
                )
                return Intervention(
                    level=InterventionLevel.NUDGE,
                    pattern=PatternType.EDIT_THRASHING,
                    message=f"File '{short_path}' has {cycle_count} read-write cycles",
                    should_inject_prompt=True,
                    prompt_injection=(
                        f"[System notice] Detected multiple read-write cycles on file '{short_path}'. "
                        "Please:\n"
                        "1. Confirm the file's full content and the parts that need changing first\n"
                        "2. Make all edits in one pass to avoid repeated reads/writes\n"
                        "3. If edits are not taking effect, analyze the root cause instead of retrying repeatedly"
                    ),
                )

        return None

    def _check_reasoning_loop(self, iteration: int) -> Intervention | None:
        """Reasoning-loop detection: LLM returns similar content across consecutive turns."""
        window = self._response_hashes[-REASONING_SIMILARITY_WINDOW:]
        if len(window) < REASONING_SIMILARITY_WINDOW:
            return None

        # Check whether the last N responses are identical (hash match)
        if len(set(window)) == 1:
            return Intervention(
                level=InterventionLevel.STRATEGY_SWITCH,
                pattern=PatternType.REASONING_LOOP,
                message=f"LLM returned identical content {REASONING_SIMILARITY_WINDOW} times",
                should_inject_prompt=True,
                should_rollback=True,
                prompt_injection=(
                    "[System notice] Your reply is identical to the previous few turns, indicating the reasoning is stuck in a loop. "
                    "Please:\n"
                    "1. Revisit the task requirements\n"
                    "2. Try a completely different line of thought and approach\n"
                    "3. If you truly cannot proceed, explain the situation to the user"
                ),
            )

        return None

    def _check_token_anomaly(self, iteration: int) -> Intervention | None:
        """Token-consumption anomaly detection (log only, not injected into the conversation)."""
        if not self._token_per_iteration:
            return None

        last_tokens = self._token_per_iteration[-1]
        if last_tokens > self._token_anomaly_threshold:
            logger.info(
                "[Supervisor] Token usage: %d tokens (threshold: %d) — logged only, not injected",
                last_tokens,
                self._token_anomaly_threshold,
            )
            return Intervention(
                level=InterventionLevel.NUDGE,
                pattern=PatternType.TOKEN_ANOMALY,
                message=f"Single iteration consumed {last_tokens} tokens (threshold: {self._token_anomaly_threshold})",
                should_inject_prompt=False,
                prompt_injection="",
            )

        return None

    def _check_extreme_iterations(
        self,
        iteration: int,
        *,
        has_active_todo: bool = False,
    ) -> Intervention | None:
        """Extreme iteration threshold detection.

        Simple tasks without a Plan/Todo terminate directly; with a Plan in place, escalate to the user instead.
        """
        if self._consecutive_tool_rounds < self._extreme_iteration_threshold:
            return None

        if self._consecutive_tool_rounds == self._extreme_iteration_threshold:
            if has_active_todo:
                return Intervention(
                    level=InterventionLevel.ESCALATE,
                    pattern=PatternType.EXTREME_ITERATIONS,
                    message=f"Reached {self._extreme_iteration_threshold} consecutive iterations (Plan active, escalating)",
                    should_inject_prompt=True,
                    should_escalate=True,
                    prompt_injection=(
                        f"[System notice] The current task has run for {self._extreme_iteration_threshold} consecutive rounds. "
                        "Report progress to the user and ask whether to continue."
                    ),
                )
            else:
                return Intervention(
                    level=InterventionLevel.TERMINATE,
                    pattern=PatternType.EXTREME_ITERATIONS,
                    message=(
                        f"Simple task exceeded {self._extreme_iteration_threshold} "
                        f"iterations without active Plan, terminating"
                    ),
                    should_terminate=True,
                )

        return None

    def _check_self_check_interval(
        self,
        iteration: int,
        has_active_todo: bool,
        plan_current_step: str,
    ) -> Intervention | None:
        """Periodic self-check reminder."""
        if self._consecutive_tool_rounds <= 0:
            return None
        if self._consecutive_tool_rounds % self._self_check_interval != 0:
            return None

        rounds = self._consecutive_tool_rounds

        if has_active_todo:
            msg = (
                f"[System notice] {rounds} consecutive rounds have run and the Plan still has incomplete steps. "
                "If you are stuck, try a different approach to keep making progress."
            )
        else:
            msg = (
                f"[System notice] You have run {rounds} consecutive rounds of tool calls. Please self-assess:\n"
                "1. How is the task progressing?\n"
                "2. Are you stuck in a loop?\n"
                "3. If the task is complete, stop calling tools and reply to the user directly."
            )

        return Intervention(
            level=InterventionLevel.NUDGE,
            pattern=PatternType.PLAN_DRIFT,
            message=f"Self-check at {rounds} consecutive rounds",
            should_inject_prompt=True,
            prompt_injection=msg,
        )

    def _check_unproductive_loop(self, iteration: int) -> Intervention | None:
        """Detect idle runs that only invoke administrative/meta tools across consecutive rounds. 3 rounds -> NUDGE, 5 rounds -> STRATEGY_SWITCH."""
        if iteration < 3:
            return None

        recent_5 = self._tool_call_history[-5:]
        recent_3 = self._tool_call_history[-3:]

        if len(recent_5) >= 5 and all(
            entry["tool_name"] in UNPRODUCTIVE_ADMIN_TOOLS for entry in recent_5
        ):
            return Intervention(
                level=InterventionLevel.STRATEGY_SWITCH,
                pattern=PatternType.UNPRODUCTIVE_LOOP,
                message="Last 5 tool calls are all administrative — escalating",
                should_inject_prompt=True,
                should_rollback=True,
                prompt_injection=(
                    "[System notice] 5 consecutive rounds have only called administrative tools; the system has rolled back. "
                    "Reply to the user with the result directly, or perform substantive actions (read files, write code, call APIs, etc.)."
                ),
            )

        if len(recent_3) >= 3 and all(
            entry["tool_name"] in UNPRODUCTIVE_ADMIN_TOOLS for entry in recent_3
        ):
            return Intervention(
                level=InterventionLevel.NUDGE,
                pattern=PatternType.UNPRODUCTIVE_LOOP,
                message="Last 3 tool calls are all administrative",
                should_inject_prompt=True,
                prompt_injection=(
                    "[System notice] You have spent several recent rounds calling only administrative/planning tools "
                    "without performing any substantive action. "
                    "Start doing concrete work immediately, or reply with the result directly."
                ),
            )
        return None

    # ==================== Helper methods ====================

    def get_summary(self) -> dict[str, Any]:
        """Return supervisor summary statistics."""
        pattern_counts: dict[str, int] = {}
        for evt in self._events:
            pattern_counts[evt.pattern.value] = pattern_counts.get(evt.pattern.value, 0) + 1

        return {
            "total_events": len(self._events),
            "pattern_counts": pattern_counts,
            "total_tool_calls": len(self._tool_call_history),
            "total_file_accesses": len(self._file_access_history),
            "max_level_reached": max(
                (e.level for e in self._events), default=InterventionLevel.NONE
            ).name,
        }
