"""软 verify_incomplete 识别 & 路径切换回归测试

覆盖范围：
1. failure_diagnoser.is_soft_verify_incomplete 的判定逻辑（纯函数）
2. failure_diagnoser.summarize 对 verify_incomplete 的根因降级
   （verify_incomplete -> verify_incomplete_with_children）
3. 对照：纯硬 verify_incomplete（无 accept_deliverable 痕迹）
   仍然返回 root_cause="verify_incomplete"，行为未改变

不引入 OrgRuntime 的全量初始化（其依赖 OrgManager 的 lifecycle，
不利于单元化测试）。runtime 中分支的逻辑等价性靠"helper 真值表 +
分支 if/elif 文本评审"双保险，本测试聚焦 helper 与诊断输出的稳定性。
"""

from __future__ import annotations

import pytest

from openakita.orgs.failure_diagnoser import (
    is_soft_verify_incomplete,
    summarize,
)


def _trace_with_accept(success: bool = True) -> list[dict]:
    """构造一个最小的 react_trace：包含一次 org_accept_deliverable 调用。"""
    return [
        {
            "iteration": 1,
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "org_accept_deliverable",
                    "input": {"from_node": "planner", "task_chain_id": "tc-1"},
                },
            ],
            "tool_results": [
                {
                    "tool_use_id": "call-1",
                    "is_error": not success,
                    "result_content": (
                        "✅ 已验收 planner 的交付" if success
                        else "❌ org_accept_deliverable 失败：找不到任务链"
                    ),
                },
            ],
        },
    ]


def _trace_without_accept() -> list[dict]:
    """协调者只 send_message，没有验收任何下属交付。"""
    return [
        {
            "iteration": 1,
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "org_send_message",
                    "input": {"to_node": "planner", "content": "请加快"},
                },
            ],
            "tool_results": [
                {
                    "tool_use_id": "call-1",
                    "is_error": False,
                    "result_content": "✅ 消息已送达",
                },
            ],
        },
    ]


class TestIsSoftVerifyIncomplete:
    def test_returns_true_when_verify_incomplete_with_accepted_child(self):
        assert is_soft_verify_incomplete(
            "verify_incomplete", _trace_with_accept(success=True),
        ) is True

    def test_returns_false_when_accept_was_error(self):
        # accept_deliverable 失败不应当触发软完成
        assert is_soft_verify_incomplete(
            "verify_incomplete", _trace_with_accept(success=False),
        ) is False

    def test_returns_false_when_no_accept_deliverable(self):
        assert is_soft_verify_incomplete(
            "verify_incomplete", _trace_without_accept(),
        ) is False

    def test_returns_false_for_other_exit_reasons(self):
        # 其它 exit_reason 即使存在 accept_deliverable 也不应被识别为软完成
        for reason in ("normal", "ask_user", "max_iterations", "loop_terminated"):
            assert is_soft_verify_incomplete(
                reason, _trace_with_accept(success=True),
            ) is False, f"reason={reason!r} 不应被识别为软完成"

    def test_returns_false_for_empty_trace(self):
        assert is_soft_verify_incomplete("verify_incomplete", None) is False
        assert is_soft_verify_incomplete("verify_incomplete", []) is False

    def test_handles_malformed_trace_gracefully(self):
        # 异常路径不应抛出
        assert is_soft_verify_incomplete(
            "verify_incomplete", [{"not": "a dict structure"}],  # type: ignore[list-item]
        ) is False
        assert is_soft_verify_incomplete(
            "verify_incomplete", "not a list",  # type: ignore[arg-type]
        ) is False


class TestSummarizeRootCauseDowngrade:
    def test_soft_case_downgrades_to_with_children(self):
        diag = summarize(_trace_with_accept(success=True), "verify_incomplete")
        assert diag["root_cause"] == "verify_incomplete_with_children"
        assert diag["exit_reason"] == "verify_incomplete"
        assert "提示性" in diag["headline"] or "verify" in diag["headline"].lower()

    def test_hard_case_stays_verify_incomplete(self):
        diag = summarize(_trace_without_accept(), "verify_incomplete")
        assert diag["root_cause"] == "verify_incomplete"
        assert diag["exit_reason"] == "verify_incomplete"

    def test_failed_accept_does_not_downgrade(self):
        # accept 失败不应触发降级（语义：没有真正"验收成功"的下属交付）
        diag = summarize(_trace_with_accept(success=False), "verify_incomplete")
        assert diag["root_cause"] == "verify_incomplete"


class TestSoftVerifyContract:
    """合同测试：保证 helper 与 summarize 的判定一致，避免日后两边漂移。"""

    @pytest.mark.parametrize(
        "trace_factory,expected_soft",
        [
            (lambda: _trace_with_accept(success=True), True),
            (lambda: _trace_with_accept(success=False), False),
            (lambda: _trace_without_accept(), False),
            (lambda: None, False),
        ],
    )
    def test_helper_matches_summarize_downgrade(
        self, trace_factory, expected_soft: bool,
    ):
        trace = trace_factory()
        helper_result = is_soft_verify_incomplete("verify_incomplete", trace)
        diag = summarize(trace, "verify_incomplete")
        downgraded = diag["root_cause"] == "verify_incomplete_with_children"
        assert helper_result == expected_soft
        assert helper_result == downgraded, (
            f"helper={helper_result} 与 summarize 降级={downgraded} 不一致，"
            f"会导致 runtime 分支判定与诊断卡片根因不匹配"
        )
