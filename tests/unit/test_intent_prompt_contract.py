import inspect
from types import SimpleNamespace

from openakita.agent.core import Agent
from openakita.core._agent_legacy import (
    _apply_previous_answer_replay_hint,
    _looks_like_previous_answer_replay_request,
    _resolve_force_tool_policy,
)
from openakita.core._brain_legacy import (
    Brain,
    Response,
    _classify_compiler_access_error,
    _compiler_configuration_fallback_reason,
    _sanitize_compiler_error,
)
from openakita.core.intent_analyzer import (
    INTENT_ANALYZER_MAX_TOKENS,
    INTENT_ANALYZER_SYSTEM,
    CapabilityScope,
    IntentAnalyzer,
    IntentResult,
    IntentType,
    MemoryScope,
    PromptDepth,
    _make_default,
    _parse_intent_output,
)
from openakita.llm.types import (
    LOCAL_ENDPOINT_DEFAULT_CONTEXT_WINDOW,
    EndpointConfig,
)
from openakita.prompt.builder import PromptMode, PromptProfile, build_system_prompt
from openakita.runtime.llm import CompilerCircuitBreaker


class _StaticCompilerBrain:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def compiler_think(self, *args, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=self.content)


def test_intent_analyzer_prompt_is_compact_and_sparse():
    assert len(INTENT_ANALYZER_SYSTEM) < 1800
    assert "每次必须输出" in INTENT_ANALYZER_SYSTEM
    assert "仅当值为 true/broad" in INTENT_ANALYZER_SYSTEM
    assert "risk_level_hint:" not in INTENT_ANALYZER_SYSTEM


async def test_intent_analyzer_uses_bounded_output_budget():
    class _CapturingBrain:
        def __init__(self):
            self.kwargs = {}

        async def compiler_think(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                content=(
                    "intent: task\n"
                    "task_type: action\n"
                    "goal: inspect current logs\n"
                    "tool_hints: [File System]\n"
                    "memory_keywords: [logs]\n"
                    "capability_scope: [files, code]\n"
                    "evidence_required: true"
                )
            )

    brain = _CapturingBrain()
    result = await IntentAnalyzer(brain).analyze("检查当前后端日志")

    assert result.intent == IntentType.TASK
    assert brain.kwargs["max_tokens"] == INTENT_ANALYZER_MAX_TOKENS == 384


def test_sparse_compound_output_derives_default_routing_fields():
    result = _parse_intent_output(
        """intent: task
task_type: compound
goal: query weather and write query.py
tool_hints: [Web Search, File System]
memory_keywords: [福州, 明天天气, query.py]
capability_scope: [web, files, code]
evidence_required: true
scope: broad""",
        "帮我查福州明天天气并写 query.py",
    )

    assert result.requires_tools is True
    assert result.force_tool is True
    assert result.requires_project_context is True
    assert result.prompt_depth == PromptDepth.STANDARD
    assert result.memory_scope == MemoryScope.RELEVANT
    assert result.todo_required is True


class _FakeToolCatalog:
    def __init__(self):
        self.deferred_tools: set[str] | None = None

    def get_tool_groups(self):
        return {}

    def set_deferred_tools(self, names):
        self.deferred_tools = set(names)


async def test_greeting_uses_llm_intent_analysis():
    brain = _StaticCompilerBrain("intent: chat\ntask_type: other\ngoal: respond to greeting")

    result = await IntentAnalyzer(brain).analyze("你好")

    assert brain.calls == 1
    assert result.intent == IntentType.CHAT
    assert result.prompt_depth == PromptDepth.MINIMAL
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is False


def test_session_preparation_has_no_sub_agent_intent_shortcut():
    source = inspect.getsource(Agent._prepare_session_context)

    assert "self._intent_analyzer.analyze(" in source
    assert "skipping IntentAnalyzer" not in source
    assert "if self._is_sub_agent_call" not in source


async def test_intent_analyzer_preserves_compiler_fallback_diagnostics():
    class _FallbackBrain:
        async def compiler_think(self, *args, **kwargs):
            return SimpleNamespace(
                content="intent: query\ngoal: explain tuples",
                compiler_source="main_fallback",
                compiler_fallback_reason="all_disabled",
                compiler_fallback_detail="提示词编译模型全部被禁用",
            )

    result = await IntentAnalyzer(_FallbackBrain()).analyze("福州明天会下雨吗")

    assert result.compiler_source == "main_fallback"
    assert result.compiler_fallback_reason == "all_disabled"
    assert result.compiler_fallback_detail == "提示词编译模型全部被禁用"


def test_slow_compiler_fallback_builds_one_shot_actionable_hint():
    agent = Agent.__new__(Agent)
    agent._compiler_fallback_hint_emitted = False
    agent._last_intent_analysis_duration_ms = 6123.4
    agent._current_intent = IntentResult(
        intent=IntentType.TASK,
        compiler_source="main_fallback",
        compiler_fallback_reason="network_unreachable",
        compiler_fallback_detail="All endpoints failed: compiler-a connection timed out",
    )

    hint = agent._build_slow_compiler_hint("session-1")

    assert hint is not None
    assert hint["type"] == "config_hint"
    assert hint["error_code"] == "compiler_unavailable"
    assert hint["reason_code"] == "network_unreachable"
    assert hint["duration_ms"] == 6123.4
    assert "网络访问失败" in hint["message"]
    assert "connection timed out" in hint["message"]
    assert hint["actions"][0]["section"] == "llm"
    assert hint["actions"][0]["anchor"] == "prompt-compiler"

    agent._compiler_fallback_hint_emitted = True
    assert agent._build_slow_compiler_hint("session-1") is None


def test_compiler_hint_requires_slow_main_fallback():
    agent = Agent.__new__(Agent)
    agent._compiler_fallback_hint_emitted = False
    agent._current_intent = IntentResult(
        intent=IntentType.QUERY,
        compiler_source="main_fallback",
        compiler_fallback_reason="not_configured",
    )
    agent._last_intent_analysis_duration_ms = 5000
    assert agent._build_slow_compiler_hint("session-1") is None

    agent._last_intent_analysis_duration_ms = 6000
    agent._current_intent.compiler_source = "compiler"
    assert agent._build_slow_compiler_hint("session-1") is None


def test_compiler_configuration_reason_distinguishes_missing_and_disabled(tmp_path, monkeypatch):
    config_path = tmp_path / "llm_endpoints.json"
    monkeypatch.setattr("openakita.core._brain_legacy.get_default_config_path", lambda: config_path)

    config_path.write_text('{"compiler_endpoints": []}', encoding="utf-8")
    assert _compiler_configuration_fallback_reason()[0] == "not_configured"

    config_path.write_text(
        '{"compiler_endpoints": [{"name": "compiler-a", "enabled": false}]}',
        encoding="utf-8",
    )
    reason, detail = _compiler_configuration_fallback_reason()
    assert reason == "all_disabled"
    assert "全部被禁用" in detail


def test_compiler_access_error_classification_and_redaction():
    assert _classify_compiler_access_error("401 invalid API key") == "authentication_failed"
    assert _classify_compiler_access_error("connection timed out") == "network_unreachable"
    assert _classify_compiler_access_error("429 rate limit exceeded") == "rate_limited"
    assert _classify_compiler_access_error("404 model_not_found") == "model_unavailable"

    sanitized = _sanitize_compiler_error("api_key=sk-secret-value connection failed")
    assert "sk-secret-value" not in sanitized


async def test_compiler_think_reports_actual_endpoint_failure_before_main_fallback():
    class _FailingCompilerClient:
        async def chat(self, **_kwargs):
            raise TimeoutError("compiler-a connection timed out with api_key=sk-secret")

    class _MainClient:
        async def chat(self, **_kwargs):
            return object()

    brain = Brain.__new__(Brain)
    brain._compiler_client = _FailingCompilerClient()
    brain._llm_client = _MainClient()
    brain._compiler_breaker = CompilerCircuitBreaker()
    brain._compiler_last_error = ""
    brain._record_usage = lambda _response: None
    brain._dump_llm_request = lambda *_args, **_kwargs: "request-1"
    brain._dump_llm_response = lambda *_args, **_kwargs: None
    brain._llm_response_to_response = lambda _response: Response(content="intent: query")

    result = await brain.compiler_think("福州明天会下雨吗")

    assert result.compiler_source == "main_fallback"
    assert result.compiler_fallback_reason == "network_unreachable"
    assert "compiler-a connection timed out" in result.compiler_fallback_detail
    assert "sk-secret" not in result.compiler_fallback_detail


async def test_desktop_screenshot_request_uses_structured_intent_analysis():
    brain = _StaticCompilerBrain(
        """intent: task
task_type: action
goal: capture the desktop
tool_hints: [Desktop]
capability_scope: [desktop]
prompt_depth: standard
memory_scope: none
requires_tools: true
evidence_required: true
requires_project_context: false
destructive: false
scope: narrow
suggest_plan: false"""
    )

    result = await IntentAnalyzer(brain).analyze("帮我把桌面截图发我")

    assert brain.calls == 1
    assert result.intent == IntentType.TASK
    assert result.force_tool is True
    assert result.requires_tools is True
    assert result.tool_hints == ["Desktop"]
    assert result.requires_project_context is False


async def test_compound_web_and_file_request_keeps_structured_analysis():
    brain = _StaticCompilerBrain(
        """intent: task
task_type: compound
goal: query the weather and create query.py
tool_hints: [Web Search, File System]
capability_scope: [web, files]
prompt_depth: standard
memory_scope: relevant
catalog_scope: [tools]
requires_tools: true
evidence_required: true
requires_project_context: true
destructive: false
scope: narrow
suggest_plan: false"""
    )

    result = await IntentAnalyzer(brain).analyze(
        "帮我查一下福州明天的温度，并且把查询温度的代码写成query.py"
    )

    assert brain.calls == 1
    assert result.intent == IntentType.TASK
    assert result.task_type == "compound"
    assert result.tool_hints == ["Web Search", "File System"]
    assert result.capability_scope == [CapabilityScope.WEB, CapabilityScope.FILES]
    assert result.requires_project_context is True


async def test_one_sentence_explanation_skips_tools_without_blocking_model_answer():
    brain = _StaticCompilerBrain("intent: query\ntask_type: question\ngoal: explain Docker briefly")
    result = await IntentAnalyzer(brain).analyze("一句话解释 Docker")

    assert brain.calls == 1
    assert result.intent == IntentType.QUERY
    assert result.requires_tools is False
    assert result.force_tool is False


def test_chat_prompt_strategy_uses_lightweight_consumer_profile():
    agent = Agent.__new__(Agent)
    intent = IntentResult(
        intent=IntentType.CHAT,
        prompt_depth=PromptDepth.FAST,
        memory_scope=MemoryScope.PINNED_ONLY,
        requires_tools=False,
    )

    strategy = agent._resolve_prompt_strategy(
        intent,
        session_type="cli",
        mode="agent",
    )

    assert strategy.profile == PromptProfile.CONSUMER_CHAT
    assert strategy.prompt_mode == PromptMode.MINIMAL
    assert strategy.memory_scope == MemoryScope.PINNED_ONLY
    assert strategy.catalog_scope == []
    assert strategy.include_project_guidelines is False
    assert strategy.include_runtime_env_policy is False
    assert strategy.include_multi_agent is False


def test_prompt_strategy_uses_structured_intent_not_user_text():
    agent = Agent.__new__(Agent)
    agent._is_sub_agent_call = False
    query = IntentResult(
        intent=IntentType.QUERY,
        prompt_depth=PromptDepth.MINIMAL,
        memory_scope=MemoryScope.PINNED_ONLY,
        requires_tools=False,
    )

    first = agent._resolve_prompt_strategy(query, session_type="cli", mode="agent")
    query.task_definition = "contains words that could look like file or shell operations"
    query.raw_output = "arbitrary analyzer explanation"
    second = agent._resolve_prompt_strategy(query, session_type="cli", mode="agent")

    assert first == second


def test_tool_backed_query_keeps_structured_catalog_scope():
    agent = Agent.__new__(Agent)
    agent._is_sub_agent_call = False
    intent = IntentResult(
        intent=IntentType.QUERY,
        prompt_depth=PromptDepth.MINIMAL,
        memory_scope=MemoryScope.RELEVANT,
        requires_tools=True,
        tool_hints=["Web Search"],
        catalog_scope=["skills", "mcp"],
    )

    strategy = agent._resolve_prompt_strategy(intent, session_type="cli", mode="agent")

    assert strategy.catalog_scope == ["skills", "mcp"]
    assert strategy.include_runtime_env_policy is True
    assert strategy.include_multi_agent is False


def test_full_im_task_keeps_multi_agent_context():
    agent = Agent.__new__(Agent)
    agent._is_sub_agent_call = False
    intent = IntentResult(
        intent=IntentType.TASK,
        prompt_depth=PromptDepth.STANDARD,
        requires_tools=True,
    )

    strategy = agent._resolve_prompt_strategy(intent, session_type="im", mode="agent")

    assert strategy.profile == PromptProfile.IM_ASSISTANT
    assert strategy.prompt_mode == PromptMode.FULL
    assert strategy.include_multi_agent is True


def test_minimal_pinned_only_prompt_still_includes_light_memory(tmp_path):
    prompt = build_system_prompt(
        identity_dir=tmp_path,
        tools_enabled=False,
        memory_manager=object(),
        task_description="记住我的偏好",
        prompt_mode=PromptMode.MINIMAL,
        prompt_profile=PromptProfile.CONSUMER_CHAT,
        memory_scope=MemoryScope.PINNED_ONLY,
    )

    assert "## 你的记忆系统" in prompt
    assert "## 核心记忆" not in prompt


def test_minimal_prompt_preserves_working_facts(tmp_path):
    prompt = build_system_prompt(
        identity_dir=tmp_path,
        tools_enabled=False,
        session_context={
            "working_facts": {
                "temporary_name": {"value": "alpha", "source_turn": 3},
            }
        },
        prompt_mode=PromptMode.MINIMAL,
        prompt_profile=PromptProfile.CONSUMER_CHAT,
        memory_scope=MemoryScope.PINNED_ONLY,
    )

    assert "## Session Working Facts" in prompt
    assert "temporary_name: alpha" in prompt


def test_minimal_consumer_prompt_uses_compact_nonduplicated_rules(tmp_path):
    prompt = build_system_prompt(
        identity_dir=tmp_path,
        tools_enabled=False,
        prompt_mode=PromptMode.MINIMAL,
        prompt_profile=PromptProfile.CONSUMER_CHAT,
        memory_scope=MemoryScope.PINNED_ONLY,
    )

    assert "## 安全约束" in prompt
    assert "## 运行环境" in prompt
    assert "当前时间:" in prompt
    assert "## 任务管理" not in prompt
    assert "### Python 环境" not in prompt
    assert "### 工具执行域" not in prompt
    assert "### 每轮自检" not in prompt


def test_main_chat_stable_mode_keeps_small_direct_toolset_across_intents():
    """Stable mode exposes the explicit core and defers other schemas."""

    agent = Agent.__new__(Agent)
    agent._tools = [
        {"name": "read_file", "category": "File System"},
        {"name": "web_search", "category": "Web Search"},
        {"name": "browser_navigate", "category": "Browser"},
        {"name": "run_shell", "category": "File System"},
        {"name": "schedule_task", "category": "Scheduled Tasks"},
        {"name": "delegate_to_agent", "category": "Agents"},
    ]
    agent._current_intent = IntentResult(
        intent=IntentType.CHAT,
        prompt_depth=PromptDepth.FAST,
        requires_tools=False,
        force_tool=False,
    )
    agent._current_user_message = "你好"
    agent._is_sub_agent_call = False
    agent._agent_tool_names = frozenset()
    agent._cron_disabled_tools = set()
    agent._current_session_type = "cli"
    agent._discovered_tools = set()
    agent.tool_catalog = _FakeToolCatalog()
    agent._get_raw_context_window = lambda: 0

    effective = agent._effective_tools
    tool_names = {tool["name"] for tool in effective}
    direct = [t["name"] for t in effective if not t.get("_deferred")]
    deferred = {t["name"] for t in effective if t.get("_deferred")}

    assert tool_names == {
        "read_file",
        "web_search",
        "browser_navigate",
        "run_shell",
        "schedule_task",
        "delegate_to_agent",
    }
    assert direct == ["read_file", "run_shell", "delegate_to_agent"]
    assert deferred == {"web_search", "browser_navigate", "schedule_task"}
    assert agent._last_minimal_toolset is False


def test_stable_main_chat_core_has_8_to_15_tools_in_fixed_order():
    from openakita.tools.defer_config import STABLE_MAIN_CHAT_CORE_TOOLS

    assert 8 <= len(STABLE_MAIN_CHAT_CORE_TOOLS) <= 15
    assert STABLE_MAIN_CHAT_CORE_TOOLS == (
        "run_shell",
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "grep",
        "ask_user",
        "tool_search",
        "get_tool_info",
        "delegate_to_agent",
        "delegate_parallel",
        "search_memory",
        "add_memory",
        "get_skill_info",
    )


def test_stable_main_chat_promotes_discovered_and_user_pinned_tools(monkeypatch):
    from openakita.config import settings as _settings

    monkeypatch.setattr(_settings, "always_load_tools", ["schedule_task"])
    monkeypatch.setattr(_settings, "always_load_categories", [])

    agent = Agent.__new__(Agent)
    tools = [
        {"name": "run_shell", "category": "File System"},
        {"name": "web_search", "category": "Web Search"},
        {"name": "schedule_task", "category": "Scheduled"},
        {"name": "browser_navigate", "category": "Browser"},
    ]
    agent._discovered_tools = {"web_search"}

    first = agent._stable_main_chat_tool_set(tools)
    second = agent._stable_main_chat_tool_set(tools)

    assert [t["name"] for t in first] == [t["name"] for t in second]
    assert [t["name"] for t in first if not t.get("_deferred")] == [
        "run_shell",
        "web_search",
        "schedule_task",
    ]
    assert first[-1]["_deferred"] is True
    assert all("_deferred" not in tool and "_promoted" not in tool for tool in tools)


def test_structured_web_search_hint_promotes_only_web_search_for_current_request():
    agent = Agent.__new__(Agent)
    tools = [
        {"name": "run_shell", "category": "File System"},
        {"name": "web_search", "category": "Web Search"},
        {"name": "news_search", "category": "Web Search"},
        {"name": "web_fetch", "category": "Web Search"},
        {"name": "browser_navigate", "category": "Browser"},
        {"name": "browser_click", "category": "Browser"},
    ]
    intent = IntentResult(
        intent=IntentType.TASK,
        tool_hints=["Web Search", "Browser", "File System"],
        requires_tools=True,
        force_tool=True,
    )

    promoted = agent._resolve_intent_schema_promotions(intent, tools)
    agent._intent_promoted_tools = promoted
    agent._discovered_tools = set()
    effective = agent._stable_main_chat_tool_set(tools)

    assert promoted == {"web_search"}
    assert [tool["name"] for tool in effective if not tool.get("_deferred")] == [
        "run_shell",
        "web_search",
    ]
    assert {tool["name"] for tool in effective if tool.get("_deferred")} == {
        "news_search",
        "web_fetch",
        "browser_navigate",
        "browser_click",
    }


def test_structured_hint_promotion_is_bounded_and_requires_tool_intent():
    agent = Agent.__new__(Agent)
    tools = [
        {"name": "web_search", "category": "Web Search"},
        {"name": "browser_navigate", "category": "Browser"},
    ]

    no_tool_intent = IntentResult(
        intent=IntentType.QUERY,
        tool_hints=["web_search"],
        requires_tools=False,
        force_tool=False,
    )
    broad_category_intent = IntentResult(
        intent=IntentType.TASK,
        tool_hints=["Browser"],
        requires_tools=True,
        force_tool=True,
    )
    exact_tool_intent = IntentResult(
        intent=IntentType.TASK,
        tool_hints=["browser_navigate", "web_search"],
        requires_tools=True,
        force_tool=True,
    )

    assert agent._resolve_intent_schema_promotions(no_tool_intent, tools) == set()
    assert agent._resolve_intent_schema_promotions(broad_category_intent, tools) == set()
    assert agent._resolve_intent_schema_promotions(exact_tool_intent, tools) == {"browser_navigate"}


def test_selfcheck_fix_policy_limits_exposed_tools():
    agent = Agent.__new__(Agent)
    agent._tools = [
        {"name": "read_file", "category": "File System"},
        {"name": "grep", "category": "File System"},
        {"name": "delegate_to_agent", "category": "Agents"},
        {"name": "browser_open", "category": "Browser"},
    ]
    agent._current_intent = None
    agent._is_sub_agent_call = False
    agent._agent_tool_names = frozenset()
    agent._cron_disabled_tools = set()
    agent._current_session_type = "cli"
    agent._discovered_tools = set()
    agent._selfcheck_allowed_tools = {"read_file", "grep"}
    agent.tool_catalog = _FakeToolCatalog()
    agent._get_raw_context_window = lambda: 0

    tool_names = {tool["name"] for tool in agent._effective_tools}

    assert tool_names == {"read_file", "grep"}
    assert "delegate_to_agent" not in tool_names
    assert "browser_open" not in tool_names


def test_sub_agent_still_excludes_delegation_tools():
    agent = Agent.__new__(Agent)
    agent._tools = [
        {"name": "read_file", "category": "File System"},
        {"name": "delegate_to_agent", "category": "Agent"},
        {"name": "delegate_parallel", "category": "Agent"},
    ]
    agent._current_intent = None
    agent._is_sub_agent_call = True
    agent._agent_tool_names = frozenset({"delegate_to_agent", "delegate_parallel"})
    agent._cron_disabled_tools = set()
    agent._current_session_type = "cli"
    agent._discovered_tools = set()
    agent.tool_catalog = _FakeToolCatalog()
    agent._get_raw_context_window = lambda: 0

    assert [tool["name"] for tool in agent._effective_tools] == ["read_file"]


def test_previous_answer_replay_request_detects_incomplete_display_followup():
    history = [
        {"role": "user", "content": "帮我分析这个线上 bug"},
        {"role": "assistant", "content": "## 完整报告\n这里是已经生成的报告内容。"},
    ]

    assert _looks_like_previous_answer_replay_request("你的完整报告并没有展示完全", history)
    assert _looks_like_previous_answer_replay_request("结果没有展示全，重新展示一下", history)


def test_previous_answer_replay_request_does_not_match_reanalysis_requests():
    history = [
        {"role": "user", "content": "帮我分析这个线上 bug"},
        {"role": "assistant", "content": "## 完整报告\n这里是已经生成的报告内容。"},
    ]

    assert not _looks_like_previous_answer_replay_request("请重新分析这个 bug", history)
    assert not _looks_like_previous_answer_replay_request("完整重新排查一遍", history)
    assert not _looks_like_previous_answer_replay_request("你的完整报告并没有展示完全", [])


def test_previous_answer_replay_request_does_not_match_turn_with_new_attachments():
    history = [
        {"role": "user", "content": "分析上一批图片"},
        {"role": "assistant", "content": "上一批图片的分析结果"},
    ]

    assert not _looks_like_previous_answer_replay_request(
        "请读取我新上传的图片并输出完整内容",
        history,
        has_new_objects=True,
    )


def test_previous_answer_replay_hint_preserves_original_user_request():
    prompted = _apply_previous_answer_replay_hint("你的完整报告并没有展示完全")

    assert "优先复用上文最近的 assistant 回复" in prompted
    assert "不要重新调用工具、重新检索或重新分析" in prompted
    assert prompted.endswith("你的完整报告并没有展示完全")


def test_local_endpoint_missing_context_window_uses_small_model_budget():
    endpoint = EndpointConfig.from_dict(
        {
            "name": "ollama-qwen3-4b",
            "provider": "ollama",
            "api_type": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3:4b",
        }
    )

    assert endpoint.context_window == LOCAL_ENDPOINT_DEFAULT_CONTEXT_WINDOW


def test_parse_prompt_contract_minimal_query():
    result = _parse_intent_output(
        """
intent: query
task_type: question
goal: 计算数字
tool_hints: []
memory_keywords: []
capability_scope: [none]
prompt_depth: minimal
memory_scope: pinned_only
catalog_scope: []
requires_tools: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "what is 19 * 23 and add 4",
    )

    assert result.intent == IntentType.QUERY
    assert result.prompt_depth == PromptDepth.MINIMAL
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is False
    assert result.evidence_required is False
    assert result.force_tool is False


def test_unknown_prompt_contract_values_fall_back_safely():
    result = _parse_intent_output(
        """
intent: query
task_type: question
goal: explain
tool_hints: []
memory_keywords: []
prompt_depth: huge
memory_scope: everything
requires_tools: false
requires_project_context: false
""",
        "什么是 API",
    )

    assert result.prompt_depth == PromptDepth.MINIMAL
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.force_tool is False
    assert result.evidence_required is False


def test_default_intent_is_minimal_tool_capable_task():
    result = _make_default("解释一下 Python GIL")

    assert result.intent == IntentType.TASK
    assert result.prompt_depth == PromptDepth.MINIMAL
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is True
    assert result.evidence_required is False
    assert result.force_tool is True


def test_write_confirmation_followup_requires_evidence_without_overprompting():
    result = _parse_intent_output(
        """
intent: chat
task_type: other
goal: 用户询问写入是否成功
tool_hints: []
memory_keywords: []
requires_tools: false
evidence_required: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "写入成功了吗",
    )

    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.evidence_required is True
    assert result.force_tool is True


def test_llm_query_misclassification_is_coerced_for_external_action():
    result = _parse_intent_output(
        """
intent: query
task_type: question
goal: 分析日志警告原因
tool_hints: []
memory_keywords: []
requires_tools: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "我手动删除了，现在再看看很多警告的日志，是什么原因导致的",
    )

    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.evidence_required is True
    assert result.force_tool is True


def test_execute_task_followup_is_guarded_as_tool_task():
    result = _parse_intent_output(
        """
intent: chat
task_type: other
goal: 请求继续执行任务而不中断
tool_hints: []
memory_keywords: []
requires_tools: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "执行任务，不要停掉",
    )

    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.evidence_required is True
    assert result.force_tool is True


def test_immediate_execute_followup_is_guarded_without_hard_timeout_policy():
    result = _parse_intent_output(
        """
intent: chat
task_type: other
goal: 用户要求立即执行上一项任务
tool_hints: []
memory_keywords: []
requires_tools: false
evidence_required: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "立即执行",
    )

    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.evidence_required is True
    assert result.force_tool is True


def test_tool_required_query_does_not_force_tool_alone():
    """P0-2 阶段 2：单独的 requires_tools 不再触发 ForceToolCall + evidence_required。

    旧语义把 requires_tools/force_tool/evidence_required 全部 OR 起来，
    导致简单 QUERY 也被打成"必须工具证据"，触发硬性重试 + disclaimer，
    被复盘判定为 P0-2 根因之一。新语义：requires_tools 仅是"任务期望调工具"，
    不强制；force_tool 才驱动 retries=2；evidence_required 才驱动 retries=1+硬证据。
    """
    result = IntentResult(
        intent=IntentType.QUERY,
        task_type="analysis",
        requires_tools=True,
        force_tool=False,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(result)

    assert force_retries == 0, "requires_tools 单独不应强制重试"
    assert evidence_required is False, (
        "requires_tools 单独不应升级为 evidence_required，否则 P0-2 回归。"
    )


def test_external_evidence_overrides_llm_false_without_changing_user_flow_to_hard_policy():
    result = _parse_intent_output(
        """
intent: query
task_type: analysis
goal: 分析 GitHub issue
tool_hints: []
memory_keywords: []
requires_tools: false
evidence_required: false
requires_project_context: false
risk_level_hint: none
destructive: false
scope: narrow
suggest_plan: false
""",
        "https://github.com/openakita/openakita/issues/532 帮我分析这个 issue 当前是否仍存在",
    )

    assert result.requires_tools is True
    assert result.evidence_required is True
    assert "Web Search" in result.tool_hints


def test_evidence_required_query_gets_only_one_soft_nudge():
    result = IntentResult(
        intent=IntentType.QUERY,
        task_type="analysis",
        requires_tools=False,
        evidence_required=True,
        force_tool=False,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(result)

    assert force_retries == 1
    assert evidence_required is True


def test_plain_query_still_disables_force_tool_guard():
    result = IntentResult(
        intent=IntentType.QUERY,
        task_type="question",
        requires_tools=False,
        evidence_required=False,
        force_tool=False,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(result)

    assert force_retries == 0
    assert evidence_required is False


def test_plain_task_without_tools_disables_force_tool_guard():
    result = IntentResult(
        intent=IntentType.TASK,
        task_type="analysis",
        requires_tools=False,
        evidence_required=False,
        force_tool=False,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(result)

    assert force_retries == 0
    assert evidence_required is False


def test_org_coordinator_resolves_force_tool_policy_even_for_writing_request():
    """Even when the message looks like pure writing ("做一份 X 宣传计划"),
    if the agent is an org coordinator (has subordinates), the sub-agent
    branch in ``Agent._prepare_session_context`` flips ``requires_tools`` and
    ``evidence_required`` to True. This locks in that the resulting
    IntentResult drives ForceToolCall, so the coordinator cannot silently
    give a final-answer text without delegating.

    P0-2 阶段 2 后语义：
    - force_tool=True   → (2, False)：允许 2 次 ForceToolCall 重试，不要求硬证据
    - 单独 evidence_required → (1, True)：1 次柔性提示 + 走阶段 0 disclaimer
    - 二者同设时，force_tool 优先（更宽松，避免重复重试）
    """
    coord_intent = IntentResult(
        intent=IntentType.TASK,
        task_type="action",
        requires_tools=True,
        evidence_required=True,
        force_tool=True,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(coord_intent)

    assert force_retries == 2, "force_tool=True 应使用 2 次重试预算"
    assert evidence_required is False, (
        "force_tool 路径不再硬绑定 evidence_required，避免阶段 0 disclaimer 重复触发。"
    )
