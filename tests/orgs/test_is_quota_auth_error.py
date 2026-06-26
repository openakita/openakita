"""Tests for OrgRuntime._is_quota_auth_error — quota/auth error classification."""

from __future__ import annotations

from openakita.llm.types import AllEndpointsFailedError, LLMError
from openakita.orgs.runtime import OrgRuntime


class TestWithErrorCategories:
    """AllEndpointsFailedError that carries structured error_categories."""

    def test_quota_category_detected(self):
        err = AllEndpointsFailedError("fail", error_categories={"quota"})
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_auth_category_detected(self):
        err = AllEndpointsFailedError("fail", error_categories={"auth"})
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_both_categories_detected(self):
        err = AllEndpointsFailedError("fail", error_categories={"quota", "auth"})
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_mixed_categories_with_quota(self):
        err = AllEndpointsFailedError("fail", error_categories={"transient", "quota"})
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_structural_category_not_detected(self):
        err = AllEndpointsFailedError("fail", error_categories={"structural"})
        assert OrgRuntime._is_quota_auth_error(err) is False

    def test_transient_category_not_detected(self):
        err = AllEndpointsFailedError("fail", error_categories={"transient"})
        assert OrgRuntime._is_quota_auth_error(err) is False

    def test_empty_categories_falls_through_to_string_match(self):
        err = AllEndpointsFailedError(
            "All endpoints failed: 401 Unauthorized",
            error_categories=set(),
        )
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_empty_categories_no_match(self):
        err = AllEndpointsFailedError(
            "All endpoints failed: 502 Bad Gateway",
            error_categories=set(),
        )
        assert OrgRuntime._is_quota_auth_error(err) is False


class TestWithoutErrorCategories:
    """AllEndpointsFailedError with default empty error_categories — string fallback."""

    def test_default_categories_is_empty_set(self):
        err = AllEndpointsFailedError("fail")
        assert err.error_categories == set()

    def test_quota_keyword_in_message(self):
        err = AllEndpointsFailedError("Insufficient balance on account")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_billing_keyword_in_message(self):
        err = AllEndpointsFailedError("billing issue detected")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_402_payment_required(self):
        err = AllEndpointsFailedError("HTTP (402) Payment Required")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_unauthorized_keyword(self):
        err = AllEndpointsFailedError("401 Unauthorized")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_transient_502_not_matched(self):
        err = AllEndpointsFailedError("API error (502): bad gateway")
        assert OrgRuntime._is_quota_auth_error(err) is False

    def test_structural_error_not_matched(self):
        err = AllEndpointsFailedError("invalid request: max_tokens too large", is_structural=True)
        assert OrgRuntime._is_quota_auth_error(err) is False


class TestNonLLMExceptions:
    """Non-AllEndpointsFailedError exceptions — only string matching applies."""

    def test_generic_exception_with_quota_keyword(self):
        err = Exception("insufficient_balance error from provider")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_generic_exception_no_match(self):
        err = Exception("connection timed out after 30s")
        assert OrgRuntime._is_quota_auth_error(err) is False

    def test_llm_error_parent_class(self):
        err = LLMError("401 Unauthorized", status_code=401)
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_runtime_error(self):
        err = RuntimeError("something went wrong")
        assert OrgRuntime._is_quota_auth_error(err) is False

    def test_value_error(self):
        err = ValueError("invalid parameter")
        assert OrgRuntime._is_quota_auth_error(err) is False


class TestEdgeCases:
    """Edge cases and regression guards."""

    def test_none_error_categories_attribute_via_getattr(self):
        """Simulate an old-style AllEndpointsFailedError without error_categories."""
        err = AllEndpointsFailedError("fail")
        del err.error_categories  # remove the attribute to simulate old code
        assert OrgRuntime._is_quota_auth_error(err) is False

    def test_none_error_categories_with_quota_in_message(self):
        """Old-style error with quota keyword in message should still be caught."""
        err = AllEndpointsFailedError("quota exceeded on all endpoints")
        del err.error_categories
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_case_insensitive_match(self):
        err = AllEndpointsFailedError("INSUFFICIENT BALANCE on upstream")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_authorization_required(self):
        err = AllEndpointsFailedError("authorization required")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_parenthesized_401_detected(self):
        err = AllEndpointsFailedError("Error (401) from upstream proxy")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_bare_401_in_unrelated_message_not_matched(self):
        """'401' without parentheses or 'unauthorized' context should not match."""
        err = AllEndpointsFailedError("processed 401 records successfully")
        assert OrgRuntime._is_quota_auth_error(err) is False

    def test_403_forbidden_detected(self):
        err = AllEndpointsFailedError("HTTP (403) Forbidden")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_invalid_api_key_detected(self):
        err = AllEndpointsFailedError("Error: invalid api key provided")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_invalid_api_key_underscore_detected(self):
        err = AllEndpointsFailedError("invalid_api_key")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_authentication_failed_detected(self):
        err = AllEndpointsFailedError("authentication failed for endpoint")
        assert OrgRuntime._is_quota_auth_error(err) is True

    def test_forbidden_keyword_detected(self):
        err = Exception("request forbidden by server policy")
        assert OrgRuntime._is_quota_auth_error(err) is True


class TestIssue578Regression:
    """Regression guards for GitHub issue #578 (task stuck on error handling crash)."""

    def test_relay_502_all_endpoints_failed_does_not_crash_or_misclassify(self):
        """502 from relay is transient — must not trigger quota pause or AttributeError."""
        err = AllEndpointsFailedError(
            "All endpoints failed: Stream: all 1 endpoints failed. "
            "Last error: Server error '502 Bad Gateway' for url 'https://lanapi.site/v1/chat/completions'",
            error_categories={"transient"},
        )
        assert OrgRuntime._is_quota_auth_error(err) is False

    def test_legacy_error_without_categories_attribute_survives_getattr(self):
        """v1.27.12 raised AttributeError here; getattr + string fallback must not."""
        err = AllEndpointsFailedError(
            "All endpoints failed: Server error '502 Bad Gateway'",
        )
        del err.error_categories
        # Must not raise AttributeError
        result = OrgRuntime._is_quota_auth_error(err)
        assert result is False
