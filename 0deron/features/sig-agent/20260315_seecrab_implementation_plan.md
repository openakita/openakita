# SeeCrab WebApp Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build SeeCrab, a real-time Agent step visualization webapp with SSE streaming, step card filtering/aggregation, and timer tracking.

**Architecture:** Backend SeeCrabAdapter layer translates raw Agent events into refined SSE events (filtering, aggregation, LLM title generation, timing). Vue 3 frontend purely renders these events. Three modes: Normal, Plan, Multi-Agent.

**Tech Stack:** Python 3.11 / FastAPI (backend adapter + routes), Vue 3 / Vite / TypeScript / Pinia (frontend), SSE over fetch+ReadableStream.

**Spec:** `0deron/features/sig-agent/20260315_seecrab_technical_design.md`

---

## Chunk 1: Backend Data Models & Pure Modules

### Task 1: SeeCrab Data Models

**Files:**
- Create: `src/openakita/api/adapters/seecrab_models.py`
- Test: `tests/unit/test_seecrab_models.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_seecrab_models.py
"""Tests for SeeCrab data models."""
from __future__ import annotations

import pytest

from openakita.api.adapters.seecrab_models import (
    AggregatorState,
    FilterResult,
    PendingCard,
    ReplyTimer,
    StepFilterConfig,
    StepTimer,
)


class TestAggregatorState:
    def test_all_states_exist(self):
        assert AggregatorState.IDLE.value == "idle"
        assert AggregatorState.SKILL_ABSORB.value == "skill_absorb"
        assert AggregatorState.MCP_ABSORB.value == "mcp_absorb"
        assert AggregatorState.PLAN_ABSORB.value == "plan_absorb"


class TestFilterResult:
    def test_all_results_exist(self):
        assert FilterResult.SKILL_TRIGGER.value == "skill_trigger"
        assert FilterResult.MCP_TRIGGER.value == "mcp_trigger"
        assert FilterResult.WHITELIST.value == "whitelist"
        assert FilterResult.USER_MENTION.value == "user_mention"
        assert FilterResult.HIDDEN.value == "hidden"


class TestStepFilterConfig:
    def test_defaults(self):
        config = StepFilterConfig()
        assert "web_search" in config.whitelist
        assert "load_skill" in config.skill_triggers
        assert "get_skill_info" not in config.skill_triggers  # read-only, not a trigger
        assert config.mcp_trigger == "call_mcp_tool"
        assert "read_file" in config.user_mention_keywords

    def test_custom_whitelist(self):
        config = StepFilterConfig(whitelist=["custom_tool"])
        assert config.whitelist == ["custom_tool"]


class TestPendingCard:
    def test_defaults(self):
        card = PendingCard(step_id="s1", title="test")
        assert card.status == "running"
        assert card.source_type == ""
        assert card.agent_id == "main"
        assert card.absorbed_calls == []
        assert card.mcp_server is None

    def test_absorbed_calls_are_independent(self):
        c1 = PendingCard(step_id="s1", title="t1")
        c2 = PendingCard(step_id="s2", title="t2")
        c1.absorbed_calls.append({"tool": "x"})
        assert c2.absorbed_calls == []


class TestReplyTimer:
    def test_defaults(self):
        timer = ReplyTimer(reply_id="r1", t_request=100.0)
        assert timer.t_first_token is None
        assert timer.t_done is None
        assert timer.step_timers == {}


class TestStepTimer:
    def test_creation(self):
        t = StepTimer(step_id="s1", t_start=100.0)
        assert t.t_end is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_seecrab_models.py -x -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write the models**

```python
# src/openakita/api/adapters/seecrab_models.py
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
        "generate_image", "deliver_artifacts",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_seecrab_models.py -x -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openakita/api/adapters/seecrab_models.py tests/unit/test_seecrab_models.py
git commit -m "feat(seecrab): add data models for adapter layer"
```

---

### Task 2: StepFilter — Tool Filtering

**Files:**
- Create: `src/openakita/api/adapters/step_filter.py`
- Test: `tests/unit/test_step_filter.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_step_filter.py
"""Tests for StepFilter — tool call classification."""
from __future__ import annotations

import pytest

from openakita.api.adapters.seecrab_models import FilterResult, StepFilterConfig
from openakita.api.adapters.step_filter import StepFilter


class TestClassify:
    def setup_method(self):
        self.f = StepFilter()

    def test_skill_triggers(self):
        assert self.f.classify("load_skill", {}) == FilterResult.SKILL_TRIGGER
        assert self.f.classify("run_skill_script", {}) == FilterResult.SKILL_TRIGGER
        # get_skill_info is read-only, should NOT trigger SKILL_ABSORB
        assert self.f.classify("get_skill_info", {}) == FilterResult.HIDDEN

    def test_mcp_trigger(self):
        assert self.f.classify("call_mcp_tool", {"server": "gh"}) == FilterResult.MCP_TRIGGER

    def test_whitelist(self):
        assert self.f.classify("web_search", {"query": "test"}) == FilterResult.WHITELIST
        assert self.f.classify("deliver_artifacts", {}) == FilterResult.WHITELIST

    def test_hidden(self):
        assert self.f.classify("read_file", {}) == FilterResult.HIDDEN
        assert self.f.classify("write_file", {}) == FilterResult.HIDDEN
        assert self.f.classify("add_memory", {}) == FilterResult.HIDDEN
        assert self.f.classify("get_tool_info", {}) == FilterResult.HIDDEN

    def test_unknown_tool_hidden(self):
        assert self.f.classify("some_unknown_tool", {}) == FilterResult.HIDDEN


class TestUserMention:
    def setup_method(self):
        self.f = StepFilter()

    def test_mention_promotes_hidden_tool(self):
        self.f.set_user_messages(["帮我读取 config.yaml 文件"])
        assert self.f.classify("read_file", {}) == FilterResult.USER_MENTION

    def test_mention_run_shell(self):
        self.f.set_user_messages(["运行 npm install"])
        assert self.f.classify("run_shell", {}) == FilterResult.USER_MENTION

    def test_no_mention_stays_hidden(self):
        self.f.set_user_messages(["今天天气怎么样"])
        assert self.f.classify("read_file", {}) == FilterResult.HIDDEN

    def test_whitelist_not_affected_by_mention(self):
        self.f.set_user_messages(["搜索一下"])
        assert self.f.classify("web_search", {"query": "test"}) == FilterResult.WHITELIST


class TestCustomConfig:
    def test_custom_whitelist(self):
        config = StepFilterConfig(whitelist=["my_tool"])
        f = StepFilter(config=config)
        assert f.classify("my_tool", {}) == FilterResult.WHITELIST
        assert f.classify("web_search", {}) == FilterResult.HIDDEN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_step_filter.py -x -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write StepFilter**

```python
# src/openakita/api/adapters/step_filter.py
"""StepFilter: classifies tool calls for step card visibility."""
from __future__ import annotations

from .seecrab_models import FilterResult, StepFilterConfig


class StepFilter:
    """Classifies tool calls as visible step cards or hidden internals."""

    def __init__(self, config: StepFilterConfig | None = None):
        self.config = config or StepFilterConfig()
        self._user_messages: list[str] = []

    def set_user_messages(self, messages: list[str]) -> None:
        """Set recent user messages for mention detection."""
        self._user_messages = messages[-5:]

    def classify(self, tool_name: str, args: dict) -> FilterResult:
        """Classify a tool call.

        Priority: skill_trigger > mcp_trigger > whitelist > user_mention > hidden.
        """
        if tool_name in self.config.skill_triggers:
            return FilterResult.SKILL_TRIGGER

        if tool_name == self.config.mcp_trigger:
            return FilterResult.MCP_TRIGGER

        if tool_name in self.config.whitelist:
            return FilterResult.WHITELIST

        if self._check_user_mention(tool_name):
            return FilterResult.USER_MENTION

        return FilterResult.HIDDEN

    def _check_user_mention(self, tool_name: str) -> bool:
        """Check if user recently mentioned this tool's operation."""
        keywords = self.config.user_mention_keywords.get(tool_name)
        if not keywords:
            return False
        combined = " ".join(self._user_messages).lower()
        return any(kw in combined for kw in keywords)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_step_filter.py -x -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openakita/api/adapters/step_filter.py tests/unit/test_step_filter.py
git commit -m "feat(seecrab): add StepFilter for tool call classification"
```

---

### Task 3: CardBuilder — Step Card Assembly

**Files:**
- Create: `src/openakita/api/adapters/card_builder.py`
- Test: `tests/unit/test_card_builder.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_card_builder.py
"""Tests for CardBuilder — step card event assembly."""
from __future__ import annotations

import pytest

from openakita.api.adapters.card_builder import CardBuilder


class TestBuildStepCard:
    def setup_method(self):
        self.builder = CardBuilder()

    def test_basic_card(self):
        card = self.builder.build_step_card(
            step_id="s1", title="搜索测试", status="running",
            source_type="tool", tool_name="web_search",
        )
        assert card["type"] == "step_card"
        assert card["step_id"] == "s1"
        assert card["title"] == "搜索测试"
        assert card["status"] == "running"
        assert card["card_type"] == "search"
        assert card["agent_id"] == "main"
        assert card["absorbed_calls"] == []

    def test_completed_with_io(self):
        card = self.builder.build_step_card(
            step_id="s2", title="done", status="completed",
            source_type="skill", tool_name="load_skill",
            duration=3.2,
            input_data={"query": "test"},
            output_data="result text",
            absorbed_calls=[{"tool": "web_search", "duration": 1.0}],
        )
        assert card["duration"] == 3.2
        assert card["input"] == {"query": "test"}
        assert card["output"] == "result text"
        assert len(card["absorbed_calls"]) == 1

    def test_plan_step_card(self):
        card = self.builder.build_step_card(
            step_id="s3", title="步骤1", status="running",
            source_type="plan_step", tool_name="web_search",
            plan_step_index=1,
        )
        assert card["plan_step_index"] == 1
        assert card["source_type"] == "plan_step"


class TestGetCardType:
    def setup_method(self):
        self.builder = CardBuilder()

    def test_search_types(self):
        assert self.builder._get_card_type("web_search") == "search"
        assert self.builder._get_card_type("news_search") == "search"

    def test_code_types(self):
        assert self.builder._get_card_type("code_execute") == "code"

    def test_file_types(self):
        assert self.builder._get_card_type("deliver_artifacts") == "file"

    def test_browser_wildcard(self):
        assert self.builder._get_card_type("browser_task") == "browser"
        assert self.builder._get_card_type("browser_navigate") == "browser"

    def test_default_fallback(self):
        assert self.builder._get_card_type("unknown_tool") == "default"
        assert self.builder._get_card_type("load_skill") == "default"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_card_builder.py -x -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write CardBuilder**

```python
# src/openakita/api/adapters/card_builder.py
"""CardBuilder: assembles step_card SSE events."""
from __future__ import annotations

from fnmatch import fnmatch


class CardBuilder:
    """Assembles step_card event dicts with card_type inference."""

    CARD_TYPE_MAP: dict[str, str] = {
        "web_search": "search",
        "news_search": "search",
        "search_*": "search",
        "code_execute": "code",
        "python_execute": "code",
        "shell_execute": "code",
        "generate_report": "file",
        "deliver_artifacts": "file",
        "export_*": "file",
        "analyze_data": "analysis",
        "chart_*": "analysis",
        "browser_*": "browser",
        "navigate_*": "browser",
    }

    def build_step_card(
        self,
        step_id: str,
        title: str,
        status: str,
        source_type: str,
        tool_name: str,
        plan_step_index: int | None = None,
        agent_id: str = "main",
        duration: float | None = None,
        input_data: dict | None = None,
        output_data: str | None = None,
        absorbed_calls: list[dict] | None = None,
    ) -> dict:
        """Assemble a complete step_card event."""
        return {
            "type": "step_card",
            "step_id": step_id,
            "title": title,
            "status": status,
            "source_type": source_type,
            "card_type": self._get_card_type(tool_name),
            "duration": duration,
            "plan_step_index": plan_step_index,
            "agent_id": agent_id,
            "input": input_data,
            "output": output_data,
            "absorbed_calls": absorbed_calls or [],
        }

    def _get_card_type(self, tool_name: str) -> str:
        """Infer card_type from tool_name using exact + wildcard matching."""
        if tool_name in self.CARD_TYPE_MAP:
            return self.CARD_TYPE_MAP[tool_name]
        for pattern, card_type in self.CARD_TYPE_MAP.items():
            if "*" in pattern and fnmatch(tool_name, pattern):
                return card_type
        return "default"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_card_builder.py -x -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openakita/api/adapters/card_builder.py tests/unit/test_card_builder.py
git commit -m "feat(seecrab): add CardBuilder for step card assembly"
```

---

### Task 4: TimerTracker — Timing Collection

**Files:**
- Create: `src/openakita/api/adapters/timer_tracker.py`
- Test: `tests/unit/test_timer_tracker.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_timer_tracker.py
"""Tests for TimerTracker — TTFT/Total/Step Duration collection."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from openakita.api.adapters.timer_tracker import TimerTracker


class TestStart:
    def test_start_creates_timer(self):
        tt = TimerTracker()
        tt.start("reply_1")
        assert tt.reply_timer is not None
        assert tt.reply_timer.reply_id == "reply_1"
        assert tt.ttft_triggered is False


class TestCheckTTFT:
    def test_first_call_triggers(self):
        tt = TimerTracker()
        tt.start("r1")
        event = tt.check_ttft()
        assert event is not None
        assert event["type"] == "timer_update"
        assert event["phase"] == "ttft"
        assert event["state"] == "done"
        assert event["value"] is not None
        assert event["value"] >= 0
        assert tt.ttft_triggered is True

    def test_second_call_returns_none(self):
        tt = TimerTracker()
        tt.start("r1")
        tt.check_ttft()
        assert tt.check_ttft() is None


class TestStepTiming:
    def test_start_and_end_step(self):
        tt = TimerTracker()
        tt.start("r1")
        tt.start_step("s1")
        assert "s1" in tt.reply_timer.step_timers
        duration = tt.end_step("s1")
        assert duration >= 0
        assert tt.reply_timer.step_timers["s1"].t_end is not None


class TestMakeEvent:
    def test_running_event_no_value(self):
        tt = TimerTracker()
        tt.start("r1")
        event = tt.make_event("total", "running")
        assert event["type"] == "timer_update"
        assert event["reply_id"] == "r1"
        assert event["phase"] == "total"
        assert event["state"] == "running"
        assert event["value"] is None

    def test_done_total_has_value(self):
        tt = TimerTracker()
        tt.start("r1")
        event = tt.make_event("total", "done")
        assert event["state"] == "done"
        assert event["value"] is not None
        assert event["value"] >= 0

    def test_cancelled_event(self):
        tt = TimerTracker()
        tt.start("r1")
        event = tt.make_event("total", "cancelled")
        assert event["state"] == "cancelled"
        assert event["value"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_timer_tracker.py -x -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write TimerTracker**

```python
# src/openakita/api/adapters/timer_tracker.py
"""TimerTracker: TTFT / Total / Step Duration collection."""
from __future__ import annotations

import time

from .seecrab_models import ReplyTimer, StepTimer


class TimerTracker:
    """Collects timing data and emits timer_update events."""

    def __init__(self):
        self.reply_timer: ReplyTimer | None = None
        self.ttft_triggered: bool = False

    def start(self, reply_id: str) -> None:
        """Start timing for a new reply."""
        self.reply_timer = ReplyTimer(
            reply_id=reply_id, t_request=time.monotonic()
        )
        self.ttft_triggered = False

    def check_ttft(self) -> dict | None:
        """Check if this is the first token. Returns timer_update event or None."""
        if self.ttft_triggered or self.reply_timer is None:
            return None
        self.ttft_triggered = True
        self.reply_timer.t_first_token = time.monotonic()
        return self.make_event("ttft", "done")

    def start_step(self, step_id: str) -> None:
        """Record step start time."""
        if self.reply_timer is None:
            return
        self.reply_timer.step_timers[step_id] = StepTimer(
            step_id=step_id, t_start=time.monotonic()
        )

    def end_step(self, step_id: str) -> float:
        """Record step end time, return duration in seconds (1 decimal)."""
        if self.reply_timer is None:
            return 0.0
        timer = self.reply_timer.step_timers.get(step_id)
        if timer is None:
            return 0.0
        timer.t_end = time.monotonic()
        return round(timer.t_end - timer.t_start, 1)

    def make_event(self, phase: str, state: str) -> dict:
        """Build a timer_update event dict."""
        if self.reply_timer is None:
            return {"type": "timer_update", "phase": phase, "state": state}

        value = None
        if state in ("done", "cancelled"):
            now = time.monotonic()
            if phase == "ttft" and self.reply_timer.t_first_token is not None:
                value = round(
                    self.reply_timer.t_first_token - self.reply_timer.t_request, 1
                )
            elif phase == "total":
                self.reply_timer.t_done = now
                value = round(now - self.reply_timer.t_request, 1)

        return {
            "type": "timer_update",
            "reply_id": self.reply_timer.reply_id,
            "phase": phase,
            "state": state,
            "value": value,
            "server_ts": time.time(),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_timer_tracker.py -x -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openakita/api/adapters/timer_tracker.py tests/unit/test_timer_tracker.py
git commit -m "feat(seecrab): add TimerTracker for TTFT/Total/Step timing"
```

---

### Task 5: TitleGenerator — LLM Title Generation + Humanize

**Files:**
- Create: `src/openakita/api/adapters/title_generator.py`
- Test: `tests/unit/test_title_generator.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_title_generator.py
"""Tests for TitleGenerator — LLM title generation + humanize mapping."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.api.adapters.title_generator import TitleGenerator


class TestHumanizeToolTitle:
    def setup_method(self):
        self.gen = TitleGenerator(brain=None, user_messages=[])

    def test_web_search(self):
        title = self.gen.humanize_tool_title("web_search", {"query": "Karpathy 2026"})
        assert "Karpathy 2026" in title

    def test_news_search(self):
        title = self.gen.humanize_tool_title("news_search", {"query": "AI"})
        assert "AI" in title

    def test_browser_task(self):
        title = self.gen.humanize_tool_title("browser_task", {})
        assert title  # non-empty

    def test_deliver_artifacts_with_filename(self):
        title = self.gen.humanize_tool_title("deliver_artifacts", {"filename": "report.pdf"})
        assert "report.pdf" in title

    def test_unknown_tool_fallback(self):
        title = self.gen.humanize_tool_title("unknown_tool", {})
        assert title  # should return a fallback


@pytest.mark.asyncio
class TestGenerateSkillTitle:
    async def test_success(self):
        brain = MagicMock()
        resp = MagicMock()
        resp.content = "分析最新 AI 趋势"
        brain.think_lightweight = AsyncMock(return_value=resp)

        gen = TitleGenerator(brain=brain, user_messages=["搜索 AI 趋势"])
        title = await gen.generate_skill_title({
            "name": "web_researcher",
            "description": "研究网络内容",
            "category": "research",
        })
        assert title == "分析最新 AI 趋势"
        brain.think_lightweight.assert_called_once()

    async def test_timeout_fallback(self):
        brain = MagicMock()
        brain.think_lightweight = AsyncMock(side_effect=asyncio.TimeoutError)

        gen = TitleGenerator(brain=brain, user_messages=[])
        title = await gen.generate_skill_title({
            "name": "web_researcher",
            "description": "研究网络内容并生成报告",
        })
        assert "web_researcher" in title

    async def test_empty_response_fallback(self):
        brain = MagicMock()
        resp = MagicMock()
        resp.content = ""
        brain.think_lightweight = AsyncMock(return_value=resp)

        gen = TitleGenerator(brain=brain, user_messages=[])
        title = await gen.generate_skill_title({"name": "test_skill", "description": "test"})
        assert "test_skill" in title

    async def test_brain_none_fallback(self):
        gen = TitleGenerator(brain=None, user_messages=[])
        title = await gen.generate_skill_title({"name": "my_skill", "description": "desc"})
        assert "my_skill" in title


@pytest.mark.asyncio
class TestGenerateMCPTitle:
    async def test_success(self):
        brain = MagicMock()
        resp = MagicMock()
        resp.content = "查询 GitHub 仓库"
        brain.think_lightweight = AsyncMock(return_value=resp)

        gen = TitleGenerator(brain=brain, user_messages=["查一下仓库"])
        title = await gen.generate_mcp_title(
            server_meta={"name": "github", "description": "GitHub API"},
            tool_meta={"name": "search_repos", "description": "Search repos"},
        )
        assert title == "查询 GitHub 仓库"

    async def test_fallback(self):
        brain = MagicMock()
        brain.think_lightweight = AsyncMock(side_effect=Exception("fail"))

        gen = TitleGenerator(brain=brain, user_messages=[])
        title = await gen.generate_mcp_title(
            server_meta={"name": "github"},
            tool_meta={"name": "search"},
        )
        assert "github" in title
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_title_generator.py -x -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write TitleGenerator**

```python
# src/openakita/api/adapters/title_generator.py
"""TitleGenerator: LLM-powered title generation + humanize fallback."""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

TITLE_TIMEOUT = 30  # seconds
MAX_CONCURRENT = 3

SKILL_TITLE_PROMPT = """根据以下信息，生成一个简短、对用户友好的步骤标题：

用户最近消息：
{recent_messages}

正在执行的技能：
- 名称：{name}
- 描述：{description}
- 分类：{category}

要求：
- 使用动词开头（如"搜索"、"分析"、"生成"、"整理"）
- 体现用户意图，而非技术操作名称
- 简洁明了，不超过 15 个字
- 使用用户的语言（中文/英文跟随用户消息）
- 只输出标题文本，不要任何额外内容"""

MCP_TITLE_PROMPT = """根据以下信息，生成一个简短、对用户友好的步骤标题：

用户最近消息：
{recent_messages}

正在调用的外部服务：
- 服务名：{server_name}
- 服务描述：{server_description}
- 工具名：{tool_name}
- 工具描述：{tool_description}

要求：
- 使用动词开头
- 体现用户意图，而非 API 名称
- 简洁明了，不超过 15 个字
- 使用用户的语言
- 只输出标题文本，不要任何额外内容"""

HUMANIZE_MAP: dict[str, object] = {
    "web_search": lambda args: f'搜索 "{args.get("query", "")}"',
    "news_search": lambda args: f'搜索新闻 "{args.get("query", "")}"',
    "browser_task": lambda _: "浏览网页获取内容",
    "generate_image": lambda _: "生成插图",
    "deliver_artifacts": lambda args: f'发送 {args.get("filename", "文件")}',
    "delegate_to_agent": lambda _: "委派专家代理处理",
    "delegate_parallel": lambda _: "并行调研多个方向",
}


class TitleGenerator:
    """Generates semantic titles for step cards."""

    def __init__(self, brain: object | None, user_messages: list[str]):
        self.brain = brain
        self.user_messages = user_messages[-5:] if user_messages else []
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    def humanize_tool_title(self, tool_name: str, args: dict) -> str:
        """Generate title for whitelisted tools using humanize map (no LLM)."""
        fn = HUMANIZE_MAP.get(tool_name)
        if fn:
            try:
                return fn(args)
            except Exception:
                pass
        return f"执行 {tool_name}"

    async def generate_skill_title(self, skill_meta: dict) -> str:
        """Generate LLM title for a Skill step card."""
        if self.brain is None:
            return self._skill_fallback(skill_meta)

        prompt = SKILL_TITLE_PROMPT.format(
            recent_messages="\n".join(self.user_messages) or "(无)",
            name=skill_meta.get("name", "unknown"),
            description=skill_meta.get("description", ""),
            category=skill_meta.get("category", ""),
        )
        return await self._call_llm(prompt, fallback=self._skill_fallback(skill_meta))

    async def generate_mcp_title(
        self, server_meta: dict, tool_meta: dict
    ) -> str:
        """Generate LLM title for an MCP step card."""
        if self.brain is None:
            return self._mcp_fallback(server_meta)

        prompt = MCP_TITLE_PROMPT.format(
            recent_messages="\n".join(self.user_messages) or "(无)",
            server_name=server_meta.get("name", "unknown"),
            server_description=server_meta.get("description", ""),
            tool_name=tool_meta.get("name", ""),
            tool_description=tool_meta.get("description", ""),
        )
        return await self._call_llm(prompt, fallback=self._mcp_fallback(server_meta))

    async def _call_llm(self, prompt: str, fallback: str) -> str:
        """Call brain.think_lightweight with timeout and fallback."""
        async with self._semaphore:
            try:
                resp = await asyncio.wait_for(
                    self.brain.think_lightweight(prompt),
                    timeout=TITLE_TIMEOUT,
                )
                title = resp.content.strip().strip('"\'')
                if not title:
                    return fallback
                return title[:30]  # safety cap
            except Exception as e:
                logger.warning(f"[TitleGenerator] LLM title failed: {e}")
                return fallback

    @staticmethod
    def _skill_fallback(meta: dict) -> str:
        name = meta.get("name", "unknown")
        desc = meta.get("description", "")
        if desc:
            return f"{name}: {desc[:15]}"
        return f"执行 {name}"

    @staticmethod
    def _mcp_fallback(server_meta: dict) -> str:
        name = server_meta.get("name", "unknown")
        return f"调用 {name} 服务"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_title_generator.py -x -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openakita/api/adapters/title_generator.py tests/unit/test_title_generator.py
git commit -m "feat(seecrab): add TitleGenerator with LLM + humanize fallback"
```

---

## Chunk 2: Backend Aggregation State Machine

### Task 6: StepAggregator — Core State Machine

**Files:**
- Create: `src/openakita/api/adapters/step_aggregator.py`
- Test: `tests/unit/test_step_aggregator.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_step_aggregator.py
"""Tests for StepAggregator — aggregation state machine."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.api.adapters.card_builder import CardBuilder
from openakita.api.adapters.seecrab_models import AggregatorState, FilterResult
from openakita.api.adapters.step_aggregator import StepAggregator
from openakita.api.adapters.title_generator import TitleGenerator
from openakita.api.adapters.timer_tracker import TimerTracker


def _make_deps():
    """Create test dependencies."""
    title_gen = TitleGenerator(brain=None, user_messages=[])
    card_builder = CardBuilder()
    timer = TimerTracker()
    timer.start("test_reply")
    return title_gen, card_builder, timer


class TestIDLEState:
    @pytest.mark.asyncio
    async def test_whitelist_creates_independent_card(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t1", FilterResult.WHITELIST
        )
        assert len(events) >= 1
        card = next(e for e in events if e["type"] == "step_card")
        assert card["status"] == "running"
        assert "test" in card["title"]
        assert agg.state == AggregatorState.IDLE
        # tool_id → (step_id, title) tracked for completion
        assert "t1" in agg._independent_cards
        step_id, title = agg._independent_cards["t1"]
        assert "test" in title

    @pytest.mark.asyncio
    async def test_independent_card_completed_on_tool_end(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t1", FilterResult.WHITELIST
        )
        events = await agg.on_tool_call_end("web_search", "t1", "results", False)
        assert len(events) == 1
        assert events[0]["status"] == "completed"
        assert events[0]["type"] == "step_card"
        assert "t1" not in agg._independent_cards  # cleaned up

    @pytest.mark.asyncio
    async def test_independent_card_failed_on_error(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t1", FilterResult.WHITELIST
        )
        events = await agg.on_tool_call_end("web_search", "t1", "error!", True)
        assert len(events) == 1
        assert events[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_skill_trigger_enters_absorb(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "load_skill", {"skill": "web_researcher"}, "t1", FilterResult.SKILL_TRIGGER
        )
        assert agg.state == AggregatorState.SKILL_ABSORB
        assert agg.pending_card is not None

    @pytest.mark.asyncio
    async def test_mcp_trigger_enters_absorb(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "github", "tool": "search"}, "t1",
            FilterResult.MCP_TRIGGER,
        )
        assert agg.state == AggregatorState.MCP_ABSORB
        assert agg.pending_card is not None
        assert agg.pending_card.mcp_server == "github"

    @pytest.mark.asyncio
    async def test_hidden_tool_no_events(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "read_file", {"path": "test"}, "t1", FilterResult.HIDDEN
        )
        assert events == []
        assert agg.state == AggregatorState.IDLE


class TestSKILL_ABSORB:
    @pytest.mark.asyncio
    async def test_absorbs_tool_calls(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        events = await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t2", FilterResult.WHITELIST
        )
        assert events == []  # absorbed
        assert len(agg.pending_card.absorbed_calls) == 1

    @pytest.mark.asyncio
    async def test_text_delta_completes_skill(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        events = await agg.on_text_delta()
        assert agg.state == AggregatorState.IDLE
        assert agg.pending_card is None
        completed = [e for e in events if e.get("type") == "step_card" and e.get("status") == "completed"]
        assert len(completed) == 1

    @pytest.mark.asyncio
    async def test_new_skill_completes_previous(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        events = await agg.on_tool_call_start(
            "load_skill", {}, "t2", FilterResult.SKILL_TRIGGER
        )
        # Previous skill completed + new skill started
        completed = [e for e in events if e.get("status") == "completed"]
        assert len(completed) == 1
        assert agg.state == AggregatorState.SKILL_ABSORB


class TestMCP_ABSORB:
    @pytest.mark.asyncio
    async def test_same_server_absorbed(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh", "tool": "t1"}, "t1",
            FilterResult.MCP_TRIGGER,
        )
        events = await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh", "tool": "t2"}, "t2",
            FilterResult.MCP_TRIGGER,
        )
        assert events == []  # absorbed
        assert agg.state == AggregatorState.MCP_ABSORB
        assert len(agg.pending_card.absorbed_calls) == 1

    @pytest.mark.asyncio
    async def test_different_server_creates_new(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh", "tool": "t1"}, "t1",
            FilterResult.MCP_TRIGGER,
        )
        events = await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "arxiv", "tool": "t2"}, "t2",
            FilterResult.MCP_TRIGGER,
        )
        completed = [e for e in events if e.get("status") == "completed"]
        assert len(completed) == 1
        assert agg.state == AggregatorState.MCP_ABSORB
        assert agg.pending_card.mcp_server == "arxiv"

    @pytest.mark.asyncio
    async def test_non_mcp_tool_breaks_absorb(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh", "tool": "t1"}, "t1",
            FilterResult.MCP_TRIGGER,
        )
        events = await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t2", FilterResult.WHITELIST
        )
        # MCP completed + whitelist card created
        completed = [e for e in events if e.get("status") == "completed"]
        assert len(completed) == 1
        assert agg.state == AggregatorState.IDLE


class TestPLAN_ABSORB:
    @pytest.mark.asyncio
    async def test_plan_created_enters_absorb(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        # Use engine format: id/description (not index/title)
        events = await agg.on_plan_created({
            "steps": [
                {"id": "step_1", "description": "步骤1", "status": "pending"},
                {"id": "step_2", "description": "步骤2", "status": "pending"},
            ]
        })
        assert agg.state == AggregatorState.PLAN_ABSORB
        checklist = next(e for e in events if e["type"] == "plan_checklist")
        assert len(checklist["steps"]) == 2
        # Verify normalization: id→index, description→title
        assert checklist["steps"][0]["index"] == 1
        assert checklist["steps"][0]["title"] == "步骤1"
        # Verify id→index mapping
        assert agg._plan_id_to_index["step_1"] == 1
        assert agg._plan_id_to_index["step_2"] == 2

    @pytest.mark.asyncio
    async def test_plan_absorbs_all_tools(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_plan_created({
            "steps": [{"id": "step_1", "description": "步骤1", "status": "pending"}]
        })
        await agg.on_plan_step_updated(1, "running")
        events = await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        assert events == []  # absorbed by plan
        events = await agg.on_tool_call_start(
            "web_search", {}, "t2", FilterResult.WHITELIST
        )
        assert events == []  # still absorbed

    @pytest.mark.asyncio
    async def test_plan_step_completed(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_plan_created({
            "steps": [
                {"id": "step_1", "description": "步骤1", "status": "pending"},
                {"id": "step_2", "description": "步骤2", "status": "pending"},
            ]
        })
        await agg.on_plan_step_updated(1, "running")
        events = await agg.on_plan_step_updated(1, "completed")
        step_cards = [e for e in events if e["type"] == "step_card"]
        assert any(c["status"] == "completed" for c in step_cards)

    @pytest.mark.asyncio
    async def test_plan_completed_returns_to_idle(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_plan_created({"steps": [{"id": "step_1", "description": "步骤1", "status": "pending"}]})
        await agg.on_plan_step_updated(1, "running")
        await agg.on_plan_step_updated(1, "completed")
        events = await agg.on_plan_completed()
        assert agg.state == AggregatorState.IDLE


class TestToolCallEnd:
    @pytest.mark.asyncio
    async def test_updates_absorbed_call_result(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        await agg.on_tool_call_start(
            "web_search", {"query": "test"}, "t2", FilterResult.WHITELIST
        )
        events = await agg.on_tool_call_end("web_search", "t2", "results", False)
        assert len(agg.pending_card.absorbed_calls) == 1
        assert agg.pending_card.absorbed_calls[0].get("result") == "results"

    @pytest.mark.asyncio
    async def test_error_flag_recorded(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        await agg.on_tool_call_start(
            "web_search", {}, "t2", FilterResult.WHITELIST
        )
        await agg.on_tool_call_end("web_search", "t2", "error!", True)
        assert agg.pending_card.absorbed_calls[0].get("is_error") is True


class TestFlush:
    @pytest.mark.asyncio
    async def test_flush_completes_skill(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "load_skill", {}, "t1", FilterResult.SKILL_TRIGGER
        )
        events = await agg.flush()
        assert agg.state == AggregatorState.IDLE
        assert any(e.get("status") == "completed" for e in events)

    @pytest.mark.asyncio
    async def test_flush_completes_mcp(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        await agg.on_tool_call_start(
            "call_mcp_tool", {"server": "gh"}, "t1", FilterResult.MCP_TRIGGER
        )
        events = await agg.flush()
        assert agg.state == AggregatorState.IDLE
        assert any(e.get("status") == "completed" for e in events)

    @pytest.mark.asyncio
    async def test_flush_idle_returns_empty(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.flush()
        assert events == []


class TestUserMentionIDLE:
    @pytest.mark.asyncio
    async def test_user_mention_creates_card(self):
        tg, cb, timer = _make_deps()
        agg = StepAggregator(title_gen=tg, card_builder=cb, timer=timer)
        events = await agg.on_tool_call_start(
            "read_file", {"path": "config.yaml"}, "t1", FilterResult.USER_MENTION
        )
        assert len(events) >= 1
        card = next(e for e in events if e["type"] == "step_card")
        assert card["status"] == "running"
        assert agg.state == AggregatorState.IDLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_step_aggregator.py -x -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write StepAggregator**

```python
# src/openakita/api/adapters/step_aggregator.py
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
                output=result[:2000] if result else None,
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
        return events

    # ── Private helpers ──

    def _handle_idle(
        self, tool_name: str, args: dict, tool_id: str, fr: FilterResult
    ) -> list[dict]:
        if fr == FilterResult.SKILL_TRIGGER:
            return self._start_skill(tool_name, args)
        if fr == FilterResult.MCP_TRIGGER:
            return self._start_mcp(tool_name, args)
        if fr in (FilterResult.WHITELIST, FilterResult.USER_MENTION):
            return self._create_independent_card(tool_name, args, tool_id)
        return []

    def _start_skill(self, tool_name: str, args: dict) -> list[dict]:
        step_id = f"skill_{uuid.uuid4().hex[:8]}"
        placeholder = "⏳"
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
        placeholder = "⏳"
        self.pending_card = PendingCard(
            step_id=step_id, title=placeholder, source_type="mcp",
            mcp_server=server,
        )
        self.state = AggregatorState.MCP_ABSORB
        self.timer.start_step(step_id)
        # Fire async LLM title generation for MCP
        server_meta = {"name": server, "description": args.get("server_description", "")}
        tool_meta = {"name": args.get("tool", ""), "description": args.get("tool_description", "")}
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
        if title == "⏳":
            title = f"执行 {self.pending_card.source_type} 操作"
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
                    return s.get("title", f"步骤 {index}")
        return f"步骤 {index}"

    def _update_plan_step_status(self, index: int, status: str) -> None:
        if self._plan_steps:
            for s in self._plan_steps:
                if s.get("index") == index:
                    s["status"] = status
                    break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_step_aggregator.py -x -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openakita/api/adapters/step_aggregator.py tests/unit/test_step_aggregator.py
git commit -m "feat(seecrab): add StepAggregator state machine"
```

---

## Chunk 3: Backend Integration — SeeCrabAdapter + Route

### Task 7: SeeCrabAdapter — Core Translation Layer

**Files:**
- Create: `src/openakita/api/adapters/seecrab_adapter.py`
- Test: `tests/unit/test_seecrab_adapter.py`

- [ ] **Step 1: Write the test file**

```python
# tests/unit/test_seecrab_adapter.py
"""Tests for SeeCrabAdapter — raw event stream → refined SSE events."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.api.adapters.seecrab_adapter import SeeCrabAdapter


async def _events_from(raw_events: list[dict], user_messages=None) -> list[dict]:
    """Helper: run adapter on a list of raw events, collect output."""
    async def gen():
        for e in raw_events:
            yield e

    adapter = SeeCrabAdapter(brain=None, user_messages=user_messages or [])
    result = []
    async for event in adapter.transform(gen(), reply_id="test_reply"):
        result.append(event)
    return result


class TestBasicFlow:
    @pytest.mark.asyncio
    async def test_empty_stream(self):
        events = await _events_from([])
        types = [e["type"] for e in events]
        assert "timer_update" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_thinking_passthrough(self):
        events = await _events_from([
            {"type": "thinking_delta", "content": "thinking..."},
            {"type": "thinking_end", "duration_ms": 500},
        ])
        thinking = [e for e in events if e["type"] == "thinking"]
        assert len(thinking) >= 1
        assert thinking[0]["content"] == "thinking..."

    @pytest.mark.asyncio
    async def test_text_delta_becomes_ai_text(self):
        events = await _events_from([
            {"type": "text_delta", "content": "Hello world"},
        ])
        ai_texts = [e for e in events if e["type"] == "ai_text"]
        assert len(ai_texts) == 1
        assert ai_texts[0]["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_ttft_triggered_on_first_token(self):
        events = await _events_from([
            {"type": "text_delta", "content": "Hi"},
        ])
        ttft_events = [e for e in events if e.get("type") == "timer_update" and e.get("phase") == "ttft"]
        # Should have running + done
        assert any(e["state"] == "done" for e in ttft_events)


class TestToolCallFlow:
    @pytest.mark.asyncio
    async def test_whitelist_tool_creates_card(self):
        events = await _events_from([
            {"type": "tool_call_start", "tool": "web_search", "args": {"query": "test"}, "id": "t1"},
            {"type": "tool_call_end", "tool": "web_search", "result": "results", "id": "t1", "is_error": False},
            {"type": "text_delta", "content": "Summary"},
        ])
        step_cards = [e for e in events if e["type"] == "step_card"]
        assert len(step_cards) >= 1

    @pytest.mark.asyncio
    async def test_hidden_tool_no_card(self):
        events = await _events_from([
            {"type": "tool_call_start", "tool": "read_file", "args": {"path": "x"}, "id": "t1"},
            {"type": "tool_call_end", "tool": "read_file", "result": "data", "id": "t1", "is_error": False},
            {"type": "text_delta", "content": "Done"},
        ])
        step_cards = [e for e in events if e["type"] == "step_card"]
        assert len(step_cards) == 0


class TestAskUser:
    @pytest.mark.asyncio
    async def test_ask_user_maps_fields(self):
        events = await _events_from([
            {
                "type": "ask_user",
                "question": "Which?",
                "options": [{"id": "a", "label": "Option A"}],
            },
        ])
        ask = next(e for e in events if e["type"] == "ask_user")
        assert ask["question"] == "Which?"
        assert ask["options"][0]["value"] == "a"
        assert ask["options"][0]["label"] == "Option A"

    @pytest.mark.asyncio
    async def test_title_update_queue_drained(self):
        """Verify title_update_queue events are yielded during transform."""
        async def raw():
            yield {"type": "tool_call_start", "tool": "load_skill",
                   "args": {"skill": "test_skill"}, "id": "t1"}
            # Give async title task a moment to enqueue
            await asyncio.sleep(0.05)
            yield {"type": "text_delta", "content": "Done"}

        adapter = SeeCrabAdapter(brain=None, user_messages=[])
        events = []
        async for e in adapter.transform(raw(), reply_id="r1"):
            events.append(e)
        # Should have at least one step_card with non-placeholder title
        # (from queue drain or from flush)
        step_cards = [e for e in events if e.get("type") == "step_card"]
        assert len(step_cards) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_seecrab_adapter.py -x -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write SeeCrabAdapter**

```python
# src/openakita/api/adapters/seecrab_adapter.py
"""SeeCrabAdapter: translates raw Agent event stream → refined SSE events."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from .card_builder import CardBuilder
from .seecrab_models import FilterResult
from .step_aggregator import StepAggregator
from .step_filter import StepFilter
from .timer_tracker import TimerTracker
from .title_generator import TitleGenerator

logger = logging.getLogger(__name__)


class SeeCrabAdapter:
    """Core translation layer: raw reason_stream events → refined SSE events."""

    def __init__(self, brain: object | None, user_messages: list[str]):
        self.step_filter = StepFilter()
        self.step_filter.set_user_messages(user_messages)
        self.timer = TimerTracker()
        self.title_gen = TitleGenerator(brain, user_messages)
        self.card_builder = CardBuilder()
        self._title_queue: asyncio.Queue[dict] = asyncio.Queue()
        self.aggregator = StepAggregator(
            title_gen=self.title_gen,
            card_builder=self.card_builder,
            timer=self.timer,
            title_update_queue=self._title_queue,
        )

    async def transform(
        self,
        raw_events: AsyncIterator[dict],
        reply_id: str,
    ) -> AsyncIterator[dict]:
        """Consume raw events + title_update_queue, yield refined events."""
        self.timer.start(reply_id)
        yield self.timer.make_event("ttft", "running")

        async for event in raw_events:
            for refined in await self._process_event(event):
                yield refined
            # Drain any pending title updates between raw events
            while not self._title_queue.empty():
                try:
                    title_event = self._title_queue.get_nowait()
                    yield title_event
                except asyncio.QueueEmpty:
                    break

        # Flush pending aggregation
        for e in await self.aggregator.flush():
            yield e

        # Drain remaining title updates after stream ends
        while not self._title_queue.empty():
            try:
                yield self._title_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Final timing
        yield self.timer.make_event("total", "done")
        yield {"type": "done"}

    async def _process_event(self, event: dict) -> list[dict]:
        """Dispatch a single raw event to handlers."""
        etype = event.get("type", "")

        if etype == "thinking_delta":
            return self._handle_thinking(event)

        if etype == "thinking_start":
            return []  # absorbed, we use delta

        if etype == "thinking_end":
            return []  # timing info only

        if etype == "text_delta":
            return await self._handle_text_delta(event)

        if etype == "tool_call_start":
            return await self._handle_tool_call_start(event)

        if etype == "tool_call_end":
            return await self._handle_tool_call_end(event)

        if etype == "plan_created":
            return await self.aggregator.on_plan_created(event.get("plan", event))

        if etype == "plan_step_updated":
            # Engine sends stepId as string (e.g. "step_1"), normalize to index
            raw_step_id = str(event.get("stepId", event.get("step_index", "")))
            if not raw_step_id:
                logger.warning("[SeeCrab] plan_step_updated with empty stepId, skipping")
                return []
            step_index = self.aggregator._plan_id_to_index.get(raw_step_id, 0)
            if step_index == 0 and "_" in raw_step_id:
                # Fallback: parse "step_N" → N
                try:
                    step_index = int(raw_step_id.split("_")[-1])
                except (ValueError, IndexError):
                    pass
            if step_index <= 0:
                logger.warning(f"[SeeCrab] plan_step_updated: unknown stepId={raw_step_id!r}, skipping")
                return []
            status = event.get("status", "")
            return await self.aggregator.on_plan_step_updated(step_index, status)

        if etype == "plan_completed":
            return await self.aggregator.on_plan_completed()

        if etype == "ask_user":
            return [self._map_ask_user(event)]

        if etype == "heartbeat":
            return [{"type": "heartbeat"}]

        if etype == "error":
            return [{"type": "error", "message": event.get("message", ""), "code": "agent_error"}]

        # Explicitly ignored event types (from engine, not relevant for SeeCrab):
        # - "done": engine done signal — adapter emits its own done
        # - "iteration_start": internal iteration counter
        # - "context_compressed": context window management
        # - "chain_text": IM-facing internal monologue
        # - "user_insert": IM gateway user injection
        # - "agent_handoff": multi-agent internal routing
        # - "tool_call_skipped": policy-denied tools
        return []

    def _handle_thinking(self, event: dict) -> list[dict]:
        events = []
        ttft = self.timer.check_ttft()
        if ttft:
            events.append(ttft)
            events.append(self.timer.make_event("total", "running"))
        events.append({
            "type": "thinking",
            "content": event.get("content", ""),
            "agent_id": "main",
        })
        return events

    async def _handle_text_delta(self, event: dict) -> list[dict]:
        events = []
        ttft = self.timer.check_ttft()
        if ttft:
            events.append(ttft)
            events.append(self.timer.make_event("total", "running"))
        # Close any active aggregation
        events += await self.aggregator.on_text_delta()
        events.append({
            "type": "ai_text",
            "content": event.get("content", ""),
            "agent_id": "main",
        })
        return events

    async def _handle_tool_call_start(self, event: dict) -> list[dict]:
        tool_name = event.get("tool", "")
        args = event.get("args", {})
        tool_id = event.get("id", "")
        fr = self.step_filter.classify(tool_name, args)
        return await self.aggregator.on_tool_call_start(tool_name, args, tool_id, fr)

    async def _handle_tool_call_end(self, event: dict) -> list[dict]:
        tool_name = event.get("tool", "")
        tool_id = event.get("id", "")
        result = event.get("result", "")
        is_error = event.get("is_error", False)
        events = await self.aggregator.on_tool_call_end(
            tool_name, tool_id, result, is_error
        )
        # Synthesize artifact from deliver_artifacts
        if tool_name == "deliver_artifacts" and not is_error:
            artifact = self._extract_artifact(result)
            if artifact:
                events.append(artifact)
        return events

    @staticmethod
    def _map_ask_user(event: dict) -> dict:
        """Map raw ask_user event (id→value)."""
        options = event.get("options", [])
        mapped = [
            {"label": o.get("label", ""), "value": o.get("id", o.get("value", ""))}
            for o in options
        ]
        return {
            "type": "ask_user",
            "ask_id": event.get("id", event.get("ask_id", "")),
            "question": event.get("question", ""),
            "options": mapped,
        }

    @staticmethod
    def _extract_artifact(result: str) -> dict | None:
        """Try to extract artifact info from deliver_artifacts result."""
        import json
        try:
            data = json.loads(result) if isinstance(result, str) else result
            receipts = data.get("receipts", []) if isinstance(data, dict) else []
            if receipts:
                r = receipts[0]
                return {
                    "type": "artifact",
                    "artifact_type": r.get("type", "file"),
                    "file_url": r.get("file_url", ""),
                    "filename": r.get("name", ""),
                    "mime_type": r.get("mime_type", ""),
                }
        except Exception:
            pass
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_seecrab_adapter.py -x -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openakita/api/adapters/seecrab_adapter.py tests/unit/test_seecrab_adapter.py
git commit -m "feat(seecrab): add SeeCrabAdapter core translation layer"
```

---

### Task 8: SeeCrab Schemas + Route

**Files:**
- Create: `src/openakita/api/schemas_seecrab.py`
- Create: `src/openakita/api/routes/seecrab.py`
- Modify: `src/openakita/api/server.py:258-281` (add route registration)
- Test: `tests/integration/test_seecrab_route.py`

- [ ] **Step 1: Write schemas**

```python
# src/openakita/api/schemas_seecrab.py
"""Pydantic schemas for SeeCrab API."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .schemas import AttachmentInfo


class SeeCrabChatRequest(BaseModel):
    """SeeCrab chat request body."""

    message: str = Field("", description="User message text")
    conversation_id: str | None = Field(None, description="Conversation ID")
    agent_profile_id: str | None = Field(None, description="Agent profile")
    endpoint: str | None = Field(None, description="LLM endpoint override")
    thinking_mode: str | None = Field(None, description="Thinking mode")
    thinking_depth: str | None = Field(None, description="Thinking depth (low/medium/high)")
    plan_mode: bool = Field(False, description="Enable Plan mode")
    attachments: list[AttachmentInfo] | None = Field(None, description="Attachments")
    client_id: str | None = Field(None, description="Client tab ID for busy-lock")


class SeeCrabAnswerRequest(BaseModel):
    """Answer to an ask_user event."""

    conversation_id: str = Field(..., description="Conversation ID")
    answer: str = Field(..., description="User answer text")
    client_id: str | None = Field(None)
```

- [ ] **Step 2: Write the test file**

```python
# tests/integration/test_seecrab_route.py
"""Integration tests for SeeCrab SSE route."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from openakita.api.routes.seecrab import router


@pytest.fixture
def mock_agent():
    agent = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield {"type": "thinking_delta", "content": "Let me think..."}
        yield {"type": "text_delta", "content": "Hello!"}

    agent.chat_with_session_stream = fake_stream
    agent._initialized = True
    agent.brain = MagicMock()
    return agent


@pytest.fixture
def app(mock_agent):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.state.agent = mock_agent
    app.state.agent_pool = None
    app.state.session_manager = None
    return app


@pytest.fixture
def client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestSeeCrabChat:
    @pytest.mark.asyncio
    async def test_sse_stream_returns_events(self, client):
        async with client:
            resp = await client.post(
                "/api/seecrab/chat",
                json={"message": "Hello", "conversation_id": "c1"},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            body = resp.text
            assert "timer_update" in body
            assert "done" in body

    @pytest.mark.asyncio
    async def test_thinking_and_text_events(self, client):
        async with client:
            resp = await client.post(
                "/api/seecrab/chat",
                json={"message": "Hi"},
            )
            lines = [l for l in resp.text.split("\n") if l.startswith("data:")]
            events = [json.loads(l[5:].strip()) for l in lines]
            types = [e["type"] for e in events]
            assert "thinking" in types
            assert "ai_text" in types
```

- [ ] **Step 3: Write the route**

```python
# src/openakita/api/routes/seecrab.py
"""SeeCrab API routes: SSE streaming chat + session management."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..schemas_seecrab import SeeCrabAnswerRequest, SeeCrabChatRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/seecrab")

# ── Busy-lock (per-conversation, same pattern as chat.py) ──

_busy_locks: dict[str, tuple[str, float]] = {}  # conv_id → (client_id, timestamp)
_busy_lock_mutex = asyncio.Lock()
_busy_thread_lock = __import__("threading").Lock()
_LOCK_TTL = 600  # seconds — consistent with chat.py BUSY_TIMEOUT_SECONDS


async def _mark_busy(conv_id: str, client_id: str) -> bool:
    """Try to acquire busy-lock. Returns True if acquired."""
    async with _busy_lock_mutex:
        _expire_stale_locks()
        if conv_id in _busy_locks:
            existing_client, _ = _busy_locks[conv_id]
            if existing_client != client_id:
                return False
        _busy_locks[conv_id] = (client_id, time.time())
        return True


def _clear_busy(conv_id: str) -> None:
    # Use thread-safe lock to support cross-loop calls (same pattern as chat.py)
    with _busy_thread_lock:
        _busy_locks.pop(conv_id, None)


def _expire_stale_locks() -> None:
    now = time.time()
    expired = [k for k, (_, ts) in _busy_locks.items() if now - ts > _LOCK_TTL]
    for k in expired:
        del _busy_locks[k]


async def _get_agent(request: Request, conversation_id: str | None, profile_id: str | None = None):
    """Get per-session agent from pool, or fallback to global agent."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None and conversation_id:
        try:
            return pool.get_or_create(conversation_id, profile_id)
        except Exception:
            pass
    return getattr(request.app.state, "agent", None)


@router.post("/chat")
async def seecrab_chat(body: SeeCrabChatRequest, request: Request):
    """SSE streaming chat via SeeCrabAdapter."""
    # Get agent from pool (per-session isolation) or fallback to global
    agent = await _get_agent(request, body.conversation_id, body.agent_profile_id)
    if agent is None:
        return JSONResponse({"error": "Agent not initialized"}, status_code=503)

    session_manager = getattr(request.app.state, "session_manager", None)
    conversation_id = body.conversation_id or f"seecrab_{uuid.uuid4().hex[:12]}"
    client_id = body.client_id or uuid.uuid4().hex[:8]

    # Busy-lock check
    if not await _mark_busy(conversation_id, client_id):
        return JSONResponse(
            {"error": "Another request is already processing this conversation"},
            status_code=409,
        )

    async def generate():
        from openakita.api.adapters.seecrab_adapter import SeeCrabAdapter

        # Disconnect watcher
        disconnect_event = asyncio.Event()

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    logger.info(f"[SeeCrab] Client disconnected: {conversation_id}")
                    if hasattr(agent, "cancel_current_task"):
                        agent.cancel_current_task("客户端断开连接", session_id=conversation_id)
                    disconnect_event.set()
                    return
                await asyncio.sleep(2)

        watcher_task = asyncio.create_task(_disconnect_watcher())
        adapter = None

        try:
            # Resolve session
            session = None
            session_messages: list[dict] = []
            user_messages: list[str] = []
            if session_manager and conversation_id:
                try:
                    session = session_manager.get_session(
                        channel="seecrab",
                        chat_id=conversation_id,
                        user_id="seecrab_user",
                        create_if_missing=True,
                    )
                    if session and body.message:
                        session.add_message("user", body.message)
                        session_messages = list(
                            session.context.messages
                        ) if hasattr(session, "context") else []
                        user_messages = [
                            m.get("content", "")
                            for m in session_messages
                            if m.get("role") == "user"
                        ][-5:]
                        session_manager.mark_dirty()
                except Exception as e:
                    logger.warning(f"[SeeCrab] Session error: {e}")

            if not user_messages and body.message:
                user_messages = [body.message]

            brain = getattr(agent, "brain", None)
            adapter = SeeCrabAdapter(brain=brain, user_messages=user_messages)
            reply_id = f"reply_{uuid.uuid4().hex[:12]}"

            raw_stream = agent.chat_with_session_stream(
                message=body.message,
                session_messages=session_messages,
                session_id=conversation_id,
                session=session,
                plan_mode=body.plan_mode,
                endpoint_override=body.endpoint,
                thinking_mode=body.thinking_mode,
                thinking_depth=body.thinking_depth,
                attachments=body.attachments,
            )

            # Dual-loop bridge if needed
            try:
                from openakita.core.engine_bridge import engine_stream, is_dual_loop
                if is_dual_loop():
                    raw_stream = engine_stream(raw_stream)
            except ImportError:
                pass

            full_reply = ""
            async for event in adapter.transform(raw_stream, reply_id=reply_id):
                if disconnect_event.is_set():
                    break
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                if event.get("type") == "ai_text":
                    full_reply += event.get("content", "")

            # Save assistant reply to session
            if session and full_reply:
                try:
                    session.add_message("assistant", full_reply)
                    if session_manager:
                        session_manager.mark_dirty()
                except Exception:
                    pass

        except Exception as e:
            logger.exception(f"[SeeCrab] Chat error: {e}")
            err = json.dumps({"type": "error", "message": str(e), "code": "internal"}, ensure_ascii=False)
            yield f"data: {err}\n\n"
            yield f'data: {{"type": "done"}}\n\n'
        finally:
            # Cleanup: flush aggregator to cancel any pending title tasks
            if adapter is not None:
                try:
                    await adapter.aggregator.flush()
                except Exception:
                    pass
            watcher_task.cancel()
            try:
                await watcher_task
            except (asyncio.CancelledError, Exception):
                pass
            _clear_busy(conversation_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
async def list_sessions(request: Request):
    """List conversation sessions."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"sessions": []})
    try:
        sessions = sm.list_sessions(channel="seecrab")
        return JSONResponse({"sessions": [
            {
                "id": s.id,
                "title": getattr(s, "title", s.id),
                "updated_at": getattr(s, "updated_at", 0),
            }
            for s in sessions
        ]})
    except Exception:
        return JSONResponse({"sessions": []})


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get session detail with message history (for SSE reconnect state recovery)."""
    sm = getattr(request.app.state, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "Session manager not available"}, status_code=503)
    try:
        session = sm.get_session(
            channel="seecrab",
            chat_id=session_id,
            user_id="seecrab_user",
            create_if_missing=False,
        )
        if session is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        messages = []
        if hasattr(session, "context") and hasattr(session.context, "messages"):
            for m in session.context.messages:
                messages.append({
                    "role": m.get("role", ""),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", 0),
                    "metadata": m.get("metadata", {}),
                })
        return JSONResponse({
            "session_id": session_id,
            "title": getattr(session, "title", session_id),
            "messages": messages,
        })
    except Exception as e:
        logger.warning(f"[SeeCrab] Get session error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/sessions")
async def create_session(request: Request):
    """Create a new conversation session."""
    session_id = f"seecrab_{uuid.uuid4().hex[:12]}"
    return JSONResponse({"session_id": session_id})


@router.post("/answer")
async def answer_ask_user(body: SeeCrabAnswerRequest, request: Request):
    """Submit answer to ask_user event.

    The Agent's ask_user mechanism works through gateway.check_interrupt(),
    which only supports IM channels. For SeeCrab (desktop/web), the answer
    should be sent as a new /api/seecrab/chat message with the same
    conversation_id. This endpoint acknowledges the answer and instructs
    the client accordingly.
    """
    return JSONResponse({
        "status": "ok",
        "conversation_id": body.conversation_id,
        "answer": body.answer,
        "hint": "Please send the answer as a new /api/seecrab/chat message with the same conversation_id",
    })
```

- [ ] **Step 4: Register route in server.py**

Add to `src/openakita/api/server.py` after line 281 (after orgs.inbox_router):

```python
    from .routes import seecrab
    app.include_router(seecrab.router, tags=["SeeCrab"])
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/integration/test_seecrab_route.py -x -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/openakita/api/schemas_seecrab.py src/openakita/api/routes/seecrab.py tests/integration/test_seecrab_route.py
git add src/openakita/api/server.py
git commit -m "feat(seecrab): add SSE route and schemas"
```

---

## Chunk 4: Frontend Foundation

### Task 9: Vue 3 Project Scaffold

**Files:**
- Create: `apps/seecrab/package.json`
- Create: `apps/seecrab/vite.config.ts`
- Create: `apps/seecrab/tsconfig.json`
- Create: `apps/seecrab/index.html`
- Create: `apps/seecrab/src/main.ts`
- Create: `apps/seecrab/src/App.vue`
- Create: `apps/seecrab/src/types/index.ts`
- Create: `apps/seecrab/src/styles/main.css`

- [ ] **Step 1: Create project directory**

```bash
mkdir -p apps/seecrab/src/{api,stores,composables,components/{layout,chat,welcome,detail},types,styles}
mkdir -p apps/seecrab/public
```

- [ ] **Step 2: Write package.json**

```json
{
  "name": "seecrab",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "vue": "^3.5.0",
    "pinia": "^2.2.0",
    "markdown-it": "^14.0.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.2.0",
    "typescript": "~5.7.0",
    "vite": "^6.1.0",
    "vue-tsc": "^2.2.0"
  }
}
```

- [ ] **Step 3: Write vite.config.ts**

```typescript
// apps/seecrab/vite.config.ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:18900',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
```

- [ ] **Step 4: Write tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "module": "ESNext",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "preserve",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src/**/*.ts", "src/**/*.tsx", "src/**/*.vue"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 5: Write types/index.ts**

```typescript
// apps/seecrab/src/types/index.ts

export type SSEEventType =
  | 'thinking' | 'plan_checklist' | 'step_card' | 'ai_text'
  | 'ask_user' | 'agent_header' | 'artifact'
  | 'timer_update' | 'heartbeat' | 'done' | 'error'

export interface SSEEvent {
  type: SSEEventType
  [key: string]: unknown
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  reply?: ReplyState
}

export interface ReplyState {
  replyId: string
  agentId: string
  agentName: string
  thinking: string
  thinkingDone: boolean
  planChecklist: PlanStep[] | null
  stepCards: StepCard[]
  summaryText: string
  timer: TimerState
  askUser: AskUserState | null
  artifacts: Artifact[]
  isDone: boolean
}

export interface StepCard {
  stepId: string
  title: string
  status: 'running' | 'completed' | 'failed'
  sourceType: 'tool' | 'skill' | 'mcp' | 'plan_step'
  cardType: 'search' | 'code' | 'file' | 'analysis' | 'browser' | 'default'
  duration: number | null
  planStepIndex: number | null
  agentId: string
  input: Record<string, unknown> | null
  output: string | null
  absorbedCalls: AbsorbedCall[]
  // SSE snake_case fields (pre-mapping)
  step_id?: string
  source_type?: string
  card_type?: string
  plan_step_index?: number | null
  agent_id?: string
  absorbed_calls?: AbsorbedCall[]
}

export interface AbsorbedCall {
  tool: string
  tool_id: string
  args: Record<string, unknown>
  duration: number | null
  result?: string
  is_error?: boolean
}

export interface TimerState {
  ttft: { state: 'idle' | 'running' | 'done' | 'cancelled'; value: number | null }
  total: { state: 'idle' | 'running' | 'done' | 'cancelled'; value: number | null }
}

export interface PlanStep {
  index: number
  title: string
  status: 'pending' | 'running' | 'completed' | 'failed'
}

export interface AskUserState {
  ask_id: string
  question: string
  options: { label: string; value: string }[]
  answered: boolean
  answer?: string
}

export interface Artifact {
  artifact_type: string
  file_url: string
  filename: string
  mime_type: string
}

export interface Session {
  id: string
  title: string
  lastMessage: string
  updatedAt: number
  messageCount: number
}
```

- [ ] **Step 6: Write tsconfig.node.json**

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 7: Write styles/main.css**

```css
/* apps/seecrab/src/styles/main.css */
:root {
  --bg-primary: #0f1117;
  --bg-secondary: #1a1d27;
  --bg-tertiary: #242836;
  --bg-hover: #2a2e3e;
  --text-primary: #e2e8f0;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --accent: #60a5fa;
  --accent-hover: #3b82f6;
  --border: #2d3348;
  --success: #4ade80;
  --error: #f87171;
  --warning: #fbbf24;
  --sidebar-width: 260px;
  --right-panel-width: 400px;
  --chat-max-width: 720px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  height: 100vh;
  overflow: hidden;
}

#app {
  display: flex;
  height: 100vh;
}

.scrollbar-thin::-webkit-scrollbar { width: 6px; }
.scrollbar-thin::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
.scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
```

- [ ] **Step 8: Write index.html, main.ts, App.vue**

```html
<!-- apps/seecrab/index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>SeeCrab - OpenAkita Agent Viewer</title>
  <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL@20..48,100..700,0..1" rel="stylesheet" />
</head>
<body>
  <div id="app"></div>
  <script type="module" src="/src/main.ts"></script>
</body>
</html>
```

```typescript
// apps/seecrab/src/main.ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './styles/main.css'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
```

```vue
<!-- apps/seecrab/src/App.vue -->
<template>
  <div class="app-layout">
    <LeftSidebar class="sidebar" />
    <ChatArea class="chat-area" />
    <Transition name="slide-right">
      <RightPanel v-if="uiStore.rightPanelOpen" class="right-panel" />
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { useUIStore } from '@/stores/ui'
import LeftSidebar from '@/components/layout/LeftSidebar.vue'
import ChatArea from '@/components/layout/ChatArea.vue'
import RightPanel from '@/components/layout/RightPanel.vue'

const uiStore = useUIStore()
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}
.sidebar { width: var(--sidebar-width); flex-shrink: 0; }
.chat-area { flex: 1; min-width: 0; }
.right-panel { width: var(--right-panel-width); flex-shrink: 0; }
.slide-right-enter-active,
.slide-right-leave-active { transition: width 0.3s ease, opacity 0.3s ease; }
.slide-right-enter-from,
.slide-right-leave-to { width: 0; opacity: 0; }
</style>
```

- [ ] **Step 9: Install dependencies and verify**

```bash
cd apps/seecrab && npm install && npm run dev
```
Expected: Dev server starts on port 5174

- [ ] **Step 10: Commit**

```bash
git add apps/seecrab/
git commit -m "feat(seecrab): Vue 3 project scaffold with types and dark theme"
```

---

### Task 10: SSE Client + Pinia Stores

**Files:**
- Create: `apps/seecrab/src/api/sse-client.ts`
- Create: `apps/seecrab/src/api/http-client.ts`
- Create: `apps/seecrab/src/stores/chat.ts`
- Create: `apps/seecrab/src/stores/session.ts`
- Create: `apps/seecrab/src/stores/ui.ts`

- [ ] **Step 1: Write SSE client**

Implement `SSEClient` class using `fetch` + `ReadableStream` for POST-based SSE.

```typescript
// apps/seecrab/src/api/sse-client.ts
import { useChatStore } from '@/stores/chat'

export class SSEClient {
  private abortController: AbortController | null = null
  private reconnectAttempts = 0
  private maxReconnectDelay = 30_000

  async sendMessage(message: string, conversationId?: string): Promise<void> {
    this.abort()
    this.abortController = new AbortController()
    const store = useChatStore()

    try {
      const resp = await fetch('/api/seecrab/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, conversation_id: conversationId }),
        signal: this.abortController.signal,
      })

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
      }

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        // SSE events are delimited by \n\n
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (!line.startsWith('data: ')) continue
            const json_str = line.slice(6).trim()
            if (!json_str) continue
            try {
              const event = JSON.parse(json_str)
              store.dispatchEvent(event)
            } catch (e) {
              console.warn('[SSE] Parse error:', e)
            }
          }
        }
      }
      this.reconnectAttempts = 0
    } catch (err: any) {
      if (err.name === 'AbortError') return
      console.error('[SSE] Connection error:', err)
      store.dispatchEvent({ type: 'error', message: err.message, code: 'connection' })
    }
  }

  abort(): void {
    this.abortController?.abort()
    this.abortController = null
  }
}

export const sseClient = new SSEClient()
```

- [ ] **Step 2: Write HTTP client**

```typescript
// apps/seecrab/src/api/http-client.ts
const BASE = '/api/seecrab'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export const httpClient = {
  listSessions: () => request<{ sessions: any[] }>('/sessions'),
  createSession: () => request<{ session_id: string }>('/sessions', { method: 'POST' }),
  submitAnswer: (conversationId: string, answer: string) =>
    request('/answer', {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, answer }),
    }),
}
```

- [ ] **Step 3: Write chat store**

```typescript
// apps/seecrab/src/stores/chat.ts
import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import type { Message, ReplyState, StepCard, PlanStep, SSEEvent } from '@/types'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const currentReply = ref<ReplyState | null>(null)
  const isStreaming = ref(false)

  function startNewReply(replyId: string) {
    currentReply.value = {
      replyId,
      agentId: 'main',
      agentName: 'OpenAkita',
      thinking: '',
      thinkingDone: false,
      planChecklist: null,
      stepCards: [],
      summaryText: '',
      timer: {
        ttft: { state: 'idle', value: null },
        total: { state: 'idle', value: null },
      },
      askUser: null,
      artifacts: [],
      isDone: false,
    }
    isStreaming.value = true
  }

  function dispatchEvent(event: SSEEvent) {
    if (!currentReply.value) {
      startNewReply(`reply_${Date.now()}`)
    }
    const reply = currentReply.value!

    switch (event.type) {
      case 'thinking':
        reply.thinking += event.content ?? ''
        break

      case 'step_card':
        _upsertStepCard(reply, event as any)
        break

      case 'ai_text':
        reply.summaryText += event.content ?? ''
        break

      case 'timer_update':
        _handleTimer(reply, event)
        break

      case 'plan_checklist':
        reply.planChecklist = (event as any).steps as PlanStep[]
        break

      case 'ask_user':
        reply.askUser = {
          ask_id: (event as any).ask_id ?? '',
          question: (event as any).question,
          options: (event as any).options,
          answered: false,
        }
        break

      case 'agent_header':
        reply.agentId = (event as any).agent_id ?? 'main'
        reply.agentName = (event as any).agent_name ?? 'Agent'
        break

      case 'artifact':
        reply.artifacts.push(event as any)
        break

      case 'done':
        reply.isDone = true
        reply.thinkingDone = true
        isStreaming.value = false
        // Finalize message
        messages.value.push({
          id: reply.replyId,
          role: 'assistant',
          content: reply.summaryText,
          timestamp: Date.now(),
          reply: { ...reply },
        })
        currentReply.value = null
        break

      case 'error':
        console.error('[Chat] Error event:', event)
        // Clean up streaming state so UI is not stuck
        reply.isDone = true
        isStreaming.value = false
        messages.value.push({
          id: reply.replyId,
          role: 'assistant',
          content: reply.summaryText || `Error: ${(event as any).message ?? 'Unknown error'}`,
          timestamp: Date.now(),
          reply: { ...reply },
        })
        currentReply.value = null
        break
    }
  }

  function _upsertStepCard(reply: ReplyState, card: StepCard) {
    const idx = reply.stepCards.findIndex(c => c.stepId === card.step_id)
    const mapped: StepCard = {
      stepId: card.step_id,
      title: card.title,
      status: card.status as StepCard['status'],
      sourceType: card.source_type as StepCard['sourceType'],
      cardType: card.card_type as StepCard['cardType'],
      duration: card.duration ?? null,
      planStepIndex: card.plan_step_index ?? null,
      agentId: card.agent_id ?? 'main',
      input: card.input ?? null,
      output: card.output ?? null,
      absorbedCalls: card.absorbed_calls ?? [],
    }
    if (idx >= 0) {
      reply.stepCards[idx] = mapped
    } else {
      reply.stepCards.push(mapped)
    }
  }

  function _handleTimer(reply: ReplyState, event: any) {
    const phase = event.phase as 'ttft' | 'total'
    if (reply.timer[phase]) {
      reply.timer[phase].state = event.state
      if (event.value != null) {
        reply.timer[phase].value = event.value
      }
    }
  }

  function addUserMessage(content: string) {
    messages.value.push({
      id: `user_${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now(),
    })
    startNewReply(`reply_${Date.now()}`)
  }

  return { messages, currentReply, isStreaming, dispatchEvent, addUserMessage, startNewReply }
})
```

- [ ] **Step 4: Write session store**

```typescript
// apps/seecrab/src/stores/session.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { httpClient } from '@/api/http-client'
import type { Session } from '@/types'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref<Session[]>([])
  const activeSessionId = ref<string | null>(null)

  async function loadSessions() {
    const { sessions: list } = await httpClient.listSessions()
    sessions.value = list
  }

  async function createSession() {
    const { session_id } = await httpClient.createSession()
    activeSessionId.value = session_id
    return session_id
  }

  function selectSession(id: string) {
    activeSessionId.value = id
  }

  return { sessions, activeSessionId, loadSessions, createSession, selectSession }
})
```

- [ ] **Step 5: Write UI store**

```typescript
// apps/seecrab/src/stores/ui.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUIStore = defineStore('ui', () => {
  const rightPanelOpen = ref(false)
  const selectedStepId = ref<string | null>(null)
  const thinkingExpanded = ref(false)

  function selectStep(stepId: string) {
    selectedStepId.value = stepId
    rightPanelOpen.value = true
  }

  function closeRightPanel() {
    rightPanelOpen.value = false
    selectedStepId.value = null
  }

  function toggleThinking() {
    thinkingExpanded.value = !thinkingExpanded.value
  }

  return { rightPanelOpen, selectedStepId, thinkingExpanded, selectStep, closeRightPanel, toggleThinking }
})
```

- [ ] **Step 6: Commit**

```bash
git add apps/seecrab/src/api/ apps/seecrab/src/stores/
git commit -m "feat(seecrab): SSE client and Pinia stores"
```

---

## Chunk 5: Frontend Components

### Task 11: Layout Components

**Files:**
- Create: `apps/seecrab/src/components/layout/LeftSidebar.vue`
- Create: `apps/seecrab/src/components/layout/ChatArea.vue`
- Create: `apps/seecrab/src/components/layout/RightPanel.vue`
- Modify: `apps/seecrab/src/App.vue` (wire up layout)

- [ ] **Step 1: Write LeftSidebar.vue**

```vue
<!-- apps/seecrab/src/components/layout/LeftSidebar.vue -->
<template>
  <aside class="left-sidebar">
    <div class="sidebar-header">
      <button class="new-chat-btn" @click="onNewChat">
        <span class="material-symbols-rounded">add</span> 新对话
      </button>
    </div>
    <div class="session-list scrollbar-thin">
      <div
        v-for="s in sessionStore.sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === sessionStore.activeSessionId }"
        @click="sessionStore.selectSession(s.id)"
      >
        <span class="session-title">{{ s.title || '新对话' }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'
import { useChatStore } from '@/stores/chat'

const sessionStore = useSessionStore()
const chatStore = useChatStore()

async function onNewChat() {
  const id = await sessionStore.createSession()
  chatStore.messages = []
}
</script>

<style scoped>
.left-sidebar {
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.sidebar-header { padding: 16px; }
.new-chat-btn {
  width: 100%;
  padding: 10px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  font-size: 14px;
}
.new-chat-btn:hover { background: var(--accent-hover); }
.session-list { flex: 1; overflow-y: auto; padding: 0 8px; }
.session-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 13px;
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-item:hover { background: var(--bg-hover); }
.session-item.active { background: var(--bg-tertiary); color: var(--text-primary); }
</style>
```

- [ ] **Step 2: Write ChatArea.vue**

```vue
<!-- apps/seecrab/src/components/layout/ChatArea.vue -->
<template>
  <main class="chat-area">
    <WelcomePage v-if="chatStore.messages.length === 0 && !chatStore.currentReply" />
    <MessageList v-else />
    <ChatInput />
  </main>
</template>

<script setup lang="ts">
import { useChatStore } from '@/stores/chat'
import WelcomePage from '@/components/welcome/WelcomePage.vue'
import MessageList from '@/components/chat/MessageList.vue'
import ChatInput from '@/components/chat/ChatInput.vue'

const chatStore = useChatStore()
</script>

<style scoped>
.chat-area {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-primary);
}
</style>
```

- [ ] **Step 3: Write RightPanel.vue**

```vue
<!-- apps/seecrab/src/components/layout/RightPanel.vue -->
<template>
  <aside class="right-panel">
    <div class="panel-header">
      <span class="panel-title">步骤详情</span>
      <button class="close-btn" @click="uiStore.closeRightPanel()">
        <span class="material-symbols-rounded">close</span>
      </button>
    </div>
    <StepDetail v-if="uiStore.selectedStepId" :step-id="uiStore.selectedStepId" />
  </aside>
</template>

<script setup lang="ts">
import { useUIStore } from '@/stores/ui'
import StepDetail from '@/components/detail/StepDetail.vue'

const uiStore = useUIStore()
</script>

<style scoped>
.right-panel {
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid var(--border);
}
.panel-title { font-weight: 600; font-size: 14px; }
.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
}
.close-btn:hover { background: var(--bg-hover); }
</style>
```

- [ ] **Step 4: Update App.vue with three-panel layout**

Flexbox layout: sidebar (260px) + chat (flex:1) + right panel (400px, conditional).

- [ ] **Step 5: Commit**

```bash
git add apps/seecrab/src/components/layout/ apps/seecrab/src/App.vue
git commit -m "feat(seecrab): layout components — sidebar, chat area, right panel"
```

---

### Task 12: Chat Components

**Files:**
- Create: `apps/seecrab/src/components/chat/MessageList.vue`
- Create: `apps/seecrab/src/components/chat/UserMessage.vue`
- Create: `apps/seecrab/src/components/chat/BotReply.vue`
- Create: `apps/seecrab/src/components/chat/ReplyHeader.vue`
- Create: `apps/seecrab/src/components/chat/ThinkingBlock.vue`
- Create: `apps/seecrab/src/components/chat/PlanChecklist.vue`
- Create: `apps/seecrab/src/components/chat/StepCardList.vue`
- Create: `apps/seecrab/src/components/chat/StepCard.vue`
- Create: `apps/seecrab/src/components/chat/SummaryOutput.vue`
- Create: `apps/seecrab/src/components/chat/AskUserBlock.vue`
- Create: `apps/seecrab/src/components/chat/ChatInput.vue`

- [ ] **Step 1: Write ChatInput.vue**

```vue
<!-- apps/seecrab/src/components/chat/ChatInput.vue -->
<template>
  <div class="chat-input-container">
    <div class="input-wrapper">
      <textarea
        ref="inputRef"
        v-model="inputText"
        placeholder="输入消息..."
        rows="1"
        @keydown.enter.exact.prevent="send"
        @input="autoResize"
      />
      <button class="send-btn" :disabled="!inputText.trim() || isStreaming" @click="send">
        <span class="material-symbols-rounded">send</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useSessionStore } from '@/stores/session'
import { sseClient } from '@/api/sse-client'

const chatStore = useChatStore()
const sessionStore = useSessionStore()
const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement>()
const isStreaming = ref(false)

function autoResize() {
  const el = inputRef.value
  if (el) { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px' }
}

async function send() {
  const msg = inputText.value.trim()
  if (!msg || isStreaming.value) return
  inputText.value = ''
  chatStore.addUserMessage(msg)
  isStreaming.value = true
  await sseClient.sendMessage(msg, sessionStore.activeSessionId ?? undefined)
  isStreaming.value = false
}

defineExpose({ prefill: (text: string) => { inputText.value = text } })
</script>

<style scoped>
.chat-input-container { padding: 16px; max-width: var(--chat-max-width); margin: 0 auto; width: 100%; }
.input-wrapper {
  display: flex; align-items: flex-end; gap: 8px;
  background: var(--bg-tertiary); border-radius: 12px; padding: 8px 12px;
  border: 1px solid var(--border);
}
textarea {
  flex: 1; background: none; border: none; color: var(--text-primary);
  font-size: 14px; resize: none; outline: none; max-height: 120px;
  font-family: inherit; line-height: 1.5;
}
.send-btn {
  background: var(--accent); border: none; color: white; border-radius: 8px;
  width: 32px; height: 32px; cursor: pointer; display: flex; align-items: center; justify-content: center;
}
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
```

- [ ] **Step 2: Write UserMessage.vue**

```vue
<!-- apps/seecrab/src/components/chat/UserMessage.vue -->
<template>
  <div class="user-message">
    <div class="message-content">{{ message.content }}</div>
  </div>
</template>

<script setup lang="ts">
import type { Message } from '@/types'
defineProps<{ message: Message }>()
</script>

<style scoped>
.user-message {
  display: flex; justify-content: flex-end; padding: 8px 0;
}
.message-content {
  background: var(--accent); color: white; border-radius: 16px 16px 4px 16px;
  padding: 10px 16px; max-width: 70%; font-size: 14px; line-height: 1.6;
  word-break: break-word;
}
</style>
```

- [ ] **Step 3: Write BotReply.vue**

```vue
<!-- apps/seecrab/src/components/chat/BotReply.vue -->
<template>
  <div class="bot-reply">
    <ReplyHeader :reply="reply" />
    <ThinkingBlock v-if="reply.thinking" :content="reply.thinking" :done="reply.thinkingDone" />
    <PlanChecklist v-if="reply.planChecklist" :steps="reply.planChecklist" />
    <StepCardList v-if="reply.stepCards.length" :cards="reply.stepCards" />
    <SummaryOutput v-if="reply.summaryText" :content="reply.summaryText" />
    <AskUserBlock v-if="reply.askUser" :ask="reply.askUser" />
  </div>
</template>

<script setup lang="ts">
import type { ReplyState } from '@/types'
import ReplyHeader from './ReplyHeader.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import PlanChecklist from './PlanChecklist.vue'
import StepCardList from './StepCardList.vue'
import SummaryOutput from './SummaryOutput.vue'
import AskUserBlock from './AskUserBlock.vue'

defineProps<{ reply: ReplyState }>()
</script>

<style scoped>
.bot-reply { padding: 12px 0; }
</style>
```

- [ ] **Step 4: Write ReplyHeader.vue**

```vue
<template>
  <div class="reply-header">
    <div class="avatar">🤖</div>
    <span class="agent-name">{{ reply.agentName }}</span>
    <span v-if="reply.timer.ttft.value != null" class="timer ttft">
      TTFT: {{ reply.timer.ttft.value }}s
    </span>
    <span v-if="reply.timer.total.state === 'running'" class="timer total pulse">
      {{ elapsed.toFixed(1) }}s
    </span>
    <span v-else-if="reply.timer.total.value != null" class="timer total">
      {{ reply.timer.total.value }}s
    </span>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import type { ReplyState } from '@/types'

const props = defineProps<{ reply: ReplyState }>()
const elapsed = ref(0)
let rafId = 0
let startTime = 0

function tick() {
  elapsed.value = (performance.now() - startTime) / 1000
  rafId = requestAnimationFrame(tick)
}

watch(() => props.reply.timer.total.state, (state) => {
  if (state === 'running' && !rafId) {
    startTime = performance.now()
    rafId = requestAnimationFrame(tick)
  }
  if (state === 'done' && rafId) {
    cancelAnimationFrame(rafId)
    rafId = 0
    if (props.reply.timer.total.value != null) {
      elapsed.value = props.reply.timer.total.value
    }
  }
}, { immediate: true })

onUnmounted(() => { if (rafId) cancelAnimationFrame(rafId) })
</script>

<style scoped>
.reply-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.avatar { font-size: 20px; }
.agent-name { font-weight: 600; font-size: 14px; }
.timer { font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.ttft { color: var(--accent); }
.pulse { animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
</style>
```

- [ ] **Step 5: Write ThinkingBlock.vue**

```vue
<template>
  <div class="thinking-block" :class="{ collapsed: !expanded }">
    <button class="toggle" @click="expanded = !expanded">
      <span class="material-symbols-rounded">{{ expanded ? 'expand_less' : 'expand_more' }}</span>
      <span class="label">{{ done ? 'Thinking' : 'Thinking...' }}</span>
    </button>
    <div v-show="expanded" class="thinking-content">{{ content }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
defineProps<{ content: string; done: boolean }>()
const expanded = ref(false)
</script>

<style scoped>
.thinking-block { margin-bottom: 8px; border-radius: 8px; background: var(--bg-tertiary); overflow: hidden; }
.toggle {
  display: flex; align-items: center; gap: 4px; width: 100%;
  padding: 8px 12px; background: none; border: none;
  color: var(--text-muted); cursor: pointer; font-size: 12px;
}
.thinking-content {
  padding: 0 12px 12px; font-size: 13px; color: var(--text-secondary);
  white-space: pre-wrap; line-height: 1.5; max-height: 200px; overflow-y: auto;
}
</style>
```

- [ ] **Step 6: Write PlanChecklist.vue**

```vue
<template>
  <div class="plan-checklist">
    <div v-for="step in steps" :key="step.index" class="plan-step" :class="step.status">
      <span class="icon material-symbols-rounded">
        {{ step.status === 'completed' ? 'check_circle' : step.status === 'running' ? 'pending' : step.status === 'failed' ? 'cancel' : 'radio_button_unchecked' }}
      </span>
      <span class="step-title">{{ step.title }}</span>
      <span v-if="step.status === 'running'" class="in-progress">(进行中)</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PlanStep } from '@/types'
defineProps<{ steps: PlanStep[] }>()
</script>

<style scoped>
.plan-checklist { margin: 8px 0; padding: 12px; background: var(--bg-tertiary); border-radius: 8px; }
.plan-step { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 13px; }
.icon { font-size: 16px; }
.completed .icon { color: var(--success); }
.running .icon { color: var(--accent); animation: pulse 1.5s infinite; }
.failed .icon { color: var(--error); }
.pending { color: var(--text-muted); }
.in-progress { color: var(--accent); font-size: 11px; }
</style>
```

- [ ] **Step 7: Write StepCard.vue**

```vue
<template>
  <div class="step-card" :class="[card.status, card.cardType]">
    <span class="status-icon material-symbols-rounded">
      {{ card.status === 'completed' ? 'check_circle' : card.status === 'failed' ? 'error' : 'pending' }}
    </span>
    <span class="card-type-icon material-symbols-rounded">{{ cardTypeIcon }}</span>
    <span class="title">{{ card.title }}</span>
    <span v-if="card.duration != null" class="duration">{{ card.duration }}s</span>
    <span class="arrow material-symbols-rounded" @click.stop="uiStore.selectStep(card.stepId)">chevron_right</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useUIStore } from '@/stores/ui'
import type { StepCard } from '@/types'

const props = defineProps<{ card: StepCard }>()
const uiStore = useUIStore()

const cardTypeIcon = computed(() => {
  const map: Record<string, string> = {
    search: 'search', code: 'code', file: 'description',
    analysis: 'analytics', browser: 'language', default: 'build',
  }
  return map[props.card.cardType] ?? 'build'
})
</script>

<style scoped>
.step-card {
  display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  background: var(--bg-tertiary); border-radius: 8px; cursor: pointer;
  margin-bottom: 4px; font-size: 13px; transition: background 0.15s;
}
.step-card:hover { background: var(--bg-hover); }
.status-icon { font-size: 16px; }
.completed .status-icon { color: var(--success); }
.running .status-icon { color: var(--accent); animation: pulse 1.5s infinite; }
.failed .status-icon { color: var(--error); }
.card-type-icon { font-size: 14px; color: var(--text-muted); }
.title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.duration { color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.arrow { color: var(--text-muted); font-size: 16px; cursor: pointer; padding: 4px; border-radius: 4px; }
.arrow:hover { background: var(--bg-hover); color: var(--accent); }
</style>
```

- [ ] **Step 8: Write StepCardList.vue**

```vue
<template>
  <div class="step-card-list">
    <StepCard v-for="card in cards" :key="card.stepId" :card="card" />
  </div>
</template>

<script setup lang="ts">
import type { StepCard as StepCardType } from '@/types'
import StepCard from './StepCard.vue'
defineProps<{ cards: StepCardType[] }>()
</script>

<style scoped>
.step-card-list { margin: 8px 0; }
</style>
```

- [ ] **Step 9: Write useMarkdown.ts composable (needed by SummaryOutput)**

```typescript
// apps/seecrab/src/composables/useMarkdown.ts
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, linkify: true, typographer: true })

export function useMarkdown() {
  function render(content: string): string {
    return md.render(content)
  }
  return { render }
}
```

- [ ] **Step 10: Write SummaryOutput.vue**

```vue
<template>
  <div class="summary-output" v-html="rendered"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{ content: string }>()
const { render } = useMarkdown()
const rendered = computed(() => render(props.content))
</script>

<style scoped>
.summary-output {
  padding: 8px 0; font-size: 14px; line-height: 1.7; color: var(--text-primary);
}
.summary-output :deep(pre) {
  background: var(--bg-tertiary); padding: 12px; border-radius: 8px;
  overflow-x: auto; font-size: 13px; margin: 8px 0;
}
.summary-output :deep(code) { font-family: 'Fira Code', monospace; }
</style>
```

- [ ] **Step 11: Write AskUserBlock.vue**

```vue
<template>
  <div class="ask-user">
    <p class="question">{{ ask.question }}</p>
    <div class="options">
      <button v-for="opt in ask.options" :key="opt.value" class="option-btn" @click="submitAnswer(opt.value)">
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'
import { httpClient } from '@/api/http-client'
import type { AskUserState } from '@/types'

defineProps<{ ask: AskUserState }>()
const sessionStore = useSessionStore()

async function submitAnswer(value: string) {
  if (sessionStore.activeSessionId) {
    await httpClient.submitAnswer(sessionStore.activeSessionId, value)
  }
}
</script>

<style scoped>
.ask-user { margin: 12px 0; padding: 16px; background: var(--bg-tertiary); border-radius: 12px; }
.question { font-size: 14px; margin-bottom: 12px; }
.options { display: flex; gap: 8px; flex-wrap: wrap; }
.option-btn {
  padding: 8px 16px; background: var(--bg-hover); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text-primary); cursor: pointer; font-size: 13px;
}
.option-btn:hover { background: var(--accent); border-color: var(--accent); }
</style>
```

- [ ] **Step 12: Write MessageList.vue**

```vue
<template>
  <div ref="listRef" class="message-list scrollbar-thin">
    <div class="messages-container">
      <template v-for="msg in chatStore.messages" :key="msg.id">
        <UserMessage v-if="msg.role === 'user'" :message="msg" />
        <BotReply v-else-if="msg.reply" :reply="msg.reply" />
      </template>
      <BotReply v-if="chatStore.currentReply" :reply="chatStore.currentReply" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import UserMessage from './UserMessage.vue'
import BotReply from './BotReply.vue'

const chatStore = useChatStore()
const listRef = ref<HTMLElement>()
</script>

<style scoped>
.message-list { flex: 1; overflow-y: auto; padding: 16px; }
.messages-container { max-width: var(--chat-max-width); margin: 0 auto; }
</style>
```

- [ ] **Step 13: Verify dev server compiles**

```bash
cd apps/seecrab && npm run dev
```
Expected: Dev server starts, no TypeScript errors

- [ ] **Step 14: Commit**

```bash
git add apps/seecrab/src/components/chat/
git commit -m "feat(seecrab): chat components — messages, steps, thinking, plan"
```

---

### Task 13: Welcome Page + Detail Panel + Timer Composable

**Files:**
- Create: `apps/seecrab/src/components/welcome/WelcomePage.vue`
- Create: `apps/seecrab/src/components/detail/StepDetail.vue`
- Create: `apps/seecrab/src/components/detail/InputViewer.vue`
- Create: `apps/seecrab/src/components/detail/OutputViewer.vue`
- Create: `apps/seecrab/src/composables/useTimer.ts`
- Create: `apps/seecrab/src/composables/useMarkdown.ts`
- Create: `apps/seecrab/src/composables/useAutoScroll.ts`

- [ ] **Step 1: Write WelcomePage.vue**

```vue
<!-- apps/seecrab/src/components/welcome/WelcomePage.vue -->
<template>
  <div class="welcome">
    <div class="welcome-inner">
      <h1 class="logo">🦀 SeeCrab</h1>
      <p class="subtitle">OpenAkita Agent 实时可视化</p>
      <div class="shortcuts">
        <button v-for="s in shortcuts" :key="s.label" class="shortcut" @click="$emit('prefill', s.prefill)">
          <span class="material-symbols-rounded">{{ s.icon }}</span>
          <span>{{ s.label }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineEmits<{ prefill: [text: string] }>()
const shortcuts = [
  { icon: 'search', label: '搜索', prefill: '帮我搜索 ' },
  { icon: 'code', label: '写代码', prefill: '帮我写 ' },
  { icon: 'description', label: '处理文档', prefill: '帮我处理文档 ' },
  { icon: 'analytics', label: '分析数据', prefill: '帮我分析 ' },
]
</script>

<style scoped>
.welcome { flex: 1; display: flex; align-items: center; justify-content: center; }
.welcome-inner { text-align: center; }
.logo { font-size: 36px; margin-bottom: 8px; }
.subtitle { color: var(--text-secondary); margin-bottom: 32px; }
.shortcuts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; max-width: 360px; }
.shortcut {
  display: flex; align-items: center; gap: 8px; padding: 12px 16px;
  background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 12px;
  color: var(--text-primary); cursor: pointer; font-size: 13px;
}
.shortcut:hover { background: var(--bg-hover); border-color: var(--accent); }
</style>
```

- [ ] **Step 2: Write useTimer.ts composable**

```typescript
// apps/seecrab/src/composables/useTimer.ts
import { ref, onUnmounted } from 'vue'

export function useTimer() {
  const displayTtft = ref<number | null>(null)
  const displayTotal = ref<number | null>(null)
  const isRunning = ref(false)
  let rafId = 0
  let startTime = 0

  function startPhase(phase: 'ttft' | 'total') {
    if (phase === 'total') {
      isRunning.value = true
      startTime = performance.now()
      tick()
    }
  }

  function endPhase(phase: 'ttft' | 'total', value: number) {
    if (phase === 'ttft') displayTtft.value = value
    if (phase === 'total') {
      displayTotal.value = value
      isRunning.value = false
      if (rafId) { cancelAnimationFrame(rafId); rafId = 0 }
    }
  }

  function tick() {
    displayTotal.value = Math.round((performance.now() - startTime) / 100) / 10
    rafId = requestAnimationFrame(tick)
  }

  onUnmounted(() => { if (rafId) cancelAnimationFrame(rafId) })

  return { displayTtft, displayTotal, isRunning, startPhase, endPhase }
}
```

- [ ] **Step 2: Write useTimer.ts composable**

```typescript
// apps/seecrab/src/composables/useAutoScroll.ts
import { ref, watch, nextTick, type Ref } from 'vue'

export function useAutoScroll(containerRef: Ref<HTMLElement | undefined>, trigger: Ref<any>) {
  const userScrolled = ref(false)

  function onScroll() {
    const el = containerRef.value
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50
    userScrolled.value = !atBottom
  }

  watch(trigger, async () => {
    if (userScrolled.value) return
    await nextTick()
    containerRef.value?.scrollTo({ top: containerRef.value.scrollHeight, behavior: 'smooth' })
  }, { deep: true })

  return { userScrolled, onScroll }
}
```

- [ ] **Step 5: Write StepDetail.vue**

```vue
<!-- apps/seecrab/src/components/detail/StepDetail.vue -->
<template>
  <div v-if="step" class="step-detail scrollbar-thin">
    <div class="detail-header">
      <span class="status-icon material-symbols-rounded" :class="step.status">
        {{ step.status === 'completed' ? 'check_circle' : step.status === 'failed' ? 'error' : 'pending' }}
      </span>
      <h3>{{ step.title }}</h3>
      <span v-if="step.duration != null" class="duration">{{ step.duration }}s</span>
    </div>
    <InputViewer v-if="step.input" :data="step.input" />
    <OutputViewer v-if="step.output" :content="step.output" />
    <div v-if="step.absorbedCalls.length" class="absorbed">
      <h4>子调用 ({{ step.absorbedCalls.length }})</h4>
      <div v-for="(call, i) in step.absorbedCalls" :key="i" class="absorbed-item">
        <span class="tool-name">{{ call.tool }}</span>
        <span v-if="call.duration" class="call-duration">{{ call.duration }}s</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '@/stores/chat'
import InputViewer from './InputViewer.vue'
import OutputViewer from './OutputViewer.vue'

const props = defineProps<{ stepId: string }>()
const chatStore = useChatStore()

const step = computed(() => {
  // Search in current reply and all messages
  if (chatStore.currentReply) {
    const found = chatStore.currentReply.stepCards.find(c => c.stepId === props.stepId)
    if (found) return found
  }
  for (const msg of chatStore.messages) {
    if (msg.reply) {
      const found = msg.reply.stepCards.find(c => c.stepId === props.stepId)
      if (found) return found
    }
  }
  return null
})
</script>

<style scoped>
.step-detail { padding: 16px; overflow-y: auto; flex: 1; }
.detail-header { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
.detail-header h3 { flex: 1; font-size: 15px; }
.completed { color: var(--success); }
.running { color: var(--accent); }
.failed { color: var(--error); }
.duration { color: var(--text-muted); font-size: 13px; }
.absorbed { margin-top: 16px; }
.absorbed h4 { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }
.absorbed-item {
  display: flex; justify-content: space-between; padding: 6px 8px;
  background: var(--bg-primary); border-radius: 4px; margin-bottom: 4px; font-size: 12px;
}
.tool-name { color: var(--text-primary); }
.call-duration { color: var(--text-muted); }
</style>
```

- [ ] **Step 6: Write InputViewer.vue + OutputViewer.vue**

```vue
<!-- apps/seecrab/src/components/detail/InputViewer.vue -->
<template>
  <div class="input-viewer">
    <h4>输入</h4>
    <pre class="json-content">{{ formatted }}</pre>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ data: Record<string, unknown> }>()
const formatted = computed(() => JSON.stringify(props.data, null, 2))
</script>

<style scoped>
.input-viewer h4 { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; }
.json-content {
  background: var(--bg-primary); padding: 12px; border-radius: 8px;
  font-size: 12px; overflow-x: auto; color: var(--text-secondary);
  white-space: pre-wrap; word-break: break-all;
}
</style>
```

```vue
<!-- apps/seecrab/src/components/detail/OutputViewer.vue -->
<template>
  <div class="output-viewer">
    <h4>输出</h4>
    <div class="output-content" v-html="rendered"></div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'
const props = defineProps<{ content: string }>()
const { render } = useMarkdown()
const rendered = computed(() => render(props.content))
</script>

<style scoped>
.output-viewer h4 { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; }
.output-content {
  background: var(--bg-primary); padding: 12px; border-radius: 8px;
  font-size: 13px; line-height: 1.6; max-height: 400px; overflow-y: auto;
}
</style>
```

- [ ] **Step 7: Verify dev server compiles**

```bash
cd apps/seecrab && npm run dev
```
Expected: Dev server starts with no errors

- [ ] **Step 8: Commit**

```bash
git add apps/seecrab/src/components/welcome/ apps/seecrab/src/components/detail/ apps/seecrab/src/composables/
git commit -m "feat(seecrab): welcome page, detail panel, timer/markdown composables"
```

---

## Chunk 6: Multi-Agent + Final Integration

### Task 14: MultiAgentAdapter (Backend)

**Files:**
- Create: `src/openakita/api/adapters/multi_agent_adapter.py`
- Test: `tests/unit/test_multi_agent_adapter.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/test_multi_agent_adapter.py
"""Tests for MultiAgentAdapter — multi-stream merging."""
from __future__ import annotations

import asyncio

import pytest

from openakita.api.adapters.multi_agent_adapter import MultiAgentAdapter


async def _fake_stream(agent_id: str, events: list[dict]):
    """Create a fake refined event stream."""
    for e in events:
        e["agent_id"] = agent_id
        yield e
        await asyncio.sleep(0.01)


class TestMergeStreams:
    @pytest.mark.asyncio
    async def test_single_agent_passthrough(self):
        adapter = MultiAgentAdapter()
        streams = {
            "agent_a": (
                {"name": "Agent A", "description": "test"},
                _fake_stream("agent_a", [
                    {"type": "thinking", "content": "..."},
                    {"type": "ai_text", "content": "hello"},
                    {"type": "done"},
                ]),
            ),
        }
        events = []
        async for e in adapter.merge_streams(streams):
            events.append(e)
        types = [e["type"] for e in events]
        assert "thinking" in types
        assert "ai_text" in types

    @pytest.mark.asyncio
    async def test_two_agents_with_headers(self):
        adapter = MultiAgentAdapter()
        streams = {
            "agent_a": (
                {"name": "Researcher", "description": "research"},
                _fake_stream("agent_a", [
                    {"type": "ai_text", "content": "research done"},
                    {"type": "done"},
                ]),
            ),
            "agent_b": (
                {"name": "Coder", "description": "code"},
                _fake_stream("agent_b", [
                    {"type": "ai_text", "content": "code done"},
                    {"type": "done"},
                ]),
            ),
        }
        events = []
        async for e in adapter.merge_streams(streams):
            events.append(e)
        headers = [e for e in events if e["type"] == "agent_header"]
        assert len(headers) >= 2
        agent_names = {h["agent_name"] for h in headers}
        assert "Researcher" in agent_names
        assert "Coder" in agent_names

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        adapter = MultiAgentAdapter()

        async def failing_stream(agent_id, events):
            yield {"type": "ai_text", "content": "before", "agent_id": agent_id}
            raise RuntimeError("agent crash")

        streams = {
            "agent_a": (
                {"name": "Fail"},
                failing_stream("agent_a", []),
            ),
            "agent_b": (
                {"name": "OK"},
                _fake_stream("agent_b", [
                    {"type": "ai_text", "content": "ok"},
                    {"type": "done"},
                ]),
            ),
        }
        events = []
        async for e in adapter.merge_streams(streams):
            events.append(e)
        # agent_b should still deliver its events
        ok_texts = [e for e in events if e.get("content") == "ok"]
        assert len(ok_texts) == 1
        # agent_a error should be surfaced
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_multi_agent_adapter.py -x -v`

- [ ] **Step 3: Write MultiAgentAdapter**

```python
# src/openakita/api/adapters/multi_agent_adapter.py
"""MultiAgentAdapter: merges multiple agent SSE streams into one."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_SENTINEL = object()


class MultiAgentAdapter:
    """Merges N agent streams into a single output stream.

    - Injects agent_header events on agent switch.
    - Error isolation: one agent failure does not kill others.
    - Preserves event ordering within each agent.
    """

    async def merge_streams(
        self,
        agent_streams: dict[str, tuple[dict, AsyncIterator[dict]]],
    ) -> AsyncIterator[dict]:
        """Merge multiple (agent_id → (meta, stream)) into one output.

        Args:
            agent_streams: {agent_id: (agent_meta, refined_event_stream)}
        """
        queue: asyncio.Queue = asyncio.Queue()
        pending = len(agent_streams)
        last_agent_id: str | None = None

        async def _feed(agent_id: str, meta: dict, stream: AsyncIterator[dict]):
            """Consume one agent stream, push events into shared queue."""
            nonlocal pending
            try:
                async for event in stream:
                    event["agent_id"] = agent_id
                    await queue.put((agent_id, meta, event))
            except Exception as e:
                logger.error(f"[MultiAgent] Agent {agent_id} error: {e}")
                await queue.put((agent_id, meta, {
                    "type": "error",
                    "message": f"Agent {meta.get('name', agent_id)} failed: {e}",
                    "code": "agent_error",
                    "agent_id": agent_id,
                }))
            finally:
                pending -= 1
                await queue.put(_SENTINEL)

        # Start all feeders
        tasks = []
        for agent_id, (meta, stream) in agent_streams.items():
            tasks.append(asyncio.create_task(_feed(agent_id, meta, stream)))

        # Emit merged events
        done_count = 0
        while done_count < len(agent_streams):
            item = await queue.get()
            if item is _SENTINEL:
                done_count += 1
                continue
            agent_id, meta, event = item

            # Inject agent_header on agent switch
            if agent_id != last_agent_id:
                yield {
                    "type": "agent_header",
                    "agent_id": agent_id,
                    "agent_name": meta.get("name", agent_id),
                    "agent_description": meta.get("description", ""),
                }
                last_agent_id = agent_id

            # Skip per-agent done events (we emit a global done)
            if event.get("type") == "done":
                continue

            yield event

        # Cleanup
        for t in tasks:
            if not t.done():
                t.cancel()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_multi_agent_adapter.py -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/openakita/api/adapters/multi_agent_adapter.py tests/unit/test_multi_agent_adapter.py
git commit -m "feat(seecrab): add MultiAgentAdapter for stream merging"
```

---

### Task 15: Static File Mounting + Build Integration

**Files:**
- Modify: `src/openakita/api/server.py` (add SeeCrab static mount)

- [ ] **Step 1: Add static file mount for SeeCrab**

In `server.py`, add the `_find_seecrab_dist()` function near `_find_web_dist()`, then mount after existing web mount:

```python
def _find_seecrab_dist() -> Path | None:
    """Locate SeeCrab frontend dist directory."""
    # Check installed package path
    pkg_path = Path(__file__).parent / "seecrab"
    if pkg_path.is_dir() and (pkg_path / "index.html").exists():
        return pkg_path
    # Check development path
    dev_path = Path(__file__).parent.parent.parent.parent / "apps" / "seecrab" / "dist"
    if dev_path.is_dir() and (dev_path / "index.html").exists():
        return dev_path
    return None
```

Then after existing web frontend mount (after `_mount_web_frontend(app)` call):

```python
# Mount SeeCrab frontend
seecrab_dist = _find_seecrab_dist()
if seecrab_dist:
    app.mount("/seecrab", StaticFiles(directory=str(seecrab_dist), html=True), name="seecrab")
```

- [ ] **Step 2: Build frontend**

```bash
cd apps/seecrab && npm run build
```

- [ ] **Step 3: Verify end-to-end**

Start backend (`python -m openakita serve`), open `http://localhost:18900/seecrab/`.

- [ ] **Step 4: Commit**

```bash
git add src/openakita/api/server.py
git commit -m "feat(seecrab): mount frontend static files"
```

---

### Task 16: Run Full Test Suite

- [ ] **Step 1: Run all SeeCrab backend tests**

```bash
pytest tests/unit/test_seecrab_models.py tests/unit/test_step_filter.py tests/unit/test_card_builder.py tests/unit/test_timer_tracker.py tests/unit/test_title_generator.py tests/unit/test_step_aggregator.py tests/unit/test_seecrab_adapter.py tests/integration/test_seecrab_route.py tests/unit/test_multi_agent_adapter.py -v
```
Expected: All PASS

- [ ] **Step 2: Run ruff lint**

```bash
ruff check src/openakita/api/adapters/ src/openakita/api/routes/seecrab.py src/openakita/api/schemas_seecrab.py
```
Expected: No errors

- [ ] **Step 3: Run existing test suite to verify no regressions**

```bash
pytest tests/unit/ -x -v -k "not TestVectorStore"
```
Expected: All existing tests still PASS

- [ ] **Step 4: Verify — no additional commit needed**

All code was committed in prior tasks. This task is purely verification.
If any lint fixes were needed, stage and commit:
```bash
git add -u && git commit -m "fix(seecrab): lint fixes from final verification"
```
