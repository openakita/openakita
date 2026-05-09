from openakita.core.agent import (
    MINIMAL_PROMPT_TOOLS,
    Agent,
    _apply_previous_answer_replay_hint,
    _looks_like_external_tool_request,
    _looks_like_previous_answer_replay_request,
    _resolve_force_tool_policy,
)
from openakita.core.intent_analyzer import (
    IntentAnalyzer,
    IntentResult,
    IntentType,
    MemoryScope,
    PromptDepth,
    _make_default,
    _parse_intent_output,
    _try_fast_query_shortcut,
)
from openakita.llm.types import (
    DEFAULT_CONTEXT_WINDOW,
    LOCAL_ENDPOINT_DEFAULT_CONTEXT_WINDOW,
    EndpointConfig,
)
from openakita.prompt.builder import PromptMode, PromptProfile, build_system_prompt


class _FailingCompilerBrain:
    async def compiler_think(self, *args, **kwargs):
        raise AssertionError("fast chat must not call the LLM intent analyzer")


class _FakeToolCatalog:
    def __init__(self):
        self.deferred_tools: set[str] | None = None

    def get_tool_groups(self):
        return {}

    def set_deferred_tools(self, names):
        self.deferred_tools = set(names)


async def test_fast_chat_shortcut_skips_llm_intent_analysis():
    result = await IntentAnalyzer(_FailingCompilerBrain()).analyze("你好")

    assert result.intent == IntentType.CHAT
    assert result.fast_reply is True
    assert result.prompt_depth == PromptDepth.FAST
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is False


async def test_fast_chat_shortcut_still_handles_unambiguous_greeting_with_history():
    result = await IntentAnalyzer(_FailingCompilerBrain()).analyze("hello", has_history=True)

    assert result.intent == IntentType.CHAT
    assert result.fast_reply is True
    assert result.requires_tools is False


async def test_direct_short_answer_role_question_skips_llm_intent_analysis():
    result = await IntentAnalyzer(_FailingCompilerBrain()).analyze(
        "请只用一句话回答，你的职责是什么？",
        has_history=True,
    )

    assert result.intent == IntentType.QUERY
    assert result.fast_reply is True
    assert result.prompt_depth == PromptDepth.FAST
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is False
    assert result.evidence_required is False


async def test_direct_identity_question_uses_fast_query_without_tools():
    result = await IntentAnalyzer(_FailingCompilerBrain()).analyze("你是谁")

    assert result.intent == IntentType.QUERY
    assert result.fast_reply is True
    assert result.prompt_depth == PromptDepth.FAST
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is False
    assert result.evidence_required is False


async def test_one_sentence_explanation_skips_tools_without_blocking_model_answer():
    result = await IntentAnalyzer(_FailingCompilerBrain()).analyze("一句话解释 Docker")

    assert result.intent == IntentType.QUERY
    assert result.fast_reply is True
    assert result.requires_tools is False
    assert result.force_tool is False


def test_chat_prompt_strategy_uses_lightweight_consumer_profile():
    agent = Agent.__new__(Agent)
    intent = IntentResult(
        intent=IntentType.CHAT,
        prompt_depth=PromptDepth.FAST,
        memory_scope=MemoryScope.PINNED_ONLY,
        requires_tools=False,
        fast_reply=True,
    )

    strategy = agent._resolve_prompt_strategy(
        intent,
        session_type="cli",
        mode="agent",
    )

    assert strategy.profile == PromptProfile.CONSUMER_CHAT
    assert strategy.prompt_mode == PromptMode.MINIMAL
    assert strategy.memory_scope == MemoryScope.PINNED_ONLY
    assert strategy.catalog_scope == ["index"]
    assert strategy.include_project_guidelines is False


def test_minimal_pinned_only_prompt_still_includes_light_memory(tmp_path):
    prompt = build_system_prompt(
        identity_dir=tmp_path,
        tools_enabled=False,
        memory_manager=object(),
        task_description="记住我的偏好",
        prompt_mode=PromptMode.MINIMAL,
        prompt_profile=PromptProfile.CONSUMER_CHAT,
        memory_scope=MemoryScope.PINNED_ONLY,
        skip_catalogs=True,
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
        skip_catalogs=True,
    )

    assert "## Session Working Facts" in prompt
    assert "temporary_name: alpha" in prompt


def test_fast_chat_effective_tools_use_minimal_schema_set():
    agent = Agent.__new__(Agent)
    agent._tools = [
        {"name": "read_file", "category": "File System"},
        {"name": "web_search", "category": "Web Search"},
        {"name": "browser_navigate", "category": "Browser"},
        {"name": "run_shell", "category": "File System"},
        {"name": "schedule_task", "category": "Scheduled Tasks"},
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

    tool_names = {tool["name"] for tool in agent._effective_tools}

    assert tool_names == {"read_file", "web_search"}
    assert tool_names <= MINIMAL_PROMPT_TOOLS
    assert agent._last_minimal_toolset is True


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


def test_previous_answer_replay_hint_preserves_original_user_request():
    prompted = _apply_previous_answer_replay_hint("你的完整报告并没有展示完全")

    assert "优先复用上文最近的 assistant 回复" in prompted
    assert "不要重新调用工具、重新检索或重新分析" in prompted
    assert prompted.endswith("你的完整报告并没有展示完全")


def test_local_endpoint_missing_context_window_uses_small_model_budget():
    endpoint = EndpointConfig(
        name="ollama-qwen3-4b",
        provider="ollama",
        api_type="openai",
        base_url="http://localhost:11434/v1",
        model="qwen3:4b",
        context_window=DEFAULT_CONTEXT_WINDOW,
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


def test_default_intent_is_minimal_non_tool_query():
    result = _make_default("解释一下 Python GIL")

    assert result.intent == IntentType.QUERY
    assert result.prompt_depth == PromptDepth.MINIMAL
    assert result.memory_scope == MemoryScope.PINNED_ONLY
    assert result.requires_tools is False
    assert result.evidence_required is False
    assert result.force_tool is False


def test_log_investigation_query_is_guarded_as_tool_task():
    result = _try_fast_query_shortcut(
        "我看你的运行日志有很多报错和警告的内容，都是关于skills技能的，你排查一下是什么原因导致的"
    )

    assert result is not None
    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.evidence_required is True
    assert result.force_tool is True
    assert result.fast_reply is False


def test_daily_record_content_is_guarded_as_tool_task():
    result = _try_fast_query_shortcut("3月18日工作：邹总问了下是否有交付确认邮件")

    assert result is not None
    assert result.intent == IntentType.TASK
    assert result.requires_tools is True
    assert result.evidence_required is True
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


def test_plain_concept_query_is_not_over_guarded():
    result = _try_fast_query_shortcut("什么是API")

    assert result is not None
    assert result.intent == IntentType.QUERY
    assert result.requires_tools is False
    assert result.evidence_required is False
    assert result.force_tool is False


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


def test_tool_required_query_keeps_force_tool_guard():
    result = IntentResult(
        intent=IntentType.QUERY,
        task_type="analysis",
        requires_tools=True,
        force_tool=False,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(result)

    assert force_retries == 1
    assert evidence_required is True


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


def test_sub_agent_plain_text_delegation_does_not_force_tools():
    message = (
        "请扮演法国总统马克龙，围绕 AI 与日本经济写一段 200 字观点。"
        "直接用纯文本回复，不需要调用任何工具。"
    )

    assert _looks_like_external_tool_request(message) is False


def test_sub_agent_external_delegation_still_requires_tools():
    message = "请读取 /tmp/report.md，并根据文件内容总结关键结论。"

    assert _looks_like_external_tool_request(message) is True


# ---------------------------------------------------------------------------
# Regression: 5/8 keyword-only delegation guard missed common Chinese
# "produce-a-deliverable" verbs. Coordinator nodes (editor-in-chief, CEO,
# tech-lead) ended up self-executing tasks like "做一份 X 计划" instead of
# delegating because none of "做一份 / 出一份 / 整理 / 宣传 / 调研" matched.
# These cases lock in the expanded marker list.
# ---------------------------------------------------------------------------


def test_make_a_plan_request_is_treated_as_external_tool_task():
    assert _looks_like_external_tool_request("帮我做一份 OpenAkita 的宣传计划") is True


def test_compile_a_report_request_is_treated_as_external_tool_task():
    assert _looks_like_external_tool_request("整理一份本周项目进展汇总") is True


def test_research_competitor_request_is_treated_as_external_tool_task():
    assert _looks_like_external_tool_request("调研一下竞品的定价策略") is True


def test_publish_announcement_request_is_treated_as_external_tool_task():
    assert _looks_like_external_tool_request("写一份本月的产品发布通告") is True


def test_english_produce_request_is_treated_as_external_tool_task():
    assert _looks_like_external_tool_request("Please produce a marketing plan") is True


def test_explicit_no_tool_keeps_pure_writing_path():
    """Even with deliverable-style verbs, an explicit "no tools" rider should
    still route the request to text-only sub-agent flow (preserves the
    "扮演 X 写一段 200 字" use case introduced in 5/8)."""
    message = (
        "请扮演记者写一份 200 字的产品介绍。直接用纯文本回复，不要调用任何工具。"
    )

    assert _looks_like_external_tool_request(message) is False


def test_org_coordinator_resolves_force_tool_policy_even_for_writing_request():
    """Even when the message looks like pure writing ("做一份 X 宣传计划"),
    if the agent is an org coordinator (has subordinates), the sub-agent
    branch in ``Agent._prepare_session_context`` flips ``requires_tools`` and
    ``evidence_required`` to True. This locks in that the resulting
    IntentResult drives ForceToolCall (max_retries=1, evidence_required=True),
    so the coordinator cannot silently give a final-answer text without
    delegating.
    """
    coord_intent = IntentResult(
        intent=IntentType.TASK,
        task_type="action",
        requires_tools=True,
        evidence_required=True,
        force_tool=True,
    )

    force_retries, evidence_required = _resolve_force_tool_policy(coord_intent)

    assert force_retries == 1
    assert evidence_required is True
