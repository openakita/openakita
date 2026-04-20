"""Tests for response_handler artifact-expectation system-prefix guard (B1)
and verify_task_completion bypass parameter (B2/B3 wiring).
"""

from openakita.core.response_handler import ResponseHandler


class TestRequestExpectsArtifactGuard:
    def test_normal_request_with_keyword_expects_artifact(self):
        # A real user request mentioning 文件 still expects an artifact.
        assert ResponseHandler._request_expects_artifact("帮我写一份分析文件") is True

    def test_post_summary_prefix_excluded(self):
        text = "[用户指令最终汇总] 请基于下级各自交付的成果输出文件、链接清单"
        assert ResponseHandler._request_expects_artifact(text) is False

    def test_system_prefix_excluded(self):
        text = "[系统] 检测到任务已完成，请输出文件汇总"
        assert ResponseHandler._request_expects_artifact(text) is False

    def test_org_prefix_excluded(self):
        text = "[组织] 协调任务已结束，文件已就绪"
        assert ResponseHandler._request_expects_artifact(text) is False

    def test_other_bracket_prefix_not_excluded(self):
        # Defensive: only the explicit whitelist gets bypassed.
        text = "[其他] 给我一个文件"
        assert ResponseHandler._request_expects_artifact(text) is True

    def test_none_or_empty_safe(self):
        assert ResponseHandler._request_expects_artifact(None) is False
        assert ResponseHandler._request_expects_artifact("") is False

    def test_english_keyword_still_works_for_normal_request(self):
        assert ResponseHandler._request_expects_artifact("please attach the file") is True

    def test_leading_whitespace_does_not_break_prefix_match(self):
        # _request_expects_artifact strips before prefix-check.
        text = "   [用户指令最终汇总] 请输出文件清单"
        assert ResponseHandler._request_expects_artifact(text) is False


class TestVerifyTaskCompletionBypass:
    async def test_bypass_true_returns_true_immediately(self):
        # When bypass=True, verify must short-circuit without calling LLM.
        rh = ResponseHandler(brain=None)
        ok = await rh.verify_task_completion(
            user_request="anything",
            assistant_response="ok",
            executed_tools=[],
            bypass=True,
        )
        assert ok is True

    async def test_bypass_false_keyword_path_still_works(self):
        # Without bypass, the keyword/system-prefix guard logic stays intact.
        # We validate via the static helper; full verify needs more deps.
        assert ResponseHandler._request_expects_artifact("帮我下载文件") is True

    async def test_org_validation_kwargs_accepted(self):
        # New keyword args must be accepted without breaking signature.
        rh = ResponseHandler(brain=None)
        ok = await rh.verify_task_completion(
            user_request="anything",
            assistant_response="ok",
            executed_tools=[],
            bypass=True,
            accepted_child_count=2,
            has_recent_accepted_signal=True,
        )
        assert ok is True
