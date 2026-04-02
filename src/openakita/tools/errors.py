"""
结构化工具错误

提供 ToolError 异常类和 ErrorType 枚举，
让 LLM 能根据错误类型决定：重试 / 换方案 / 报告用户。

Usage:
    from openakita.tools.errors import ToolError, ErrorType

    try:
        result = await shell_tool.run(command)
    except TimeoutError:
        raise ToolError(
            error_type=ErrorType.TIMEOUT,
            tool_name="run_shell",
            message="命令执行超时",
            retry_suggestion="请增加 timeout 参数后重试",
        )
"""

import json
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """工具错误类型"""

    TRANSIENT = "transient"  # 暂时性错误（网络超时、服务不可用等），可重试
    PERMANENT = "permanent"  # 永久性错误（逻辑错误、不支持的操作），需换方案
    PERMISSION = "permission"  # 权限错误，不可操作
    TIMEOUT = "timeout"  # 超时，可增加超时时间重试
    VALIDATION = "validation"  # 参数验证失败，需修正参数
    RESOURCE_NOT_FOUND = "not_found"  # 资源不存在（文件、URL 等）
    RATE_LIMIT = "rate_limit"  # 速率限制，需等待后重试
    DEPENDENCY = "dependency"  # 依赖缺失（缺少命令、库等）


# LLM 友好的错误类型说明，注入到 tool_result 中帮助 LLM 决策
_ERROR_TYPE_HINTS: dict[ErrorType, str] = {
    ErrorType.TRANSIENT: "暂时性错误，可以直接重试",
    ErrorType.PERMANENT: "永久性错误，请换一种方法或工具",
    ErrorType.PERMISSION: "权限不足，无法执行此操作",
    ErrorType.TIMEOUT: "执行超时，可以增加 timeout 参数后重试",
    ErrorType.VALIDATION: "参数有误，请检查并修正参数后重试",
    ErrorType.RESOURCE_NOT_FOUND: "目标资源不存在，请确认路径/URL 后重试",
    ErrorType.RATE_LIMIT: "请求频率过高，请等待几秒后重试",
    ErrorType.DEPENDENCY: "缺少依赖（命令或库），请先安装后重试",
}


class ToolError(Exception):
    """
    结构化工具错误。

    包含错误类型、重试建议、替代工具等信息，
    序列化后以 JSON 格式返回给 LLM，帮助其做出更好的决策。
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
        """序列化为字典"""
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
        序列化为 tool_result 字符串。

        以 JSON 格式返回，LLM 可以解析 error_type 字段来决策。
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)


def classify_error(
    error: Exception,
    tool_name: str = "",
) -> ToolError:
    """
    将通用异常分类为结构化 ToolError。

    根据异常类型自动推断 ErrorType：
    - TimeoutError -> TIMEOUT
    - FileNotFoundError -> RESOURCE_NOT_FOUND
    - PermissionError -> PERMISSION
    - ValueError -> VALIDATION
    - ConnectionError -> TRANSIENT
    - 其他 -> PERMANENT
    """
    error_msg = str(error)

    if isinstance(error, ToolError):
        return error

    if isinstance(error, TimeoutError):
        return ToolError(
            error_type=ErrorType.TIMEOUT,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="增加 timeout 参数后重试",
        )

    if isinstance(error, FileNotFoundError):
        return ToolError(
            error_type=ErrorType.RESOURCE_NOT_FOUND,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="请确认文件路径是否正确",
        )

    if isinstance(error, IsADirectoryError):
        return ToolError(
            error_type=ErrorType.VALIDATION,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="该路径是目录而非文件，请使用 list_directory 查看目录内容",
            alternative_tools=["list_directory"],
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
            retry_suggestion="请检查参数格式和取值范围",
        )

    if isinstance(error, (ConnectionError, OSError)):
        # 检查是否是连接/网络相关
        lower_msg = error_msg.lower()
        if any(kw in lower_msg for kw in ("connect", "network", "refused", "timeout", "dns")):
            return ToolError(
                error_type=ErrorType.TRANSIENT,
                tool_name=tool_name,
                message=error_msg,
                retry_suggestion="网络问题，请稍后重试",
            )

    # 检查常见的错误模式
    lower_msg = error_msg.lower()

    if "rate limit" in lower_msg or "too many requests" in lower_msg or "429" in lower_msg:
        return ToolError(
            error_type=ErrorType.RATE_LIMIT,
            tool_name=tool_name,
            message=error_msg,
            retry_suggestion="请等待 5 秒后重试",
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
            retry_suggestion="请先安装所需的命令或工具",
        )

    # 默认为永久性错误
    return ToolError(
        error_type=ErrorType.PERMANENT,
        tool_name=tool_name,
        message=error_msg,
    )
