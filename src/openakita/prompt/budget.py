"""
Prompt Budget - Token 预算裁剪模块

控制各部分的 token 预算，确保系统提示词不超出限制。

预算分配:
- identity_budget: 6000 tokens (SOUL 全文 + agent.core + agent.tooling + policies)
  - SOUL.md 全文注入（~3600, 60%）：保留哲学基调和情感共鸣
  - agent.core（~720, 12%）：手写的核心执行原则精简版
  - agent.tooling（~480, 8%）：手写的工具使用原则精简版
  - policies（~1200, 20%）：系统策略 + 用户策略
- catalogs_budget: 12000 tokens (tools 30% + skills 50% + mcp 20%)
- user_budget: 300 tokens (user.summary + runtime_facts)
- memory_budget: 2500 tokens (retriever 输出)

默认总预算约 ~20800 tokens，占 128k 上下文约 16.3%。
对于小上下文窗口模型，使用 BudgetConfig.for_context_window(ctx) 自适应缩放。
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Token 估算常量
CHARS_PER_TOKEN = 4  # 保守估计，中文约 1.5-2，英文约 4


@dataclass
class BudgetConfig:
    """Token 预算配置"""

    # 各部分预算（tokens）
    identity_budget: int = 6000   # SOUL全文 + agent.core + agent.tooling + policies
    catalogs_budget: int = 12000  # tools(30%) + skills(50%) + mcp(20%) 全量注入
    user_budget: int = 300        # user.summary + runtime_facts
    memory_budget: int = 2500     # retriever 输出（含 MEMORY.md + pinned rules + vector memory）

    # 总预算（作为硬限制）
    total_budget: int = 21000

    # 裁剪优先级（数字越小越先被裁剪）
    # 高优先级的内容会在预算不足时保留
    priority_order: list = field(
        default_factory=lambda: [
            "memory",  # 1 - 最先裁剪（可以只保留最相关的）
            "skills",  # 2 - 只保留最近使用的
            "mcp",  # 3 - 只保留已启用的
            "user",  # 4 - 用户信息
            "tools",  # 5 - 工具清单（较重要）
            "identity",  # 6 - 身份信息（最后裁剪）
        ]
    )

    @classmethod
    def for_context_window(cls, context_window: int) -> "BudgetConfig":
        """根据模型上下文窗口大小自适应调整预算。

        系统提示词应控制在 context_window 的 40% 以内（剩余留给对话和输出）。
        大于 64K 时使用默认预算（为大模型优化）。
        """
        if context_window <= 0 or context_window > 64000:
            return cls()

        prompt_budget = int(context_window * 0.40)

        if context_window > 32000:
            return cls(
                identity_budget=5000,
                catalogs_budget=10000,
                user_budget=300,
                memory_budget=2000,
                total_budget=min(prompt_budget, 18000),
            )
        elif context_window >= 16000:
            return cls(
                identity_budget=4000,
                catalogs_budget=6000,
                user_budget=250,
                memory_budget=1500,
                total_budget=min(prompt_budget, 12000),
            )
        elif context_window >= 8000:
            return cls(
                identity_budget=2500,
                catalogs_budget=4000,
                user_budget=200,
                memory_budget=1000,
                total_budget=min(prompt_budget, 8000),
            )
        else:
            return cls(
                identity_budget=800,
                catalogs_budget=800,
                user_budget=100,
                memory_budget=300,
                total_budget=min(prompt_budget, 2000),
            )


@dataclass
class BudgetResult:
    """预算裁剪结果"""

    content: str
    original_tokens: int
    final_tokens: int
    truncated: bool
    truncation_info: str | None = None


def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数量

    简单估算，不调用 tokenizer。
    中英文混合内容使用平均值。

    Args:
        text: 输入文本

    Returns:
        估算的 token 数
    """
    if not text:
        return 0

    # 统计中文字符数量
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    total_chars = len(text)
    english_chars = total_chars - chinese_chars

    # 中文约 1.5 字符/token，英文约 4 字符/token
    chinese_tokens = chinese_chars / 1.5
    english_tokens = english_chars / 4

    return int(chinese_tokens + english_tokens)


_TRUNCATE_THRESHOLD_PCT = 20  # 超预算 20% 以上才截断
_TOKEN_TO_CHAR_RATIO = 3.5  # token → 字符估算系数


def apply_budget(
    content: str,
    budget_tokens: int,
    section_name: str = "unknown",
    truncate_strategy: str = "end",
) -> BudgetResult:
    """
    对内容应用 token 预算。

    - 在预算内或轻微超标（<20%）：记录日志，原样返回
    - 超标 ≥20%：按 truncate_strategy 截断

    Args:
        content: 原始内容
        budget_tokens: 预算 token 数
        section_name: 区域名称（用于日志）
        truncate_strategy: "end"（默认）/ "start" / "middle"
    """
    if not content:
        return BudgetResult(
            content="",
            original_tokens=0,
            final_tokens=0,
            truncated=False,
        )

    original_tokens = estimate_tokens(content)

    if original_tokens <= budget_tokens:
        logger.info(
            f"[Budget] {section_name}: {original_tokens} tokens "
            f"(budget: {budget_tokens}, headroom: {budget_tokens - original_tokens})"
        )
        return BudgetResult(
            content=content,
            original_tokens=original_tokens,
            final_tokens=original_tokens,
            truncated=False,
        )

    overflow = original_tokens - budget_tokens
    pct = overflow / budget_tokens * 100 if budget_tokens > 0 else float("inf")

    if pct < _TRUNCATE_THRESHOLD_PCT:
        logger.info(
            f"[Budget] {section_name}: {original_tokens} tokens "
            f"slightly over budget {budget_tokens} (+{pct:.0f}%), allowing"
        )
        return BudgetResult(
            content=content,
            original_tokens=original_tokens,
            final_tokens=original_tokens,
            truncated=False,
        )

    target_chars = int(budget_tokens * _TOKEN_TO_CHAR_RATIO)

    if truncate_strategy == "start":
        truncated = _truncate_start(content, target_chars)
    elif truncate_strategy == "middle":
        truncated = _truncate_middle(content, target_chars)
    else:
        truncated = _truncate_end(content, target_chars)

    final_tokens = estimate_tokens(truncated)
    logger.warning(
        f"[Budget] {section_name}: {original_tokens} -> {final_tokens} tokens "
        f"(budget: {budget_tokens}, truncated via {truncate_strategy})"
    )

    return BudgetResult(
        content=truncated,
        original_tokens=original_tokens,
        final_tokens=final_tokens,
        truncated=True,
    )


def _truncate_end(content: str, target_chars: int) -> str:
    """从末尾截断"""
    if len(content) <= target_chars:
        return content

    truncated = content[:target_chars]

    # 尝试在最后一个完整行处截断
    last_newline = truncated.rfind("\n")
    if last_newline > target_chars * 0.8:
        truncated = truncated[:last_newline]

    return truncated + "\n...(已截断)"


def _truncate_start(content: str, target_chars: int) -> str:
    """从开头截断（保留最新内容）"""
    if len(content) <= target_chars:
        return content

    start = len(content) - target_chars
    truncated = content[start:]

    # 尝试在第一个完整行处截断
    first_newline = truncated.find("\n")
    if first_newline > 0 and first_newline < len(truncated) * 0.2:
        truncated = truncated[first_newline + 1 :]

    return "...(已截断)\n" + truncated


def _truncate_middle(content: str, target_chars: int) -> str:
    """截断中间，保留首尾"""
    if len(content) <= target_chars:
        return content

    # 保留首 40% 和尾 40%
    keep_each = int(target_chars * 0.4)
    head = content[:keep_each]
    tail = content[-keep_each:]

    # 尝试在完整行处截断
    last_newline_head = head.rfind("\n")
    if last_newline_head > keep_each * 0.7:
        head = head[:last_newline_head]

    first_newline_tail = tail.find("\n")
    if first_newline_tail > 0 and first_newline_tail < len(tail) * 0.3:
        tail = tail[first_newline_tail + 1 :]

    return head + "\n...(中间已截断)...\n" + tail


def apply_budget_to_sections(
    sections: dict[str, str],
    config: BudgetConfig,
) -> dict[str, BudgetResult]:
    """
    对多个区域应用预算

    按优先级顺序裁剪，确保总预算不超限。

    Args:
        sections: 区域名称 -> 内容
        config: 预算配置

    Returns:
        区域名称 -> BudgetResult
    """
    results = {}

    # 按区域分配预算
    budget_map = {
        "soul": config.identity_budget * 60 // 100,
        "agent_core": config.identity_budget * 12 // 100,
        "agent_tooling": config.identity_budget * 8 // 100,
        "policies": config.identity_budget * 20 // 100,
        "tools": config.catalogs_budget * 30 // 100,    # 30%
        "skills": config.catalogs_budget * 50 // 100,    # 50%
        "mcp": config.catalogs_budget * 20 // 100,       # 20%
        "user": config.user_budget // 2,
        "runtime_facts": config.user_budget // 2,
        "memory": config.memory_budget,
    }

    # 截断策略
    strategy_map = {
        "memory": "start",  # 记忆保留最新
        "skills": "end",  # 技能截断末尾
        "mcp": "end",  # MCP 截断末尾
        "tools": "end",  # 工具截断末尾
    }

    # 应用预算
    total_tokens = 0
    for name, content in sections.items():
        if not content:
            results[name] = BudgetResult(
                content="",
                original_tokens=0,
                final_tokens=0,
                truncated=False,
            )
            continue

        budget = budget_map.get(name, 200)  # 默认 200 tokens
        strategy = strategy_map.get(name, "end")

        result = apply_budget(content, budget, name, strategy)
        results[name] = result
        total_tokens += result.final_tokens

    # 汇总日志
    if total_tokens > config.total_budget:
        logger.warning(
            f"[Budget] TOTAL: {total_tokens} tokens "
            f"EXCEEDS budget {config.total_budget} by {total_tokens - config.total_budget}"
        )
    else:
        logger.info(
            f"[Budget] TOTAL: {total_tokens} tokens "
            f"(budget: {config.total_budget}, headroom: {config.total_budget - total_tokens})"
        )

    return results
