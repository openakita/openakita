"""failure_diagnoser 文案语气微调测试。

覆盖：
1. ``verify_incomplete*`` 根因走「ℹ️ 复盘提示」中性表述，避免对所有非完美
   退出都喷「为什么失败」的硬失败语气；
2. ``loop_terminated`` / ``max_iterations`` 等硬失败仍然显示「为什么失败」；
3. ``verify_incomplete`` 模板的 headline 已聚焦"附件交付"语义，suggestion
   提到 ``write_file`` / ``org_submit_deliverable``。
"""

from openakita.orgs.failure_diagnoser import (
    _DIAGNOSIS_TEMPLATES,
    format_human_summary,
)


class TestFormatHumanSummaryTone:
    def test_verify_incomplete_uses_soft_label(self):
        diag = {
            "root_cause": "verify_incomplete",
            "headline": _DIAGNOSIS_TEMPLATES["verify_incomplete"]["headline"],
            "suggestion": _DIAGNOSIS_TEMPLATES["verify_incomplete"]["suggestion"],
            "evidence": [],
        }
        out = format_human_summary(diag)
        assert "复盘提示" in out
        assert "为什么失败" not in out

    def test_verify_incomplete_with_children_uses_soft_label(self):
        diag = {
            "root_cause": "verify_incomplete_with_children",
            "headline": "已通过下属交付完成",
            "suggestion": "ok",
            "evidence": [],
        }
        out = format_human_summary(diag)
        assert "复盘提示" in out
        assert "为什么失败" not in out

    def test_loop_terminated_keeps_hard_failure_label(self):
        diag = {
            "root_cause": "loop_terminated",
            "headline": "节点被强制终止",
            "suggestion": "请检查 supervisor 触发原因",
            "evidence": [],
        }
        out = format_human_summary(diag)
        assert "为什么失败" in out

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


class TestVerifyIncompleteTemplateContent:
    def test_headline_emphasizes_artifact_delivery(self):
        h = _DIAGNOSIS_TEMPLATES["verify_incomplete"]["headline"]
        assert "附件" in h or "文件" in h

    def test_suggestion_mentions_write_file_and_submit_tools(self):
        s = _DIAGNOSIS_TEMPLATES["verify_incomplete"]["suggestion"]
        assert "write_file" in s
        assert "org_submit_deliverable" in s
