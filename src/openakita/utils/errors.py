"""
Unified user-friendly error formatting.

Provides a single entry point for mapping technical error strings to
human-readable messages. Used by CLI, IM gateway, and API layers.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """Coarse error classification for UI treatment."""

    AUTH = "auth"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    CONTENT_FILTER = "content_filter"
    NETWORK = "network"
    SERVER = "server"
    UNKNOWN = "unknown"


def classify_error(error: str) -> ErrorCategory:
    """Classify a raw error string into a coarse category."""
    el = error.lower()

    if "data_inspection" in el or "inappropriate content" in el:
        return ErrorCategory.CONTENT_FILTER

    # "all endpoints failed" must be checked first and sub-classified,
    # matching the original gateway.py logic to avoid behavioral regression.
    if "all endpoints failed" in el or "allendpointsfailederror" in el:
        if any(k in el for k in ("api key", "auth", "unauthorized", "401", "forbidden", "403")):
            return ErrorCategory.AUTH
        if any(k in el for k in ("quota", "rate limit", "429", "余额", "insufficient")):
            return ErrorCategory.QUOTA
        return ErrorCategory.SERVER

    if any(k in el for k in ("api key", "auth", "unauthorized", "401", "forbidden", "403")):
        return ErrorCategory.AUTH

    if any(k in el for k in ("quota", "rate limit", "429", "余额", "insufficient")):
        return ErrorCategory.QUOTA

    if any(k in el for k in ("timeout", "timed out", "deadline")):
        return ErrorCategory.TIMEOUT

    if any(k in el for k in ("connect", "dns", "resolve", "network", "unreachable")):
        return ErrorCategory.NETWORK

    if any(k in el for k in ("500", "502", "503", "504", "internal server")):
        return ErrorCategory.SERVER

    return ErrorCategory.UNKNOWN


_CATEGORY_MESSAGES: dict[ErrorCategory, str] = {
    ErrorCategory.CONTENT_FILTER: (
        "⚠️ 抱歉，处理过程中获取到的部分内容触发了平台安全审核，"
        "请换个方式重新提问。"
    ),
    ErrorCategory.AUTH: "⚠️ AI 服务认证失败，请检查 API Key 配置是否正确。",
    ErrorCategory.QUOTA: "⚠️ AI 服务配额已用尽或请求过于频繁，请稍后重试。",
    ErrorCategory.TIMEOUT: "⚠️ 处理超时，请稍后重试或简化您的问题。",
    ErrorCategory.NETWORK: "⚠️ 网络连接失败，请检查网络设置后重试。",
    ErrorCategory.SERVER: "⚠️ AI 服务暂时不可用，请稍后重试。",
}


def format_user_friendly_error(error: str) -> str:
    """Map a technical error string to a user-visible message.

    Keeps the full error for logging; only the return value should be
    shown to end users (IM / CLI / Desktop).
    """
    cat = classify_error(error)
    msg = _CATEGORY_MESSAGES.get(cat)
    if msg:
        return msg
    short = error[:120].split("\n")[0]
    return f"⚠️ 处理出错: {short}"
