"""Regression tests for content-safety error classification chain.

Covers the fix that prevents DashScope DataInspectionFailed errors from being
silently downgraded to a generic "check API key" message. The keyword
``data_inspection`` MUST be preserved end-to-end so that:

- ``_humanize_upstream_error`` keeps it in the user-facing message
- ``LLMProvider._classify_error`` returns ``FailoverReason.CONTENT_SAFETY``
- ``_friendly_error_hint`` returns the content-safety hint
- ``utils.errors.classify_error`` returns ``ErrorCategory.CONTENT_FILTER``
"""

from __future__ import annotations

from openakita.llm.client import _friendly_error_hint
from openakita.llm.error_types import FailoverReason
from openakita.llm.providers.base import LLMProvider
from openakita.llm.providers.openai import _humanize_upstream_error
from openakita.utils.errors import ErrorCategory, classify_error

DASHSCOPE_DATA_INSPECTION_BODY = (
    '{"error":{"message":"<400> InternalError.Algo.DataInspectionFailed: '
    'Input text data may contain inappropriate content.",'
    '"type":"data_inspection_failed","param":null,'
    '"code":"data_inspection_failed"},'
    '"id":"chatcmpl-xxx","request_id":"xxx"}'
)


class TestHumanizeUpstreamError:
    def test_data_inspection_keyword_preserved(self):
        msg = _humanize_upstream_error(400, DASHSCOPE_DATA_INSPECTION_BODY)
        assert "data_inspection_failed" in msg, (
            "humanized message must keep the keyword for downstream "
            "classification to work"
        )
        assert "内容安全审核" in msg

    def test_inappropriate_content_also_matches(self):
        body = '{"error":{"message":"Inappropriate content detected"}}'
        msg = _humanize_upstream_error(400, body)
        assert "data_inspection_failed" in msg

    def test_normal_400_not_misclassified(self):
        body = '{"error":{"message":"Bad request: missing parameter"}}'
        msg = _humanize_upstream_error(400, body)
        assert "data_inspection" not in msg
        assert "云端模型调用失败" in msg

    def test_401_still_returns_auth_message(self):
        msg = _humanize_upstream_error(401, "invalid api key")
        assert "API Key" in msg
        assert "data_inspection" not in msg


class TestClassifyError:
    def test_data_inspection_is_content_safety(self):
        assert (
            LLMProvider._classify_error("data_inspection_failed (400)")
            == FailoverReason.CONTENT_SAFETY
        )

    def test_inappropriate_content_is_content_safety(self):
        assert (
            LLMProvider._classify_error("inappropriate content detected")
            == FailoverReason.CONTENT_SAFETY
        )

    def test_content_safety_takes_priority_over_structural_400(self):
        # Even with "(400)" in the string, CONTENT_SAFETY wins so that we don't
        # waste retries against the same input on other endpoints.
        err = "data_inspection_failed (400) invalid_request"
        assert LLMProvider._classify_error(err) == FailoverReason.CONTENT_SAFETY

    def test_structural_400_still_works(self):
        assert (
            LLMProvider._classify_error("invalid_parameter (400)")
            == FailoverReason.STRUCTURAL
        )

    def test_invalid_function_response_is_structural(self):
        err = (
            "API error (400): Invalid function response: "
            "✅ 计划已创建：plan_20260408_190537_3c9850"
        )
        assert LLMProvider._classify_error(err) == FailoverReason.STRUCTURAL


class TestFriendlyErrorHint:
    def test_hint_from_last_error_keyword(self):
        hint = _friendly_error_hint(
            failed_providers=None,
            last_error="data_inspection_failed: input contains sensitive content",
        )
        assert "内容安全审核" in hint
        assert "/clear" in hint

    def test_hint_falls_back_when_no_keyword(self):
        hint = _friendly_error_hint(
            failed_providers=None,
            last_error="some unknown error",
        )
        assert "内容安全审核" not in hint
        assert "API Key" in hint or "网络" in hint or "余额" in hint

    def test_invalid_function_response_hint_is_specific(self):
        hint = _friendly_error_hint(
            failed_providers=None,
            last_error="API error (400): Invalid function response: ok",
        )
        assert "工具返回格式" in hint
        assert "API Key" not in hint


class TestUtilsErrors:
    def test_data_inspection_maps_to_content_filter(self):
        # Should remain unchanged after the fix
        assert classify_error("data_inspection_failed") == ErrorCategory.CONTENT_FILTER

    def test_humanized_message_still_classifies_correctly(self):
        # End-to-end: humanized output -> downstream classifier
        body = DASHSCOPE_DATA_INSPECTION_BODY
        humanized = _humanize_upstream_error(400, body)
        assert classify_error(humanized) == ErrorCategory.CONTENT_FILTER
