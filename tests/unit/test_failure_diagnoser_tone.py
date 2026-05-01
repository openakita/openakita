"""failure_diagnoser 文案语气测试。

覆盖：
1. ``verify_incomplete*`` 系列（verify_incomplete / verify_incomplete_with_children）
   被 ``format_human_summary`` 全量静默——返回空串，**不再**输出「ℹ️ 复盘提示」
   到用户可见 UI（2026-04-28 收紧：用户多次明确反馈这类卡片是噪音，
   runtime 已在 emit 前置空 diagnosis，本函数作为双保险）；
2. ``loop_terminated`` / ``max_iterations`` / 未知 root_cause 等真硬失败
   仍然显示「为什么失败」卡片，行为不变；
3. 模板字符串 ``_DIAGNOSIS_TEMPLATES["verify_incomplete"]`` 保留（供日志/审计
   internal use），不在本测试里删除。
"""

from openakita.orgs.failure_diagnoser import (
    _DIAGNOSIS_TEMPLATES,
    format_human_summary,
)


class TestFormatHumanSummaryTone:
    def test_verify_incomplete_returns_empty_string(self):
        """verify_incomplete 不再吐任何用户可见文案——双保险静默。"""
        diag = {
            "root_cause": "verify_incomplete",
            "headline": _DIAGNOSIS_TEMPLATES["verify_incomplete"]["headline"],
            "suggestion": _DIAGNOSIS_TEMPLATES["verify_incomplete"]["suggestion"],
            "evidence": [],
        }
        out = format_human_summary(diag)
        assert out == ""
        assert "复盘提示" not in out
        assert "为什么失败" not in out

    def test_verify_incomplete_with_children_returns_empty_string(self):
        """verify_incomplete_with_children 同样静默——软完成路径也不吐卡片。"""
        diag = {
            "root_cause": "verify_incomplete_with_children",
            "headline": "已通过下属交付完成",
            "suggestion": "ok",
            "evidence": [{"iter": 1, "tool": "x", "args_summary": "", "error": "y"}],
        }
        out = format_human_summary(diag)
        assert out == ""
        assert "复盘提示" not in out

    def test_loop_terminated_keeps_hard_failure_label(self):
        diag = {
            "root_cause": "loop_terminated",
            "headline": "节点被强制终止",
            "suggestion": "请检查 supervisor 触发原因",
            "evidence": [],
        }
        out = format_human_summary(diag)
        assert "为什么失败" in out
        assert "节点被强制终止" in out

    def test_max_iterations_keeps_hard_failure_label(self):
        diag = {
            "root_cause": "max_iterations",
            "headline": "超过最大迭代",
            "suggestion": "提高上限或简化任务",
            "evidence": [],
        }
        out = format_human_summary(diag)
        assert "为什么失败" in out

    def test_unknown_root_cause_keeps_hard_failure_label(self):
        diag = {
            "root_cause": "unknown",
            "headline": "未知",
            "suggestion": "查 trace",
            "evidence": [],
        }
        out = format_human_summary(diag)
        assert "为什么失败" in out

    def test_org_delegate_loop_keeps_hard_failure_label(self):
        """硬失败子类同样保留「为什么失败」标签。"""
        diag = {
            "root_cause": "org_delegate_loop",
            "headline": "派发陷入死循环",
            "suggestion": "改用 org_submit_deliverable",
            "evidence": [],
        }
        out = format_human_summary(diag)
        assert "为什么失败" in out


class TestVerifyIncompleteTemplateRetained:
    """模板字符串本身保留，仅 format_human_summary 出口拦截——日志/审计仍可访问。"""

    def test_verify_incomplete_template_still_exists(self):
        """模板未被删，runtime 内部路径（日志、调试）仍能拿到 root_cause + headline。"""
        assert "verify_incomplete" in _DIAGNOSIS_TEMPLATES
        assert "headline" in _DIAGNOSIS_TEMPLATES["verify_incomplete"]
        assert "suggestion" in _DIAGNOSIS_TEMPLATES["verify_incomplete"]

    def test_verify_incomplete_with_children_template_still_exists(self):
        assert "verify_incomplete_with_children" in _DIAGNOSIS_TEMPLATES

    def test_headline_emphasizes_artifact_delivery(self):
        h = _DIAGNOSIS_TEMPLATES["verify_incomplete"]["headline"]
        assert "附件" in h or "文件" in h

    def test_suggestion_mentions_write_file_and_submit_tools(self):
        s = _DIAGNOSIS_TEMPLATES["verify_incomplete"]["suggestion"]
        assert "write_file" in s
        assert "org_submit_deliverable" in s
