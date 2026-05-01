"""Shared hard-budget guard for ReAct loops."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


READONLY_EXPLORATION_TOOLS = frozenset({
    "read_file",
    "list_directory",
    "grep",
    "glob",
    "get_tool_info",
    "list_skills",
    "get_skill_info",
    "search_memory",
    "get_memory_stats",
    "get_session_context",
})


@dataclass(frozen=True)
class LoopBudgetDecision:
    should_stop: bool
    exit_reason: str = ""
    message: str = ""
    should_warn: bool = False


@dataclass
class LoopBudgetGuard:
    max_total_tool_calls: int = 30
    readonly_stagnation_limit: int = 3
    readonly_stagnation_hard_limit: int = 6
    token_anomaly_threshold: int = 40_000
    # Default 0.98 → only force termination when prompt is essentially at the
    # model context window. Set lower for stricter safety, higher (up to 0.99)
    # for "let long tasks finish at all costs". Overridable per-call via
    # ``check_token_growth(near_context_ratio=...)``.
    near_context_ratio: float = 0.98
    total_tool_calls_seen: int = 0
    token_anomaly_recoveries: int = 0
    readonly_seen_fingerprints: set[str] = field(default_factory=set)
    readonly_stagnation_rounds: int = 0

    def record_tool_calls(self, tool_calls: list[dict]) -> LoopBudgetDecision:
        self.total_tool_calls_seen += len(tool_calls or [])
        if self.total_tool_calls_seen > self.max_total_tool_calls:
            return LoopBudgetDecision(
                True,
                "tool_budget_exceeded",
                f"⚠️ 本轮任务工具调用已达到预算上限（{self.max_total_tool_calls} 次），"
                "已自动终止以避免继续消耗 token。请基于已有结果给出结论，"
                "或缩小范围后重新发起。",
            )
        return LoopBudgetDecision(False)

    def record_tool_results(
        self,
        tool_calls: list[dict],
        tool_results: list[dict],
    ) -> LoopBudgetDecision:
        if self._is_readonly_exploration_round(tool_calls):
            fingerprint = self._tool_result_fingerprint(tool_results)
            if not fingerprint or fingerprint in self.readonly_seen_fingerprints:
                self.readonly_stagnation_rounds += 1
            else:
                self.readonly_seen_fingerprints.add(fingerprint)
                self.readonly_stagnation_rounds = 0
            if self.readonly_stagnation_rounds >= self.readonly_stagnation_hard_limit:
                return LoopBudgetDecision(
                    True,
                    "readonly_stagnation",
                    "⚠️ 只读探索已经连续多轮没有获得新信息，任务已自动终止。"
                    "请基于已经读取到的内容总结结论，或提供更具体的文件/关键词继续。",
                )
            if self.readonly_stagnation_rounds >= self.readonly_stagnation_limit:
                return LoopBudgetDecision(
                    False,
                    "readonly_stagnation_warning",
                    "⚠️ 只读探索已经连续多轮没有获得明显新信息。请换查询角度、扩大/缩小关键词，"
                    "或基于已有材料收敛结论。",
                    should_warn=True,
                )
        else:
            self.readonly_stagnation_rounds = 0
        return LoopBudgetDecision(False)

    def check_token_growth(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        recovered: bool = False,
        max_recoveries: int = 1,
        context_safe: bool | None = None,
        max_context_tokens: int | None = None,
        near_context_ratio: float | None = None,
    ) -> LoopBudgetDecision:
        total_tokens = input_tokens + output_tokens
        # Resolve effective ratio: per-call override > dataclass default.
        # Clamp to a sensible range so a misconfigured value cannot produce
        # nonsense (e.g. negative or >1).
        eff_ratio = (
            near_context_ratio
            if near_context_ratio is not None
            else self.near_context_ratio
        )
        try:
            eff_ratio = float(eff_ratio)
        except (TypeError, ValueError):
            eff_ratio = 0.98
        eff_ratio = min(max(eff_ratio, 0.5), 0.99)

        near_context_limit = (
            bool(max_context_tokens)
            and total_tokens >= int(max_context_tokens * eff_ratio)
        )

        # A large prompt is not necessarily context bloat. Long-running tasks can
        # have high fixed overhead from system prompt and tool schemas while the
        # message history is still safely below its budget.
        if context_safe is True and not near_context_limit:
            return LoopBudgetDecision(False)

        if recovered:
            self.token_anomaly_recoveries += 1
            return LoopBudgetDecision(False)
        if (
            total_tokens > self.token_anomaly_threshold
            and self.total_tool_calls_seen >= max(5, self.max_total_tool_calls // 2)
        ):
            diag = (
                f" [input={input_tokens}, output={output_tokens}, "
                f"ctx_max={max_context_tokens or '?'}, "
                f"hard_terminate_ratio={eff_ratio:.2f}, "
                f"anomaly_threshold={self.token_anomaly_threshold}, "
                f"tool_calls={self.total_tool_calls_seen}/{self.max_total_tool_calls}]"
            )
            if self.token_anomaly_recoveries < max_recoveries:
                return LoopBudgetDecision(
                    False,
                    "token_growth_recoverable",
                    "⚠️ 检测到上下文 token 异常膨胀，将先执行强制压缩并在下一轮继续。" + diag,
                    should_warn=True,
                )
            return LoopBudgetDecision(
                True,
                "token_growth_terminated",
                "⚠️ 检测到上下文 token 异常膨胀且工具调用已接近预算，"
                "已尝试压缩但仍未恢复到安全区，已自动终止以避免继续扩大上下文。"
                "请基于已有信息总结结论。" + diag,
            )
        return LoopBudgetDecision(False)

    @staticmethod
    def _tool_result_fingerprint(tool_results: list[dict]) -> str:
        parts: list[str] = []
        for result in tool_results:
            if not isinstance(result, dict):
                continue
            content = str(result.get("content", ""))
            parts.append(hashlib.md5(content[:4000].encode("utf-8", errors="ignore")).hexdigest()[:10])
        return "|".join(parts)

    @staticmethod
    def _is_readonly_exploration_round(tool_calls: list[dict]) -> bool:
        if not tool_calls:
            return False
        names = {str(tc.get("name", "")) for tc in tool_calls if isinstance(tc, dict)}
        return bool(names) and names.issubset(READONLY_EXPLORATION_TOOLS)
