"""SeeCrab data models — shared across all adapter sub-modules."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum


class AggregatorState(Enum):
    """Aggregation state machine states."""

    IDLE = "idle"
    SKILL_ABSORB = "skill_absorb"
    MCP_ABSORB = "mcp_absorb"
    PLAN_ABSORB = "plan_absorb"


class FilterResult(Enum):
    """Tool call classification results."""

    SKILL_TRIGGER = "skill_trigger"
    MCP_TRIGGER = "mcp_trigger"
    WHITELIST = "whitelist"
    USER_MENTION = "user_mention"
    HIDDEN = "hidden"


@dataclass
class StepFilterConfig:
    """Step filter configuration — runtime adjustable."""

    whitelist: list[str] = field(default_factory=lambda: [
        "web_search", "news_search", "browser_task",
        "generate_image",
        "delegate_to_agent", "delegate_parallel",
    ])
    skill_triggers: list[str] = field(default_factory=lambda: [
        "load_skill", "run_skill_script",
    ])
    mcp_trigger: str = "call_mcp_tool"
    user_mention_keywords: dict[str, list[str]] = field(default_factory=lambda: {
        "read_file": ["读取", "读", "查看文件", "打开文件", "read"],
        "write_file": ["写入", "写", "创建文件", "生成文件", "write"],
        "run_shell": ["运行", "执行", "跑", "run", "execute"],
    })


@dataclass
class PendingCard:
    """Working buffer for the aggregation state machine."""

    step_id: str
    title: str
    title_task: asyncio.Task | None = None
    status: str = "running"
    source_type: str = ""
    card_type: str = "default"
    plan_step_index: int | None = None
    agent_id: str = "main"
    t_start: float = 0.0
    input_summary: dict | None = None
    absorbed_calls: list[dict] = field(default_factory=list)
    mcp_server: str | None = None


@dataclass
class ReplyTimer:
    """Per-reply timing state."""

    reply_id: str
    t_request: float
    t_first_token: float | None = None
    t_done: float | None = None
    step_timers: dict[str, StepTimer] = field(default_factory=dict)


@dataclass
class StepTimer:
    """Per-step timing state."""

    step_id: str
    t_start: float
    t_end: float | None = None
