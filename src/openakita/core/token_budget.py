"""
Token 预算控制

参考 Claude Code 的 token budget 设计:
- 用户可在消息中写 +500k 设置预算
- 达到百分比时注入提示
- 超出预算优雅终止
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TokenBudget:
    """Token 消耗预算"""

    total_limit: int = 0  # 0 = 无限制
    used: int = 0
    warning_threshold: float = 0.8  # 80% 时警告

    @property
    def remaining(self) -> int:
        if self.total_limit <= 0:
            return 999_999_999
        return max(0, self.total_limit - self.used)

    @property
    def usage_ratio(self) -> float:
        if self.total_limit <= 0:
            return 0.0
        return self.used / self.total_limit

    @property
    def is_exceeded(self) -> bool:
        return self.total_limit > 0 and self.used >= self.total_limit

    @property
    def should_warn(self) -> bool:
        return (
            self.total_limit > 0
            and self.usage_ratio >= self.warning_threshold
            and not self.is_exceeded
        )

    def record(self, tokens: int) -> None:
        """记录 token 消耗。"""
        self.used += tokens

    def get_warning_message(self) -> str | None:
        """获取预算警告消息（注入到系统提示）。"""
        if self.is_exceeded:
            return (
                f"[TOKEN BUDGET EXCEEDED] You have used {self.used:,} tokens, "
                f"exceeding the budget of {self.total_limit:,}. "
                "Please wrap up immediately and provide a summary."
            )
        if self.should_warn:
            pct = int(self.usage_ratio * 100)
            return (
                f"[TOKEN BUDGET WARNING] {pct}% of token budget used "
                f"({self.used:,}/{self.total_limit:,}). "
                "Please prioritize completing the task efficiently."
            )
        return None


def parse_token_budget(text: str) -> int | None:
    """从用户消息中解析 token 预算。

    支持格式:
    - "+500k" → 500,000
    - "+1m" → 1,000,000
    - "+100000" → 100,000

    Returns:
        token 数量，如果没有预算指令则返回 None
    """
    patterns = [
        (r"\+(\d+)k\b", lambda m: int(m.group(1)) * 1000),
        (r"\+(\d+)m\b", lambda m: int(m.group(1)) * 1_000_000),
        (r"\+(\d{4,})\b", lambda m: int(m.group(1))),
    ]

    for pattern, parser in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            budget = parser(match)
            logger.info("Parsed token budget: %d from '%s'", budget, match.group(0))
            return budget

    return None
