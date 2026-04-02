"""L1 Unit Tests: Error types, classification, and tool errors."""

import pytest

from openakita.core.errors import UserCancelledError
from openakita.tools.errors import ToolError, ErrorType, classify_error


class TestUserCancelledError:
    def test_create_basic(self):
        err = UserCancelledError()
        assert isinstance(err, Exception)

    def test_create_with_reason(self):
        err = UserCancelledError(reason="用户按了取消", source="cli")
        assert "取消" in err.reason
        assert err.source == "cli"


class TestToolError:
    def test_create_tool_error(self):
        err = ToolError(
            error_type=ErrorType.TRANSIENT,
            tool_name="web_search",
            message="Connection timeout",
        )
        assert err.error_type == ErrorType.TRANSIENT
        assert err.tool_name == "web_search"

    def test_to_dict(self):
        err = ToolError(
            error_type=ErrorType.PERMISSION,
            tool_name="write_file",
            message="Permission denied",
            retry_suggestion="Try a different path",
        )
        d = err.to_dict()
        assert d["error_type"] == "permission"
        assert d["tool_name"] == "write_file"
        assert "retry_suggestion" in d

    def test_to_tool_result(self):
        err = ToolError(
            error_type=ErrorType.RESOURCE_NOT_FOUND,
            tool_name="read_file",
            message="File not found",
        )
        result = err.to_tool_result()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_with_alternatives(self):
        err = ToolError(
            error_type=ErrorType.PERMANENT,
            tool_name="browser_open",
            message="Browser not available",
            alternative_tools=["web_search"],
        )
        assert err.alternative_tools == ["web_search"]


class TestErrorClassification:
    def test_classify_timeout(self):
        err = classify_error(TimeoutError("Request timed out"), tool_name="web_search")
        assert isinstance(err, ToolError)
        assert err.error_type in (ErrorType.TIMEOUT, ErrorType.TRANSIENT)

    def test_classify_permission(self):
        err = classify_error(PermissionError("Access denied"), tool_name="write_file")
        assert isinstance(err, ToolError)
        assert err.error_type == ErrorType.PERMISSION

    def test_classify_file_not_found(self):
        err = classify_error(FileNotFoundError("No such file"), tool_name="read_file")
        assert isinstance(err, ToolError)
        assert err.error_type == ErrorType.RESOURCE_NOT_FOUND

    def test_classify_generic(self):
        err = classify_error(RuntimeError("Something broke"), tool_name="unknown")
        assert isinstance(err, ToolError)


class TestLLMErrorClassification:
    """LLMProvider._classify_error: model-not-found should be structural, not transient."""

    @staticmethod
    def _classify(error: str) -> str:
        from openakita.llm.providers.base import LLMProvider
        return LLMProvider._classify_error(error)

    def test_model_not_found_503_is_structural(self):
        err = (
            'API error (503): {"error":{"code":"model_not_found",'
            '"message":"No available channel for model gpt-5.4-thinking '
            'under group default (distributor)","type":"new_api_error"}}'
        )
        assert self._classify(err) == "structural"

    def test_no_available_channel_is_structural(self):
        assert self._classify("no available channel for model xyz") == "structural"

    def test_model_decommissioned_is_structural(self):
        assert self._classify("model_decommissioned: gpt-4-vision") == "structural"

    def test_model_not_available_is_structural(self):
        assert self._classify("The model 'xxx' is model_not_available") == "structural"

    def test_model_does_not_exist_is_structural(self):
        assert self._classify("The model 'gpt-99' model does not exist") == "structural"

    def test_generic_does_not_exist_stays_out_of_model_check(self):
        """'does not exist' without 'model' should NOT match the model-unavailable check."""
        result = self._classify("function 'xyz' does not exist in tool definitions (400)")
        assert result == "structural"
        # Still structural via the format-error check, NOT via model_not_found path.
        # Verify by checking that it would NOT match if we only had the model check:
        from openakita.llm.providers.base import LLMProvider
        model_kws = [
            "model_not_found", "model not found", "no available channel",
            "model_decommissioned", "deprecated_model",
            "model_not_available", "model not available",
            "model does not exist",
        ]
        err_lower = "function 'xyz' does not exist in tool definitions (400)".lower()
        assert not any(kw in err_lower for kw in model_kws)

    def test_generic_503_still_transient(self):
        assert self._classify("API error (503): service unavailable") == "transient"

    def test_cpu_overloaded_503_still_transient(self):
        err = (
            'API error (503): {"error":{"message":"system cpu overloaded",'
            '"type":"new_api_error","code":"system_cpu_overloaded"}}'
        )
        assert self._classify(err) == "transient"

    def test_quota_still_quota(self):
        err = 'API error (403): {"error":{"code":"insufficient_user_quota"}}'
        assert self._classify(err) == "quota"

    def test_auth_still_auth(self):
        assert self._classify("401 Unauthorized: invalid api key") == "auth"


class TestErrorTypes:
    def test_all_types_exist(self):
        types = [
            ErrorType.TRANSIENT, ErrorType.PERMANENT, ErrorType.PERMISSION,
            ErrorType.TIMEOUT, ErrorType.VALIDATION, ErrorType.RESOURCE_NOT_FOUND,
            ErrorType.RATE_LIMIT, ErrorType.DEPENDENCY,
        ]
        assert len(types) == 8
