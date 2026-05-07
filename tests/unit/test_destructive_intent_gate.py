from types import SimpleNamespace

from openakita.core.agent import _build_destructive_intent_question, _classify_risk_intent
from openakita.core.confirmation_state import ConfirmationDecision, get_confirmation_store
from openakita.core.loop_budget_guard import LoopBudgetGuard
from openakita.core.risk_intent import (
    ORG_SYNTH_PREFIXES,
    OperationKind,
    RiskLevel,
    TargetKind,
    classify_risk_intent,
)
from openakita.core.working_facts import extract_working_facts, format_working_facts


def test_destructive_intent_detects_policy_allowlist_delete():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "删除 security user_allowlist 第 0 条")
    assert result.requires_confirmation
    assert result.target_kind == TargetKind.SECURITY_USER_ALLOWLIST
    assert result.action == "remove_security_allowlist_entry"
    assert result.parameters["index"] == 0


def test_destructive_intent_uses_intent_analyzer_flag():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=True))

    assert _classify_risk_intent(intent, "改一下配置").requires_confirmation


def test_readonly_allowlist_explanation_does_not_confirm():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "解释 allowlist 三者区别")

    assert not result.requires_confirmation


def test_arithmetic_add_does_not_confirm():
    intent = SimpleNamespace(
        complexity=SimpleNamespace(destructive_potential=False),
        requires_tools=False,
        risk_level_hint="none",
    )

    result = _classify_risk_intent(intent, "what is 19 * 23, and then add 4")

    assert not result.requires_confirmation


def test_removed_fact_revision_does_not_confirm():
    intent = SimpleNamespace(
        complexity=SimpleNamespace(destructive_potential=False),
        requires_tools=False,
        risk_level_hint="none",
    )

    result = _classify_risk_intent(
        intent,
        "one module was removed, calculate the revised count",
    )

    assert not result.requires_confirmation


def test_hypothetical_delete_discussion_does_not_confirm():
    intent = SimpleNamespace(
        complexity=SimpleNamespace(destructive_potential=False),
        requires_tools=False,
        risk_level_hint="low",
    )

    result = _classify_risk_intent(
        intent,
        "suppose I say delete files, what should you do?",
    )

    assert not result.requires_confirmation


def test_rm_rf_still_requires_confirmation():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "rm -rf data")

    assert result.requires_confirmation


def test_destructive_intent_question_requires_confirmation():
    result = _classify_risk_intent(None, "删除 security user_allowlist 第 0 条")
    question = _build_destructive_intent_question("删除 security user_allowlist 第 0 条", result)

    assert "继续" in question
    assert "只查看" in question
    assert "取消" in question


def test_pending_confirmation_consumes_known_answers():
    store = get_confirmation_store()
    store.clear("conv-test")
    pending = store.create(
        conversation_id="conv-test",
        original_message="删除 security user_allowlist 第 0 条",
        classification=_classify_risk_intent(None, "删除 security user_allowlist 第 0 条").to_dict(),
        request_id="req-test",
    )

    decision, consumed = store.consume("conv-test", "确认继续")

    assert decision == ConfirmationDecision.CONFIRM
    assert consumed is pending
    assert store.get("conv-test") is None


def test_working_facts_extracts_maple_code():
    facts = extract_working_facts("测试代号是 Maple-42", source_turn=20)
    rendered = format_working_facts(facts)

    assert facts["test_code"]["value"] == "Maple-42"
    assert "Maple-42" in rendered


def test_loop_budget_guard_exit_reasons():
    guard = LoopBudgetGuard(max_total_tool_calls=1)
    decision = guard.record_tool_calls([{"name": "read_file"}, {"name": "grep"}])

    assert decision.should_stop
    assert decision.exit_reason == "tool_budget_exceeded"


# ===========================================================================
# P0-1：组织/系统合成消息前缀豁免（修复 RiskIntentGate 误拦交付物）
# ===========================================================================
#
# 背景：editor-in-chief 收到 [收到任务交付] 这种 OrgRuntime 合成消息时，旧实
# 现会用 _EXECUTE_RE 命中正文里的「执行/运行」普通中文动词，秒退验收链路。
# 修复后所有 ORG_SYNTH_PREFIXES 前缀的消息一律视为非危险输入。


def test_task_delivered_message_skips_risk_gate():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    msg = (
        "[收到任务交付] 来自 seo-opt [任务链: 2026-04-28T0]:\n"
        "任务交付: # OpenAkita SEO 优化建议交付物\n"
        "## 交付文件\n"
        "- `openakita-seo-plan.md` - 完整 SEO 优化建议文档（含执行时间线和关键指标监控）\n"
        "## 建议后续行动\n"
        "1. 优先执行 Phase 1（官网和 GitHub 基础优化）\n"
    )

    result = _classify_risk_intent(intent, msg)

    assert not result.requires_confirmation
    assert result.risk_level == RiskLevel.NONE
    assert result.operation_kind == OperationKind.NONE
    assert result.reason == "org_synthesized_message"
    # 不应再从日期 2026 抓出 index 参数
    assert "index" not in result.parameters


def test_summary_round_message_skips_risk_gate():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    msg = (
        "[用户指令最终汇总] 你最初接到的用户指令所触发的所有委派任务均已关闭。"
        "请基于下级各自交付的成果，向用户输出一份完整的最终汇总——"
        "重要约束：本次激活只用于产出汇总文本，禁止再调 org_delegate_task。"
    )

    result = _classify_risk_intent(intent, msg)

    assert not result.requires_confirmation
    assert result.reason == "org_synthesized_message"


def test_system_prompt_message_skips_risk_gate():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    msg = "[系统] 你已经连续多次未输出可见文字，请立即调用工具完成任务。"

    result = _classify_risk_intent(intent, msg)

    assert not result.requires_confirmation
    assert result.reason == "org_synthesized_message"


def test_all_runtime_synth_prefixes_are_skipped():
    """runtime.py:_format_incoming_message 的全部 13 种 type_label 都必须豁免。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    runtime_labels = [
        "[收到任务]",
        "[收到任务结果]",
        "[收到任务交付]",
        "[任务已通过验收]",
        "[任务被打回]",
        "[收到汇报]",
        "[收到提问]",
        "[收到回答]",
        "[收到上报]",
        "[收到组织公告]",
        "[收到部门公告]",
        "[收到反馈]",
        "[收到握手请求]",
        "[收到消息]",
    ]
    for label in runtime_labels:
        # 拼一段必然命中 _EXECUTE_RE / _WRITE_RE 的正文，确认前缀豁免生效
        msg = f"{label} 来自 worker：请执行删除并重置数据。"
        result = classify_risk_intent(msg, intent)
        assert not result.requires_confirmation, f"prefix {label!r} not exempted"
        assert result.reason == "org_synthesized_message"
        # ORG_SYNTH_PREFIXES 应是常量集合的真子集（防止重构遗漏）
        assert label in ORG_SYNTH_PREFIXES


def test_real_user_destructive_request_still_blocked_after_fix():
    """前缀豁免不应放过真正的危险用户请求。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "删除 security user_allowlist 第 3 条")

    assert result.requires_confirmation
    assert result.target_kind == TargetKind.SECURITY_USER_ALLOWLIST
    assert result.parameters["index"] == 3


def test_synth_prefix_with_leading_whitespace_still_skipped():
    """允许消息有前导空白（trim 之后还是 [xxx] 开头）。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(
        intent, "  \n[收到任务交付] 请执行 Phase 1"
    )

    assert not result.requires_confirmation
    assert result.reason == "org_synthesized_message"


# ===========================================================================
# P0-2：_INDEX_RE 收紧（不再把日期年份 / 版本号当 index）
# ===========================================================================


def test_index_regex_does_not_grab_year_from_date():
    """日期 2026-04-28 / 版本号 2024 不应被抓为 index 参数。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    # 删除场景命中 _WRITE_RE，会走到 _extract_parameters；但句子里只有
    # 「2026-04-28」这种日期年份，不应被识别成 index=2026
    result = _classify_risk_intent(
        intent, "删除 security user_allowlist 中 2026-04-28 之前的条目"
    )

    assert "index" not in result.parameters


def test_index_regex_still_extracts_chinese_index():
    """显式『第 N 条/项/个』格式必须能正确抓出来。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    for sample, expected in [
        ("删除 security user_allowlist 第 0 条", 0),
        ("删除 security user_allowlist 第3项", 3),
        ("删除 security user_allowlist 第 99 个", 99),
    ]:
        result = _classify_risk_intent(intent, sample)
        assert result.parameters.get("index") == expected, sample


def test_index_regex_still_extracts_english_index():
    """『index N』英文格式必须能正确抓出来。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    for sample, expected in [
        ("删除 security user_allowlist index 5", 5),
        ("删除 security user_allowlist index=12", 12),
        ("删除 security user_allowlist index: 7", 7),
    ]:
        result = _classify_risk_intent(intent, sample)
        assert result.parameters.get("index") == expected, sample


def test_index_regex_rejects_4_digit_numbers():
    """index 数字限制 1-3 位，避免抓四位年份/版本号。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    # 即便写成「第 1234 条」，1234 是 4 位也不抓——这里是防御性约束，
    # 实际业务里 allowlist index 不会到 1000+。
    result = _classify_risk_intent(
        intent, "删除 security user_allowlist 第 1234 条"
    )
    assert "index" not in result.parameters


# ===========================================================================
# P0-3：「执行/运行」中文动词不再无条件升级 EXECUTE
# ===========================================================================
#
# 背景：旧版 _EXECUTE_RE 包含中文「执行|运行」，导致以下普通推进语句
# 全部被误判为 high-risk shell：
#   - 「请你立刻重新真实地执行：用 edit_file...」
#   - 「方案 OK，确认开始执行」
#   - 「请按这个方案创建两个文件」（含"执行"字眼时）
# 修复：通用动词必须配合 _SHELL_CONTEXT_RE（shell/命令/bash/cmd/script
# 等）才升 EXECUTE；高敏感的 kill/rm -rf/sudo 等仍直接命中。


def test_chinese_execute_verb_alone_does_not_escalate():
    """「请你执行 edit_file」不应被判为 high-risk shell。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "请你立刻重新真实地执行：用 edit_file 改一行")

    assert result.operation_kind != OperationKind.EXECUTE
    assert result.target_kind != TargetKind.SHELL_COMMAND


def test_chinese_run_verb_alone_does_not_escalate():
    """「方案 OK，确认开始执行」不应被判为 high-risk。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "方案 OK，请按这个方案推进，开始执行第一步")

    assert result.operation_kind != OperationKind.EXECUTE


def test_chinese_execute_with_shell_context_still_escalates():
    """显式 shell 上下文（命令/bash/powershell/脚本）仍升 EXECUTE。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    samples = [
        "执行这条 shell 命令：ls /tmp",
        "运行下面的 bash 脚本",
        "在 powershell 里执行 Get-Process",
        "请用 run_shell 执行这条命令",
    ]
    for sample in samples:
        result = _classify_risk_intent(intent, sample)
        assert result.operation_kind == OperationKind.EXECUTE, sample
        assert result.requires_confirmation, sample


def test_high_sensitivity_shell_verb_still_blocked():
    """rm -rf / kill / sudo 等不依赖中文上下文，必须仍被拦截。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    for sample in ["rm -rf /tmp", "sudo apt remove", "format c:", "kill 1234"]:
        result = _classify_risk_intent(intent, sample)
        assert result.operation_kind == OperationKind.EXECUTE, sample
        assert result.requires_confirmation, sample


# ===========================================================================
# P0-4：用户对上一轮 ask_user/risk-confirm 的肯定回复必须短路豁免
# ===========================================================================
#
# 背景：用户回 "确认继续 / 开始执行 / 方案 OK / 同意" 时本身不是新动作，
# 应该让 _handle_pending_risk_answer 处理；不能再被分类器升 EXECUTE。


def test_affirmative_reply_confirm_continue_is_exempted():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "确认继续")

    assert not result.requires_confirmation
    assert result.reason == "affirmative_reply_to_prior_turn"


def test_affirmative_reply_variants_all_exempted():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    for reply in ["继续", "继续吧", "开始执行", "方案 OK", "方案ok", "已确认", "同意", "可以", "ok", "OK", "yes"]:
        result = _classify_risk_intent(intent, reply)
        assert not result.requires_confirmation, reply
        assert result.reason == "affirmative_reply_to_prior_turn", reply


def test_affirmative_reply_with_extra_text_is_NOT_exempted():
    """如果"确认继续"后面跟了实质性新动作，不应豁免。"""
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    # 这个 reply 不是纯肯定回复，包含新指令
    result = _classify_risk_intent(intent, "确认继续，并且 rm -rf /tmp")

    # 这里 _AFFIRMATIVE_REPLY_RE 不会匹配（因为不是纯肯定句结构），
    # 后续会走 _EXECUTE_RE 命中 rm -rf
    assert result.operation_kind == OperationKind.EXECUTE
