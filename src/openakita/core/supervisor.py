"""
运行时监督器 (Runtime Supervisor)

基于 Agent Harness 设计理论，提供运行时行为模式检测与分级干预。
整合并增强 ReasoningEngine._detect_loops() 和 TaskMonitor 的监督能力。

检测能力:
- 工具抖动: 同一工具连续多次失败（不同参数但持续失败）
- 编辑抖动: 对同一文件反复读写循环
- 推理死循环: LLM 连续返回相似内容
- Token 消耗速率异常: 单轮 token 消耗超阈值
- Plan 偏离: 当前操作与 Plan 步骤不相关

干预策略（分级）:
1. Nudge: 注入提示消息引导换策略
2. StrategySwitch: 强制回滚到检查点 + 注入新策略提示
3. ModelSwitch: 切换到不同模型
4. Escalate: 暂停执行，请求用户介入
5. Terminate: 安全终止并保存进度
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class InterventionLevel(IntEnum):
    """干预级别（递增严重程度）"""
    NONE = 0
    NUDGE = 1           # 注入提示消息
    STRATEGY_SWITCH = 2  # 回滚 + 换策略
    MODEL_SWITCH = 3     # 切换模型
    ESCALATE = 4         # 请求用户介入
    TERMINATE = 5        # 安全终止


class PatternType(str, Enum):
    """检测到的问题模式类型"""
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
    """监督事件记录"""
    timestamp: float
    pattern: PatternType
    level: InterventionLevel
    detail: str
    iteration: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Intervention:
    """干预指令"""
    level: InterventionLevel
    pattern: PatternType
    message: str = ""
    should_inject_prompt: bool = False
    prompt_injection: str = ""
    should_rollback: bool = False
    should_terminate: bool = False
    should_escalate: bool = False
    should_switch_model: bool = False


# -- 配置常量 --
TOOL_THRASH_WINDOW = 8
TOOL_THRASH_FAIL_THRESHOLD = 3
EDIT_THRASH_WINDOW = 10
EDIT_THRASH_THRESHOLD = 3
REASONING_SIMILARITY_THRESHOLD = 0.80
REASONING_SIMILARITY_WINDOW = 3
TOKEN_ANOMALY_THRESHOLD = 40000
SIGNATURE_REPEAT_WARN = 2
SIGNATURE_REPEAT_STRATEGY_SWITCH = 3
SIGNATURE_REPEAT_TERMINATE = 4
PLAN_DRIFT_WINDOW = 5
EXTREME_ITERATION_THRESHOLD = 50
SELF_CHECK_INTERVAL = 10
UNPRODUCTIVE_WINDOW = 5
UNPRODUCTIVE_ADMIN_TOOLS = frozenset({
    "create_todo", "update_todo_step", "get_todo_status", "complete_todo",
    "search_memory", "add_memory", "list_directory",
})


class RuntimeSupervisor:
    """
    运行时监督器。

    作为 ReasoningEngine 的观察者，每轮迭代后调用 evaluate()
    返回干预指令。不直接修改 Agent 状态——干预由调用方执行。
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

        # 观测状态（每次 reset() 清空）
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
        """重置所有观测状态（新任务开始时调用）"""
        self._tool_call_history.clear()
        self._file_access_history.clear()
        self._response_hashes.clear()
        self._signature_history.clear()
        self._token_per_iteration.clear()
        self._events.clear()
        self._consecutive_tool_rounds = 0

    # ==================== 数据记录 ====================

    def record_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
        success: bool = True,
        iteration: int = 0,
    ) -> None:
        """记录一次工具调用"""
        if not self._enabled:
            return
        self._tool_call_history.append({
            "tool_name": tool_name,
            "params": params or {},
            "success": success,
            "iteration": iteration,
            "timestamp": time.time(),
        })
        # 文件操作追踪
        if tool_name in ("read_file", "write_file", "edit_file", "search_replace"):
            path = ""
            if params:
                path = params.get("path", "") or params.get("file_path", "") or ""
            if path:
                op = "write" if tool_name in ("write_file", "edit_file", "search_replace") else "read"
                self._file_access_history.append({"path": path, "op": op, "iteration": str(iteration)})

    def record_tool_signature(self, signature: str) -> None:
        """记录工具调用签名（用于签名重复检测）"""
        if not self._enabled:
            return
        self._signature_history.append(signature)
        if len(self._signature_history) > TOOL_THRASH_WINDOW * 4:
            self._signature_history = self._signature_history[-TOOL_THRASH_WINDOW * 3:]

    def record_response(self, text_content: str) -> None:
        """记录 LLM 响应文本（用于推理死循环检测）"""
        if not self._enabled or not text_content:
            return
        h = hashlib.md5(text_content.strip()[:2000].encode("utf-8", errors="ignore")).hexdigest()
        self._response_hashes.append(h)
        if len(self._response_hashes) > REASONING_SIMILARITY_WINDOW * 3:
            self._response_hashes = self._response_hashes[-REASONING_SIMILARITY_WINDOW * 2:]

    def record_token_usage(self, tokens: int) -> None:
        """记录单轮 token 消耗"""
        if not self._enabled:
            return
        self._token_per_iteration.append(tokens)

    def record_consecutive_tool_rounds(self, count: int) -> None:
        """更新连续工具调用轮数"""
        self._consecutive_tool_rounds = count

    # ==================== 评估入口 ====================

    def evaluate(
        self,
        iteration: int,
        *,
        has_active_todo: bool = False,
        plan_current_step: str = "",
    ) -> Intervention | None:
        """
        综合评估当前状态，返回最严重的干预指令。

        在 ReasoningEngine 每轮迭代的 OBSERVE 阶段结束后调用。
        返回 None 表示无需干预。
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
            iteration, has_active_todo=has_active_todo,
        )
        if extreme_intervention:
            interventions.append(extreme_intervention)

        unproductive_intervention = self._check_unproductive_loop(iteration)
        if unproductive_intervention:
            interventions.append(unproductive_intervention)

        selfcheck_intervention = self._check_self_check_interval(
            iteration, has_active_todo, plan_current_step,
        )
        if selfcheck_intervention:
            interventions.append(selfcheck_intervention)

        if not interventions:
            return None

        # 返回最严重的干预
        interventions.sort(key=lambda i: i.level, reverse=True)
        chosen = interventions[0]

        self._events.append(SupervisionEvent(
            timestamp=time.time(),
            pattern=chosen.pattern,
            level=chosen.level,
            detail=chosen.message,
            iteration=iteration,
        ))

        logger.info(
            f"[Supervisor] Iter {iteration} — pattern={chosen.pattern.value} "
            f"level={chosen.level.name}: {chosen.message}"
        )

        # Decision Trace: 记录监督事件
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

    # ==================== 检测器 ====================

    def _check_signature_repeat(self, iteration: int) -> Intervention | None:
        """签名重复检测：工具名维度优先于精确签名。

        三级干预：WARN(2次) -> STRATEGY_SWITCH(3次) -> TERMINATE(4次)
        TERMINATE 级别的检测优先执行，避免低级别干预抢先 return。
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

        # --- TERMINATE checks first (highest severity) ---
        if top_count >= self._signature_repeat_terminate:
            return Intervention(
                level=InterventionLevel.TERMINATE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=(
                    f"Dead loop: tool '{top_name}' called {top_count} times "
                    f"(exact sig max={most_common_count})"
                ),
                should_terminate=True,
            )

        if most_common_count >= self._signature_repeat_terminate:
            return Intervention(
                level=InterventionLevel.TERMINATE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=f"Dead loop: '{most_common_sig[:60]}' repeated {most_common_count} times",
                should_terminate=True,
            )

        if most_common_count >= SIGNATURE_REPEAT_STRATEGY_SWITCH:
            return Intervention(
                level=InterventionLevel.STRATEGY_SWITCH,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=f"Repeated signature '{most_common_sig[:60]}' ({most_common_count}x) — rollback",
                should_inject_prompt=True,
                should_rollback=True,
                prompt_injection=(
                    "[系统提示] 检测到连续相同工具调用已达 4 次，系统已回滚。"
                    "如果任务已完成，请直接回复用户最终结果，不要再调用任何工具。"
                    "如果确实需要继续，必须使用完全不同的工具或参数。"
                    "禁止再次调用与之前相同的工具+参数组合。"
                ),
            )

        # 交替模式检测：窗口内仅 1-2 种签名以 ping-pong 方式反复切换
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
                        "[系统提示] 检测到工具调用在两个操作间交替循环。"
                        "请停止当前模式，直接回复用户结果。"
                    ),
                )

        # --- NUDGE checks (lower severity) ---
        if top_count >= self._signature_repeat_warn:
            return Intervention(
                level=InterventionLevel.NUDGE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=f"Tool '{top_name}' called {top_count} times with varying args",
                should_inject_prompt=True,
                prompt_injection=(
                    f"[系统提示] 你已经连续 {top_count} 次调用 {top_name}，"
                    "工具已返回结果。请立即停止调用工具，用自然语言整理结果回复用户。"
                    "如果还需要其他信息，请换一个不同的工具或方法。"
                ),
            )


        if most_common_count >= self._signature_repeat_warn:
            return Intervention(
                level=InterventionLevel.NUDGE,
                pattern=PatternType.SIGNATURE_REPEAT,
                message=f"Repeated signature '{most_common_sig[:60]}' ({most_common_count} times)",
                should_inject_prompt=True,
                prompt_injection=(
                    "[系统提示] 你在最近几轮中用完全相同的参数重复调用了同一个工具。"
                    "请立即停止调用工具，用自然语言回复用户。"
                ),
            )

        return None

    def _check_tool_thrashing(self, iteration: int) -> Intervention | None:
        """工具抖动检测：同一工具连续多次失败（不同参数）"""
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
                        f"[系统提示] 工具 '{tool_name}' 在最近的调用中连续失败了 {fail_count} 次。"
                        "这表明当前策略不可行。请：\n"
                        "1. 分析失败原因\n"
                        "2. 选择完全不同的方法或工具\n"
                        "3. 如果确实无法完成，请告知用户原因"
                    ),
                )

        return None

    def _check_edit_thrashing(self, iteration: int) -> Intervention | None:
        """编辑抖动检测：对同一文件反复读写"""
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
                short_path = path.rsplit("/", 1)[-1] if "/" in path else path.rsplit("\\", 1)[-1] if "\\" in path else path
                return Intervention(
                    level=InterventionLevel.NUDGE,
                    pattern=PatternType.EDIT_THRASHING,
                    message=f"File '{short_path}' has {cycle_count} read-write cycles",
                    should_inject_prompt=True,
                    prompt_injection=(
                        f"[系统提示] 检测到你对文件 '{short_path}' 进行了多次读写循环。"
                        "请：\n"
                        "1. 先确认文件的完整内容和需要修改的部分\n"
                        "2. 一次性完成所有修改，避免反复读写\n"
                        "3. 如果修改不生效，分析根本原因而不是重复尝试"
                    ),
                )

        return None

    def _check_reasoning_loop(self, iteration: int) -> Intervention | None:
        """推理死循环检测：LLM 连续返回相似内容"""
        window = self._response_hashes[-REASONING_SIMILARITY_WINDOW:]
        if len(window) < REASONING_SIMILARITY_WINDOW:
            return None

        # 检查最近 N 个响应是否完全相同（hash 匹配）
        if len(set(window)) == 1:
            return Intervention(
                level=InterventionLevel.STRATEGY_SWITCH,
                pattern=PatternType.REASONING_LOOP,
                message=f"LLM returned identical content {REASONING_SIMILARITY_WINDOW} times",
                should_inject_prompt=True,
                should_rollback=True,
                prompt_injection=(
                    "[系统提示] 你的回复内容与之前几轮完全相同，表明推理已陷入循环。"
                    "请：\n"
                    "1. 重新审视任务需求\n"
                    "2. 尝试完全不同的思路和方法\n"
                    "3. 如果确实无法继续，请向用户说明情况"
                ),
            )

        return None

    def _check_token_anomaly(self, iteration: int) -> Intervention | None:
        """Token 消耗速率异常检测（仅记录日志，不注入对话）"""
        if not self._token_per_iteration:
            return None

        last_tokens = self._token_per_iteration[-1]
        if last_tokens > self._token_anomaly_threshold:
            logger.info(
                "[Supervisor] Token usage: %d tokens (threshold: %d) — logged only, not injected",
                last_tokens, self._token_anomaly_threshold,
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
        self, iteration: int, *, has_active_todo: bool = False,
    ) -> Intervention | None:
        """极端迭代阈值检测。

        无 Plan/Todo 的简单任务直接 TERMINATE；有 Plan 时仍 ESCALATE 给用户。
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
                        f"[系统提示] 当前任务已连续执行了 {self._extreme_iteration_threshold} 轮。"
                        "请向用户汇报进度并询问是否继续。"
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
        """定期自检提醒"""
        if self._consecutive_tool_rounds <= 0:
            return None
        if self._consecutive_tool_rounds % self._self_check_interval != 0:
            return None

        rounds = self._consecutive_tool_rounds

        if has_active_todo:
            msg = (
                f"[系统提示] 已连续执行 {rounds} 轮，Plan 仍有未完成步骤。"
                "如果遇到困难，请换一种方法继续推进。"
            )
        else:
            msg = (
                f"[系统提示] 你已连续执行了 {rounds} 轮工具调用。请自我评估：\n"
                "1. 当前任务进度如何？\n"
                "2. 是否陷入了循环？\n"
                "3. 如果任务已完成，请停止工具调用，直接回复用户。"
            )

        return Intervention(
            level=InterventionLevel.NUDGE,
            pattern=PatternType.PLAN_DRIFT,
            message=f"Self-check at {rounds} consecutive rounds",
            should_inject_prompt=True,
            prompt_injection=msg,
        )

    def _check_unproductive_loop(self, iteration: int) -> Intervention | None:
        """检测连续多轮只调用行政/元工具的空转。3轮NUDGE，5轮STRATEGY_SWITCH。"""
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
                message=f"Last 5 tool calls are all administrative — escalating",
                should_inject_prompt=True,
                should_rollback=True,
                prompt_injection=(
                    "[系统提示] 连续 5 轮仅调用管理类工具，系统已回滚。"
                    "请直接回复用户结果，或执行实质操作（读取文件、编写代码、调用 API 等）。"
                ),
            )

        if len(recent_3) >= 3 and all(
            entry["tool_name"] in UNPRODUCTIVE_ADMIN_TOOLS for entry in recent_3
        ):
            return Intervention(
                level=InterventionLevel.NUDGE,
                pattern=PatternType.UNPRODUCTIVE_LOOP,
                message=f"Last 3 tool calls are all administrative",
                should_inject_prompt=True,
                prompt_injection=(
                    "[系统提示] 你最近连续多轮都只在调用管理/计划类工具，"
                    "没有执行任何实质性操作。"
                    "请立即开始执行具体工作，或直接回复结果。"
                ),
            )
        return None

    # ==================== 辅助方法 ====================

    def get_summary(self) -> dict[str, Any]:
        """获取监督器摘要统计"""
        pattern_counts: dict[str, int] = {}
        for evt in self._events:
            pattern_counts[evt.pattern.value] = pattern_counts.get(evt.pattern.value, 0) + 1

        return {
            "total_events": len(self._events),
            "pattern_counts": pattern_counts,
            "total_tool_calls": len(self._tool_call_history),
            "total_file_accesses": len(self._file_access_history),
            "max_level_reached": max((e.level for e in self._events), default=InterventionLevel.NONE).name,
        }
