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


class TestErrorTypes:
    def test_all_types_exist(self):
        types = [
            ErrorType.TRANSIENT, ErrorType.PERMANENT, ErrorType.PERMISSION,
            ErrorType.TIMEOUT, ErrorType.VALIDATION, ErrorType.RESOURCE_NOT_FOUND,
            ErrorType.RATE_LIMIT, ErrorType.DEPENDENCY,
        ]
        assert len(types) == 8

