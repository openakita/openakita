"""LLM 错误分类枚举。

将原先散落在 LLMProvider._classify_error (字符串返回) 和
LLMClient._resolve_providers_with_fallback / _friendly_error_hint (字符串比较)
中的错误分类统一为枚举，消除拼写风险并提供单一分类入口。
"""

from __future__ import annotations

from enum import StrEnum


class FailoverReason(StrEnum):
    """LLM 端点错误分类。

    值与原 ``LLMProvider._error_category`` 字符串保持一致，
    确保 ``mark_unhealthy(category=...)`` 等现有调用无需改签名。
    """

    QUOTA = "quota"
    AUTH = "auth"
    STRUCTURAL = "structural"
    TRANSIENT = "transient"
    CONTENT_SAFETY = "content_safety"
    UNKNOWN = "unknown"
