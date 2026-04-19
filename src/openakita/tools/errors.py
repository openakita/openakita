"""
Structured tool errors

Provides the ToolError exception class and ErrorType enum,
allowing the LLM to decide based on error type: retry / try another approach / report to user.

Usage:
    from openakita.tools.errors import ToolError, ErrorType

    try:
        result = await shell_tool.run(command)
    except TimeoutError:
        raise ToolError(
            error_type=ErrorType.TIMEOUT,
            tool_name="run_shell",
            message="Command execution timed out",
            retry_suggestion="Please increase the timeout parameter and retry",
        )
"""

import json
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Tool error types"""

    TRANSIENT = "transient"  # Transient error (network timeout, service unavailable, etc.), retryable
    PERMANENT = "permanent"  # Permanent error (logic error, unsupported operation), try another approach
    PERMISSION = "permission"  # Permission error, operation not allowed
    TIMEOUT = "timeout"  # Timeout, may retry with a longer timeout
    VALIDATION = "validation"  # Parameter validation failed, fix parameters
    RESOURCE_NOT_FOUND = "not_found"  # Resource not found (file, URL, etc.)
    RATE_LIMIT = "rate_limit"  # Rate limit exceeded, retry after waiting
    DEPENDENCY = "dependency"  # Dependency missing (missing command, library, etc.)


# LLM-friendly error type hints, injected into tool_result to help the LLM decide
_ERROR_TYPE_HINTS: dict[ErrorType, str] = {
    ErrorType.TRANSIENT: "Transient error, you can retry directly",
    ErrorType.PERMANENT: "Permanent error, please try a different method or tool",
    ErrorType.PERMISSION: "Insufficient permissions, unable to perform this operation",
    ErrorType.TIMEOUT: "Execution timed out, you can increase the timeout parameter and retry",
    ErrorType.VALIDATION: "Invalid parameters, please check and correct the parameters before retrying",
    ErrorType.RESOURCE_NOT_FOUND: "Target resource not found, please confirm the path/URL and retry",
    ErrorType.RATE_LIMIT: "Request rate too high, please wait a few seconds and retry",
    ErrorType.DEPENDENCY: "Missing dependency (command or library), please install it first and retry",
}


class ToolError(Exception):
    """
    Structured tool error.

    Contains error type, retry suggestion, alternative tools, and other info,
    serialized as JSON and returned to the LLM to help it make better decisions.
    """

    def __init__(
        self,
        error_type: ErrorType,
        tool_name: str,
        message: str,
        *,
        retry_suggestion: str | None = None,
        alternative_tools: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.error_type = error_type
        self.tool_name = tool_name
        self.message = message
        self.retry_suggestion = retry_suggestion
        self.alternative_tools = alternative_tools
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        result: dict[str, Any] = {
            "error": True,
            "error_type": self.error_type.value,
            "message": self.message,
            "tool_name": self.tool_name,
            "hint": _ERROR_TYPE_HINTS.get(self.error_type, ""),
        }
        if self.retry_suggestion:
            result["retry_suggestion"] = self.retry_suggestion
        if self.alternative_tools:
            result["alternative_tools"] = self.alternative_tools
        if self.details:
            result["details"] = self.details
        return result

    def to_tool_result(self) -> str:
        """
        Serialize as a tool_result string.

        Returned in JSON format so the LLM can parse the error_type field for decision-making.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)


def classify_error(
    error: Exception,
    tool_name: str = "",
) -> ToolError:
    """
    Classify a generic exception as a structured ToolError.

    Automatically infers ErrorType based on exception type:
    - TimeoutError -> TIMEOUT
    - FileNotFoundError -> RESOURCE_NOT_FOUND
    - PermissionError -> PERMISSION
    - ValueError -> VALIDATION
    - ConnectionError -> TRANSIENT
    - Other -> PERMANENT
    """
    error_msg = str(error)

    if isinstance(error, ToolError):
        return error

    if isinstance(error, TimeoutError):
        return ToolError(
            error_type=ErrorType.TIMEOUT,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="Increase the timeout parameter and retry",
        )

    if isinstance(error, FileNotFoundError):
        return ToolError(
            error_type=ErrorType.RESOURCE_NOT_FOUND,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="Please confirm the file path is correct",
        )

    if isinstance(error, PermissionError):
        return ToolError(
            error_type=ErrorType.PERMISSION,
            tool_name=tool_name,
            message=error_msg,
        )

    if isinstance(error, ValueError):
        return ToolError(
            error_type=ErrorType.VALIDATION,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="Please check the parameter format and value range",
        )

    if isinstance(error, (ConnectionError, OSError)):
        # Check if it's connection/network related
        lower_msg = error_msg.lower()
        if any(kw in lower_msg for kw in ("connect", "network", "refused", "timeout", "dns")):
            return ToolError(
                error_type=ErrorType.TRANSIENT,
                tool_name=tool_name,
                message=error_msg,
                retry_suggestion="Network issue, please retry later",
            )

    # Check common error patterns
    lower_msg = error_msg.lower()

    if "rate limit" in lower_msg or "too many requests" in lower_msg or "429" in lower_msg:
        return ToolError(
            error_type=ErrorType.RATE_LIMIT,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="Please wait 5 seconds and retry",
        )

    if "not found" in lower_msg or "no such file" in lower_msg or "does not exist" in lower_msg:
        return ToolError(
            error_type=ErrorType.RESOURCE_NOT_FOUND,
            tool_name=tool_name,
            message=error_msg,
        )

    if "command not found" in lower_msg or "not recognized" in lower_msg:
        return ToolError(
            error_type=ErrorType.DEPENDENCY,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="Please install the required command or tool first",
        )

    # Default to permanent error
    return ToolError(
        error_type=ErrorType.PERMANENT,
        tool_name=tool_name,
        message=error_msg,
    )
