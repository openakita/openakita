"""
Prompt Budget - Token budget trimming module

Controls token budgets for each part, ensuring system prompts stay within limits.

Budget allocation:
- identity_budget: 6000 tokens (SOUL.md ~60% + agent.core ~25% + user_policies ~15%)
  - SOUL.md simplified to ~60 lines of behavior constraints (~500 tokens)
  - agent.core compiled approximately ~600 tokens
  - User-defined policies (optional)
- catalogs_budget: 8000 tokens (tools 33% + skills 55% + mcp 10%)
  - Tool definitions passed via API tools parameter, catalog in system prompt only supplements descriptions
- user_budget: 300 tokens (user.summary + runtime_facts)
- memory_budget: 2500 tokens (retriever output)

Default total budget approximately ~14000 tokens.
For small context window models, use BudgetConfig.for_context_window(ctx) for adaptive scaling.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Token estimation constants
CHARS_PER_TOKEN = 4  # Conservative estimate: Chinese approximately 1.5-2, English approximately 4


@dataclass
class BudgetConfig:
    """Token budget configuration"""

    # Budget for each part (tokens)
    identity_budget: int = 6000  # SOUL.md(60%) + agent.core(25%) + user_policies(15%)
    catalogs_budget: int = (
        8000  # tools(33%) + skills(55%) + mcp(10%) — tool definitions passed via API tools parameter
    )
    user_budget: int = 300  # user.summary + runtime_facts
    memory_budget: int = 2500  # retriever output (includes MEMORY.md + pinned rules + vector memory)

    # Total budget (as hard limit)
    total_budget: int = 18000

    # Trimming priority (lower numbers trimmed first)
    # Higher priority content is retained when budget is insufficient
    priority_order: list = field(
        default_factory=lambda: [
            "memory",  # 1 - trimmed first (can keep only most relevant)
            "skills",  # 2 - keep only recently used
            "mcp",  # 3 - keep only enabled
            "user",  # 4 - user information
            "tools",  # 5 - tool catalog (more important)
            "identity",  # 6 - identity information (trimmed last)
        ]
    )

    @classmethod
    def for_context_window(cls, context_window: int) -> "BudgetConfig":
        """Adaptively adjust budget based on model context window size.

        System prompts should be limited to 40% of context_window (remainder for conversation and output).
        For windows larger than 64K, use default budget (optimized for large models).
        """
        if context_window <= 0 or context_window > 64000:
            return cls()

        prompt_budget = int(context_window * 0.40)

        if context_window > 32000:
            # sum: 5000+10000+300+2000 = 17300
            return cls(
                identity_budget=5000,
                catalogs_budget=10000,
                user_budget=300,
                memory_budget=2000,
                total_budget=min(prompt_budget, 18000),
            )
        elif context_window >= 16000:
            # sum: 3500+6000+250+1500 = 11250
            return cls(
                identity_budget=3500,
                catalogs_budget=6000,
                user_budget=250,
                memory_budget=1500,
                total_budget=min(prompt_budget, 12000),
            )
        elif context_window >= 8000:
            # sum: 2500+4000+200+1000 = 7700
            return cls(
                identity_budget=2500,
                catalogs_budget=4000,
                user_budget=200,
                memory_budget=1000,
                total_budget=min(prompt_budget, 8000),
            )
        else:
            return cls(
                identity_budget=600,
                catalogs_budget=800,
                user_budget=100,
                memory_budget=300,
                total_budget=min(prompt_budget, 2000),
            )

    @classmethod
    def for_tier(cls, tier: "PromptTier", context_window: int = 0) -> "BudgetConfig":
        """Allocate budget based on PromptTier (recommended, replaces for_context_window).

        PromptTier is determined by resolve_tier(), used together with this method:
            tier = resolve_tier(context_window)
            budget = BudgetConfig.for_tier(tier, context_window)
        """
        from .builder import PromptTier

        prompt_budget = int(context_window * 0.40) if context_window > 0 else 18000

        if tier == PromptTier.SMALL:
            return cls(
                identity_budget=600,
                catalogs_budget=800,
                user_budget=100,
                memory_budget=300,
                total_budget=min(prompt_budget, 2000),
            )
        elif tier == PromptTier.MEDIUM:
            return cls(
                identity_budget=3000,
                catalogs_budget=5000,
                user_budget=250,
                memory_budget=1500,
                total_budget=min(prompt_budget, 10000),
            )
        else:
            return cls()


@dataclass
class BudgetResult:
    """Budget trimming result"""

    content: str
    original_tokens: int
    final_tokens: int
    truncated: bool
    truncation_info: str | None = None


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for text

    Simple estimation without calling tokenizer.
    Uses average for mixed Chinese and English content.

    Args:
        text: Input text

    Returns:
        Estimated token count
    """
    if not text:
        return 0

    # Count Chinese characters
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    total_chars = len(text)
    english_chars = total_chars - chinese_chars

    # Chinese approximately 1.5 chars/token, English approximately 4 chars/token
    chinese_tokens = chinese_chars / 1.5
    english_tokens = english_chars / 4

    return int(chinese_tokens + english_tokens)


_TRUNCATE_THRESHOLD_PCT = 20  # Only truncate if over budget by 20% or more
_TOKEN_TO_CHAR_RATIO = 3.5  # token → character estimation ratio


def apply_budget(
    content: str,
    budget_tokens: int,
    section_name: str = "unknown",
    truncate_strategy: str = "end",
) -> BudgetResult:
    """
    Apply token budget to content.

    - Within budget or slightly over (<20%): log and return as-is
    - Over budget ≥20%: truncate using truncate_strategy

    Args:
        content: Original content
        budget_tokens: Budget token count
        section_name: Section name (for logging)
        truncate_strategy: "end" (default) / "start" / "middle"
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
    """Truncate from end"""
    if len(content) <= target_chars:
        return content

    truncated = content[:target_chars]

    # Try to truncate at last complete line
    last_newline = truncated.rfind("\n")
    if last_newline > target_chars * 0.8:
        truncated = truncated[:last_newline]

    return truncated + "\n...(truncated)"


def _truncate_start(content: str, target_chars: int) -> str:
    """Truncate from start (keep newest content)"""
    if len(content) <= target_chars:
        return content

    start = len(content) - target_chars
    truncated = content[start:]

    # Try to truncate at first complete line
    first_newline = truncated.find("\n")
    if first_newline > 0 and first_newline < len(truncated) * 0.2:
        truncated = truncated[first_newline + 1 :]

    return "...(truncated)\n" + truncated


def _truncate_middle(content: str, target_chars: int) -> str:
    """Truncate middle, keep head and tail"""
    if len(content) <= target_chars:
        return content

    # Keep 40% of head and 40% of tail
    keep_each = int(target_chars * 0.4)
    head = content[:keep_each]
    tail = content[-keep_each:]

    # Try to truncate at complete lines
    last_newline_head = head.rfind("\n")
    if last_newline_head > keep_each * 0.7:
        head = head[:last_newline_head]

    first_newline_tail = tail.find("\n")
    if first_newline_tail > 0 and first_newline_tail < len(tail) * 0.3:
        tail = tail[first_newline_tail + 1 :]

    return head + "\n...(middle truncated)...\n" + tail


def apply_budget_to_sections(
    sections: dict[str, str],
    config: BudgetConfig,
) -> dict[str, BudgetResult]:
    """
    Apply budget to multiple sections

    Trim by priority order, ensure total budget is not exceeded.

    Args:
        sections: Section name -> content
        config: Budget configuration

    Returns:
        Section name -> BudgetResult
    """
    results = {}

    # Allocate budget by section
    budget_map = {
        "soul": config.identity_budget * 60 // 100,
        "agent_core": config.identity_budget * 25 // 100,
        "user_policies": config.identity_budget * 15 // 100,
        "tools": config.catalogs_budget // 3,  # 33%
        "skills": config.catalogs_budget * 55 // 100,  # 55%
        "mcp": config.catalogs_budget // 10,  # 10%
        "user": config.user_budget // 2,
        "runtime_facts": config.user_budget // 2,
        "memory": config.memory_budget,
    }

    # Truncation strategy
    strategy_map = {
        "memory": "start",  # memory keeps newest
        "skills": "end",  # skills truncate from end
        "mcp": "end",  # mcp truncate from end
        "tools": "end",  # tools truncate from end
    }

    # Apply budget
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

        budget = budget_map.get(name, 200)  # Default 200 tokens
        strategy = strategy_map.get(name, "end")

        result = apply_budget(content, budget, name, strategy)
        results[name] = result
        total_tokens += result.final_tokens

    # Summary log
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
